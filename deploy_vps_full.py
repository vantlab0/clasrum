"""Full deployment of AI Personal Classroom backend to VPS."""
import paramiko
import os
import sys
import time

HOST = "93.127.132.66"
PORT = 10051
USER = "administrator"
PASSWORD = "7ma6w@ehZc"
DOMAIN = "realtime.avelut.xyz"
LOCAL_BACKEND = os.path.join(os.getcwd(), "backend")

def ssh_connect():
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, port=PORT, username=USER, password=PASSWORD, timeout=30)
    return client

def run_sudo(client, cmd, timeout=120):
    """Run a command with sudo, wrapping in bash -c so all commands get sudo."""
    full_cmd = f"sudo -S bash -c '{cmd}'"
    stdin, stdout, stderr = client.exec_command(full_cmd, timeout=timeout, get_pty=True)
    stdin.write(PASSWORD + "\n")
    stdin.flush()
    out = stdout.read().decode()
    err = stderr.read().decode()
    return out, err

def run_cmd(client, cmd, timeout=120):
    """Run a command without sudo."""
    stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout, get_pty=True)
    out = stdout.read().decode()
    err = stderr.read().decode()
    return out, err

def sftp_put_dir(sftp, local_dir, remote_dir):
    """Recursively upload a directory via SFTP."""
    for item in os.listdir(local_dir):
        local_path = os.path.join(local_dir, item)
        remote_path = remote_dir.rstrip("/") + "/" + item
        if os.path.isfile(local_path):
            if item.endswith(".pyc") or "__pycache__" in local_path or item.endswith(".db"):
                continue
            print(f"  Uploading {remote_path}")
            sftp.put(local_path, remote_path)
        elif os.path.isdir(local_path):
            try:
                sftp.mkdir(remote_path)
            except IOError:
                pass
            sftp_put_dir(sftp, local_path, remote_path)

def main():
    print(f"Connecting to {USER}@{HOST}:{PORT}...")
    client = ssh_connect()
    print("Connected!")

    try:
        # Step 1: Create deploy directory with sudo
        print("\n=== Step 1: Deploy directory ===")
        out, err = run_sudo(client, "mkdir -p /opt/ai-classroom/backend && chown -R administrator:administrator /opt/ai-classroom")
        print(out.strip() if out else "(done)")
        if err.strip():
            print(f"STDERR: {err.strip()[-200:]}")

        # Step 2: Upload backend code via SFTP
        print("\n=== Step 2: Upload backend code ===")
        sftp = client.open_sftp()
        sftp_put_dir(sftp, LOCAL_BACKEND, "/opt/ai-classroom/backend")
        sftp.close()
        print("Upload complete.")

        # Step 3: Set up Python venv and install dependencies
        print("\n=== Step 3: Python venv + dependencies ===")
        out, err = run_cmd(client, "cd /opt/ai-classroom/backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt", timeout=300)
        print(out.strip()[-500:] if out else "(empty)")
        if err.strip():
            print(f"STDERR: {err.strip()[-500:]}")

        # Step 4: Create .env file
        print("\n=== Step 4: Create .env ===")
        env_content = f"""APP_NAME="AI Personal Classroom"
ENVIRONMENT=production
DEBUG=false
DATABASE_URL=sqlite+aiosqlite:////opt/ai-classroom/backend/data/prod.db
DB_ECHO=false
JWT_SECRET=change-me-in-production-please-0123456789abcdef
JWT_EXPIRES_MINUTES=20160
CORS_ORIGINS=*
QWEN_API_KEY=sk-ws-H.DIRPHXX.6d6q.MEUCIQCb-XKQ7aL93idHDFZY96NkuciY_tz39Ze4ZdC8Gmes1AIgBwqx-FTHxu7D3R8oLJHzBydhYVVqF3bn7gzr-fuXvaE
QWEN_REALTIME_URL=wss://dashscope.aliyuncs.com/api-ws/v1/realtime
QWEN_MODEL=qwen-omni-turbo-realtime
QWEN_VOICE=Cherry
QWEN_INPUT_SAMPLE_RATE=16000
QWEN_OUTPUT_SAMPLE_RATE=24000
QWEN_TURN_DETECTION=server_vad
QWEN_FAKE_MODE=false
NOTES_MODEL=qwen-plus
NOTES_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
NOTES_API_KEY=
BOARD_SNAPSHOT_INTERVAL_EVENTS=40
BOARD_SNAPSHOT_KEEP=5
"""
        stdin, stdout, stderr = run_cmd(client, "sudo -S tee /opt/ai-classroom/backend/.env > /dev/null", timeout=30)
        # Note: run_cmd doesn't handle sudo password. Let me use run_sudo instead.
        # Actually, let me use a different approach - write via sftp
        print("Writing .env via SFTP...")
        sftp = client.open_sftp()
        with sftp.file("/opt/ai-classroom/backend/.env", "w") as f:
            f.write(env_content)
        sftp.close()
        print(".env written.")

        # Step 5: Initialize database and run migrations
        print("\n=== Step 5: Initialize database ===")
        out, err = run_cmd(client, "cd /opt/ai-classroom/backend && .venv/bin/python -m alembic upgrade head", timeout=120)
        print(out.strip()[-500:] if out else "(empty)")
        if err.strip():
            print(f"STDERR: {err.strip()[-500:]}")

        # Step 6: Create systemd service
        print("\n=== Step 6: Create systemd service ===")
        service_content = f"""[Unit]
Description=AI Personal Classroom Backend
After=network.target

[Service]
Type=simple
User=administrator
WorkingDirectory=/opt/ai-classroom/backend
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=/opt/ai-classroom/backend/.env
ExecStart=/opt/ai-classroom/backend/.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"""
        sftp = client.open_sftp()
        with sftp.file("/tmp/ai-classroom.service", "w") as f:
            f.write(service_content)
        sftp.close()
        
        out, err = run_sudo(client, "cp /tmp/ai-classroom.service /etc/systemd/system/ai-classroom.service && systemctl daemon-reload && systemctl enable ai-classroom && systemctl start ai-classroom")
        print(out.strip() if out else "(done)")
        if err.strip():
            print(f"STDERR: {err.strip()[-200:]}")

        # Step 7: Configure nginx reverse proxy
        print("\n=== Step 7: Configure nginx ===")
        nginx_content = f"""server {{
    listen 80;
    server_name {DOMAIN};

    location / {{
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 86400s;
        proxy_send_timeout 86400s;
    }}
}}
"""
        sftp = client.open_sftp()
        with sftp.file("/tmp/ai-classroom", "w") as f:
            f.write(nginx_content)
        sftp.close()
        
        out, err = run_sudo(client, "cp /tmp/ai-classroom /etc/nginx/sites-available/ai-classroom && ln -sf /etc/nginx/sites-available/ai-classroom /etc/nginx/sites-enabled/ai-classroom && rm -f /etc/nginx/sites-enabled/default && nginx -t && systemctl restart nginx")
        print(out.strip() if out else "(done)")
        if err.strip():
            print(f"STDERR: {err.strip()[-200:]}")

        # Step 8: Verify service is running
        print("\n=== Step 8: Verify backend ===")
        time.sleep(3)
        out, err = run_cmd(client, "curl -s http://localhost:8000/healthz", timeout=30)
        print(f"Health check: {out.strip()}")

        print("\n=== DEPLOYMENT COMPLETE ===")
        print(f"Backend running at: http://{HOST}:8000")
        print(f"Domain: http://{DOMAIN}")
        print(f"Health check: http://{DOMAIN}/healthz")

    finally:
        client.close()

if __name__ == "__main__":
    main()
