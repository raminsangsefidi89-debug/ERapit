@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    py -3 -m venv .venv || goto :fail
)
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt || goto :fail
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onefile --windowed --name PrintMasterPro --add-data "assets;assets" --hidden-import keyring.backends.Windows main.py || goto :fail
echo Output: dist\PrintMasterPro.exe
exit /b 0
:fail
echo Build failed. See README.md.
pause
exit /b 1
