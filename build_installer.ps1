param(
  [string]$AppName = "DropAir",
  [string]$AppVersion = "",
  [string]$Publisher = "Drop Air",
  [string]$OutputBaseFilename = "",
  [string]$IconPath = "assets\icon\drop_air_minimal.ico",
  [string]$ReleaseDir = "",
  [switch]$SkipExeBuild,
  [switch]$SkipReleaseBundle,
  [switch]$CreateDraftRelease
)

$ErrorActionPreference = "Stop"

function Resolve-AppVersion {
  param([string]$RequestedVersion)

  if ($RequestedVersion) {
    return $RequestedVersion.Trim().TrimStart("v")
  }

  if (Test-Path "VERSION") {
    $fileVersion = (Get-Content "VERSION" -Raw).Trim().TrimStart("v")
    if ($fileVersion) {
      return $fileVersion
    }
  }

  return "1.0.0"
}

function Get-InnoCompilerPath {
  if ($env:ISCC_PATH -and (Test-Path $env:ISCC_PATH)) {
    return (Resolve-Path $env:ISCC_PATH).Path
  }

  $pf86 = ${env:ProgramFiles(x86)}
  $pf = $env:ProgramFiles

  $candidates = @(
    (Join-Path $pf86 "Inno Setup 6\ISCC.exe"),
    (Join-Path $pf "Inno Setup 6\ISCC.exe"),
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    "C:\Program Files\Inno Setup 6\ISCC.exe"
  )

  foreach ($candidate in $candidates) {
    if ($candidate -and (Test-Path $candidate)) {
      return (Resolve-Path $candidate).Path
    }
  }

  throw "Inno Setup compiler not found. Install Inno Setup 6 and retry, or set ISCC_PATH to ISCC.exe."
}

function Get-GitHubRepo {
  try {
    $remote = (git config --get remote.origin.url).Trim()
  } catch {
    return ""
  }

  if (-not $remote) {
    return ""
  }

  $match = [regex]::Match($remote, "github\.com[:/](?<owner>[^/]+)/(?<repo>[^/\s]+?)(?:\.git)?$")
  if (-not $match.Success) {
    return ""
  }

  return "$($match.Groups["owner"].Value)/$($match.Groups["repo"].Value)"
}

function New-ReleaseBundle {
  param(
    [string]$Version,
    [string]$PortableExePath,
    [string]$InstallerExePath,
    [string]$TargetDir,
    [string]$Repo
  )

  if (Test-Path $TargetDir) {
    Remove-Item -LiteralPath $TargetDir -Recurse -Force
  }
  New-Item -ItemType Directory -Path $TargetDir | Out-Null

  $portableName = "$AppName-$Version.exe"
  $installerName = "$AppName-Setup-$Version.exe"
  $zipName = "$AppName-$Version-portable.zip"

  $portableTarget = Join-Path $TargetDir $portableName
  $installerTarget = Join-Path $TargetDir $installerName
  $zipTarget = Join-Path $TargetDir $zipName
  $checksumsPath = Join-Path $TargetDir "SHA256SUMS.txt"
  $notesPath = Join-Path $TargetDir "RELEASE-STEPS.txt"
  $publishScriptPath = Join-Path $TargetDir "publish-github-release.ps1"

  Copy-Item -LiteralPath $PortableExePath -Destination $portableTarget -Force
  Copy-Item -LiteralPath $InstallerExePath -Destination $installerTarget -Force
  Compress-Archive -LiteralPath $portableTarget -DestinationPath $zipTarget -CompressionLevel Optimal -Force

  $assets = @($portableTarget, $installerTarget, $zipTarget)
  $checksumLines = foreach ($asset in $assets) {
    $hash = Get-FileHash -LiteralPath $asset -Algorithm SHA256
    "$($hash.Hash.ToLower()) *$([System.IO.Path]::GetFileName($asset))"
  }
  Set-Content -LiteralPath $checksumsPath -Value $checksumLines -Encoding utf8

  $releaseUrl = if ($Repo) { "https://github.com/$Repo/releases" } else { "your GitHub releases page" }
  $notes = @(
    "Drop Air release bundle for $Version",
    "",
    "Local files prepared:",
    "- $portableName",
    "- $installerName",
    "- $zipName",
    "- SHA256SUMS.txt",
    "",
    "Publish steps:",
    "1. Commit and push your changes.",
    "2. Create or push the git tag $Version.",
    "3. Open $releaseUrl",
    "4. Create a release from tag $Version.",
    "5. Upload $installerName as the release asset.",
    "6. The in-app updater downloads that setup installer, runs it silently, and restarts Drop Air."
  )
  Set-Content -LiteralPath $notesPath -Value $notes -Encoding utf8

  if ($Repo) {
    $publishScript = @(
      '$ErrorActionPreference = "Stop"',
      '$root = Split-Path -Parent $MyInvocation.MyCommand.Path',
      '$installer = Join-Path $root "' + $installerName + '"',
      'gh release create ' + $Version + ' $installer --repo ' + $Repo + ' --title "' + $Version + '" --notes "Drop Air ' + $Version + ' release." --draft'
    )
    Set-Content -LiteralPath $publishScriptPath -Value $publishScript -Encoding utf8
  }

  return @{
    Portable = $portableTarget
    Installer = $installerTarget
    PortableZip = $zipTarget
    Checksums = $checksumsPath
    Notes = $notesPath
    PublishScript = $publishScriptPath
  }
}

$AppVersion = Resolve-AppVersion -RequestedVersion $AppVersion

if (-not $OutputBaseFilename) {
  $OutputBaseFilename = "$AppName-Setup-$AppVersion"
}

if (-not $ReleaseDir) {
  $ReleaseDir = Join-Path "dist\release" $AppVersion
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

$repo = Get-GitHubRepo
$bundle = $null

if (-not $SkipReleaseBundle) {
  $bundle = New-ReleaseBundle `
    -Version $AppVersion `
    -PortableExePath $exePath `
    -InstallerExePath $installerPath `
    -TargetDir $ReleaseDir `
    -Repo $repo
}

if ($CreateDraftRelease) {
  if (-not $repo) {
    throw "Could not detect a GitHub repository from origin. Set the remote first or create the release manually."
  }
  if (-not $bundle) {
    throw "CreateDraftRelease requires the release bundle. Remove -SkipReleaseBundle."
  }

  gh release create $AppVersion `
    $bundle.Portable `
    $bundle.Installer `
    $bundle.PortableZip `
    $bundle.Checksums `
    --repo $repo `
    --title $AppVersion `
    --notes "Drop Air $AppVersion release." `
    --draft
}

Write-Host ""
Write-Host "Installer created: $installerPath"
Write-Host "Portable EXE: $exePath"

if ($bundle) {
  Write-Host "Release bundle: $ReleaseDir"
  Write-Host "Checksums: $($bundle.Checksums)"
  Write-Host "Publish helper: $($bundle.PublishScript)"
}

if ($repo) {
  Write-Host "GitHub repo: $repo"
  Write-Host "Suggested tag: $AppVersion"
}
