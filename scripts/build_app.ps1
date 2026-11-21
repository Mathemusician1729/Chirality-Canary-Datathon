# Build dist\WellWatch\WellWatch.exe (one-folder app).  Run from the repo root:
#   powershell -ExecutionPolicy Bypass -File scripts\build_app.ps1
# Needs: pip install pyinstaller (see requirements.txt). Takes a few minutes (Qt WebEngine is large).
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

python scripts\make_icon.py build\wellwatch.ico
python -m PyInstaller wellwatch.spec --noconfirm --clean
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

# Safety: nothing secret or data-heavy may end up in the bundle.
$bad = Get-ChildItem dist\WellWatch -Recurse -File |
    Where-Object { $_.Name -match '\.(parquet|lic|db)$' -or $_.Name -match '^(gurobi\.lic|app_settings\.json)$' }
if ($bad) { $bad | ForEach-Object { Write-Host "UNEXPECTED FILE IN BUNDLE: $($_.FullName)" }; throw "bundle check failed" }

$size = (Get-ChildItem dist\WellWatch -Recurse -File | Measure-Object Length -Sum).Sum / 1MB
Write-Host ("Built dist\WellWatch\WellWatch.exe  ({0:N0} MB folder)" -f $size)
