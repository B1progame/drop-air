# Builds the Windows installers into dist\release.
#   .\build.ps1                 both editions
#   .\build.ps1 -Edition rust   Rust 2.x only
param(
  [ValidateSet("rust", "python", "all")][string]$Edition = "all",
  [string]$Python = ".venv\Scripts\python.exe"
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Find-InnoCompiler {
  $candidates = @($env:ISCC_PATH)
  foreach ($root in @($env:ProgramFiles, ${env:ProgramFiles(x86)}, (Join-Path $env:LOCALAPPDATA "Programs"))) {
    foreach ($version in 7, 6) { $candidates += Join-Path $root "Inno Setup $version\ISCC.exe" }
  }
  $found = $candidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
  if (-not $found) { throw "Inno Setup not found. Install it or set ISCC_PATH." }
  return $found
}

function Invoke-Checked([string]$Program, [string[]]$Arguments) {
  & $Program @Arguments
  if ($LASTEXITCODE -ne 0) { throw "$Program failed with exit code $LASTEXITCODE" }
}

function New-Installer([string]$SourceDir, [string]$Version) {
  Set-Content -Path (Join-Path $SourceDir "VERSION") -Value $Version -Encoding ascii
  Invoke-Checked (Find-InnoCompiler) @("/Qp", "/DSourceDir=$((Resolve-Path $SourceDir).Path)", "/DAppVersion=$Version", "installer\DropAir.iss")
  # Portable ZIP: the marker file keeps all data in a folder beside the program.
  $marker = Join-Path $SourceDir "portable.mode"
  Copy-Item "installer\portable.mode" $marker
  Compress-Archive -Path (Join-Path $SourceDir "*") -DestinationPath "dist\release\DropAir-$Version-portable.zip" -Force
  Remove-Item $marker
}

$iscc = Find-InnoCompiler

if ($Edition -in "rust", "all") {
  $version = (Select-String -Path rust\Cargo.toml -Pattern '^version = "(.+)"').Matches[0].Groups[1].Value
  # Keep the multi-gigabyte build cache out of synced folders.
  if (-not $env:CARGO_TARGET_DIR) { $env:CARGO_TARGET_DIR = Join-Path $env:LOCALAPPDATA "dropair-build" }
  Push-Location rust
  try { Invoke-Checked "cargo" @("build", "--release") } finally { Pop-Location }
  $stage = "dist\rust"
  if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
  New-Item -ItemType Directory -Force $stage | Out-Null
  Copy-Item (Join-Path $env:CARGO_TARGET_DIR "release\DropAir.exe") $stage
  New-Installer $stage $version
}

if ($Edition -in "python", "all") {
  $version = (Get-Content VERSION -Raw).Trim()
  Invoke-Checked $Python @("-m", "pip", "install", "-q", "-r", "requirements.txt", "-r", "requirements-build.txt")
  $work = Join-Path $env:LOCALAPPDATA "dropair-build\pyinstaller"
  $root = (Get-Location).Path
  $arguments = @(
    "-m", "PyInstaller", "--noconfirm", "--clean", "--log-level", "WARN",
    "--name", "DropAir", "--onedir", "--windowed",
    "--icon", "$root\assets\icon\drop_air_brand.ico",
    "--workpath", $work, "--specpath", $work, "--distpath", "$root\dist\python",
    "--add-data", "$root\web;web", "--add-data", "$root\VERSION;.",
    "--hidden-import", "app"
  )
  # Nothing here uses these; leaving them out keeps the install small.
  foreach ($module in "tkinter", "numpy", "PIL._avif", "PIL._webp", "PIL._imagingft", "PIL._imagingcms",
    "webview.platforms.qt", "webview.platforms.gtk", "webview.platforms.cef", "webview.platforms.cocoa", "webview.platforms.android") {
    $arguments += "--exclude-module", $module
  }
  Invoke-Checked $Python ($arguments + "desktop_app.py")
  New-Installer "dist\python\DropAir" $version
}

Get-ChildItem dist\release -Filter "DropAir-Setup-*.exe" | ForEach-Object {
  "{0}  {1:N1} MB  SHA256 {2}" -f $_.Name, ($_.Length / 1MB), (Get-FileHash $_.FullName).Hash
}
