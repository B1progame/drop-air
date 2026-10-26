param(
  [string]$AppName = "DropAir",
  [string]$IconPath = "assets\icon\drop_air_brand.ico",
  [switch]$SkipCleanup
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path ".venv\Scripts\python.exe")) {
  throw "Virtual environment not found. Create it first: python -m venv .venv"
}

& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
& .\.venv\Scripts\python.exe -m pip install -r requirements-build.txt

if (-not (Test-Path $IconPath)) {
  Write-Host "Icon not found at $IconPath, creating default neon icon..."
  & .\.venv\Scripts\python.exe tools\make_icon.py --style neon --output $IconPath
}

$root = (Get-Location).Path
$iconAbs = (Resolve-Path $IconPath).Path
$templatesAbs = (Resolve-Path "templates").Path
$assetsAbs = (Resolve-Path "assets").Path
$staticAbs = (Resolve-Path "static").Path
$versionAbs = (Resolve-Path "VERSION").Path
$appAbs = (Resolve-Path "desktop_app.py").Path
$launcherAbs = (Resolve-Path "launcher\native_launcher.c").Path
$coreName = "${AppName}Core"

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$workPath = "build\work_$stamp"
$specPath = "build\spec_$stamp"

& .\.venv\Scripts\python.exe -m PyInstaller `
  --noconfirm `
  --clean `
  --name $coreName `
  --onedir `
  --windowed `
  --hidden-import app `
  --hidden-import winrt.windows.devices.bluetooth.advertisement `
  --hidden-import werkzeug.serving `
  --icon $iconAbs `
  --workpath $workPath `
  --specpath $specPath `
  --add-data "$templatesAbs;templates" `
  --add-data "$assetsAbs;assets" `
  --add-data "$staticAbs;static" `
  --add-data "$versionAbs;." `
  $appAbs

if ($LASTEXITCODE -ne 0) {
  throw "PyInstaller failed with exit code $LASTEXITCODE"
}

$coreDir = "dist\$coreName"
$coreExePath = Join-Path $coreDir "$coreName.exe"
if (-not (Test-Path $coreExePath)) {
  throw "Build finished without output exe at $coreExePath"
}

$launcherCompiler = Get-Command gcc -ErrorAction SilentlyContinue
if (-not $launcherCompiler) {
  throw "A MinGW-w64 gcc compiler is required to build the native startup launcher. Install GCC and retry."
}

$exeDir = "dist\$AppName"
if (Test-Path $exeDir) {
  Remove-Item -LiteralPath $exeDir -Recurse -Force
}
New-Item -ItemType Directory -Path $exeDir | Out-Null
Get-ChildItem -LiteralPath $coreDir -Force | ForEach-Object {
  Copy-Item -LiteralPath $_.FullName -Destination $exeDir -Recurse -Force
}
Copy-Item -LiteralPath $assetsAbs -Destination (Join-Path $exeDir "assets") -Recurse -Force
$zeroconfLicense = Get-ChildItem ".venv\Lib\site-packages\zeroconf-*.dist-info\licenses\COPYING" -ErrorAction Stop | Select-Object -First 1
$licenseDir = Join-Path $exeDir "licenses"
New-Item -ItemType Directory -Path $licenseDir -Force | Out-Null
Copy-Item -LiteralPath $zeroconfLicense.FullName -Destination (Join-Path $licenseDir "zeroconf-COPYING") -Force

$exePath = Join-Path $exeDir "$AppName.exe"
& $launcherCompiler.Source `
  -municode `
  -mwindows `
  -O2 `
  -s `
  -static `
  -static-libgcc `
  "-Wl,--subsystem,windows" `
  "-o" $exePath `
  $launcherAbs `
  -luser32 `
  -lgdi32

if ($LASTEXITCODE -ne 0) {
  throw "Native startup launcher compilation failed with exit code $LASTEXITCODE"
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

  Write-Host "Cleanup finished. Kept executable: $exePath"
}
