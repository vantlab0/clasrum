import io

for p in (".env", "backend/.env"):
    for line in io.open(p, encoding="utf-8"):
        if line.startswith("SUPABASE_JWT_SECRET="):
            val = line.split("=", 1)[1].strip()
            print(p, "SUPABASE_JWT_SECRET len =", len(val), "| valid_base64_len_ok =", len(val) > 80)
