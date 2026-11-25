#ifndef MyAppName
#define MyAppName "DropAir"
#endif

#ifndef MyAppVersion
#define MyAppVersion "1.0.0"
#endif

#ifndef MyAppPublisher
#define MyAppPublisher "Drop Air"
#endif

#ifndef MyOutputBaseFilename
#define MyOutputBaseFilename "DropAirSetup"
#endif

#ifndef MyAppExeName
#define MyAppExeName "DropAir.exe"
#endif

#ifndef MyAppIconFile
#define MyAppIconFile "..\\assets\\icon\\drop_air_brand.ico"
#endif

[Setup]
AppId={{B4EBC2F9-28A9-4675-9A4D-CE3A13E8B4E7}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename={#MyOutputBaseFilename}
SetupIconFile={#MyAppIconFile}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
UninstallDisplayIcon={app}\{#MyAppExeName}
CloseApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Components: normal; Flags: unchecked
Name: "startmenu"; Description: "Create a Start menu shortcut"; GroupDescription: "{cm:AdditionalIcons}"; Components: normal

[Types]
Name: "normal"; Description: "Normal installation (shortcuts and per-user app data)"
Name: "portable"; Description: "Portable installation (keeps app data beside Drop Air)"

[Components]
Name: "normal"; Description: "Normal installation with shortcuts and per-user app data"; Types: normal; Flags: fixed
Name: "portable"; Description: "Portable installation with app data beside Drop Air"; Types: portable; Flags: fixed

[Dirs]
Name: "{app}\uploads"
Name: "{app}\data"; Check: IsPortableInstall

[Files]
Source: "..\dist\{#MyAppName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "portable.mode"; DestDir: "{app}"; Flags: ignoreversion; Check: IsPortableInstall
Source: "..\templates\*"; DestDir: "{app}\templates"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\assets\*"; DestDir: "{app}\assets"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
Type: files; Name: "{app}\portable.mode"; Check: not IsPortableInstall

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\assets\icon\drop_air_brand.ico"; Tasks: startmenu; Components: normal
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\assets\icon\drop_air_brand.ico"; Tasks: desktopicon; Components: normal

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
function IsPortableInstall: Boolean;
begin
  Result := WizardIsComponentSelected('portable');
end;
