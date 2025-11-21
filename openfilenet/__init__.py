"""
OpenFilenet MVP: simple LAN P2P file sharing via Python API.

Public API:
    openfilenet.token                # room/namespace, default "default"
    openfilenet.port.udp_discovery   # UDP discovery port, default 51230
    openfilenet.port.tcp_server      # TCP server port, default 0 (OS-chosen)
    openfilenet.debug                # debug printing, default False
    openfilenet.encrypt              # enable AES-256-GCM encryption, default False
    openfilenet.key                  # shared secret string for encryption (required if encrypt=True)

    from openfilenet import share_files, list_files, get_file, add_peer
"""

from __future__ import annotations

import base64
import json
import os
import socket
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Any

# ---------------------------------------------------------------------------
# Optional crypto imports
# ---------------------------------------------------------------------------

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # type: ignore
except ImportError:  # cryptography is optional; only needed if encrypt=True
    AESGCM = None

import hashlib

# ---------------------------------------------------------------------------
# Public configuration knobs
# ---------------------------------------------------------------------------

# Room / namespace. Peers only connect if their tokens match.
token: str = "default"

# Debug flag: when True, prints internal events to stdout.
debug: bool = False

# Encryption flag: when True, file contents transferred via get_file() are
# encrypted using AES-256-GCM with a key derived from openfilenet.key.
encrypt: bool = False

# Shared secret string used to derive the AES-256-GCM key when encrypt=True.
# Must be the same on all peers that want to talk securely.
key: Optional[str] = None


class _PortConfig:
    """
    Configuration container for ports.

    NOTE: These should be set BEFORE the first call to share_files/list_files/get_file.
    Changing them after networking has started will NOT rebind sockets in this MVP.
    """

    def __init__(self) -> None:
        # UDP discovery port (must match across peers to see each other)
        self.udp_discovery: int = 51230

        # TCP server port (0 = OS picks ephemeral free port)
        # This is per-node; other peers learn it via UDP discovery.
        self.tcp_server: int = 0


port = _PortConfig()

# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------


@dataclass
class FileMeta:
    rel_path: str
    abs_path: Path
    size: int
    mtime: float


@dataclass
class PeerInfo:
    peer_id: str
    host: str
    port: int
    last_seen: float = field(default_factory=time.time)


_state_lock = threading.Lock()

_started: bool = False
_peer_id: str = str(uuid.uuid4())
_server_port: Optional[int] = None

_shared_index: Dict[str, FileMeta] = {}       # rel_path -> FileMeta
_peers: Dict[str, PeerInfo] = {}             # peer_id -> PeerInfo

_DISCOVERY_INTERVAL = 1.0        # seconds between broadcasts
_PEER_TIMEOUT = 15.0             # drop peers after this many seconds

# Cached encryption key bytes
_key_bytes: Optional[bytes] = None


def _log(msg: str) -> None:
    if debug:
        print(f"[openfilenet] {msg}")


def _get_key_bytes() -> bytes:
    """
    Derive and cache a 32-byte AES key from openfilenet.key when encrypt=True.
    """
    global _key_bytes

    if not encrypt:
        raise RuntimeError("Encryption is disabled (encrypt=False) but crypto key requested")

    if AESGCM is None:
        raise RuntimeError(
            "Encryption requested (openfilenet.encrypt=True) but the 'cryptography' "
            "package is not installed. Install it with 'pip install cryptography'."
        )

    if key is None:
        raise RuntimeError("Encryption requested (encrypt=True) but openfilenet.key is not set")

    if _key_bytes is None:
        # Derive 32-byte key from key string via SHA-256
        _key_bytes = hashlib.sha256(key.encode("utf-8")).digest()
        _log("Derived AES-256-GCM key from openfilenet.key")

    return _key_bytes


# ---------------------------------------------------------------------------
# Startup helpers
# ---------------------------------------------------------------------------


def _ensure_started() -> None:
    """Start TCP server and discovery thread once, lazily."""
    global _started, _server_port
    with _state_lock:
        if _started:
            return

        _log("Starting networking...")
        # Start TCP server first so we know the actual port for discovery
        _server_port = _start_tcp_server_thread()
        _start_discovery_thread()
        _started = True
        _log(f"Networking started. TCP server port={_server_port}, UDP discovery port={port.udp_discovery}")


# ---------------------------------------------------------------------------
# Discovery (UDP broadcast)
# ---------------------------------------------------------------------------


def _start_discovery_thread() -> None:
    thread = threading.Thread(target=_discovery_loop, daemon=True)
    thread.start()
    _log("Discovery thread started")


def _discovery_loop() -> None:
    """
    Periodically broadcast our presence and listen for announces from other peers.
    """
    udp_port = port.udp_discovery

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    except OSError:
        # Some systems may not support some of these; ignore for MVP.
        pass

    # Bind to all interfaces on the discovery port to receive broadcasts
    try:
        sock.bind(("", udp_port))
    except OSError as e:
        _log(f"Failed to bind UDP discovery on port {udp_port}: {e}")
        return

    _log(f"UDP discovery bound on 0.0.0.0:{udp_port}")
    sock.settimeout(1.0)

    last_broadcast = 0.0

    while True:
        now = time.time()

        # 1) Broadcast our own presence periodically
        if _server_port is not None and now - last_broadcast >= _DISCOVERY_INTERVAL:
            msg = {
                "type": "announce",
                "token": token,
                "peer_id": _peer_id,
                "port": _server_port,
            }
            try:
                data = (json.dumps(msg) + "\n").encode("utf-8")
                # Explicit 255.255.255.255 broadcast
                sock.sendto(data, ("255.255.255.255", udp_port))
                _log(f"Sent announce: token={token} peer_id={_peer_id} port={_server_port}")
            except OSError as e:
                _log(f"Error sending UDP broadcast: {e}")
            last_broadcast = now

        # 2) Receive announces from others
        try:
            data, (host, _port) = sock.recvfrom(65535)
        except socket.timeout:
            # No packet, just continue
            pass
        except OSError as e:
            _log(f"UDP socket error: {e}; stopping discovery loop")
            break
        else:
            try:
                line = data.decode("utf-8").strip()
                if not line:
                    continue
                msg = json.loads(line)
            except Exception:
                continue

            msg_type = msg.get("type")
            if msg_type not in ("announce", "announce_reply"):
                continue

            if msg.get("token") != token:
                continue  # different room

            peer_id = msg.get("peer_id")
            peer_port = msg.get("port")
            if not peer_id or not isinstance(peer_port, int):
                continue

            if peer_id == _peer_id:
                # Ignore our own messages
                continue

            # Record the peer
            with _state_lock:
                _peers[peer_id] = PeerInfo(
                    peer_id=peer_id,
                    host=host,
                    port=peer_port,
                    last_seen=time.time(),
                )
            _log(f"Discovered peer: peer_id={peer_id} host={host} port={peer_port}")

            # If this was a broadcast announce, send a unicast reply back so
            # the original sender also learns about us (handles asymmetric broadcast).
            if msg_type == "announce" and _server_port is not None:
                reply = {
                    "type": "announce_reply",
                    "token": token,
                    "peer_id": _peer_id,
                    "port": _server_port,
                }
                try:
                    data = (json.dumps(reply) + "\n").encode("utf-8")
                    sock.sendto(data, (host, udp_port))
                    _log(f"Sent unicast announce_reply to {host}:{udp_port} with port={_server_port}")
                except OSError as e:
                    _log(f"Error sending unicast announce_reply: {e}")

        # 3) Prune stale peers
        now = time.time()
        with _state_lock:
            to_delete = [
                pid
                for pid, info in _peers.items()
                if now - info.last_seen > _PEER_TIMEOUT
            ]
            for pid in to_delete:
                _log(f"Removing stale peer: peer_id={pid}")
                del _peers[pid]


# ---------------------------------------------------------------------------
# TCP server
# ---------------------------------------------------------------------------


def _start_tcp_server_thread() -> int:
    """
    Start a TCP server that can respond to list_files and get_file requests.
    Returns the bound port.
    """
    tcp_port = port.tcp_server

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    try:
        sock.bind(("", tcp_port))  # 0 means OS chooses
        sock.listen()
    except OSError as e:
        _log(f"Failed to bind TCP server on port {tcp_port}: {e}")
        raise

    bound_port = sock.getsockname()[1]
    _log(f"TCP server listening on 0.0.0.0:{bound_port}")

    def _accept_loop() -> None:
        while True:
            try:
                conn, addr = sock.accept()
            except OSError as e:
                _log(f"TCP accept error: {e}; stopping accept loop")
                break
            _log(f"Accepted TCP connection from {addr}")
            threading.Thread(
                target=_handle_connection,
                args=(conn, addr),
                daemon=True,
            ).start()

    threading.Thread(target=_accept_loop, daemon=True).start()
    return bound_port


def _handle_connection(conn: socket.socket, addr: Any) -> None:
    f = conn.makefile("rb")
    try:
        line = f.readline()
        if not line:
            return
        try:
            msg = json.loads(line.decode("utf-8"))
        except Exception:
            _log(f"Invalid JSON from {addr}")
            return

        if msg.get("token") != token:
            # Token mismatch, ignore
            _log(f"Token mismatch from {addr}")
            return

        msg_type = msg.get("type")
        if msg_type == "list_files":
            _log(f"Handling list_files from {addr}")
            _handle_list_files(conn)
        elif msg_type == "get_file":
            path = msg.get("path")
            if isinstance(path, str):
                _log(f"Handling get_file for {path} from {addr}")
                _handle_get_file(conn, path)
    finally:
        try:
            f.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass


def _handle_list_files(conn: socket.socket) -> None:
    with _state_lock:
        files = [
            {
                "path": rel,
                "size": meta.size,
                "mtime": meta.mtime,
            }
            for rel, meta in _shared_index.items()
        ]

    _log(f"Sending file_list with {len(files)} entries")
    resp = {
        "type": "file_list",
        "files": files,
    }
    data = (json.dumps(resp) + "\n").encode("utf-8")
    try:
        conn.sendall(data)
    except OSError as e:
        _log(f"Error sending file_list: {e}")


def _handle_get_file(conn: socket.socket, rel_path: str) -> None:
    """
    Server-side handler for get_file: sends either plaintext bytes (default)
    or AES-256-GCM encrypted bytes if openfilenet.encrypt=True.
    """
    with _state_lock:
        meta = _shared_index.get(rel_path)

    if meta is None:
        _log(f"get_file: not found: {rel_path}")
        resp = {
            "type": "file_info",
            "status": "error",
            "message": "not found",
        }
        data = (json.dumps(resp) + "\n").encode("utf-8")
        try:
            conn.sendall(data)
        except OSError:
            pass
        return

    _log(f"get_file: sending {rel_path} ({meta.size} bytes)")

    if not encrypt:
        # Original behavior: send plaintext header + plaintext bytes
        resp = {
            "type": "file_info",
            "status": "ok",
            "size": meta.size,
        }
        header = (json.dumps(resp) + "\n").encode("utf-8")
        try:
            conn.sendall(header)
        except OSError as e:
            _log(f"Error sending file_info header: {e}")
            return

        # Stream file bytes
        try:
            with meta.abs_path.open("rb") as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    conn.sendall(chunk)
        except OSError as e:
            _log(f"Error streaming file bytes: {e}")
            return
        return

    # Encryption path: read file into memory and send AES-256-GCM ciphertext
    try:
        plaintext = meta.abs_path.read_bytes()
    except OSError as e:
        _log(f"Error reading file for encryption: {e}")
        resp = {
            "type": "file_info",
            "status": "error",
            "message": "read_error",
        }
        data = (json.dumps(resp) + "\n").encode("utf-8")
        try:
            conn.sendall(data)
        except OSError:
            pass
        return

    try:
        key_bytes = _get_key_bytes()
        aead = AESGCM(key_bytes)  # type: ignore
        nonce = os.urandom(12)
        aad = token.encode("utf-8")  # bind to room token
        ciphertext = aead.encrypt(nonce, plaintext, aad)
    except Exception as e:
        _log(f"Error encrypting file: {e}")
        resp = {
            "type": "file_info",
            "status": "error",
            "message": "encrypt_error",
        }
        data = (json.dumps(resp) + "\n").encode("utf-8")
        try:
            conn.sendall(data)
        except OSError:
            pass
        return

    resp = {
        "type": "file_info",
        "status": "ok",
        "size": meta.size,  # original plaintext size (for info)
        "encrypted": True,
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "ciphertext_size": len(ciphertext),
    }
    header = (json.dumps(resp) + "\n").encode("utf-8")
    try:
        conn.sendall(header)
    except OSError as e:
        _log(f"Error sending encrypted file_info header: {e}")
        return

    try:
        conn.sendall(ciphertext)
    except OSError as e:
        _log(f"Error sending encrypted file bytes: {e}")
        return


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def share_files(paths: Any) -> None:
    """
    Register files/directories to share over the network.

    paths:
        - str or Path -> single directory or file
        - list/tuple of str/Path -> multiple entries
    """
    _ensure_started()

    if isinstance(paths, (str, Path)):
        paths = [paths]

    new_index: Dict[str, FileMeta] = {}

    for p in paths:
        path = Path(p).expanduser().resolve()
        if path.is_dir():
            base = path.name
            for file in path.rglob("*"):
                if file.is_file():
                    rel = str(Path(base) / file.relative_to(path))
                    try:
                        st = file.stat()
                    except OSError:
                        continue
                    new_index[rel] = FileMeta(
                        rel_path=rel,
                        abs_path=file,
                        size=st.st_size,
                        mtime=st.st_mtime,
                    )
        elif path.is_file():
            rel = path.name
            try:
                st = path.stat()
            except OSError:
                continue
            new_index[rel] = FileMeta(
                rel_path=rel,
                abs_path=path,
                size=st.st_size,
                mtime=st.st_mtime,
            )
        else:
            # Non-existing path: ignore in MVP
            _log(f"share_files: path does not exist, ignoring: {path}")
            continue

    with _state_lock:
        _shared_index.update(new_index)

    _log(f"share_files: indexed {len(new_index)} new files, total shared={len(_shared_index)}")


def list_files() -> List[Dict[str, Any]]:
    """
    Discover peers (same token) and list all files they are sharing.

    Returns a list of dicts:
        {
            "peer_id": str,
            "path": str,
            "size": int,
            "mtime": float,
        }
    """
    _ensure_started()

    with _state_lock:
        peers = list(_peers.values())
    _log(f"list_files: known peers={len(peers)}")

    all_files: List[Dict[str, Any]] = []

    for peer in peers:
        try:
            files = _list_files_from_peer(peer)
        except Exception as e:
            _log(f"Error listing files from peer {peer.peer_id}@{peer.host}:{peer.port}: {e}")
            continue
        _log(f"Received {len(files)} files from peer {peer.peer_id}")
        for f in files:
            all_files.append(
                {
                    "peer_id": peer.peer_id,
                    "path": f.get("path"),
                    "size": f.get("size"),
                    "mtime": f.get("mtime"),
                }
            )

    _log(f"list_files: returning {len(all_files)} total files")
    return all_files


def _list_files_from_peer(peer: PeerInfo) -> List[Dict[str, Any]]:
    _log(f"Connecting to peer {peer.peer_id} at {peer.host}:{peer.port} for list_files")
    with socket.create_connection((peer.host, peer.port), timeout=3.0) as conn:
        req = {
            "type": "list_files",
            "token": token,
        }
        data = (json.dumps(req) + "\n").encode("utf-8")
        conn.sendall(data)
        f = conn.makefile("rb")
        line = f.readline()
        if not line:
            _log("list_files: no response line from peer")
            return []
        try:
            msg = json.loads(line.decode("utf-8"))
        except Exception:
            _log("list_files: invalid JSON from peer")
            return []
        if msg.get("type") != "file_list":
            _log(f"list_files: unexpected msg type {msg.get('type')}")
            return []
        files = msg.get("files")
        if not isinstance(files, list):
            _log("list_files: 'files' is not a list")
            return []
        return files


def get_file(peer_id: str, path: str, dest: Optional[Any] = None) -> Optional[bytes]:
    """
    Download a file from a specific peer.

    peer_id:
        Must be a peer_id obtained from list_files().
    path:
        Must match the "path" field returned by list_files().
    dest:
        - If None: return file contents as bytes.
        - If str/Path: write file to disk at that path, return None.
    """
    _ensure_started()

    with _state_lock:
        peer = _peers.get(peer_id)
    if peer is None:
        raise ValueError(f"Unknown peer_id {peer_id!r}")

    _log(f"Connecting to peer {peer.peer_id} at {peer.host}:{peer.port} for get_file({path})")

    with socket.create_connection((peer.host, peer.port), timeout=5.0) as conn:
        req = {
            "type": "get_file",
            "token": token,
            "path": path,
        }
        data = (json.dumps(req) + "\n").encode("utf-8")
        conn.sendall(data)

        f = conn.makefile("rb")
        header_line = f.readline()
        if not header_line:
            raise IOError("No response from peer")

        try:
            header = json.loads(header_line.decode("utf-8"))
        except Exception as e:
            raise IOError(f"Invalid header from peer: {e}") from e

        if header.get("status") != "ok":
            raise IOError(header.get("message", "Unknown error"))

        encrypted_flag = header.get("encrypted", False)

        if not encrypted_flag:
            # Original plaintext path
            size = header.get("size")
            if not isinstance(size, int):
                raise IOError("Invalid size from peer")

            _log(f"get_file: expecting {size} plaintext bytes for {path}")

            if dest is not None:
                dest_path = Path(dest)
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                with dest_path.open("wb") as out:
                    remaining = size
                    while remaining > 0:
                        chunk = f.read(min(65536, remaining))
                        if not chunk:
                            break
                        out.write(chunk)
                        remaining -= len(chunk)
                _log(f"get_file: wrote plaintext to {dest_path}")
                return None
            else:
                chunks: List[bytes] = []
                remaining = size
                while remaining > 0:
                    chunk = f.read(min(65536, remaining))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    remaining -= len(chunk)
                data_bytes = b"".join(chunks)
                _log(f"get_file: received {size - remaining} plaintext bytes in memory")
                return data_bytes

        # Encrypted path
        if not encrypt:
            raise IOError(
                "Remote peer sent encrypted content but openfilenet.encrypt=False locally. "
                "Enable encryption and set the same openfilenet.key on both peers."
            )

        nonce_b64 = header.get("nonce")
        cipher_size = header.get("ciphertext_size")
        if not isinstance(nonce_b64, str) or not isinstance(cipher_size, int):
            raise IOError("Invalid encrypted header from peer")

        try:
            nonce = base64.b64decode(nonce_b64.encode("ascii"))
        except Exception as e:
            raise IOError(f"Invalid nonce from peer: {e}") from e

        _log(f"get_file: expecting {cipher_size} encrypted bytes for {path}")

        ciphertext_chunks: List[bytes] = []
        remaining = cipher_size
        while remaining > 0:
            chunk = f.read(min(65536, remaining))
            if not chunk:
                break
            ciphertext_chunks.append(chunk)
            remaining -= len(chunk)

        ciphertext = b"".join(ciphertext_chunks)
        if len(ciphertext) != cipher_size:
            raise IOError("Did not receive the expected amount of ciphertext from peer")

        try:
            key_bytes = _get_key_bytes()
            aead = AESGCM(key_bytes)  # type: ignore
            aad = token.encode("utf-8")
            plaintext = aead.decrypt(nonce, ciphertext, aad)
        except Exception as e:
            raise IOError(f"Decryption failed: {e}") from e

        _log(f"get_file: decrypted {len(plaintext)} bytes for {path}")

        if dest is not None:
            dest_path = Path(dest)
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            with dest_path.open("wb") as out:
                out.write(plaintext)
            _log(f"get_file: wrote decrypted file to {dest_path}")
            return None
        else:
            return plaintext


def add_peer(host: str, port_num: int, peer_id: Optional[str] = None) -> None:
    """
    Manually add a peer (host, port) to the peer list.

    Useful when UDP discovery isn't working due to router/AP/firewall quirks.
    """
    _ensure_started()

    if peer_id is None:
        peer_id = f"static:{host}:{port_num}"

    info = PeerInfo(peer_id=peer_id, host=host, port=port_num, last_seen=time.time())
    with _state_lock:
        _peers[peer_id] = info
    _log(f"add_peer: manually added peer {peer_id} at {host}:{port_num}")
