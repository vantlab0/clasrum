"""Upload one local file to the VPS over SFTP.

Usage: python scripts/upload_one.py <local> <remote>
"""
import sys

import paramiko

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect("93.127.132.66", port=10051, username="administrator", password="7ma6w@ehZc", timeout=30)
try:
    sftp = client.open_sftp()
    sftp.put(sys.argv[1], sys.argv[2])
    print(f"uploaded {sys.argv[1]} -> {sys.argv[2]}")
finally:
    client.close()
