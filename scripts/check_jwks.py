import json
import urllib.request

url = "https://sazpockqjxeuthewdtku.supabase.co/auth/v1/.well-known/jwks.json"
try:
    with urllib.request.urlopen(url, timeout=15) as resp:
        data = json.load(resp)
    print("status ok, keys:", [k.get("kty") for k in data.get("keys", [])])
except Exception as exc:  # noqa: BLE001
    print("JWKS fetch failed:", exc)
