"""Probe DashScope realtime handshake variants with the configured key."""
import asyncio
import os
import re
from urllib.parse import urlparse

import websockets

env = {}
for line in open("/opt/ai-classroom/backend/.env", encoding="utf-8"):
    line = line.strip()
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()

key = env["QWEN_API_KEY"]
url_base = env["QWEN_REALTIME_URL"]
model = env.get("QWEN_MODEL", "qwen-omni-turbo-realtime")
ws_id = (urlparse(url_base).hostname or "").split(".")[0]
print("url:", url_base)
print("workspace:", ws_id, "model:", model)
print("key prefix:", key[:8], "len:", len(key))


async def try_connect(name, url, headers):
    try:
        async with websockets.connect(url, additional_headers=headers, open_timeout=20) as ws:
            print(f"{name}: CONNECTED")
            await ws.send('{"type":"session.update","session":{"modalities":["text"]}}')
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=10)
                print(f"{name}: first event {str(msg)[:200]}")
            except asyncio.TimeoutError:
                print(f"{name}: connected, no event")
    except Exception as exc:
        print(f"{name}: {type(exc).__name__}: {str(exc)[:160]}")


async def main():
    base_headers = {
        "Authorization": f"Bearer {key}",
        "X-DashScope-DataInspection": "disable",
    }
    ws_headers = {**base_headers, "X-DashScope-WorkSpace": ws_id}
    await try_connect("auth-only", f"{url_base}?model={model}", base_headers)
    await try_connect("auth+workspace", f"{url_base}?model={model}", ws_headers)
    global_url = f"wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model={model}"
    await try_connect("global", global_url, base_headers)


asyncio.run(main())
