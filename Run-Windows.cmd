@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -3.12 -m venv .venv
  if errorlevel 1 goto fail
)
if not exist ".venv\.terrain-deps-ready" (
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 goto fail
  type nul > ".venv\.terrain-deps-ready"
)
".venv\Scripts\python.exe" run_app.py
if errorlevel 1 goto fail
exit /b 0
:fail
echo.
echo Launch failed. Install Python 3.12 x64 and check the error above.
pause
exit /b 1
