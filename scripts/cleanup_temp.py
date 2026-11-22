import os

p = r"C:/Users/ThankGod Tec/AppData/Local/Temp/opencode/jwt_secret.txt"
try:
    os.remove(p)
    print("temp secret file deleted")
except FileNotFoundError:
    print("temp file already gone")
