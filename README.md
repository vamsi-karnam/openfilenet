# OpenFilenet

**Author:** Vamsi Karnam

**License:** Apache License 2.0

*“ Openfilenet is an open-source, modern, LAN-native, cross-platform, P2P file share for real programming workflows - developed and maintained by [Vamsi Karnam](https://www.linkedin.com/in/saivamsikarnam/) - Distributed file access made stupidly simple.”*

---

**OpenFilenet**  lets your code access files on other machines **as if they were local**, using a tiny, dependency-free P2P protocol (optionally secured with AES-256-GCM) using a lightweight, zero-config **peer-to-peer file sharing API** designed for **code runtimes** to be as developer friendly as possible.

Forget SMB, SFTP, FTP, NFS, SCP, HTTP file servers, shared folders, or network mounts.

* Works over local networks / Wi-Fi / Ethernet / VPN
* Cross-platform (Windows, Linux, macOS, Raspberry Pi)
* No servers, no Daemons, no config files
* File fetching in *memory* (no need to write temp files)
* Async discovery via UDP broadcast
* Encrypted transfer option
* Ideal for ML pipelines, sensors, robotics, distributed data collection, edge devices, experiments
* Simple API: `share_files`, `list_files`, `get_file`, `add_peer`

- OpenFilenet abstracts away networking so *your code* can access remote files with zero overhead.

---

## Table of contents

* [Intro & description](#openfilenet)
* [Architecture](#architecture)
* [Core modules](#openfilenet-modules)
* [Framework](#framework)
* [Encryption](#encryption-aes-256-gcm)
* [Language Support](#language-support)
* [Installation](#installation)
* [Requirements](#requirements)
* [Example applications](#example-applications)
* [License / Author / Contact](#license--author--contact--citation)

---

## Architecture
```mermaid
flowchart LR

subgraph System-A
  P1[Openfilenet Peer]
  A1[Share Files on TCP]
  A2[TCP File Server]
end

subgraph System-B
  P2[Openfilenet Peer]
  B1[Get files on TCP]
end

P1 -- "1. UDP Broadcast" --> P2
P2 -- "2. UDP Reply" --> P1
P1 -- "3. Announce Reply" --> P2
P2 -- "4. Request Files on TCP" --> A2 --> B1
A1 --> A2 

```

---

## Openfilenet Modules
For modules see [function reference doc](https://github.com/vamsi-karnam/openfilenet/tree/main/docs)

---


## Framework

OpenFilenet uses a **true peer-to-peer model**. Every instance is both:

1. **A broadcast server** (UDP discovery)
2. **A file server** (TCP file provider)
3. **A file client** (TCP file consumer)

### 1. Discovery (broadcast)

**Description**: Every peer periodically (*default: every 1 sec*) broadcasts a packet over UDP:
**Default protocol**: UDP
**Default port**: 51230

```json
{
  "type": "announce",
  "token": "<token>",
  "peer_id": "...",
  "port": <tcp_port>
}
```
- Any peers listening on the same UDP port receive this broadcast and record the (IP addr, TCP port) of the sender.

> Sometimes discovery can be asymmetric (e.g., some routers drop broadcast packets), OpenFilenet sends “***unicast announce replies***” so both sides learn about each other. The function `add_peer()` exists as a fallback for unusual networks.

### 2. File Index

**Description**: A peer who wants to share the files/directory calls:
```python
share_files("/path/to/myfile.txt")
share_files("/path/to/myfolder")
share_files(["/path/to/a", "/path/to/b"])
```
- OpenFilenet can share a single file or recursively index all files in the directory.
- Other peers can retrieve this index via `list_files()`.

### 3. File Transfer

**Description**: A peer who wants to get the shared files/directory listing calls:
```python
list_files() # to list all files being served by all peers using the same token
get_file(peer_id, path)
```
**Default protocol**: TCP
**Default port**: Ephemeral/Random

- Requests the file to download.
- Receives plaintext or AES-256-GCM encrypted bytes.

This enables processing remote files in-memory without saving to local disk.

---

## Encryption (AES-256-GCM)

OpenFilenet includes optional encryption using the industry-standard AEAD cipher suite:

* **AES-256-GCM**
* 12-byte random nonce
* Authenticated Additional Data (AAD) = token
* Integrity-protected ciphertext

### How to enable:

```python
openfilenet.encrypt = True
openfilenet.key = "my_secret_key"
```

In the background:

* `key` → SHA-256 → 32-byte AES key.
* A random nonce is generated per file: `os.urandom(12)`.
* File bytes are encrypted fully into memory and then sent.

If `encrypt=True`, both peers *must* set the **same key**.

Encryption affects only file transfer, not UDP discovery or metadata.

---


## Language support

<table>
  <thead>
    <tr>
      <th>Language</th>
      <th>Status</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td><strong>Python</strong></td>
      <td>Implemented</td>
    </tr>
    <tr>
      <td><strong>C</strong></td>
      <td>Planned</td>
    </tr>
    <tr>
      <td><strong>C++</strong></td>
      <td>Planned</td>
    </tr>
  </tbody>
</table>

---

## Installation

### Option 1 - PyPI release (recommended usage)

```bash
pip install openfilenet
```

### Option 2 - Local clone (recommended for testing and development)

```bash
git clone https://github.com/vamsi-karnam/openfilenet.git
cd openfilenet
pip install -r requirements.txt
```

### Requirements

Only required **if encryption is enabled**:

```
cryptography
```

If you don't use encryption, OpenFilenet has **zero external dependencies**.

---

## Example applications

* Distributed ML dataset sharing without NFS/Samba.
* Python workers processing remote sensor logs.
* Raspberry Pi cluster exchanging data dynamically.
* Local LLM workers sharing checkpoints.
* “Ad-hoc cluster mode” for laptops on a network.
* Edge AI pipelines that pass data P2P.

---

## License / Author / Contact / Citation

OpenFilenet
Developed by **Vamsi Karnam**, 2025.  
Released under the **Apache 2.0 License**.

If you use this software in any of your works, please cite:
> Karnam, S. V. (2025). *Openfilenet: an open-source, modern, LAN-native, cross-platform, P2P file sharing for real programming workflows. *

For contact, collaborations, bug reports, or business, reach out via saivamsi.karnam@gmail.com.

Author Social: [LinkedIn](https://www.linkedin.com/in/saivamsikarnam/)

OpenFilenet is open-source and contributions are welcome.

---

> *"Data should empower, not overwhelm"*
