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

#define MyAppName "OpenVINO Windows LLM"
#define MyAppPublisher "Quazmoz"
#define MyAppExeName "OpenVINOWindowsLLM.exe"

[Setup]
AppId={{F94A3938-C943-4E6D-B482-852D4AAE06F8}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL=https://github.com/Quazmoz/openvino-windows-llm
AppSupportURL=https://github.com/Quazmoz/openvino-windows-llm/issues
AppUpdatesURL=https://github.com/Quazmoz/openvino-windows-llm/releases
DefaultDirName={localappdata}\Programs\OpenVINOWindowsLLM
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir={#ArtifactDir}
OutputBaseFilename=OpenVINO-Windows-LLM-{#MyAppVersion}-windows-x64-installer
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
VersionInfoProductVersion={#MyAppVersion}
CloseApplications=yes
CloseApplicationsFilter={#MyAppExeName},*.dll,*.pyd
RestartApplications=yes
SetupLogging=yes
UsePreviousAppDir=yes
UsePreviousGroup=yes
DisableDirPage=auto

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

; Mutable data is stored under %LOCALAPPDATA%\OpenVINOWindowsLLM, outside {app}.
; Remove the previous immutable payload before copying the new build so upgrades cannot
; mix Python modules with stale native extensions such as an older psutil Windows binary.
[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\app"
Type: filesandordirs; Name: "{app}\runtime"
Type: filesandordirs; Name: "{app}\web"
Type: files; Name: "{app}\{#MyAppExeName}"
Type: files; Name: "{app}\portable.flag"
Type: files; Name: "{app}\python*.dll"
Type: files; Name: "{app}\*.pyd"
Type: files; Name: "{app}\*.dll"

[Files]
Source: "{#SourceRoot}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
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

function InitializeSetup(): Boolean;
var
  Existing: String;
begin
  Result := True;
  Existing := InstalledVersion();
  if (Existing <> '') and (CompareCoreVersions(Existing, '{#MyAppVersion}') > 0) then
    Result := MsgBox(
      'A newer version (' + Existing + ') is installed.' + #13#10 + #13#10 +
      'Downgrading can leave configuration that this older release cannot read. Review the rollback documentation and create a configuration backup before continuing.' + #13#10 + #13#10 +
      'Continue with the downgrade?',
      mbConfirmation, MB_YESNO) = IDYES;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ResultCode: Integer;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    ResultCode := SuppressibleMsgBox(
      'Keep downloaded models, settings, logs, benchmark data, onboarding state, and configuration backups?' + #13#10 + #13#10 +
      'Choose Yes to preserve data for a future installation. Choose No to remove the user data directory.',
      mbConfirmation, MB_YESNO, IDYES);
    if ResultCode = IDNO then
      DelTree(ExpandConstant('{localappdata}\OpenVINOWindowsLLM'), True, True, True);
  end;
end;
