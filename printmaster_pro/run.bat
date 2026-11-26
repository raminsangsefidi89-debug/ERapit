@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    py -3 -m venv .venv || goto :fail
)
".venv\Scripts\python.exe" -c "import sys; sys.exit(sys.version_info < (3,11))" || goto :fail
if not exist ".venv\printmaster-deps.ok" (
    ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt || goto :fail
    type nul > ".venv\printmaster-deps.ok"
)
".venv\Scripts\python.exe" main.py %* || goto :fail
exit /b 0
:fail
echo Startup failed. Install Python 3.11+ and see README.md.
pause
exit /b 1
