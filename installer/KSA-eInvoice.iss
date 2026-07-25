; ============================================================================
;  KSA e-Invoice - Windows installer (Inno Setup 6)
;
;  Ships the application source, creates Desktop + Start Menu shortcuts, and on
;  install:
;    * detects Python (installs Python 3.11 automatically if missing),
;    * creates a virtual environment and pip-installs requirements.txt,
;    * asks for the PostgreSQL host/port/database/user/password,
;    * generates .env, tests the connection, runs all migrations, provisions
;      the admin account, and verifies the app starts.
;
;  PostgreSQL is NOT installed - it must already be present on the machine.
;
;  Build:  installer\build_installer.ps1   (or run ISCC.exe on this file)
;  Output: ..\dist\KSA-eInvoice-Setup.exe
;
;  Silent install (for automation/validation):
;    KSA-eInvoice-Setup.exe /VERYSILENT /SUPPRESSMSGBOXES ^
;      /DBHOST=localhost /DBPORT=5432 /DBNAME=ksa_einvoice ^
;      /DBUSER=postgres /DBPASS=yourpassword
; ============================================================================

#define AppName "KSA e-Invoice"
#define AppVersion "1.0.0"
#define AppPublisher "abohassan1789"
#define AppURL "https://github.com/abohassan1789-svg/ksa-einvoice"
#define AppExeIcon "installer\assets\app.ico"

[Setup]
AppId={{7A3E9C21-5D48-4B16-9F2A-3C6E1B08D7F4}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
DefaultDirName={autopf}\KSA-eInvoice
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=no
AllowNoIcons=yes
OutputDir={#SourcePath}\..\dist
OutputBaseFilename=KSA-eInvoice-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
SourceDir={#SourcePath}\..
SetupIconFile=installer\assets\app.ico
UninstallDisplayIcon={app}\installer\assets\app.ico
UninstallDisplayName={#AppName}
LicenseFile=LICENSE

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Dirs]
; The app creates its venv, writes .env, logs and backups inside its own folder,
; so standard users need write access even though it lives under Program Files.
Name: "{app}"; Permissions: users-modify
Name: "{app}\config"; Permissions: users-modify
Name: "{app}\app\logs"; Permissions: users-modify
Name: "{app}\Backups"; Permissions: users-modify

[Files]
Source: "app\*"; DestDir: "{app}\app"; Flags: recursesubdirs createallsubdirs ignoreversion; Excludes: "*.pyc,__pycache__\*,logs\*.png,logs\*.jpg"
Source: "installer\scripts\*"; DestDir: "{app}\installer\scripts"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "installer\assets\app.ico"; DestDir: "{app}\installer\assets"; Flags: ignoreversion
Source: "requirements.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "requirements-dev.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: ".env.example"; DestDir: "{app}"; Flags: ignoreversion
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "run_app.vbs"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{sys}\wscript.exe"; Parameters: """{app}\run_app.vbs"""; WorkingDir: "{app}"; IconFilename: "{app}\installer\assets\app.ico"; Comment: "Launch KSA e-Invoice"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{sys}\wscript.exe"; Parameters: """{app}\run_app.vbs"""; WorkingDir: "{app}"; IconFilename: "{app}\installer\assets\app.ico"; Comment: "Launch KSA e-Invoice"; Tasks: desktopicon

[Run]
Filename: "{sys}\wscript.exe"; Parameters: """{app}\run_app.vbs"""; WorkingDir: "{app}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent runasoriginaluser; Check: SetupSucceeded

[UninstallDelete]
Type: filesandordirs; Name: "{app}\.venv"
Type: filesandordirs; Name: "{app}\config"
Type: files; Name: "{app}\.env"
Type: filesandordirs; Name: "{app}\app\logs"
Type: filesandordirs; Name: "{app}\Backups"
Type: files; Name: "{app}\install_setup.log"

[Code]
var
  DbPage: TInputQueryWizardPage;
  SetupOk: Boolean;

function InitializeSetup(): Boolean;
begin
  SetupOk := False;
  Result := True;
end;

procedure InitializeWizard();
begin
  DbPage := CreateInputQueryPage(wpSelectDir,
    'Database Connection',
    'Where is your PostgreSQL server?',
    'PostgreSQL must already be installed (this installer does not install it). ' +
    'Enter the connection details below. The database will be created automatically ' +
    'if it does not exist.');
  DbPage.Add('Database host:', False);
  DbPage.Add('Database port:', False);
  DbPage.Add('Database name:', False);
  DbPage.Add('Database username:', False);
  DbPage.Add('Database password:', True);

  { Defaults, overridable by /DBHOST= /DBPORT= ... on the command line (silent). }
  DbPage.Values[0] := ExpandConstant('{param:DBHOST|localhost}');
  DbPage.Values[1] := ExpandConstant('{param:DBPORT|5432}');
  DbPage.Values[2] := ExpandConstant('{param:DBNAME|ksa_einvoice}');
  DbPage.Values[3] := ExpandConstant('{param:DBUSER|postgres}');
  DbPage.Values[4] := ExpandConstant('{param:DBPASS|}');
end;

function IsValidPort(const S: String): Boolean;
var
  N, I: Integer;
begin
  Result := False;
  if (Length(S) = 0) or (Length(S) > 5) then Exit;
  for I := 1 to Length(S) do
    if (S[I] < '0') or (S[I] > '9') then Exit;
  N := StrToIntDef(S, -1);
  Result := (N >= 1) and (N <= 65535);
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = DbPage.ID then
  begin
    if Trim(DbPage.Values[0]) = '' then
    begin
      MsgBox('Please enter the database host.', mbError, MB_OK); Result := False; Exit;
    end;
    if not IsValidPort(Trim(DbPage.Values[1])) then
    begin
      MsgBox('Please enter a valid port (1-65535).', mbError, MB_OK); Result := False; Exit;
    end;
    if Trim(DbPage.Values[2]) = '' then
    begin
      MsgBox('Please enter the database name.', mbError, MB_OK); Result := False; Exit;
    end;
    if Trim(DbPage.Values[3]) = '' then
    begin
      MsgBox('Please enter the database username.', mbError, MB_OK); Result := False; Exit;
    end;
  end;
end;

function SetupSucceeded(): Boolean;
begin
  Result := SetupOk;
end;

procedure RunEnvironmentSetup();
var
  PwPath, Params, PsExe: String;
  ResultCode: Integer;
begin
  { Pass the password via a temp file so it never appears on a command line. }
  PwPath := ExpandConstant('{tmp}\dbpw.txt');
  SaveStringToFile(PwPath, DbPage.Values[4], False);

  PsExe := ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe');
  Params :=
    '-NoProfile -ExecutionPolicy Bypass -File "' + ExpandConstant('{app}\installer\scripts\setup_env.ps1') + '"' +
    ' -AppDir "' + ExpandConstant('{app}') + '"' +
    ' -DbHost "' + Trim(DbPage.Values[0]) + '"' +
    ' -DbPort "' + Trim(DbPage.Values[1]) + '"' +
    ' -DbName "' + Trim(DbPage.Values[2]) + '"' +
    ' -DbUser "' + Trim(DbPage.Values[3]) + '"' +
    ' -PasswordFile "' + PwPath + '"' +
    ' -LogFile "' + ExpandConstant('{app}\install_setup.log') + '"';

  WizardForm.StatusLabel.Caption :=
    'Setting up Python, installing dependencies and preparing the database. ' +
    'This can take several minutes...';

  if not Exec(PsExe, Params, '', SW_SHOW, ewWaitUntilTerminated, ResultCode) then
  begin
    SetupOk := False;
    MsgBox('Could not start the environment setup (PowerShell). ' +
           'See ' + ExpandConstant('{app}\install_setup.log'), mbError, MB_OK);
  end
  else if ResultCode <> 0 then
  begin
    SetupOk := False;
    MsgBox('Environment / database setup did not complete successfully.' + #13#10 +
           'The application files were installed, but Python setup, the database ' +
           'connection, migrations or app verification failed.' + #13#10#13#10 +
           'Please review the log for details:' + #13#10 +
           ExpandConstant('{app}\install_setup.log'), mbError, MB_OK);
  end
  else
  begin
    SetupOk := True;
  end;

  // Best-effort scrub of the temp password file (the temp folder is removed anyway).
  DeleteFile(PwPath);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    RunEnvironmentSetup();
end;
