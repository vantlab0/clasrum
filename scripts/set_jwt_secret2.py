import io

KEY = "SUPABASE_JWT_SECRET"
SECRET = io.open(
    r"C:\Users\ThankGod Tec\AppData\Local\Temp\opencode\jwt_secret.txt",
    encoding="utf-8",
).read().strip()

for path in (".env", "backend/.env"):
    with io.open(path, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    out = []
    found = False
    for line in lines:
        if line.strip().startswith(KEY + "="):
            out.append(f"{KEY}={SECRET}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"{KEY}={SECRET}")
    with io.open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    print(path, "->", "replaced" if found else "appended")
