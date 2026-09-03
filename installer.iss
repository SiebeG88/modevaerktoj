; Inno Setup script for Mødeværktøj
; Builds a single self-contained installer .exe that deploys the
; PyInstaller one-folder bundle to {autopf}\Mødeværktøj (Program Files).
; Kræver administrator; migrerer automatisk fra den gamle pr.-bruger-
; installation i %LOCALAPPDATA%\Programs\Mødeværktøj.

#define MyAppName "Mødeværktøj"
#define MyAppVersion "1.2.0"
#define MyAppPublisher "TBO"
#define MyAppExeName "Mødeværktøj.exe"
#define MyAppURL "https://github.com/SiebeG88/modevaerktoj"

[Setup]
AppId={{B6E5E2C7-7E4F-4F2A-9B0D-2C3D4E5F6A7B}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
; Altid Program Files — også ved opgradering fra den gamle pr.-bruger-sti.
UsePreviousAppDir=no
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=admin
CloseApplications=yes
OutputDir=installer_out
OutputBaseFilename=Modevaerktoj-Setup-{#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
SetupIconFile=AppIcon.ico
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName} {#MyAppVersion}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "danish"; MessagesFile: "compiler:Languages\Danish.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce

[InstallDelete]
; Migration: fjern den gamle pr.-bruger-installation og dens genveje, så appen
; ikke ligger to steder. (Bruger-config i %APPDATA%\Mødeværktøj røres ikke.)
Type: filesandordirs; Name: "{localappdata}\Programs\{#MyAppName}"
Type: files; Name: "{userdesktop}\{#MyAppName}.lnk"
Type: filesandordirs; Name: "{userprograms}\{#MyAppName}"

[Registry]
; Fjern den gamle pr.-bruger-uninstall-post (HKCU), så appen ikke optræder
; dobbelt under "Installerede apps".
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Uninstall\{{B6E5E2C7-7E4F-4F2A-9B0D-2C3D4E5F6A7B}_is1"; ValueType: none; Flags: deletekey

[Files]
Source: "dist\Mødeværktøj\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{commondesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent
