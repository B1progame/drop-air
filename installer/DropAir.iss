; One installer script for both editions (build.ps1 passes the defines).
; Both use the same AppId, so installing one cleanly replaces the other
; and settings in %LOCALAPPDATA%\DropAir stay.
#ifndef SourceDir
#define SourceDir "..\dist\app"
#endif
#ifndef AppVersion
#define AppVersion "2.0.0"
#endif

[Setup]
AppId={{B4EBC2F9-28A9-4675-9A4D-CE3A13E8B4E7}
AppName=DropAir
AppVersion={#AppVersion}
AppPublisher=Drop Air
DefaultDirName={autopf}\DropAir
DisableProgramGroupPage=yes
OutputDir=..\dist\release
OutputBaseFilename=DropAir-Setup-{#AppVersion}
SetupIconFile=..\assets\icon\drop_air_brand.ico
UninstallDisplayIcon={app}\DropAir.exe
Compression=lzma2/ultra
SolidCompression=yes
WizardStyle=modern dynamic
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
CloseApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; Remove everything older releases installed, including the ~385 MB Qt runtime.
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\templates"
Type: filesandordirs; Name: "{app}\assets"
Type: filesandordirs; Name: "{app}\licenses"
Type: files; Name: "{app}\DropAirCore.exe"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\DropAir"; Filename: "{app}\DropAir.exe"
Name: "{autodesktop}\DropAir"; Filename: "{app}\DropAir.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\DropAir.exe"; Description: "Launch DropAir"; Flags: nowait postinstall skipifsilent runasoriginaluser

[Code]
// Some old installs ended up inside the data folder (…\DropAir\rollback).
// A normal, visible install moves back to Program Files. Silent updates keep
// the folder they were given, because the old updater checks the files there.
procedure InitializeWizard;
begin
  if (not WizardSilent) and (Pos('\dropair\rollback', Lowercase(WizardForm.DirEdit.Text)) > 0) then
    WizardForm.DirEdit.Text := ExpandConstant('{autopf}\DropAir');
end;
