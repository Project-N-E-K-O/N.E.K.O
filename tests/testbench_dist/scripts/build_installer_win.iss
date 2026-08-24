; Inno Setup script for N.E.K.O. Testbench standalone.
; Compile with Inno Setup 6+ after PyInstaller one-dir output exists at
;   ..\output\pyinstaller\Testbench\
;
; Branding images: ..\assets\installer\win\

#define MyAppName "N.E.K.O. Testbench"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "Project N.E.K.O."
#define MyAppExeName "Testbench.exe"

[Setup]
AppId={{A7E3C2D1-9B4F-4E8A-9C11-TESTBENCH0001}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\NEKO-Testbench
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\output\installer
OutputBaseFilename=TestbenchSetup
; lzma/solid on ~1.5GB (onnx) can hang for hours on some machines — use zip.
Compression=zip
SolidCompression=no
WizardStyle=modern
WizardImageFile=..\assets\installer\win\wizard-sidebar.png
WizardSmallImageFile=..\assets\installer\win\wizard-small.png
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
; Optional Chinese UI — ship a copy next to this script when available:
; Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\output\pyinstaller\Testbench\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
