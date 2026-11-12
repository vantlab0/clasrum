"""Try the avelut proxy credentials against DashScope realtime, without printing secrets."""
import asyncio
import os
from urllib.parse import urlparse

import websockets


def load_env(path):
    env = {}
    try:
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    except FileNotFoundError:
        pass
    return env


classroom = load_env("/opt/ai-classroom/backend/.env")
proxy = load_env("/opt/avelut-realtime-proxy/.env")

keys = {
    "classroom": classroom.get("QWEN_API_KEY", ""),
    "proxy": proxy.get("ALIBABA_API_KEY", ""),
}
print("proxy key present:", bool(keys["proxy"]), "same as classroom:", keys["proxy"] == keys["classroom"])
print("proxy workspace:", proxy.get("ALIBABA_WORKSPACE_ID") or "(default ws-o3v6mh0i8y9tqdfx)")

workspace = proxy.get("ALIBABA_WORKSPACE_ID") or "ws-o3v6mh0i8y9tqdfx"
models = ["qwen-omni-turbo-realtime", "qwen3.8-omni-flash-realtime"]


async def try_one(label, key, model):
    url = f"wss://{workspace}.ap-southeast-1.maas.aliyuncs.com/api-ws/v1/realtime?model={model}"
    headers = {
        "Authorization": f"Bearer {key}",
        "X-DashScope-WorkSpace": workspace,
        "X-DashScope-DataInspection": "disable",
    }
    try:
        async with websockets.connect(url, additional_headers=headers, open_timeout=20) as ws:
            print(f"{label}/{model}: CONNECTED")
    except Exception as exc:
        print(f"{label}/{model}: {type(exc).__name__}: {str(exc)[:100]}")


async def main():
    for name, key in keys.items():
        if not key:
            continue
        for model in models:
            await try_one(name, key, model)


asyncio.run(main())
