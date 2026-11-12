"""Deploy the AI Personal Classroom backend to the VPS.

Idempotent-ish: safe to re-run. Uploads a snapshot of ./backend, sets up a
Python 3.12 virtualenv with uv, runs Alembic, installs a systemd service and
adds a path-scoped nginx reverse proxy for realtime.avelut.xyz so the existing
Node realtime proxy on port 10000 keeps working at the domain root.
"""
from __future__ import annotations

import io
import os
import secrets
import sys
import tarfile
import time

import paramiko

HOST = "93.127.132.66"
PORT = 10051
USER = "administrator"
PASSWORD = "7ma6w@ehZc"

DOMAIN = "realtime.avelut.xyz"
REMOTE_ROOT = "/opt/ai-classroom"
REMOTE_BACKEND = REMOTE_ROOT + "/backend"
REMOTE_DATA = REMOTE_ROOT + "/data"
UPLOAD_TMP = "/tmp/ai-classroom-backend.tar.gz"

LOCAL_BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")

EXCLUDES = {".venv", "__pycache__", ".pytest_cache", ".git", "node_modules", "data"}


def connect() -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, port=PORT, username=USER, password=PASSWORD, timeout=30)
    return client


def run(client, cmd: str, *, sudo: bool = False, timeout: int = 600, check: bool = True) -> str:
    if sudo:
        cmd = "sudo -S -p '' bash -lc " + shquote(cmd)
    stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    if sudo:
        stdin.write(PASSWORD + "\n")
        stdin.flush()
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    code = stdout.channel.recv_exit_status()
    if check and code != 0:
        raise RuntimeError(f"command failed ({code}): {cmd}\n--- stdout ---\n{out}\n--- stderr ---\n{err}")
    return out + (("\n[stderr]\n" + err) if err.strip() else "")


def shquote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def make_tarball() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for root, dirs, files in os.walk(LOCAL_BACKEND):
            dirs[:] = [d for d in dirs if d not in EXCLUDES]
            for name in files:
                if name.endswith((".pyc", ".db", ".db-wal", ".db-shm")):
                    continue
                path = os.path.join(root, name)
                arcname = os.path.relpath(path, LOCAL_BACKEND).replace("\\", "/")
                tar.add(path, arcname=arcname)
    data = buf.getvalue()
    print(f"  tarball: {len(data) / 1024:.1f} KiB")
    return data


def read_remote(sftp: paramiko.SFTPClient, path: str) -> str | None:
    try:
        with sftp.file(path, "r") as f:
            return f.read().decode("utf-8", "replace")
    except IOError:
        return None


def main() -> int:
    print(f"==> Connecting to {USER}@{HOST}:{PORT}")
    client = connect()
    print("    connected")

    try:
        print("==> Preparing directories")
        run(client, f"mkdir -p {REMOTE_BACKEND} {REMOTE_DATA} && chown -R {USER}:{USER} {REMOTE_ROOT}", sudo=True)

        print("==> Uploading backend snapshot")
        payload = make_tarball()
        sftp = client.open_sftp()
        with sftp.file(UPLOAD_TMP, "w") as f:
            f.write(payload)
        run(client, f"tar -xzf {UPLOAD_TMP} -C {REMOTE_BACKEND} && rm -f {UPLOAD_TMP}")
        print("    extracted")

        print("==> Installing Python 3.12 (uv) + dependencies")
        out = run(client, "test -x ~/.local/bin/uv && echo OK || echo MISSING")
        if "MISSING" in out:
            run(client, "curl -LsSf https://astral.sh/uv/install.sh | sh", timeout=600)
        run(client, "~/.local/bin/uv python install 3.12", timeout=900)
        current = run(
            client,
            f"test -x {REMOTE_BACKEND}/.venv/bin/python "
            f"&& {REMOTE_BACKEND}/.venv/bin/python -V || echo none",
        ).strip().splitlines()[-1]
        if "3.12" not in current:
            run(client, f"cd {REMOTE_BACKEND} && rm -rf .venv && ~/.local/bin/uv venv --python 3.12 .venv", timeout=300)
        run(
            client,
            f"cd {REMOTE_BACKEND} && ~/.local/bin/uv pip install --python .venv/bin/python -r requirements.txt",
            timeout=1800,
        )
        print("    dependencies installed")

        print("==> Writing production .env")
        existing_env = read_remote(sftp, REMOTE_BACKEND + "/.env")
        jwt_secret = None
        if existing_env:
            for line in existing_env.splitlines():
                if line.startswith("JWT_SECRET="):
                    jwt_secret = line.split("=", 1)[1].strip()
        jwt_secret = jwt_secret or secrets.token_hex(32)
        env_content = f"""# Managed by scripts/deploy.py — production settings
APP_NAME=AI Personal Classroom
ENVIRONMENT=production
DEBUG=false
API_PREFIX=/api/v1

DATABASE_URL=sqlite+aiosqlite:///{REMOTE_DATA}/prod.db
DB_ECHO=false

JWT_SECRET={jwt_secret}
JWT_EXPIRES_MINUTES=20160
CORS_ORIGINS=["*"]

QWEN_API_KEY=sk-ws-H.DIRPHXX.6d6q.MEUCIQCb-XKQ7aL93idHDFZY96NkuciY_tz39Ze4ZdC8Gmes1AIgBwqx-FTHxu7D3R8oLJHzBydhYVVqF3bn7gzr-fuXvaE
QWEN_REALTIME_URL=wss://ws-o3v6mh0i8y9tqdfx.ap-southeast-1.maas.aliyuncs.com/api-ws/v1/realtime
QWEN_MODEL=qwen-omni-turbo-realtime
QWEN_VOICE=Cherry
QWEN_INPUT_SAMPLE_RATE=16000
QWEN_OUTPUT_SAMPLE_RATE=24000
QWEN_TURN_DETECTION=server_vad
QWEN_FAKE_MODE=true
# NOTE: the Qwen key above is rejected by DashScope with HTTP 401 (expired or
# revoked). Until a valid key is supplied, fake mode keeps lessons usable. Replace
# QWEN_API_KEY and set QWEN_FAKE_MODE=false to enable the live voice tutor.

NOTES_MODEL=qwen-plus
NOTES_BASE_URL=https://ws-o3v6mh0i8y9tqdfx.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1
NOTES_API_KEY=

BOARD_SNAPSHOT_INTERVAL_EVENTS=40
BOARD_SNAPSHOT_KEEP=5
"""
        with sftp.file(REMOTE_BACKEND + "/.env", "w") as f:
            f.write(env_content)
        run(client, f"chmod 600 {REMOTE_BACKEND}/.env")

        print("==> Running database migrations")
        out = run(client, f"cd {REMOTE_BACKEND} && .venv/bin/python -m alembic upgrade head", timeout=300)
        print("    " + out.strip().splitlines()[-1] if out.strip() else "    done")

        print("==> Installing systemd service")
        unit = """[Unit]
Description=AI Personal Classroom backend (FastAPI)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=administrator
Group=administrator
WorkingDirectory=/opt/ai-classroom/backend
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=/opt/ai-classroom/backend/.env
ExecStart=/opt/ai-classroom/backend/.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips=127.0.0.1
Restart=always
RestartSec=3
TimeoutStartSec=120
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
"""
        with sftp.file("/tmp/ai-classroom.service", "w") as f:
            f.write(unit)
        run(
            client,
            "cp /tmp/ai-classroom.service /etc/systemd/system/ai-classroom.service "
            "&& rm -f /tmp/ai-classroom.service "
            "&& systemctl daemon-reload && systemctl enable --now ai-classroom "
            "&& systemctl restart ai-classroom",
            sudo=True,
            timeout=180,
        )
        print("    service started")

        print("==> Configuring nginx (path-scoped; existing proxy kept at /)")
        nginx = f"""# Managed by AI Classroom deploy script.
# The hosting panel maps realtime.avelut.xyz -> this VM's port 10000, so nginx
# listens there and splits traffic: /api, /healthz and docs go to the FastAPI
# backend, everything else (the LiveTeacher websocket proxy) stays on Node.
server {{
    listen 80;
    listen 10000;
    server_name {DOMAIN};

    # Let's Encrypt checks
    location ^~ /.well-known/acme-challenge/ {{
        default_type "text/plain";
        root /var/www/html;
    }}

    # --- AI Personal Classroom backend --------------------------------
    location /api/ {{
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 86400s;
        proxy_send_timeout 86400s;
        proxy_buffering off;
    }}

    location = /healthz {{
        proxy_pass http://127.0.0.1:8000/healthz;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
    }}

    location = /openapi.json {{
        proxy_pass http://127.0.0.1:8000/openapi.json;
        proxy_set_header Host $host;
    }}

    location /docs {{
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
    }}

    location /redoc {{
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
    }}

    # --- existing Avelut realtime proxy (LiveTeacher) ------------------
    location / {{
        proxy_pass http://127.0.0.1:10001;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "Upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 86400s;
        proxy_send_timeout 86400s;
    }}
}}
"""
        backup = run(
            client,
            "test -f /etc/nginx/sites-available/realtime "
            "&& cp /etc/nginx/sites-available/realtime /etc/nginx/sites-available/realtime.bak.$(date +%s) "
            "&& echo backed-up || echo no-original",
            sudo=True,
        )
        print("    backup: " + backup.strip().splitlines()[-1])
        with sftp.file("/tmp/ai-classroom-nginx.conf", "w") as f:
            f.write(nginx)
        run(
            client,
            "cp /tmp/ai-classroom-nginx.conf /etc/nginx/sites-available/realtime "
            "&& rm -f /tmp/ai-classroom-nginx.conf "
            "&& ln -sf /etc/nginx/sites-available/realtime /etc/nginx/sites-enabled/realtime "
            "&& nginx -t && systemctl reload nginx",
            sudo=True,
            timeout=120,
        )
        print("    nginx reloaded")

        print("==> Verifying")
        time.sleep(3)
        local_health = run(client, "curl -sS -m 10 http://127.0.0.1:8000/healthz", check=False)
        proxy_health = run(client, "curl -sS -m 10 -H 'Host: realtime.avelut.xyz' http://127.0.0.1/healthz", check=False)
        status = run(client, "systemctl is-active ai-classroom", check=False).strip()
        print(f"    service: {status}")
        print(f"    direct:  {local_health.strip()[:200]}")
        print(f"    via nginx: {proxy_health.strip()[:200]}")

        print("\n==> Done")
        print(f"    REST base:  https://{DOMAIN}/api/v1")
        print(f"    Health:     https://{DOMAIN}/healthz")
        print(f"    Docs:       https://{DOMAIN}/docs")
        sftp.close()
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
