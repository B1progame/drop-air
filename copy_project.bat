@echo off
setlocal

if "%~1"=="" (
  echo Usage: %~nx0 ^<output_zip_path_or_base_path^>
  echo Example 1: %~nx0 "C:\backups\drop-air.zip"
  echo Example 2: %~nx0 "C:\backups\drop-air"
  exit /b 1
)

set "SRC=%~dp0"
if "%SRC:~-1%"=="\" set "SRC=%SRC:~0,-1%"
set "OUT=%~1"

echo Creating zip from:
 echo   %SRC%
echo Output base:
 echo   %OUT%
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $src='%SRC%'; $out='%OUT%'; if ([IO.Path]::GetExtension($out) -eq '') { $zip = $out + '.zip' } else { $zip = $out }; $parent = Split-Path -Parent $zip; if ($parent -and -not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Path $parent | Out-Null }; $tmp = Join-Path $env:TEMP ('dropair_zip_' + [guid]::NewGuid().ToString()); New-Item -ItemType Directory -Path $tmp | Out-Null; try { robocopy $src $tmp /E /R:1 /W:1 /NFL /NDL /NJH /NJS /NP /XD .venv build dist __pycache__ | Out-Null; if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }; Compress-Archive -Path (Join-Path $tmp '*') -DestinationPath $zip -CompressionLevel Optimal -Force; Write-Host ('Created: ' + $zip) } finally { Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue }"

if errorlevel 1 (
  echo Zip creation failed.
  exit /b 1
)

echo Zip created successfully.
exit /b 0
