"""Deploy the AI Personal Classroom backend and Flutter web app to the VPS.

Idempotent-ish: safe to re-run. Uploads snapshots of ./backend and ./app, sets up
a Python 3.12 virtualenv with uv, runs Alembic, installs a systemd service,
builds the Flutter web app for /classroom/, and configures nginx for
realtime.avelut.xyz so the backend, Flutter classroom, and existing Node realtime
proxy can coexist.
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
REMOTE_ROOT = "/opt/pwony"
REMOTE_BACKEND = REMOTE_ROOT + "/backend"
REMOTE_DATA = REMOTE_ROOT + "/data"
REMOTE_WEB = "/var/www/pwony-classroom"
UPLOAD_TMP = "/tmp/pwony-backend.tar.gz"
UPLOAD_WEB_TMP = "/tmp/pwony-app.tar.gz"

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCAL_BACKEND = os.path.join(REPO_ROOT, "backend")
LOCAL_APP = os.path.join(REPO_ROOT, "app")

EXCLUDES = {".venv", "__pycache__", ".pytest_cache", ".git", "node_modules", "data"}
WEB_EXCLUDES = {
    ".dart_tool",
    ".git",
    ".idea",
    ".vscode",
    "build",
    "android",
    "ios",
    "linux",
    "macos",
    "windows",
    "test",
}


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


def make_tarball(local_dir: str, excludes: set[str], *, skip_suffixes: tuple[str, ...] = ()) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for root, dirs, files in os.walk(local_dir):
            dirs[:] = [d for d in dirs if d not in excludes]
            for name in files:
                if skip_suffixes and name.endswith(skip_suffixes):
                    continue
                path = os.path.join(root, name)
                arcname = os.path.relpath(path, local_dir).replace("\\", "/")
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
        payload = make_tarball(LOCAL_BACKEND, EXCLUDES, skip_suffixes=(".pyc", ".db", ".db-wal", ".db-shm"))
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

SUPABASE_URL=https://sazpockqjxeuthewdtku.supabase.co
SUPABASE_JWKS_URL=https://sazpockqjxeuthewdtku.supabase.co/auth/v1/.well-known/jwks.json
SUPABASE_JWT_AUDIENCE=authenticated

QWEN_API_KEY=
QWEN_REALTIME_URL=wss://ws-o3v6mh0i8y9tqdfx.ap-southeast-1.maas.aliyuncs.com/api-ws/v1/realtime
QWEN_MODEL=qwen3.8-omni-flash-realtime
QWEN_VOICE=Katerina
QWEN_INPUT_SAMPLE_RATE=16000
QWEN_OUTPUT_SAMPLE_RATE=24000
QWEN_TURN_DETECTION=semantic_vad_ec

NOTES_MODEL=qwen3.7-flash
NOTES_BASE_URL=https://ws-o3v6mh0i8y9tqdfx.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1
NOTES_API_KEY=sk-ws-H.DIYRHHR.61XA.MEUCIQDACyeZhYCl7Wd2X8za6CvKaQX4erEfQHmCEaIyu5dOwQIgfla7_W5DepkPZYUXqfFemSdNFzT8xTqBtbQbjxzrbGo
BOARD_DIRECTOR_MODEL=qwen3.7-flash
BOARD_DIRECTOR_BASE_URL=https://ws-o3v6mh0i8y9tqdfx.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1
BOARD_DIRECTOR_TIMEOUT_SECONDS=90
BOARD_DIRECTOR_API_KEY=sk-ws-H.DIYRHHR.61XA.MEUCIQDACyeZhYCl7Wd2X8za6CvKaQX4erEfQHmCEaIyu5dOwQIgfla7_W5DepkPZYUXqfFemSdNFzT8xTqBtbQbjxzrbGo

BOARD_SNAPSHOT_INTERVAL_EVENTS=40
BOARD_SNAPSHOT_KEEP=5
"""
        with sftp.file(REMOTE_BACKEND + "/.env", "w") as f:
            f.write(env_content)
        run(client, f"chmod 600 {REMOTE_BACKEND}/.env")

        print("==> Running database migrations")
        out = run(client, f"cd {REMOTE_BACKEND} && .venv/bin/python -m alembic upgrade head", timeout=300)
        print("    " + out.strip().splitlines()[-1] if out.strip() else "    done")

        print("==> Building and deploying Flutter web (/classroom/)")
        run(client, f"mkdir -p {REMOTE_WEB} /opt/pwony/app-src", sudo=True)
        run(client, "chown -R {user}:{user} /opt/pwony/app-src".format(user=USER), sudo=True)
        web_payload = make_tarball(LOCAL_APP, WEB_EXCLUDES)
        with sftp.file(UPLOAD_WEB_TMP, "w") as f:
            f.write(web_payload)
        run(client, f"rm -rf /opt/pwony/app-src/* && tar -xzf {UPLOAD_WEB_TMP} -C /opt/pwony/app-src && rm -f {UPLOAD_WEB_TMP}")
        flutter_bin = run(
            client,
            "test -x /snap/bin/flutter && echo /snap/bin/flutter "
            "|| (command -v flutter >/dev/null 2>&1 && command -v flutter) "
            "|| echo MISSING",
            check=False,
        ).strip().splitlines()[-1]
        if flutter_bin == "MISSING":
            raise RuntimeError("Flutter is not installed on the VPS; cannot build web app")
        run(client, f"cd /opt/pwony/app-src && {flutter_bin} pub get", timeout=900)
        supabase_url = "https://sazpockqjxeuthewdtku.supabase.co"
        supabase_anon_key = "sb_publishable_08eKb2lUGqluBUsG5Kb4Tg_cL9alEO-"
        run(
            client,
            f"cd /opt/pwony/app-src && {flutter_bin} build web --release --base-href /classroom/ "
            f"--dart-define=SUPABASE_URL={supabase_url} "
            f"--dart-define=SUPABASE_ANON_KEY={supabase_anon_key}",
            timeout=1800,
        )
        run(
            client,
            f"test -f /opt/pwony/app-src/build/web/index.html && "
            f"rm -rf {REMOTE_WEB}/* && cp -r /opt/pwony/app-src/build/web/* {REMOTE_WEB}/ && "
            f"chown -R www-data:www-data {REMOTE_WEB}",
            sudo=True,
            timeout=300,
        )
        print("    web deployed")

        print("==> Installing systemd service")
        unit = """[Unit]
Description=AI Personal Classroom backend (FastAPI)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=administrator
Group=administrator
WorkingDirectory=/opt/pwony/backend
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=/opt/pwony/backend/.env
ExecStart=/opt/pwony/backend/.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips=127.0.0.1
Restart=always
RestartSec=3
TimeoutStartSec=120
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
"""
        with sftp.file("/tmp/pwony.service", "w") as f:
            f.write(unit)
        run(
            client,
            "cp /tmp/pwony.service /etc/systemd/system/pwony.service "
            "&& rm -f /tmp/pwony.service "
            "&& systemctl daemon-reload && systemctl enable --now pwony "
            "&& systemctl restart pwony",
            sudo=True,
            timeout=180,
        )
        print("    service started")

        print("==> Configuring nginx (path-scoped; /classroom/ static, /permit2/ proxy, existing proxy kept at /)")
        nginx = f"""# Managed by Pwony deploy script.
# The hosting panel maps realtime.avelut.xyz -> this VM's port 10000, so nginx
# listens there and splits traffic: /api, /healthz and docs go to the FastAPI
# backend, /classroom/ serves the Flutter web app, /permit2/ proxies to the
# Permit2 app, everything else (the LiveTeacher websocket proxy) stays on Node.
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

    # --- AI Personal Classroom web (Flutter, /classroom/) ---------------
    location = /classroom {{
        return 301 /classroom/;
    }}

    location /classroom/ {{
        alias {REMOTE_WEB}/;
        index index.html;
        try_files $uri $uri/ /classroom/index.html;
        add_header Cache-Control "no-cache";
    }}

    # --- Permit2 Realtime app (/permit2/) --------------------------------
    location = /permit2 {{
        return 301 /permit2/;
    }}

    location /permit2/ {{
        proxy_pass http://127.0.0.1:3020/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 86400s;
        proxy_send_timeout 86400s;
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
        with sftp.file("/tmp/pwony-nginx.conf", "w") as f:
            f.write(nginx)
        run(
            client,
            "cp /tmp/pwony-nginx.conf /etc/nginx/sites-available/realtime "
            "&& rm -f /tmp/pwony-nginx.conf "
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
        classroom_head = run(
            client,
            "curl -sS -m 10 -o /dev/null -w '%{http_code}' -H 'Host: realtime.avelut.xyz' http://127.0.0.1/classroom/",
            check=False,
        ).strip()
        classroom_bootstrap = run(
            client,
            "curl -sS -m 10 -o /dev/null -w '%{http_code}' -H 'Host: realtime.avelut.xyz' http://127.0.0.1/classroom/flutter_bootstrap.js",
            check=False,
        ).strip()
        status = run(client, "systemctl is-active pwony", check=False).strip()
        print(f"    service: {status}")
        print(f"    direct:  {local_health.strip()[:200]}")
        print(f"    via nginx: {proxy_health.strip()[:200]}")
        print(f"    classroom /: {classroom_head}")
        print(f"    classroom flutter_bootstrap.js: {classroom_bootstrap}")

        print("\n==> Done")
        print(f"    REST base:  https://{DOMAIN}/api/v1")
        print(f"    Health:     https://{DOMAIN}/healthz")
        print(f"    Docs:       https://{DOMAIN}/docs")
        print(f"    Classroom:  https://{DOMAIN}/classroom/")
        sftp.close()
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
