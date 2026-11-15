@echo off
cd /d "%~dp0"
if exist "logs\latest.log" (
    start "" notepad.exe "logs\latest.log"
) else (
    echo Ainda nao existe logs\latest.log. Rode run_gui.bat primeiro.
    pause
)
