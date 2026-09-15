; *** Inno Setup Script for MaaAlibabaSupplier ***
; Based on Inno Setup 6. Compile with: ISCC.exe Setup.iss /DMyAppVersion=<tag>
; Docs: https://jrsoftware.org/ishelp.php

#define MyAppName "MaaAlibabaSupplier"
#ifndef MyAppVersion
; Fallback when compiled without /DMyAppVersion; mirrors the tools default
; (v0.0.0-local) minus the "v" prefix used by AppVersion.
#define MyAppVersion "0.0.0-local"
#endif
#define MyAppPublisher "EgooAI"
#define MyAppURL "https://github.com/EgooAI/MaaAlibabaSupplier"

[Setup]
; WARN: The value of AppId uniquely identifies this app. Do not use the same AppId value in installers for other apps.
AppId                ={{580868F7-B96A-4214-829A-609D552F2C3A}
AppName              ={#MyAppName}
AppVersion           ={#MyAppVersion}
AppVerName           ="{#MyAppName} {#MyAppVersion}"
AppPublisher         ={#MyAppPublisher}
AppPublisherURL      ={#MyAppURL}
AppSupportURL        ={#MyAppURL}

AllowNoIcons         =yes
Compression          =lzma2/max
DefaultDirName       ={userpf}\{#MyAppName}
DefaultGroupName     ={#MyAppName}
PrivilegesRequired   =lowest
OutputBaseFilename   ={#MyAppName}-v{#MyAppVersion}-Setup
OutputDir            =..\..\dist
SolidCompression     =yes
UninstallDisplayIcon ={app}\backend\python\python.exe
WizardStyle          =modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "..\..\install\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\backend\python\pythonw.exe"; Parameters: "-m backend.app.main"; WorkingDir: "{app}"
Name: "{group}\{#MyAppName} (Debug)"; Filename: "{app}\Start-Debug.bat"; WorkingDir: "{app}"
Name: "{group}\{cm:ProgramOnTheWeb,{#MyAppName}}"; Filename: "{#MyAppURL}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\backend\python\pythonw.exe"; Parameters: "-m backend.app.main"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\backend\python\pythonw.exe"; Parameters: "-m backend.app.main"; WorkingDir: "{app}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall

[UninstallDelete]
; Runtime-generated data: CRM/pools SQLite, IM cache, MITM/API/MaaFW logs.
Type: filesandordirs; Name: "{app}\data"
Type: filesandordirs; Name: "{app}\backend\data"
Type: filesandordirs; Name: "{app}\backend\debug"
