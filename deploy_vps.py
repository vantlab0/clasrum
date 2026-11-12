"""Deploy AI Personal Classroom backend to VPS via SSH."""
import paramiko
import sys
import time

HOST = "93.127.132.66"
PORT = 10051
USER = "administrator"
PASSWORD = "7ma6w@ehZc"
DOMAIN = "realtime.avelut.xyz"

def ssh_connect():
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, port=PORT, username=USER, password=PASSWORD, timeout=30)
    return client

def run_cmd(client, cmd, timeout=120, use_sudo=False):
    """Run a command, optionally with sudo by feeding password to stdin."""
    if use_sudo:
        full_cmd = f"sudo -S {cmd}"
    else:
        full_cmd = cmd
    stdin, stdout, stderr = client.exec_command(full_cmd, timeout=timeout, get_pty=True)
    if use_sudo:
        stdin.write(PASSWORD + "\n")
        stdin.flush()
    out = stdout.read().decode()
    err = stderr.read().decode()
    return out, err

def main():
    print(f"Connecting to {USER}@{HOST}:{PORT}...")
    client = ssh_connect()
    print("Connected!")

    try:
        # Step 1: Check system
        print("\n=== Step 1: System check ===")
        out, err = run_cmd(client, "whoami && hostname && cat /etc/os-release | head -3")
        print(out.strip())

        # Step 2: Configure passwordless sudo for administrator
        print("\n=== Step 2: Configure passwordless sudo ===")
        out, err = run_cmd(client, f"echo '{PASSWORD}' | sudo -S bash -c 'echo \"{USER} ALL=(ALL) NOPASSWD:ALL\" > /etc/sudoers.d/{USER}'", timeout=30)
        print(out.strip() if out else "(no output)")
        if err.strip():
            print(f"STDERR: {err.strip()[-200:]}")

        # Step 3: Update system
        print("\n=== Step 3: apt update ===")
        out, err = run_cmd(client, "apt-get update -y", timeout=180, use_sudo=True)
        print(out.strip()[-300:] if out else "(empty)")
        if err.strip():
            print(f"STDERR: {err.strip()[-300:]}")

        # Step 4: Install packages
        print("\n=== Step 4: Install packages ===")
        out, err = run_cmd(client, "apt-get install -y python3 python3-pip python3-venv git nginx curl", timeout=300, use_sudo=True)
        print(out.strip()[-300:] if out else "(empty)")
        if err.strip():
            print(f"STDERR: {err.strip()[-300:]}")

        # Step 5: Create deployment directory
        print("\n=== Step 5: Create deploy directory ===")
        out, err = run_cmd(client, "mkdir -p /opt/ai-classroom && chown administrator:administrator /opt/ai-classroom", use_sudo=True)
        print(out.strip())
        if err.strip():
            print(f"STDERR: {err.strip()}")

        print("\n=== Basic setup complete ===")
        print(f"Server: {USER}@{HOST}:{PORT}")
        print(f"Domain: {DOMAIN}")
        print(f"Deploy dir: /opt/ai-classroom")

    finally:
        client.close()

if __name__ == "__main__":
    main()
