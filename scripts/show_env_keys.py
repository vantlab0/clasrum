import io

for path in (".env", "backend/.env"):
    print(path, "->")
    try:
        for line in io.open(path, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.strip().strip('"')
            shown = value if key.startswith(("SUPABASE_URL", "SUPABASE_JWKS_URL", "SUPABASE_JWT_AUDIENCE", "ENVIRONMENT", "JWT_EXPIRES_MINUTES")) else (f"<set len={len(value)}>" if value else "<empty>")
            print("  ", key.strip(), "=", shown)
    except FileNotFoundError:
        print("   missing")
