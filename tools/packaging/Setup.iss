; *** Inno Setup Script for MaaAlibabaSupplier ***
; Based on Inno Setup 6. Compile with: ISCC.exe Setup.iss /DMyAppVersion=<tag>
; Docs: https://jrsoftware.org/ishelp.php

#define MyAppName "MaaAlibabaSupplier"
#ifndef MyAppVersion
#define MyAppVersion "0.0.0"
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
Source: "..\..\install\*"; DestDir: "{app}"; Excludes: "vc_redist.x64.exe"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\..\install\vc_redist.x64.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\backend\python\pythonw.exe"; Parameters: "-m backend.app.main"; WorkingDir: "{app}"
Name: "{group}\{#MyAppName} (Debug)"; Filename: "{app}\Start-Debug.bat"; WorkingDir: "{app}"
Name: "{group}\{cm:ProgramOnTheWeb,{#MyAppName}}"; Filename: "{#MyAppURL}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\backend\python\pythonw.exe"; Parameters: "-m backend.app.main"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
; MaaFramework binaries require the VC++ 2015-2022 x64 runtime.
Filename: "{tmp}\vc_redist.x64.exe"; Parameters: "/install /quiet /norestart"; Flags: waituntilterminated runhidden; Check: not VCRedistInstalled
Filename: "{app}\backend\python\pythonw.exe"; Parameters: "-m backend.app.main"; WorkingDir: "{app}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall

[UninstallDelete]
; Runtime-generated data: CRM/pools SQLite, IM cache, MITM/API/MaaFW logs.
Type: filesandordirs; Name: "{app}\data"
Type: filesandordirs; Name: "{app}\backend\data"
Type: filesandordirs; Name: "{app}\backend\debug"

[Code]
function VCRedistInstalled: Boolean;
begin
  Result := RegKeyExists(HKLM, 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64');
end;
