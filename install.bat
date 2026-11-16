@echo off
rem gazehop installer for Windows 10/11. Double-click to run.
setlocal
cd /d "%~dp0"
title gazehop installer

echo == 1. Looking for Python ==
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
    python -c "import sys" >nul 2>nul && set "PY=python"
)
if not defined PY goto :nopython

%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 goto :oldpython
for /f "delims=" %%v in ('%PY% --version') do echo   ok    %%v

echo.
echo == 2. Creating the virtual environment ==
if not exist ".venv\Scripts\python.exe" %PY% -m venv .venv
if not exist ".venv\Scripts\python.exe" goto :venvfail
echo   ok    .venv ready

echo.
echo == 3. Installing packages - this takes a few minutes the first time ==
".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt
if errorlevel 1 goto :pipfail
echo   ok    packages installed

".venv\Scripts\python.exe" -m gazehop selftest
if errorlevel 1 echo   note  self-check failed, see the message above

echo.
echo == 4. Shortcuts ==
powershell -NoProfile -ExecutionPolicy Bypass -Command "foreach ($f in 'Desktop','Programs') { $s = (New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath($f) + '\gazehop.lnk'); $s.TargetPath = '%CD%\.venv\Scripts\pythonw.exe'; $s.Arguments = '-m gazehop --window'; $s.WorkingDirectory = '%CD%'; $s.IconLocation = '%CD%\gazehop\assets\icon.ico'; $s.Description = 'Keyboard focus follows your eyes'; $s.Save() }"
if errorlevel 1 (echo   note  could not create the shortcuts, use run.bat instead) else (echo   ok    gazehop added to the Desktop and the Start menu)

echo.
echo == Done ==
echo Start gazehop from the Desktop or Start menu icon. It opens a window with the
echo camera view and puts an icon in the tray. run.bat starts it in a terminal instead.
echo The first start asks for camera access and runs a 45 second calibration.
echo To try it without changing focus:  run.bat --dry-run --dot
echo.
pause
exit /b 0

:nopython
echo   Python was not found.
where winget >nul 2>nul
if errorlevel 1 goto :manualpython
choice /c YN /m "  Install Python 3.12 now with winget"
if errorlevel 2 goto :manualpython
winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
echo.
echo   Python is installed. Close this window and double-click install.bat again.
pause
exit /b 1

:manualpython
echo   Install Python 3.12 from https://www.python.org/downloads/windows/
echo   Tick "Add python.exe to PATH" in its installer, then run install.bat again.
pause
exit /b 1

:oldpython
echo   Python 3.10 or newer is needed. Install a newer one from https://www.python.org/downloads/windows/
pause
exit /b 1

:venvfail
echo   Could not create the virtual environment in .venv
pause
exit /b 1

:pipfail
echo   Installing the packages failed. See the messages above.
echo   If it mentions mediapipe, your Python may be too new for it: install Python 3.12 and rerun.
pause
exit /b 1
