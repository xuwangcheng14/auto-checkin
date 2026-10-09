@echo off
rem Checkin Manager startup script (Windows)
rem Usage: start.bat [--host 127.0.0.1] [--port 7777]
cd /d "%~dp0"

set PY=python
where python >nul 2>&1 || set PY=py

%PY% -c "import sys" >nul 2>&1
if errorlevel 1 (
    echo [error] Python not found. Please install Python 3.9+ first.
    pause
    exit /b 1
)

%PY% -c "import flask" >nul 2>&1
if errorlevel 1 (
    echo [setup] Installing dependencies...
    %PY% -m pip install -r requirements.txt
)

echo [start] http://127.0.0.1:7777  (Ctrl+C to stop)
%PY% app.py %*
pause
