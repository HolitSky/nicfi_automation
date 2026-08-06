@echo off
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    echo Virtual environment belum tersedia. Jalankan setup_area_selector.bat.
    pause
    exit /b 1
)
call .venv\Scripts\activate.bat
python -m streamlit run area_selector_app.py
