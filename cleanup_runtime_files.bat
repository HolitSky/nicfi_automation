@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" cleanup_runtime_files.py
) else (
    python cleanup_runtime_files.py
)

pause
