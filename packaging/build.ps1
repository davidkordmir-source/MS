$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
py -3.12 -m venv .venv
if ($LASTEXITCODE) { throw 'Python 3.12 is required' }
& .\.venv\Scripts\python.exe -m pip install -r packaging/requirements-windows.txt
if ($LASTEXITCODE) { throw 'Dependency installation failed' }
& .\.venv\Scripts\python.exe -m unittest discover -s tests -v
if ($LASTEXITCODE) { throw 'Tests failed' }
& .\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --windowed --name TerrainPolygon --collect-all rasterio --collect-all fiona --collect-all pyproj --collect-all shapely --add-data "examples;examples" run_app.py
if ($LASTEXITCODE) { throw 'Packaging failed' }
$report = Join-Path (Get-Location) 'build/selftest.json'
if (Test-Path $report) { Remove-Item $report }
$smoke = Start-Process -FilePath .\dist\TerrainPolygon\TerrainPolygon.exe -ArgumentList @('--self-test', ('"' + $report + '"')) -Wait -PassThru
if ($smoke.ExitCode -ne 0 -or !(Test-Path $report)) { throw 'Packaged functional test failed' }
if ((Get-Content $report -Raw | ConvertFrom-Json).status -ne 'passed') { throw 'Packaged functional test failed' }
& .\.venv\Scripts\python.exe packaging/collect_notices.py
if ($LASTEXITCODE) { throw 'License collection failed' }
& .\.venv\Scripts\python.exe packaging/prepare_nsis.py
if ($LASTEXITCODE) { throw 'Installer manifest generation failed' }
$nsis = Get-Command makensis.exe -ErrorAction SilentlyContinue
if ($nsis) { $compiler = $nsis.Source } else { $compiler = "${env:ProgramFiles(x86)}\NSIS\makensis.exe" }
& $compiler packaging\installer.nsi
if ($LASTEXITCODE) { throw 'Installer compilation failed' }
