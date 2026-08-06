@echo off
setlocal
cd /d "%~dp0.."
echo ============================================================
echo SETUP PLANET QUAD AREA SELECTOR
 echo ============================================================
if not exist .venv (
    py -m venv .venv
    if errorlevel 1 goto :error
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
if errorlevel 1 goto :error
python -m pip install -r requirements.txt
if errorlevel 1 goto :error
python -m pip check
if errorlevel 1 goto :error
if not exist .env (
    copy .env.example .env >nul
    echo File .env dibuat. Isi PL_API_KEY sebelum menjalankan aplikasi.
)
if not exist config mkdir config
if not exist downloads mkdir downloads
if not exist "downloads\Excel Files" mkdir "downloads\Excel Files"
echo.
echo Setup selesai.
pause
exit /b 0
:error
echo.
echo Setup gagal. Periksa pesan error di atas.
pause
exit /b 1
