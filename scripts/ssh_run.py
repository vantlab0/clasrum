"""Small SSH helper: runs a command on the VPS and prints stdout/stderr.

Usage:
    .venv/Scripts/python.exe scripts/ssh_run.py "command here"
    .venv/Scripts/python.exe scripts/ssh_run.py --sudo "command here"
"""
import sys
import paramiko

HOST = "93.127.132.66"
PORT = 10051
USER = "administrator"
PASSWORD = "7ma6w@ehZc"


def main() -> int:
    args = sys.argv[1:]
    use_sudo = False
    if args and args[0] == "--sudo":
        use_sudo = True
        args = args[1:]
    if len(args) == 1 and args[0].startswith("@"):
        with open(args[0][1:], "r", encoding="utf-8") as f:
            cmd = f.read()
    else:
        cmd = " ".join(args)

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, port=PORT, username=USER, password=PASSWORD, timeout=30)

    try:
        if use_sudo:
            full = f"sudo -S -p '' bash -c {quote(cmd)}"
        else:
            full = cmd
        stdin, stdout, stderr = client.exec_command(full, timeout=int(__import__("os").environ.get("SSH_TIMEOUT", "300")))
        if use_sudo:
            stdin.write(PASSWORD + "\n")
            stdin.flush()
        out = stdout.read().decode(errors="replace")
        err = stderr.read().decode(errors="replace")
        code = stdout.channel.recv_exit_status()
        sys.stdout.buffer.write(out.encode("utf-8", errors="replace"))
        sys.stdout.flush()
        if err.strip():
            sys.stderr.buffer.write(("\n[stderr]\n" + err).encode("utf-8", errors="replace"))
            sys.stderr.flush()
        return code
    finally:
        client.close()


def quote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


if __name__ == "__main__":
    raise SystemExit(main())
