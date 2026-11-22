@echo off
cd /d "C:\Users\ThankGod Tec\Desktop\tutr\ai-classroom\backend"
start "" /b "C:\Users\ThankGod Tec\Desktop\tutr\ai-classroom\.venv\Scripts\pythonw.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
start "" /b "C:\Users\ThankGod Tec\Desktop\tutr\ai-classroom\.venv\Scripts\pythonw.exe" -m http.server 8080 --directory "C:\Users\ThankGod Tec\Desktop\tutr\ai-classroom\app\build\web"
