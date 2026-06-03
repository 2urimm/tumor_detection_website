@echo off
setlocal
set "BACKEND=%~dp0backend"

if not exist "%BACKEND%\main.py" (
    echo ERROR: Cannot find %BACKEND%\main.py
    pause
    exit /b 1
)

cd /d "%BACKEND%"

echo =========================================
echo  Brain Tumor MRI Analysis System
echo =========================================
echo.

if not exist ".venv\Scripts\activate.bat" (
    echo [1/3] Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo Python not found. Please install Python first.
        pause
        exit /b 1
    )
)

echo [2/3] Installing packages (first run may take a few minutes)...
call .venv\Scripts\activate.bat
pip install -r requirements.txt -q

echo [3/3] Starting server...
echo.
echo  URL   : http://localhost:8000/app/index.html
echo  Login : demo@hospital.kr / demo1234
echo  Docs  : http://localhost:8000/api/docs
echo.
echo  Press Ctrl+C to stop.
echo.

python main.py
pause
