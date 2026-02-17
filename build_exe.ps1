param(
  [string]$AppName = "DropAir",
  [string]$IconPath = "assets\icon\drop_air_minimal.ico",
  [switch]$SkipCleanup
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path ".venv\Scripts\python.exe")) {
  throw "Virtual environment not found. Create it first: python -m venv .venv"
}

& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements-build.txt

if (-not (Test-Path $IconPath)) {
  Write-Host "Icon not found at $IconPath, creating default neon icon..."
  & .\.venv\Scripts\python.exe tools\make_icon.py --style neon --output $IconPath
}

$root = (Get-Location).Path
$iconAbs = (Resolve-Path $IconPath).Path
$templatesAbs = (Resolve-Path "templates").Path
$appAbs = (Resolve-Path "app.py").Path

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$workPath = "build\work_$stamp"
$specPath = "build\spec_$stamp"

& .\.venv\Scripts\pyinstaller.exe `
  --noconfirm `
  --clean `
  --name $AppName `
  --onefile `
  --console `
  --icon $iconAbs `
  --workpath $workPath `
  --specpath $specPath `
  --add-data "$templatesAbs;templates" `
  $appAbs

if ($LASTEXITCODE -ne 0) {
  throw "PyInstaller failed with exit code $LASTEXITCODE"
}

$exePath = "dist\$AppName.exe"
if (-not (Test-Path $exePath)) {
  throw "Build finished without output exe at $exePath"
}

Write-Host ""
Write-Host "Build finished: $exePath"

if (-not $SkipCleanup) {
  Write-Host "Cleaning temporary build/cache artifacts..."

  $cleanupPaths = @(
    "build",
    "__pycache__",
    "$AppName.spec"
  )

  foreach ($path in $cleanupPaths) {
    if (Test-Path $path) {
      Remove-Item -Recurse -Force $path
      Write-Host "Removed: $path"
    }
  }

  Get-ChildItem -Path . -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
    ForEach-Object {
      Remove-Item -Recurse -Force $_.FullName
      Write-Host "Removed: $($_.FullName)"
    }

  Get-ChildItem -Path . -Recurse -File -Include "*.pyc","*.pyo" -ErrorAction SilentlyContinue |
    ForEach-Object {
      Remove-Item -Force $_.FullName
      Write-Host "Removed: $($_.FullName)"
    }

  if (Test-Path "dist") {
    Get-ChildItem -Path "dist" -File | Where-Object { $_.Name -ne "$AppName.exe" } |
      ForEach-Object {
        Remove-Item -Force $_.FullName
        Write-Host "Removed: $($_.FullName)"
      }
  }

  Write-Host "Cleanup finished. Kept executable: $exePath"
}
