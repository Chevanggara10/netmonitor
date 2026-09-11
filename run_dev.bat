@echo off
cd /d "%~dp0"
venv\Scripts\uvicorn.exe app.main:app --host 127.0.0.1 --port %PORT%
