@echo off
setlocal

if "%~1"=="" goto :usage

set "SIZE_ARG=%~1"
set "TARGET_ARG=%~2"

powershell -NoProfile -Command ^
  "$ErrorActionPreference = 'Stop';" ^
  "$sizeText = '%SIZE_ARG%'.Trim().ToLowerInvariant();" ^
  "$rawTarget = '%TARGET_ARG%'.Trim();" ^
  "if ([string]::IsNullOrWhiteSpace($rawTarget)) { $rawTarget = Join-Path (Join-Path (Get-Location) 'generated-test-files') ('test_' + $sizeText + '.bin') }" ^
  "$target = [System.IO.Path]::GetFullPath($rawTarget);" ^
  "if ($sizeText -notmatch '^(?<value>\d+(?:\.\d+)?)(?<unit>kb|mb|gb|tb|b)$') { throw 'Invalid size. Use values like 0.1gb, 500mb, 1024kb, or 1048576b.' }" ^
  "$value = [double]$matches['value'];" ^
  "$unit = $matches['unit'];" ^
  "$scale = switch ($unit) { 'b' { 1 } 'kb' { 1KB } 'mb' { 1MB } 'gb' { 1GB } 'tb' { 1TB } default { throw 'Unsupported unit.' } };" ^
  "$bytes = [long][math]::Round($value * $scale);" ^
  "if ($bytes -lt 1) { throw 'Size must be at least 1 byte.' }" ^
  "$dir = Split-Path -Parent $target;" ^
  "if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir | Out-Null }" ^
  "$stream = [System.IO.File]::Open($target, [System.IO.FileMode]::Create, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None);" ^
  "try { $stream.SetLength($bytes) } finally { $stream.Dispose() };" ^
  "Write-Host ('Created {0} ({1} bytes)' -f $target, $bytes)"

if errorlevel 1 (
  echo Failed to create test file.
  exit /b 1
)

echo.
echo Done.
echo File created for size %SIZE_ARG%
exit /b 0

:usage
echo Usage:
echo   %~nx0 SIZE [OUTPUT_FILE]
echo.
echo Examples:
echo   %~nx0 0.1gb
echo   %~nx0 500mb
echo   %~nx0 10gb my-big-test.bin
echo.
echo If OUTPUT_FILE is omitted, the file is created in generated-test-files\ next to this script.
exit /b 1
