#ifndef MyAppVersion
  #error MyAppVersion must be supplied by scripts\build_release.ps1
#endif
#ifndef MyAppVersionNumeric
  #error MyAppVersionNumeric must be supplied by scripts\build_release.ps1
#endif
#ifndef SourceRoot
  #error SourceRoot must be supplied by scripts\build_release.ps1
#endif
#ifndef ArtifactDir
  #error ArtifactDir must be supplied by scripts\build_release.ps1
#endif
#ifndef AppIconPath
  #error AppIconPath must be supplied by scripts\build_release.ps1
#endif

#define MyAppName "InferBridge"
#define MyLegacyAppName "OpenVINO Windows LLM"
#define MyAppPublisher "Quazmoz"
#define MyAppExeName "InferBridge.exe"
#define MyLegacyAppExeName "OpenVINOWindowsLLM.exe"

[Setup]
AppId={{F94A3938-C943-4E6D-B482-852D4AAE06F8}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL=https://github.com/Quazmoz/InferBridge
AppSupportURL=https://github.com/Quazmoz/InferBridge/issues
AppUpdatesURL=https://github.com/Quazmoz/InferBridge/releases
DefaultDirName={localappdata}\Programs\InferBridge
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir={#ArtifactDir}
OutputBaseFilename=InferBridge-{#MyAppVersion}-windows-x64-installer
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.19041
SetupIconFile={#AppIconPath}
UninstallDisplayIcon={app}\{#MyAppExeName}
LicenseFile=..\LICENSE
VersionInfoVersion={#MyAppVersionNumeric}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} installer
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersionNumeric}
; The tray launcher and its server child are windowed processes with no top-level window,
; so Restart Manager's graceful shutdown cannot close them and an upgrade previously failed
; with "Setup was unable to automatically close all applications". PrepareToInstall asks a
; running instance to exit through its own command file first; force is the backstop that
; guarantees the locked files are released. Force is scoped by Restart Manager to processes
; holding files under {app}.
CloseApplications=force
CloseApplicationsFilter={#MyAppExeName},{#MyLegacyAppExeName},*.dll,*.pyd
RestartApplications=yes
SetupLogging=yes
UsePreviousAppDir=yes
UsePreviousGroup=no
DisableDirPage=auto

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

; Mutable data remains outside {app}; the application resolves InferBridge and legacy data roots safely.
; Remove the previous immutable payload before copying the new build so upgrades cannot
; mix Python modules with stale native extensions such as an older psutil Windows binary.
[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\app"
Type: filesandordirs; Name: "{app}\runtime"
Type: filesandordirs; Name: "{app}\web"
Type: files; Name: "{app}\{#MyAppExeName}"
Type: files; Name: "{app}\{#MyLegacyAppExeName}"
Type: filesandordirs; Name: "{userprograms}\{#MyLegacyAppName}"
Type: files; Name: "{userdesktop}\{#MyLegacyAppName}.lnk"
Type: files; Name: "{commondesktop}\{#MyLegacyAppName}.lnk"
Type: files; Name: "{app}\portable.flag"
Type: files; Name: "{app}\python*.dll"
Type: files; Name: "{app}\*.pyd"
Type: files; Name: "{app}\*.dll"

; The uninstaller removes its own files last, so the emptied program directory can survive
; the run. Removing it only when empty can never take user files with it.
[UninstallDelete]
Type: dirifempty; Name: "{app}"

[Files]
Source: "{#SourceRoot}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent; Check: CanLaunchInstalledRuntime

[Code]
const
  ShutdownWaitMilliseconds = 15000;
  ShutdownPollMilliseconds = 500;
  ForceStopSettleMilliseconds = 2000;
  RemoveTreeAttempts = 3;
  RemoveTreeSettleMilliseconds = 750;
  RuntimeCheckAttempts = 3;
  RuntimeCheckSettleMilliseconds = 500;
  RunKey = 'Software\Microsoft\Windows\CurrentVersion\Run';

var
  InstalledRuntimeReady: Boolean;

function DataRootPath(Index: Integer): String;
begin
  if Index = 0 then
    Result := ExpandConstant('{localappdata}\InferBridge')
  else
    Result := ExpandConstant('{localappdata}\OpenVINOWindowsLLM');
end;

function InstanceRunning(): Boolean;
var
  Index: Integer;
  LockFile: String;
begin
  Result := False;
  for Index := 0 to 1 do
  begin
    LockFile := DataRootPath(Index) + '\desktop-instance.lock';
    if not FileExists(LockFile) then
      Continue;
    { A live tray holds this file open without delete sharing. A lock file left behind by
      an earlier run deletes cleanly and the application recreates it at the next start. }
    if not DeleteFile(LockFile) then
    begin
      Result := True;
      exit;
    end;
  end;
end;

procedure RequestGracefulShutdown();
var
  Index: Integer;
  Root: String;
begin
  for Index := 0 to 1 do
  begin
    Root := DataRootPath(Index);
    if DirExists(Root) then
      SaveStringToFile(
        Root + '\tray-command.json',
        '{"command": "quit", "created_at": "installer"}',
        False);
  end;
end;

procedure TerminateImage(const ImageName: String);
var
  ResultCode: Integer;
begin
  { /T also ends the server child, which holds the same program files as its parent. }
  Exec(
    ExpandConstant('{sys}\taskkill.exe'),
    '/F /T /IM "' + ImageName + '"',
    '',
    SW_HIDE,
    ewWaitUntilTerminated,
    ResultCode);
end;

procedure StopRunningInstance();
var
  Waited: Integer;
begin
  if not InstanceRunning() then
    exit;
  { Ask the running instance to stop its server and exit on its own so in-flight state is
    written out. }
  RequestGracefulShutdown();
  Waited := 0;
  while (Waited < ShutdownWaitMilliseconds) and InstanceRunning() do
  begin
    Sleep(ShutdownPollMilliseconds);
    Waited := Waited + ShutdownPollMilliseconds;
  end;
  if not InstanceRunning() then
    exit;
  { A release older than 0.9.2-beta.1 ignores the shutdown request and a hung instance
    cannot honour it. Setup could fall back to a forced Restart Manager pass, but the
    uninstaller performs no such pass, so terminate here instead of leaving the program
    directory and the user data directory behind while reporting success.

    This only runs while the installed instance still holds its lock, so an unrelated
    portable instance is normally untouched; one running at the same moment is ended too. }
  TerminateImage('{#MyAppExeName}');
  TerminateImage('{#MyLegacyAppExeName}');
  Sleep(ForceStopSettleMilliseconds);
end;

function RemoveTree(const Path: String): Boolean;
var
  Attempt, ResultCode: Integer;
begin
  if not DirExists(Path) then
  begin
    Result := True;
    exit;
  end;
  for Attempt := 1 to RemoveTreeAttempts do
  begin
    DelTree(Path, True, True, True);
    if not DirExists(Path) then
    begin
      Result := True;
      exit;
    end;
    { Read-only, system, and hidden attributes block deletion, and an antivirus scan or a
      recently exited process can hold a handle for a moment. Clear the attributes, let the
      transient handle close, and try again before reporting the path as remaining. }
    Exec(
      ExpandConstant('{cmd}'),
      '/c attrib -r -s -h "' + Path + '\*" /s /d',
      '',
      SW_HIDE,
      ewWaitUntilTerminated,
      ResultCode);
    Sleep(RemoveTreeSettleMilliseconds);
  end;
  Result := not DirExists(Path);
end;

procedure RemoveStartupRegistration();
begin
  { Start with Windows survives uninstall otherwise, and Windows then tries to launch a
    deleted executable at every logon. }
  RegDeleteValue(HKCU, RunKey, 'InferBridge');
  RegDeleteValue(HKCU, RunKey, 'OpenVINOWindowsLLM');
end;

function CoreVersionPart(const Value: String; PartIndex: Integer): Integer;
var
  Clean, Segment: String;
  DashPos, DotPos, Index: Integer;
begin
  Result := 0;
  Clean := Value;
  DashPos := Pos('-', Clean);
  if DashPos > 0 then
    Delete(Clean, DashPos, Length(Clean));
  for Index := 0 to PartIndex do
  begin
    DotPos := Pos('.', Clean);
    if DotPos > 0 then
    begin
      Segment := Copy(Clean, 1, DotPos - 1);
      Delete(Clean, 1, DotPos);
    end
    else
    begin
      Segment := Clean;
      Clean := '';
    end;
  end;
  Result := StrToIntDef(Segment, 0);
end;

function CompareCoreVersions(const Left, Right: String): Integer;
var
  Index, LeftPart, RightPart: Integer;
begin
  Result := 0;
  for Index := 0 to 2 do
  begin
    LeftPart := CoreVersionPart(Left, Index);
    RightPart := CoreVersionPart(Right, Index);
    if LeftPart < RightPart then begin Result := -1; exit; end;
    if LeftPart > RightPart then begin Result := 1; exit; end;
  end;
end;

function InstalledVersion(): String;
var
  Key: String;
begin
  Result := '';
  Key := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{F94A3938-C943-4E6D-B482-852D4AAE06F8}_is1';
  RegQueryStringValue(HKCU, Key, 'DisplayVersion', Result);
  if Result = '' then
    RegQueryStringValue(HKLM, Key, 'DisplayVersion', Result);
end;

function VerifyInstalledRuntime(): Boolean;
var
  Attempt, ResultCode: Integer;
  Started: Boolean;
begin
  Result := False;
  for Attempt := 1 to RuntimeCheckAttempts do
  begin
    ResultCode := -1;
    Started := Exec(
      ExpandConstant('{app}\{#MyAppExeName}'),
      '--bootstrap-smoke',
      ExpandConstant('{app}'),
      SW_HIDE,
      ewWaitUntilTerminated,
      ResultCode);
    if Started and (ResultCode = 0) then
    begin
      Result := True;
      exit;
    end;
    if Attempt < RuntimeCheckAttempts then
      Sleep(RuntimeCheckSettleMilliseconds);
  end;
end;

function CanLaunchInstalledRuntime(): Boolean;
begin
  Result := InstalledRuntimeReady;
end;

function InitializeSetup(): Boolean;
var
  Existing: String;
begin
  InstalledRuntimeReady := False;
  Result := True;
  Existing := InstalledVersion();
  if (Existing <> '') and (CompareCoreVersions(Existing, '{#MyAppVersion}') > 0) then
    Result := MsgBox(
      'A newer version (' + Existing + ') is installed.' + #13#10 + #13#10 +
      'Downgrading can leave configuration that this older release cannot read. Review the rollback documentation and create a configuration backup before continuing.' + #13#10 + #13#10 +
      'Continue with the downgrade?',
      mbConfirmation, MB_YESNO) = IDYES;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  StopRunningInstance();
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep <> ssPostInstall then
    exit;

  InstalledRuntimeReady := VerifyInstalledRuntime();
  if not InstalledRuntimeReady then
    SuppressibleMsgBox(
      'InferBridge was copied to disk, but its installed runtime failed the startup self-check.' + #13#10 + #13#10 +
      'Setup will not launch the application automatically. Run the latest installer over this installation again. Your downloaded models and settings are preserved.',
      mbError, MB_OK, IDOK);
end;

function InitializeUninstall(): Boolean;
begin
  { Runs before any file is removed. Without this the tray keeps its program files and its
    log files open, so the uninstaller leaves a partly populated installation behind. }
  StopRunningInstance();
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Choice: Integer;
  Remaining: String;
begin
  if CurUninstallStep <> usPostUninstall then
    exit;

  Remaining := '';
  RemoveStartupRegistration();
  { The uninstaller only removes files it recorded at install time. Compiled Python caches
    and any payload left by an interrupted upgrade are not recorded and would otherwise
    keep the program directory alive. }
  if not RemoveTree(ExpandConstant('{app}\_internal')) then
    Remaining := Remaining + ExpandConstant('{app}\_internal') + #13#10;

  Choice := SuppressibleMsgBox(
    'Keep downloaded models, settings, logs, benchmark data, onboarding state, and configuration backups?' + #13#10 + #13#10 +
    'Choose Yes to preserve data for a future installation. Choose No to remove the user data directory.',
    mbConfirmation, MB_YESNO, IDYES);
  if Choice = IDNO then
  begin
    if not RemoveTree(DataRootPath(0)) then
      Remaining := Remaining + DataRootPath(0) + #13#10;
    if not RemoveTree(DataRootPath(1)) then
      Remaining := Remaining + DataRootPath(1) + #13#10;
  end;

  { Reporting the exact paths beats silently discarding the DelTree result and telling the
    user the uninstall succeeded while gigabytes of model data remain on disk. }
  if Remaining <> '' then
    SuppressibleMsgBox(
      'These folders could not be removed completely:' + #13#10 + #13#10 + Remaining + #13#10 +
      'Close any program still using them and delete them manually.',
      mbInformation, MB_OK, IDOK);
end;
