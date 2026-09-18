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
Compression          =lzma2/normal
DefaultDirName       ={userpf}\{#MyAppName}
DefaultGroupName     ={#MyAppName}
PrivilegesRequired   =lowest
OutputBaseFilename   ={#MyAppName}-v{#MyAppVersion}-Setup
OutputDir            =..\..\dist
SolidCompression     =yes
UninstallDisplayIcon ={app}\backend\python\python.exe
WizardStyle          =modern
; The updater owns the restart after a silent installation.
RestartApplications  =no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "..\..\install\*"; DestDir: "{app}"; Excludes: "\.env,\build-info.json,\backend\data,\backend\debug,\backend\assets\config,\backend\assets\data,\backend\assets\debug,\data,\debug"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\..\install\build-info.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\install\.env"; DestDir: "{app}"; Flags: ignoreversion

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\backend\python\pythonw.exe"; Parameters: "-m backend.app.main"; WorkingDir: "{app}"
Name: "{group}\{#MyAppName} (Debug)"; Filename: "{app}\Start-Debug.bat"; WorkingDir: "{app}"
Name: "{group}\{cm:ProgramOnTheWeb,{#MyAppName}}"; Filename: "{#MyAppURL}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\backend\python\pythonw.exe"; Parameters: "-m backend.app.main"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\backend\python\pythonw.exe"; Parameters: "-m backend.app.main"; WorkingDir: "{app}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Runtime-generated data: CRM/pools SQLite, IM cache, MITM/API/MaaFW logs.
Type: filesandordirs; Name: "{app}\data"
Type: filesandordirs; Name: "{app}\backend\data"
Type: filesandordirs; Name: "{app}\backend\debug"

[Code]
function GetFileAttributes(FileName: String): LongWord;
  external 'GetFileAttributesW@kernel32.dll stdcall';

procedure RequireOrdinaryPath(Path: String);
begin
  { A linked ancestor could redirect source replacement into unrelated user files. }
  while Path <> '' do begin
    if DirExists(Path) then
      if (GetFileAttributes(Path) and $400) <> 0 then
        RaiseException('Cannot replace application source through a directory link: ' + Path);
    Path := ExtractFileDir(RemoveBackslashUnlessRoot(Path));
    if Path = ExtractFileDrive(Path) + '\' then
      Break;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  AppPath, RegisteredPath, BackendSource, FrontendSource, ExePath: String;
  Locator, Services, Processes, Process: Variant;
  Index: Integer;
begin
  if CurStep <> ssInstall then
    Exit;
  AppPath := AddBackslash(ExpandConstant('{app}'));
  BackendSource := AppPath + 'backend\app';
  FrontendSource := AppPath + 'frontend\out';
  if not DirExists(BackendSource) and not DirExists(FrontendSource) then
    Exit;

  { Only an existing per-user installation of this AppId owns these source trees. }
  if not RegQueryStringValue(HKCU,
      'Software\Microsoft\Windows\CurrentVersion\Uninstall\{580868F7-B96A-4214-829A-609D552F2C3A}_is1',
      'InstallLocation', RegisteredPath) then
    RaiseException('Application source already exists in an unregistered directory. Choose a new installation directory.');
  if CompareText(AddBackslash(RegisteredPath), AppPath) <> 0 then
    RaiseException('Application source does not belong to the registered installation. Choose a new installation directory.');
  RequireOrdinaryPath(BackendSource);
  RequireOrdinaryPath(FrontendSource);

  { Only readable executable paths belonging to this installation can block it.
    Unreadable processes and external Python runtimes cannot be attributed here;
    the updater is responsible for stopping its application job before Setup. }
  Locator := CreateOleObject('WbemScripting.SWbemLocator');
  Services := Locator.ConnectServer('', 'root\CIMV2');
  Processes := Services.ExecQuery('SELECT ExecutablePath FROM Win32_Process');
  for Index := 0 to Processes.Count - 1 do begin
    Process := Processes.ItemIndex(Index);
    ExePath := '';
    if not VarIsNull(Process.ExecutablePath) then
      ExePath := Process.ExecutablePath;
    ExePath := Lowercase(ExePath);
    if (Pos(Lowercase(AppPath + 'backend\python\'), ExePath) = 1) or
       (Pos(Lowercase(AppPath + 'backend\deps\bin\'), ExePath) = 1) or
       (Pos(Lowercase(AppPath + 'backend\.portable\yak\'), ExePath) = 1) then
      RaiseException('Close MaaAlibabaSupplier and its background processes before installing the update.');
  end;

  { V1 deliberately leaves obsolete source files in place. All shipped file
    replacement belongs to Inno Setup; deleting or moving entire source trees
    here bypasses its rollback and can destroy the old app on a later failure.
    Obsolete-file cleanup is deferred until it has a tested recovery policy. }
end;
