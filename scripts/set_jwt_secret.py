import io
import sys

SECRET = sys.argv[1]
KEY = "SUPABASE_JWT_SECRET"

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
    print(path, "updated", "(replaced)" if found else "(appended)")
