# Main Server Installer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a new self-contained CRM main server installer that bundles the current database data and defaults PostgreSQL to port `5432`.

**Architecture:** Keep application connection behavior in Python (`app/config/*`) and keep machine setup in installer scripts (`installer/scripts/*`). Recreate the missing top-level installer build files so Inno Setup copies the frozen app, bundled PostgreSQL prerequisite, PowerShell setup scripts, and `installer/assets/data/crm-current-data.sql` into one customer-facing setup executable.

**Tech Stack:** Python 3.12, PyInstaller, Inno Setup 6, PowerShell, PostgreSQL, Pytest, PySide6 application runtime.

## Global Constraints

- This is the main server installer only; the client-machine installer is out of scope.
- The installer must be self-contained and must not depend on a client-side `D:` drive, external backup path, or developer workspace path.
- The bundled current data source is `installer/assets/data/crm-current-data.sql`.
- The default PostgreSQL port is `5432`.
- The restore must run only when the target database has no public user tables.
- Normal install must never drop, truncate, or overwrite an existing client database.
- The connection profile path is `%ProgramData%\CRM\crm-connection.json`.
- The client profile path is `%ProgramData%\CRM\crm-client-profile.json`.
- PostgreSQL passwords must be encrypted in the server profile and must not be written as plain text in the client profile.
- The installer must support `localhost` mode using `127.0.0.1` and LAN/server mode using a detected or manually entered IP address.

---

## File Structure

- Modify `app/config/connection_config.py`: set `DEFAULT_PORT = 5432` and update examples/comments.
- Modify `tests/test_connection_config.py`: replace hard-coded installer/profile port expectations from `8000` to `5432`.
- Create `tests/test_installer_contract.py`: fast tests that lock down required installer assets, build files, default port text, and bundled dump presence.
- Modify `installer/scripts/firewall.ps1`: update examples to use `5432`.
- Modify `installer/scripts/configure_postgres.ps1`: allow reading the PostgreSQL password from a temporary password file.
- Modify `installer/scripts/restore_database.ps1`: allow reading the PostgreSQL password from a temporary password file.
- Modify `installer/README_BUILD.md`: update product/output naming and default port documentation.
- Create `installer/build.ps1`: one-command build orchestration for PyInstaller and Inno Setup.
- Create `installer/CRM-Main-Server.iss`: Inno Setup script for the main server installer wizard and install/uninstall actions.

---

### Task 1: Set Default PostgreSQL Port To 5432

**Files:**
- Modify: `app/config/connection_config.py`
- Modify: `tests/test_connection_config.py`
- Modify: `installer/scripts/firewall.ps1`

**Interfaces:**
- Consumes: existing `ConnectionProfile`, `DEFAULT_PORT`, and profile serialization.
- Produces: `app.config.connection_config.DEFAULT_PORT == 5432`, and saved/exported profiles default to port `5432`.

- [ ] **Step 1: Write failing tests for the default port**

Edit `tests/test_connection_config.py` so it asserts the default and uses `5432` in profile examples:

```python
def test_default_port_is_postgresql_default():
    assert cc.DEFAULT_PORT == 5432
    assert cc.ConnectionProfile().server_port == 5432
```

Also update these existing assertions and constructors:

```python
p = cc.ConnectionProfile(server_address="192.168.1.10", server_port=5432)
```

```python
cloud_hostname="erp.example.com", server_port=5432,
```

```python
assert "erp.example.com:5432" in url
```

```python
connection_mode=cc.MODE_LAN, server_address="10.0.0.5", server_port=5432,
```

```python
assert on_disk["server"]["port"] == 5432
```

```python
server_address="10.0.0.5", server_port=5432, db_password="hunter2",
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```powershell
py -m pytest tests\test_connection_config.py -q
```

Expected before implementation: failure showing `8000 != 5432`.

- [ ] **Step 3: Update the implementation**

In `app/config/connection_config.py`, change the file-format example and constant:

```python
"server": {"address": "192.168.1.10", "port": 5432},
```

```python
DEFAULT_PORT = 5432  # PostgreSQL listening port advertised to clients.
```

In `installer/scripts/firewall.ps1`, update examples:

```powershell
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File firewall.ps1 -Port 5432
    powershell -ExecutionPolicy Bypass -File firewall.ps1 -Port 5432 -Remove
```

- [ ] **Step 4: Run focused tests**

Run:

```powershell
py -m pytest tests\test_connection_config.py -q
```

Expected: all tests in `tests/test_connection_config.py` pass.

- [ ] **Step 5: Commit**

```powershell
git add app\config\connection_config.py tests\test_connection_config.py installer\scripts\firewall.ps1
git commit -m "Use PostgreSQL default port for CRM server profiles"
```

---

### Task 2: Add Installer Contract Tests

**Files:**
- Create: `tests/test_installer_contract.py`
- Modify: `installer/README_BUILD.md`

**Interfaces:**
- Consumes: installer file layout under `installer/`.
- Produces: test coverage that fails if the main server installer is missing its required build files, bundled current data, or `5432` default references.

- [ ] **Step 1: Write failing tests for installer contract**

Create `tests/test_installer_contract.py`:

```python
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "installer"


def test_bundled_current_data_dump_exists_and_is_not_empty():
    dump = INSTALLER / "assets" / "data" / "crm-current-data.sql"
    assert dump.exists()
    assert dump.stat().st_size > 1_000_000


def test_main_server_installer_build_files_exist():
    assert (INSTALLER / "build.ps1").exists()
    assert (INSTALLER / "CRM-Main-Server.iss").exists()


def test_installer_sources_reference_postgresql_default_port():
    build_text = (INSTALLER / "CRM-Main-Server.iss").read_text(encoding="utf-8")
    readme_text = (INSTALLER / "README_BUILD.md").read_text(encoding="utf-8")
    assert "5432" in build_text
    assert "5432" in readme_text
    assert "8000" not in build_text


def test_restore_script_keeps_existing_database_data():
    restore_text = (INSTALLER / "scripts" / "restore_database.ps1").read_text(encoding="utf-8")
    assert "already has $tableCount public tables" in restore_text
    assert "exit 0" in restore_text
    forbidden = ["DROP DATABASE", "DROP TABLE", "TRUNCATE"]
    upper = restore_text.upper()
    for word in forbidden:
        assert word not in upper
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
py -m pytest tests\test_installer_contract.py -q
```

Expected before build files exist: failure for missing `installer/build.ps1` and `installer/CRM-Main-Server.iss`.

- [ ] **Step 3: Update build README default port and output name**

In `installer/README_BUILD.md`, replace the old default port references with:

```markdown
default **5432**
```

Replace output references with:

```markdown
Output: **`installer\Output\CRM-Main-Server-Setup.exe`**
```

Keep the data-seeding description explicit:

```markdown
The installer bundles `installer\assets\data\crm-current-data.sql` and restores
it only when the target `crm` database has no public user tables.
```

- [ ] **Step 4: Run tests that can pass before Inno files are created**

Run:

```powershell
py -m pytest tests\test_connection_config.py tests\test_installer_contract.py -q
```

Expected at this task boundary: only the missing build-file assertions fail if Task 3 has not yet run.

- [ ] **Step 5: Commit**

```powershell
git add tests\test_installer_contract.py installer\README_BUILD.md
git commit -m "Add installer packaging contract tests"
```

---

### Task 3: Recreate Installer Build Script

**Files:**
- Create: `installer/build.ps1`

**Interfaces:**
- Consumes: `requirements.txt`, `installer/entrypoints/crm_app.py`, `installer/entrypoints/crm_config.py`, `installer/CRM-Main-Server.iss`.
- Produces: `installer/dist/CRM/CRM.exe`, `installer/dist/crm-config.exe`, optional PostgreSQL prerequisite under `installer/dist/prereq/`, and `installer/Output/CRM-Main-Server-Setup.exe`.

- [ ] **Step 1: Confirm test failure for missing build script**

Run:

```powershell
py -m pytest tests\test_installer_contract.py::test_main_server_installer_build_files_exist -q
```

Expected: failure because `installer/build.ps1` or `installer/CRM-Main-Server.iss` is missing.

- [ ] **Step 2: Create `installer/build.ps1`**

Create `installer/build.ps1` with this structure:

```powershell
[CmdletBinding()]
param(
    [switch]$WithPostgres,
    [string]$PostgresUrl = 'https://get.enterprisedb.com/postgresql/postgresql-16.4-1-windows-x64.exe',
    [string]$InnoCompiler = ''
)

$ErrorActionPreference = 'Stop'

$InstallerDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $InstallerDir
$VenvDir = Join-Path $InstallerDir '.buildvenv'
$DistDir = Join-Path $InstallerDir 'dist'
$PrereqDir = Join-Path $DistDir 'prereq'
$OutputDir = Join-Path $InstallerDir 'Output'
$DumpPath = Join-Path $InstallerDir 'assets\data\crm-current-data.sql'
$IssPath = Join-Path $InstallerDir 'CRM-Main-Server.iss'

function Write-Step($Message) { Write-Host "[..] $Message" }
function Write-Ok($Message) { Write-Host "[OK] $Message" }

if (-not (Test-Path $DumpPath)) {
    throw "Missing bundled CRM data dump: $DumpPath"
}
if ((Get-Item $DumpPath).Length -le 0) {
    throw "Bundled CRM data dump is empty: $DumpPath"
}

if (-not (Test-Path $VenvDir)) {
    Write-Step 'Creating build virtual environment'
    py -3.12 -m venv $VenvDir
}

$Python = Join-Path $VenvDir 'Scripts\python.exe'
Write-Step 'Installing build dependencies'
& $Python -m pip install --upgrade pip
& $Python -m pip install -r (Join-Path $ProjectRoot 'requirements.txt') pyinstaller

if (Test-Path $DistDir) {
    Remove-Item -Recurse -Force $DistDir
}
New-Item -ItemType Directory -Force -Path $DistDir, $OutputDir | Out-Null

Write-Step 'Freezing CRM.exe'
& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --onedir `
    --name CRM `
    --distpath $DistDir `
    --workpath (Join-Path $InstallerDir 'build') `
    --add-data "$ProjectRoot\app\database\migrations;app\database\migrations" `
    (Join-Path $InstallerDir 'entrypoints\crm_app.py')
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed for CRM.exe' }

Write-Step 'Freezing crm-config.exe'
& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --console `
    --onefile `
    --name crm-config `
    --distpath $DistDir `
    --workpath (Join-Path $InstallerDir 'build') `
    (Join-Path $InstallerDir 'entrypoints\crm_config.py')
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed for crm-config.exe' }

if ($WithPostgres) {
    New-Item -ItemType Directory -Force -Path $PrereqDir | Out-Null
    $PgInstaller = Join-Path $PrereqDir 'postgresql-windows-x64.exe'
    if (-not (Test-Path $PgInstaller)) {
        Write-Step "Downloading PostgreSQL prerequisite from $PostgresUrl"
        Invoke-WebRequest -Uri $PostgresUrl -OutFile $PgInstaller
    }
    Write-Ok "PostgreSQL prerequisite ready: $PgInstaller"
}

if (-not $InnoCompiler) {
    $InnoCompiler = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
}
if (-not (Test-Path $InnoCompiler)) {
    throw "Inno Setup compiler not found: $InnoCompiler"
}

Write-Step 'Compiling Inno Setup installer'
& $InnoCompiler $IssPath
if ($LASTEXITCODE -ne 0) { throw 'Inno Setup compilation failed' }

Write-Ok 'Installer build complete.'
Write-Ok (Join-Path $OutputDir 'CRM-Main-Server-Setup.exe')
```

- [ ] **Step 3: Run static contract test**

Run:

```powershell
py -m pytest tests\test_installer_contract.py::test_main_server_installer_build_files_exist -q
```

Expected: still fails until Task 4 creates `CRM-Main-Server.iss`.

- [ ] **Step 4: Commit**

```powershell
git add installer\build.ps1
git commit -m "Add main server installer build script"
```

---

### Task 4: Add Password-File Support To PostgreSQL Setup Scripts

**Files:**
- Modify: `installer/scripts/configure_postgres.ps1`
- Modify: `installer/scripts/restore_database.ps1`
- Modify: `tests/test_installer_contract.py`

**Interfaces:**
- Consumes: temporary password file path created by the installer.
- Produces: both setup scripts accept `-SuperUserPasswordFile`, resolve it before using `PGPASSWORD`, and still support the existing `-SuperUserPassword` parameter for local developer use.

- [ ] **Step 1: Add failing contract test**

Append this test to `tests/test_installer_contract.py`:

```python
def test_postgres_setup_scripts_accept_password_file():
    for name in ["configure_postgres.ps1", "restore_database.ps1"]:
        text = (INSTALLER / "scripts" / name).read_text(encoding="utf-8")
        assert "SuperUserPasswordFile" in text
        assert "Get-Content -Raw" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
py -m pytest tests\test_installer_contract.py::test_postgres_setup_scripts_accept_password_file -q
```

Expected before implementation: failure because the scripts do not yet mention `SuperUserPasswordFile`.

- [ ] **Step 3: Update `configure_postgres.ps1` parameters**

Change the parameter block to include the file path:

```powershell
param(
    [Parameter(Mandatory = $true)][int]$Port,
    [Parameter(Mandatory = $true)][ValidateSet('lan', 'cloud')][string]$Mode,
    [string]$DbName = 'crm',
    [string]$SuperUser = 'postgres',
    [string]$SuperUserPassword = '',
    [string]$SuperUserPasswordFile = '',
    [string]$SubnetCidr = '',
    [string]$PgRoot = ''
)
```

After `$ErrorActionPreference = 'Stop'`, add:

```powershell
if ($SuperUserPasswordFile) {
    if (-not (Test-Path $SuperUserPasswordFile)) {
        throw "PostgreSQL password file was not found: $SuperUserPasswordFile"
    }
    $SuperUserPassword = (Get-Content -Raw -Path $SuperUserPasswordFile).TrimEnd("`r", "`n")
}
```

- [ ] **Step 4: Update `restore_database.ps1` parameters**

Change the parameter block to include the file path:

```powershell
param(
    [Parameter(Mandatory = $true)][int]$Port,
    [string]$DbName = 'crm',
    [string]$SuperUser = 'postgres',
    [string]$SuperUserPassword = '',
    [string]$SuperUserPasswordFile = '',
    [Parameter(Mandatory = $true)][string]$DumpPath,
    [string]$PgRoot = ''
)
```

After `$ErrorActionPreference = 'Stop'`, add:

```powershell
if ($SuperUserPasswordFile) {
    if (-not (Test-Path $SuperUserPasswordFile)) {
        throw "PostgreSQL password file was not found: $SuperUserPasswordFile"
    }
    $SuperUserPassword = (Get-Content -Raw -Path $SuperUserPasswordFile).TrimEnd("`r", "`n")
}
```

- [ ] **Step 5: Run contract test**

Run:

```powershell
py -m pytest tests\test_installer_contract.py::test_postgres_setup_scripts_accept_password_file -q
```

Expected: pass.

- [ ] **Step 6: Commit**

```powershell
git add installer\scripts\configure_postgres.ps1 installer\scripts\restore_database.ps1 tests\test_installer_contract.py
git commit -m "Allow installer scripts to read PostgreSQL password files"
```

---

### Task 5: Recreate Main Server Inno Setup Script

**Files:**
- Create: `installer/CRM-Main-Server.iss`

**Interfaces:**
- Consumes: `installer/dist/CRM/*`, `installer/dist/crm-config.exe`, `installer/scripts/*.ps1`, `installer/assets/data/crm-current-data.sql`, optional `installer/dist/prereq/postgresql-windows-x64.exe`.
- Produces: `installer/Output/CRM-Main-Server-Setup.exe`, installed app files, generated server/client connection profiles, PostgreSQL configuration, firewall rule, and data restore.

- [ ] **Step 1: Write the minimal Inno script**

Create `installer/CRM-Main-Server.iss` with the sections below. Keep all paths relative to the installer source folder:

```ini
#define MyAppName "CRM"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "CRM"
#define MyAppExeName "CRM.exe"

[Setup]
AppId={{4C5A6B9D-9D41-4F5B-AC43-202607105432}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\CRM
DefaultGroupName=CRM
OutputDir=Output
OutputBaseFilename=CRM-Main-Server-Setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64

[Files]
Source: "dist\CRM\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "dist\crm-config.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "scripts\configure_postgres.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion
Source: "scripts\firewall.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion
Source: "scripts\restore_database.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion
Source: "assets\data\crm-current-data.sql"; DestDir: "{app}\data"; Flags: ignoreversion
Source: "dist\prereq\postgresql-windows-x64.exe"; DestDir: "{tmp}"; Flags: ignoreversion skipifsourcedoesntexist

[Icons]
Name: "{group}\Crm-APP"; Filename: "{app}\CRM.exe"
Name: "{commondesktop}\Crm-APP"; Filename: "{app}\CRM.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[UninstallRun]
Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -File ""{app}\scripts\firewall.ps1"" -Port {code:GetSelectedPort} -Remove"; Flags: runhidden
```

- [ ] **Step 2: Add wizard state and pages**

Append this `[Code]` block skeleton, then implement the functions in the following steps:

```pascal
[Code]
var
  ModePage: TInputOptionWizardPage;
  LanPage: TInputQueryWizardPage;
  DbPage: TInputQueryWizardPage;
  SummaryPage: TWizardPage;
  SelectedMode: String;
  SelectedAddress: String;
  SelectedPort: String;
  DbPassword: String;

function GetSelectedPort(Param: String): String;
begin
  if SelectedPort = '' then
    Result := '5432'
  else
    Result := SelectedPort;
end;

procedure InitializeWizard;
begin
  SelectedPort := '5432';

  ModePage := CreateInputOptionPage(
    wpSelectDir,
    'Connection Mode',
    'Choose how the CRM main server will be used.',
    'Choose localhost for this machine only, or LAN/server for client machines.',
    True,
    False
  );
  ModePage.Add('Localhost only (127.0.0.1)');
  ModePage.Add('Server on LAN');
  ModePage.SelectedValueIndex := 0;

  LanPage := CreateInputQueryPage(
    ModePage.ID,
    'Server Address',
    'Choose the address clients will use.',
    'For localhost mode, keep 127.0.0.1. For LAN mode, enter this machine IP.'
  );
  LanPage.Add('Server address:', False);
  LanPage.Add('PostgreSQL port:', False);
  LanPage.Values[0] := '127.0.0.1';
  LanPage.Values[1] := '5432';

  DbPage := CreateInputQueryPage(
    LanPage.ID,
    'PostgreSQL Password',
    'Enter the PostgreSQL postgres user password.',
    'This password is used for setup and is stored encrypted in the server profile.'
  );
  DbPage.Add('postgres password:', True);
end;
```

- [ ] **Step 3: Add page validation**

Add validation that stores mode/address/port and rejects invalid choices:

```pascal
function NextButtonClick(CurPageID: Integer): Boolean;
var
  PortNum: Integer;
begin
  Result := True;

  if CurPageID = ModePage.ID then begin
    if ModePage.SelectedValueIndex = 0 then begin
      SelectedMode := 'lan';
      SelectedAddress := '127.0.0.1';
      SelectedPort := '5432';
      LanPage.Values[0] := SelectedAddress;
      LanPage.Values[1] := SelectedPort;
    end else begin
      SelectedMode := 'lan';
      if LanPage.Values[0] = '127.0.0.1' then
        LanPage.Values[0] := '';
    end;
  end;

  if CurPageID = LanPage.ID then begin
    SelectedAddress := Trim(LanPage.Values[0]);
    SelectedPort := Trim(LanPage.Values[1]);
    if SelectedAddress = '' then begin
      MsgBox('Server address is required.', mbError, MB_OK);
      Result := False;
      exit;
    end;
    if (not StrToIntEx(SelectedPort, PortNum)) or (PortNum < 1) or (PortNum > 65535) then begin
      MsgBox('Port must be between 1 and 65535.', mbError, MB_OK);
      Result := False;
      exit;
    end;
  end;

  if CurPageID = DbPage.ID then begin
    DbPassword := DbPage.Values[0];
    if DbPassword = '' then begin
      MsgBox('PostgreSQL password is required.', mbError, MB_OK);
      Result := False;
      exit;
    end;
  end;
end;
```

- [ ] **Step 4: Add install execution commands**

Add `CurStepChanged` with calls to the existing scripts. Use a temporary password file so the password is not passed directly to `crm-config.exe` arguments:

```pascal
procedure WritePasswordFile(var PasswordFile: String);
begin
  PasswordFile := ExpandConstant('{tmp}\crm-pg-password.txt');
  SaveStringToFile(PasswordFile, DbPassword, False);
end;

procedure RunOrFail(FileName: String; Params: String; Description: String);
var
  Code: Integer;
begin
  if not Exec(FileName, Params, '', SW_HIDE, ewWaitUntilTerminated, Code) then
    RaiseException(Description + ' could not start.');
  if Code <> 0 then
    RaiseException(Description + ' failed with exit code ' + IntToStr(Code) + '.');
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  PasswordFile: String;
  ProfilePath: String;
  ClientProfilePath: String;
begin
  if CurStep = ssPostInstall then begin
    WritePasswordFile(PasswordFile);
    ProfilePath := ExpandConstant('{commonappdata}\CRM\crm-connection.json');
    ClientProfilePath := ExpandConstant('{commonappdata}\CRM\crm-client-profile.json');

    RunOrFail(
      'powershell.exe',
      '-ExecutionPolicy Bypass -File "' + ExpandConstant('{app}\scripts\configure_postgres.ps1') +
      '" -Port ' + SelectedPort +
      ' -Mode lan -DbName crm -SuperUser postgres -SuperUserPasswordFile "' + PasswordFile + '"',
      'PostgreSQL configuration'
    );

    if SelectedAddress <> '127.0.0.1' then begin
      RunOrFail(
        'powershell.exe',
        '-ExecutionPolicy Bypass -File "' + ExpandConstant('{app}\scripts\firewall.ps1') +
        '" -Port ' + SelectedPort,
        'Firewall configuration'
      );
    end;

    RunOrFail(
      ExpandConstant('{app}\crm-config.exe'),
      'write --mode lan --address "' + SelectedAddress +
      '" --port ' + SelectedPort +
      ' --db crm --user postgres --password-file "' + PasswordFile +
      '" --out "' + ProfilePath +
      '" --export-client "' + ClientProfilePath + '"',
      'Connection profile creation'
    );

    RunOrFail(
      'powershell.exe',
      '-ExecutionPolicy Bypass -File "' + ExpandConstant('{app}\scripts\restore_database.ps1') +
      '" -Port ' + SelectedPort +
      ' -DbName crm -SuperUser postgres -SuperUserPasswordFile "' + PasswordFile +
      '" -DumpPath "' + ExpandConstant('{app}\data\crm-current-data.sql') + '"',
      'Bundled CRM data restore'
    );

    DeleteFile(PasswordFile);
  end;
end;
```

- [ ] **Step 5: Run contract tests**

Run:

```powershell
py -m pytest tests\test_installer_contract.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```powershell
git add installer\CRM-Main-Server.iss tests\test_installer_contract.py
git commit -m "Add main server Inno installer script"
```

---

### Task 6: Build And Smoke-Test The Installer Package

**Files:**
- Generated: `installer/dist/CRM/`
- Generated: `installer/dist/crm-config.exe`
- Generated: `installer/Output/CRM-Main-Server-Setup.exe`
- Modify only if needed: `installer/build.ps1`, `installer/CRM-Main-Server.iss`

**Interfaces:**
- Consumes: tasks 1-5.
- Produces: buildable main server installer executable.

- [ ] **Step 1: Run fast Python tests**

Run:

```powershell
py -m pytest tests\test_connection_config.py tests\test_installer_contract.py -q
```

Expected: all selected tests pass.

- [ ] **Step 2: Build app-only installer**

Run:

```powershell
powershell -ExecutionPolicy Bypass -File installer\build.ps1
```

Expected:

```text
[OK] Installer build complete.
[OK] ...\installer\Output\CRM-Main-Server-Setup.exe
```

If Inno Setup is not installed, stop and install Inno Setup 6 on the build machine, then rerun the command.

- [ ] **Step 3: Build self-contained installer with PostgreSQL prerequisite**

Run:

```powershell
powershell -ExecutionPolicy Bypass -File installer\build.ps1 -WithPostgres
```

Expected:

```text
[OK] PostgreSQL prerequisite ready: ...\installer\dist\prereq\postgresql-windows-x64.exe
[OK] Installer build complete.
```

- [ ] **Step 4: Inspect output**

Run:

```powershell
Get-Item installer\Output\CRM-Main-Server-Setup.exe | Select-Object FullName,Length,LastWriteTime
```

Expected: file exists and `Length` is greater than zero.

- [ ] **Step 5: Commit build-source fixes only**

Do not commit generated `installer/dist/`, `installer/build/`, `installer/Output/`, or `.buildvenv`.

Run:

```powershell
git status --short
```

If only source files need committing:

```powershell
git add installer\build.ps1 installer\CRM-Main-Server.iss installer\README_BUILD.md app\config\connection_config.py tests\test_connection_config.py tests\test_installer_contract.py installer\scripts\firewall.ps1
git commit -m "Build main server installer package"
```

---

### Task 7: Clean VM Acceptance Checklist

**Files:**
- Modify: `installer/README_BUILD.md` if verification notes need clarification.

**Interfaces:**
- Consumes: `installer/Output/CRM-Main-Server-Setup.exe`.
- Produces: verified installer ready for first customer delivery.

- [ ] **Step 1: Fresh localhost install**

On a clean Windows VM, run `CRM-Main-Server-Setup.exe` as administrator and choose localhost mode.

Expected:

```text
%ProgramData%\CRM\crm-connection.json exists
server.address == 127.0.0.1
server.port == 5432
database.password starts with dpapi:
crm database contains restored tables/data
Crm-APP launches and reaches the database
```

- [ ] **Step 2: Re-run installer on same VM**

Run the same installer again.

Expected: restore script reports that the database already has public tables and skips bundled data import. Existing records remain.

- [ ] **Step 3: Fresh LAN/server install**

On a second clean Windows VM, choose LAN/server mode and enter the machine IP.

Expected:

```text
%ProgramData%\CRM\crm-connection.json exists
server.address == selected LAN IP
server.port == 5432
%ProgramData%\CRM\crm-client-profile.json exists
Windows Firewall rule "CRM Server (PostgreSQL)" exists for TCP 5432
```

- [ ] **Step 4: Uninstall**

Uninstall from Windows Apps/Programs.

Expected: app is removed and the installer-managed firewall rule is removed.

- [ ] **Step 5: Record verification result**

Append a dated verification note to `installer/README_BUILD.md`:

```markdown
## Verification Result

- Date: 2026-07-10
- Installer: `installer\Output\CRM-Main-Server-Setup.exe`
- Localhost VM install: passed
- LAN/server VM install: passed
- Reinstall data protection: passed
- Uninstall firewall cleanup: passed
```

- [ ] **Step 6: Commit verification note**

```powershell
git add installer\README_BUILD.md
git commit -m "Document main server installer verification"
```
