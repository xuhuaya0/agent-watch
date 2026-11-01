@echo off
cd /d "%~dp0"
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw.exe "%~dp0tray.py"
) else (
    python.exe "%~dp0tray.py"
)
