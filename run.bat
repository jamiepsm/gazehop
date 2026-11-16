@echo off
rem Starts gazehop. Arguments are passed through, e.g.  run.bat doctor
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Run install.bat first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m gazehop %*
if errorlevel 1 pause
