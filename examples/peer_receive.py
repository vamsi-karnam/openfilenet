"""
Peer B: Discover a remote CSV file via OpenFilenet and process it in memory.
"""

import time
import io
import csv
from pathlib import Path

import openfilenet as ofn

# ---------------------------------------------------------------------------
# openfilenet configuration
# ---------------------------------------------------------------------------

# Must match the token used on the sharing peer.
ofn.token = "demo"

# Enable debug logs to see discovery and connections.
# ofn.debug = True

# Optional: enable encryption (must match sharer if used).
ofn.encrypt = True
ofn.key = "secret"


# ---------------------------------------------------------------------------
# Wait for files to appear on the P2P network
# ---------------------------------------------------------------------------

def wait_for_remote_files(timeout_seconds: int = 10, poll_interval: float = 1.0):
    """
    Poll openfilenet.list_files() until we see at least one file
    or until timeout is reached.

    Discovery is asynchronous, so this ensures we see peers that come online.
    """
    start = time.time()
    while time.time() - start < timeout_seconds:
        files = ofn.list_files()
        if files:
            return files
        print("No remote files yet, waiting...")
        time.sleep(poll_interval)
    return []


print("Looking for remote files...")

files = wait_for_remote_files(timeout_seconds=10)

if not files:
    print(
        "No remote files found. "
        f"Is the sharing peer running and using token {ofn.token!r}?"
    )
    raise SystemExit

print("\nDiscovered remote files:")
for f in files:
    print(f'  peer={f["peer_id"]}  path={f["path"]}  size={f["size"]}')
print()


# ---------------------------------------------------------------------------
# Fetch and process the file (CSV) in memory
# ---------------------------------------------------------------------------

# fetch the file the sharer is exposing. (use a loop for multiple files)
fmeta = files[0]
peer_id = fmeta["peer_id"]
remote_path = fmeta["path"]

print(f"Fetching remote CSV from peer={peer_id} path={remote_path}")

# Get file bytes IN MEMORY (no temp files).
data = ofn.get_file(peer_id, remote_path)  # dest=None => returns bytes

# Decode bytes into text (UTF-8 is common for CSV).
text = data.decode("utf-8", errors="replace")

# Parse CSV using Python's built-in CSV reader.
buf = io.StringIO(text)
reader = csv.reader(buf)

try:
    header = next(reader)   # first row = column names
except StopIteration:
    print("The CSV file appears to be empty.")
    raise SystemExit

# Count remaining rows
row_count = sum(1 for _ in reader)

print("\nCSV metadata:")
print(f"  Columns ({len(header)}): {header}")
print(f"  Rows: {row_count}")
print(f"  Dimensions: {row_count} rows × {len(header)} columns\n")


# ---------------------------------------------------------------------------
# Additionally plug in your own processing logic
# ---------------------------------------------------------------------------
# For example:
#   - load into pandas
#   - feed into a model
#   - compute statistics
#   - filter rows, etc.
#
# Example:
#
# import pandas as pd
# buf.seek(0)    # rewind to beginning
# df = pd.read_csv(buf)
# print(df.head())