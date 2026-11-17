; =====================================================================
; Inno Setup Script for AI Movie Translator & Auto Dubbing Studio
; Properly bundles _internal\python314.dll, FFmpeg, Whisper AI model,
; and assets so it never shows "Failed to load Python DLL" on other PCs.
; =====================================================================

#define MyAppName "AI Movie Dubbing Studio"
#define MyAppVersion "1.1.0"
#define MyAppPublisher "KhmerAIDubStudio"
#define MyAppExeName "AI_Movie_Dubbing_Studio.exe"
#define MyDistDir "d:\summray_mo\dist\AI_Movie_Dubbing_Studio"

[Setup]
AppId={{8F4E7A21-5C9B-4D3E-9A12-7B6D8E3F1029}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
; Output installer file will be saved in d:\summray_mo\dist\
OutputDir=d:\summray_mo\dist
OutputBaseFilename=AI_Movie_Dubbing_Studio_Setup_v{#MyAppVersion}
SetupIconFile=d:\summray_mo\assets\logo.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/fast
SolidCompression=no
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
DisableDirPage=no
DisableProgramGroupPage=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Dirs]
; Grant full read/write permissions to app folder and data/temp/projects so the app never fails in Program Files
Name: "{app}"; Permissions: users-modify
Name: "{app}\data"; Permissions: users-modify
Name: "{app}\temp"; Permissions: users-modify
Name: "{app}\projects"; Permissions: users-modify
Name: "{app}\whisper_models"; Permissions: users-modify

[Files]
; CRITICAL: Copy the entire PyInstaller onedir contents including _internal\, ffmpeg_bin\, whisper_models\, and assets\
Source: "{#MyDistDir}\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#MyDistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\assets\logo.ico"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\assets\logo.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

