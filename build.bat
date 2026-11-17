@echo off
REM Build SmartClipboard.exe using the project virtual environment.
REM Produces dist\SmartClipboard.exe (single file, no console window).

setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    python -m venv .venv
)

call .venv\Scripts\python.exe -m pip install --upgrade pip
call .venv\Scripts\python.exe -m pip install -r requirements-build.txt

REM --windowed  : no console window (background app)
REM --onefile   : single portable .exe
REM --noupx     : do not compress; UPX-packed exes trip more AV heuristics
call .venv\Scripts\python.exe -m PyInstaller ^
    --noconfirm --clean ^
    --onefile --windowed --noupx ^
    --name SmartClipboard ^
    run.py

echo.
echo Done. See dist\SmartClipboard.exe
endlocal
