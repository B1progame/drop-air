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
#define MyAppIconFile "..\\assets\\icon\\drop_air_minimal.ico"
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

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Dirs]
Name: "{app}\uploads"

[Files]
Source: "..\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\templates\*"; DestDir: "{app}\templates"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\assets\*"; DestDir: "{app}\assets"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\settings.json"; DestDir: "{app}"; Flags: onlyifdoesntexist

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
