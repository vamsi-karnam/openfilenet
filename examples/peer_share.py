"""
Peer A: Share a local CSV file via OpenFilenet to all the peers.
"""
import time
import openfilenet as ofn

# -------------------------------------------------------------------
# openfilenet configuration
# -------------------------------------------------------------------
# Room / namespace. All peers in this room / namespace can see each other on the network.
ofn.token = "demo"

# Optional: Enable debug
# ofn.debug = True

# Optional: override UDP discovery port (default: 51230)
# ofn.port.udp_discovery = 51230

# Optional: force a specific TCP server port (default: 0 = OS chooses)
# ofn.port.tcp_server = 50000

# Optional: enable AES256 encryption (AES-256-GCM) for get_file() transfers
# If you enable this, all peers must set the SAME key and have the `cryptography` package installed.
ofn.encrypt = True
ofn.key = "secret"

# -------------------------------------------------------------------
# Share a file or directory on the P2P network
# -------------------------------------------------------------------
# Change this to the absolute or relative path of file or directory.
CSV_PATH = "path/to/your/dir/or/file"  # e.g. on windows "C:\\Users\\username\\data\\my_data.csv"

ofn.share_files(CSV_PATH)

print(f"Sharing CSV file: {CSV_PATH}")
print("Peers using the same token: ", ofn.token," can now discover and fetch the shared file(s).")
print("Press Ctrl+C to exit.")

# Keep the process alive so the TCP server and discovery keep running
while True:
    time.sleep(10)