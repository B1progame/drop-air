param(
  [string]$AppName = "DropAir",
  [string]$AppVersion = "1.0.0",
  [string]$Publisher = "Drop Air",
  [string]$OutputBaseFilename = "DropAirSetup",
  [string]$IconPath = "assets\icon\drop_air_minimal.ico",
  [switch]$SkipExeBuild
)

$ErrorActionPreference = "Stop"

function Get-InnoCompilerPath {
  if ($env:ISCC_PATH -and (Test-Path $env:ISCC_PATH)) {
    return (Resolve-Path $env:ISCC_PATH).Path
  }

  $candidates = @(
    "$env:ProgramFiles(x86)\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
  )

  foreach ($candidate in $candidates) {
    if (Test-Path $candidate) {
      return (Resolve-Path $candidate).Path
    }
  }

  throw "Inno Setup compiler not found. Install Inno Setup 6 and retry, or set ISCC_PATH to ISCC.exe."
}

if (-not $SkipExeBuild) {
  & .\build_exe.ps1 -AppName $AppName -IconPath $IconPath
  if ($LASTEXITCODE -ne 0) {
    throw "build_exe.ps1 failed with exit code $LASTEXITCODE"
  }
}

$exePath = Join-Path "dist" ("$AppName.exe")
if (-not (Test-Path $exePath)) {
  throw "Expected executable not found: $exePath"
}

$issPath = "installer\DropAir.iss"
if (-not (Test-Path $issPath)) {
  throw "Inno script not found: $issPath"
}

if (-not (Test-Path $IconPath)) {
  throw "Icon not found: $IconPath"
}

$root = (Get-Location).Path
$compiler = Get-InnoCompilerPath
$iconAbs = (Resolve-Path $IconPath).Path

& $compiler `
  "/DMyAppName=$AppName" `
  "/DMyAppVersion=$AppVersion" `
  "/DMyAppPublisher=$Publisher" `
  "/DMyOutputBaseFilename=$OutputBaseFilename" `
  "/DMyAppExeName=$AppName.exe" `
  "/DMyAppIconFile=$iconAbs" `
  "$issPath"

if ($LASTEXITCODE -ne 0) {
  throw "ISCC failed with exit code $LASTEXITCODE"
}

$installerPath = Join-Path "dist" ("$OutputBaseFilename.exe")
if (-not (Test-Path $installerPath)) {
  throw "Installer build reported success, but output not found: $installerPath"
}

Write-Host ""
Write-Host "Installer created: $installerPath"
