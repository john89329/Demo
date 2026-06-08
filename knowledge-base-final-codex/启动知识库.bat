@echo off
cd /d "%~dp0"

:: Use python.exe (not pythonw.exe) for proper console + subprocess support
if exist "%~dp0venv311\Scripts\python.exe" (
    "%~dp0venv311\Scripts\python.exe" "%~dp0app.py"
) else if exist "%~dp0venv\Scripts\python.exe" (
    "%~dp0venv\Scripts\python.exe" "%~dp0app.py"
) else (
    python "%~dp0app.py"
)
pause
