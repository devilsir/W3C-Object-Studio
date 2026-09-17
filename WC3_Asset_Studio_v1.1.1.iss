#define MyAppName "WC3 Asset Studio"
#define MyAppVersion "1.1.1"
#define MyAppPublisher "DarkSir#1620"
#define MyAppExeName "WC3 Asset Studio.exe"

[Setup]
AppId={{D57E92E4-A72C-4D2C-A5E7-5A805A5B5711}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} v{#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\WC3 Asset Studio
DefaultGroupName=WC3 Asset Studio
DisableProgramGroupPage=yes
OutputDir=output
OutputBaseFilename=WC3 Asset Studio v1.1.1 Setup
SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
VersionInfoVersion=1.1.1.0
VersionInfoProductName=WC3 Asset Studio
VersionInfoDescription=WC3 Asset Studio Installer
VersionInfoCompany={#MyAppPublisher}
LicenseFile=source\LICENSE

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "source\dist\win-unpacked\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\WC3 Asset Studio"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\WC3 Asset Studio"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,WC3 Asset Studio}"; Flags: nowait postinstall skipifsilent
