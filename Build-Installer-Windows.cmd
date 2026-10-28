@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0packaging\build.ps1"
if errorlevel 1 (
  echo Build failed. Install Python 3.12 x64 and NSIS 3. See the error above.
  pause
  exit /b 1
)
echo Installer created in dist\installer\TerrainStudio-0.3.0-Setup.exe
explorer.exe "%~dp0dist\installer"
pause
