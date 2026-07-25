<#
    setup_env.ps1 — post-install environment + database bootstrap for KSA e-Invoice.

    Runs (in order):
      1. Ensure a suitable Python (3.9–3.13, 64-bit) exists; download + install
         Python 3.11 silently if none is found.  (PostgreSQL is NOT installed.)
      2. Create a virtual environment in <AppDir>\.venv.
      3. pip install -r requirements.txt into that venv.
      4. Generate <AppDir>\.env from the database values.
      5. Create the database if missing, TEST the connection, run all pending
         migrations, and provision the admin account + permissions.
      6. Write the app's encrypted connection profile so the GUI starts without
         prompting.
      7. Verify the application starts (headless smoke test).

    Designed to be safe to re-run. Every step logs to -LogFile and to the console.
    Exit code 0 = success; non-zero = the step that failed is the last log line.

    This script is intentionally standalone so it can be tested outside Inno Setup.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string]$AppDir,
    [Parameter(Mandatory = $true)] [string]$DbHost,
    [Parameter(Mandatory = $true)] [string]$DbPort,
    [Parameter(Mandatory = $true)] [string]$DbName,
    [Parameter(Mandatory = $true)] [string]$DbUser,
    [string]$PasswordFile = "",
    [string]$LogFile = "",
    [string]$PythonUrl = "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe"
)

$ErrorActionPreference = "Stop"
if ([string]::IsNullOrEmpty($LogFile)) { $LogFile = Join-Path $AppDir "install_setup.log" }

function Log {
    param([string]$Message)
    $line = ("[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $Message)
    Write-Host $line
    try { Add-Content -Path $LogFile -Value $line -Encoding UTF8 } catch { }
}

function Fail {
    param([string]$Message)
    Log "ERROR: $Message"
    Log "Setup FAILED. See the full log at: $LogFile"
    exit 1
}

# --- Probe a candidate Python: prints "<major>.<minor> <64|32>" if runnable ----
function Test-PythonExe {
    param([string[]]$Command)
    # Probe deliberately uses no double-quotes and no '%' so PowerShell 5.1 does
    # not mangle the -c argument when passing it to the native interpreter.
    $ErrorActionPreference = "SilentlyContinue"
    try {
        $rest = @()
        if ($Command.Length -gt 1) { $rest = $Command[1..($Command.Length - 1)] }
        $probe = 'import sys;print(sys.version_info[0]);print(sys.version_info[1]);print(sys.maxsize > 2**32)'
        $out = & $Command[0] @($rest + @("-c", $probe)) 2>$null
        if ($LASTEXITCODE -ne 0) { return $null }
        $lines = @($out | ForEach-Object { "$_".Trim() } | Where-Object { $_ -ne "" })
        if ($lines.Count -lt 3) { return $null }
        $maj = [int]$lines[0]; $min = [int]$lines[1]; $is64 = ($lines[2] -eq "True")
        if (-not $is64) { return $null }
        if ($maj -ne 3 -or $min -lt 9 -or $min -gt 13) { return $null }
        # Resolve the real interpreter path (handles the "py" launcher).
        $exeOut = & $Command[0] @($rest + @("-c", "import sys;print(sys.executable)")) 2>$null
        if ($LASTEXITCODE -ne 0) { return $null }
        $exeLines = @($exeOut | ForEach-Object { "$_".Trim() } | Where-Object { $_ -ne "" })
        if ($exeLines.Count -lt 1) { return $null }
        return $exeLines[$exeLines.Count - 1]
    } catch {
        return $null
    }
}

function Resolve-Python {
    $candidates = @(
        @("py", "-3.11"),
        @("py", "-3.12"),
        @("py", "-3.10"),
        @("py", "-3"),
        @("python"),
        @(Join-Path $env:ProgramFiles "Python311\python.exe"),
        @(Join-Path $env:ProgramFiles "Python312\python.exe"),
        @(Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"),
        @(Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe")
    )
    foreach ($c in $candidates) {
        if ($c.Count -eq 1 -and ($c[0] -like "*\*") -and -not (Test-Path $c[0])) { continue }
        $exe = Test-PythonExe -Command $c
        if ($exe) { return $exe }
    }
    return $null
}

function Install-Python {
    Log "No suitable Python found. Downloading Python 3.11 from python.org ..."
    $dest = Join-Path $env:TEMP "python-3.11-ksa-einvoice.exe"
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $PythonUrl -OutFile $dest -UseBasicParsing
    } catch {
        Fail "Could not download Python from $PythonUrl. Install Python 3.11 (64-bit) manually and re-run. Details: $($_.Exception.Message)"
    }
    Log "Installing Python silently (all users, add to PATH) ..."
    $p = Start-Process -FilePath $dest -ArgumentList @("/quiet", "InstallAllUsers=1", "PrependPath=1", "Include_pip=1", "Include_test=0") -Wait -PassThru
    if ($p.ExitCode -ne 0) {
        Fail "Python installer returned exit code $($p.ExitCode)."
    }
    Log "Python installed."
}

# --- Run configure_cli / migrate through the venv python -----------------------
function Invoke-Py {
    param([string]$PyExe, [string[]]$ArgList, [string]$What)
    Log "-> $What"
    $out = & $PyExe @ArgList
    $code = $LASTEXITCODE
    foreach ($l in $out) { Log "   $l" }
    return [pscustomobject]@{ Code = $code; Text = ($out -join "`n") }
}

function Invoke-CliJson {
    param([string]$PyExe, [string[]]$ArgList, [string]$What)
    $r = Invoke-Py -PyExe $PyExe -ArgList $ArgList -What $What
    $jsonLine = ($r.Text -split "`n" | Where-Object { $_.Trim().StartsWith("{") } | Select-Object -Last 1)
    $obj = $null
    if ($jsonLine) { try { $obj = $jsonLine | ConvertFrom-Json } catch { } }
    if ($r.Code -ne 0 -or ($obj -ne $null -and -not $obj.ok)) {
        $msg = if ($obj -ne $null -and $obj.message) { $obj.message } else { "exit code $($r.Code)" }
        Fail "$What failed: $msg"
    }
    return $obj
}

# ------------------------------------------------------------------------------
Log "==============================================================="
Log "KSA e-Invoice environment setup"
Log "AppDir=$AppDir  Db=$DbUser@${DbHost}:${DbPort}/$DbName"
Log "==============================================================="

if (-not (Test-Path $AppDir)) { Fail "Application directory not found: $AppDir" }
Set-Location $AppDir
$env:PYTHONIOENCODING = "utf-8"
# PYTHONPATH is intentionally NOT set yet: it would pollute Python detection
# below. It is set after the venv exists so the app package imports for the DB
# tools (python -m app...).
Remove-Item Env:\PYTHONPATH -ErrorAction SilentlyContinue
Remove-Item Env:\PYTHONHOME -ErrorAction SilentlyContinue

# 1) Python -------------------------------------------------------------------
$basePy = Resolve-Python
if (-not $basePy) {
    Install-Python
    $basePy = Resolve-Python
    if (-not $basePy) { Fail "Python is still not available after installation." }
}
Log "Using Python: $basePy"

# 2) Virtual environment ------------------------------------------------------
$venvPy = Join-Path $AppDir ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    Log "Creating virtual environment in .venv ..."
    & $basePy -m venv (Join-Path $AppDir ".venv")
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $venvPy)) { Fail "Failed to create the virtual environment." }
} else {
    Log "Virtual environment already exists; reusing it."
}

# The app package must be importable for the `python -m app...` calls below.
$env:PYTHONPATH = $AppDir

# 3) Dependencies -------------------------------------------------------------
Log "Upgrading pip ..."
& $venvPy -m pip install --upgrade pip --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { Fail "pip self-upgrade failed." }

Log "Installing dependencies from requirements.txt (this downloads ~200 MB and can take several minutes) ..."
& $venvPy -m pip install -r (Join-Path $AppDir "requirements.txt") --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { Fail "pip install -r requirements.txt failed. Check your internet connection." }
Log "Dependencies installed."

# 4) .env ---------------------------------------------------------------------
Log "Generating .env ..."
$makeEnv = Join-Path $AppDir "installer\scripts\make_env.py"
& $venvPy $makeEnv (Join-Path $AppDir ".env") $DbHost $DbPort $DbName $DbUser $PasswordFile
if ($LASTEXITCODE -ne 0) { Fail "Failed to write .env." }

# 5) Database: create (if missing) -> test -> migrate -> provision ------------
$pwArgs = @()
if ($PasswordFile -and (Test-Path $PasswordFile)) { $pwArgs = @("--password-file", $PasswordFile) }

Invoke-CliJson -PyExe $venvPy -What "Creating database (if missing)" -ArgList (@(
    "-m", "app.config.configure_cli", "create-db",
    "--host", $DbHost, "--port", $DbPort, "--db", $DbName, "--user", $DbUser) + $pwArgs) | Out-Null

Invoke-CliJson -PyExe $venvPy -What "Testing database connection" -ArgList (@(
    "-m", "app.config.configure_cli", "test-db",
    "--host", $DbHost, "--port", $DbPort, "--db", $DbName, "--user", $DbUser) + $pwArgs) | Out-Null

$mig = Invoke-Py -PyExe $venvPy -What "Running database migrations" -ArgList (@(
    "-m", "app.database.migrate",
    "--host", $DbHost, "--port", $DbPort, "--db", $DbName, "--user", $DbUser) + $pwArgs)
if ($mig.Code -ne 0) { Fail "Database migrations failed." }

Invoke-CliJson -PyExe $venvPy -What "Provisioning admin account + permissions" -ArgList (@(
    "-m", "app.config.configure_cli", "provision",
    "--host", $DbHost, "--port", $DbPort, "--db", $DbName, "--user", $DbUser) + $pwArgs) | Out-Null

# 6) Connection profile (so the GUI does not prompt on first launch) ----------
$profilePath = Join-Path $AppDir "config\connection.json"
Invoke-CliJson -PyExe $venvPy -What "Writing connection profile" -ArgList (@(
    "-m", "app.config.configure_cli", "write",
    "--mode", "lan", "--address", $DbHost, "--port", $DbPort,
    "--db", $DbName, "--user", $DbUser, "--out", $profilePath) + $pwArgs) | Out-Null

# 7) Verify the app starts ----------------------------------------------------
Log "Verifying the application starts (headless) ..."
$env:QT_QPA_PLATFORM = "offscreen"
& $venvPy (Join-Path $AppDir "installer\scripts\verify_app.py")
if ($LASTEXITCODE -ne 0) { Fail "Application verification failed." }
Remove-Item Env:\QT_QPA_PLATFORM -ErrorAction SilentlyContinue

Log "==============================================================="
Log "SUCCESS: environment ready, database migrated, application verified."
Log "==============================================================="
exit 0
