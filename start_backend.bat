@echo off
chcp 65001 >nul
cd /d "%~dp0backend"
if not exist .env (
  echo [提示] 未找到 backend\.env，将复制 .env.example，请编辑填入密钥。
  copy .env.example .env >nul
)
if not defined VIRTUAL_ENV pip install -r requirements.txt
uvicorn app.api.main:app --reload --port 8000
pause
