@echo off
setlocal
cd /d "%~dp0.."

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" scripts\cleanup_runtime.py
) else (
    python scripts\cleanup_runtime.py
)

pause
