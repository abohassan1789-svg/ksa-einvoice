<#
    build_installer.ps1 - compile the KSA e-Invoice Windows installer.

    Compiles installer\KSA-eInvoice.iss with Inno Setup 6 (ISCC.exe) and writes
    the single-file installer to  ..\dist\KSA-eInvoice-Setup.exe .

    Usage:
        powershell -ExecutionPolicy Bypass -File installer\build_installer.ps1
        powershell -ExecutionPolicy Bypass -File installer\build_installer.ps1 -Version 1.0.1

    Requires Inno Setup 6 (https://jrsoftware.org/isdl.php). If ISCC.exe is not on
    PATH, the script looks in the default install locations.
#>
[CmdletBinding()]
param(
    [string]$Version = "",
    [string]$Iscc = ""
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Definition
$iss = Join-Path $here "KSA-eInvoice.iss"
$distDir = Join-Path (Split-Path -Parent $here) "dist"

if (-not (Test-Path $iss)) { throw "Cannot find $iss" }

function Find-Iscc {
    param([string]$Explicit)
    if ($Explicit -and (Test-Path $Explicit)) { return $Explicit }
    $cmd = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $candidates = @(
        (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
        (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe")
    )
    foreach ($c in $candidates) { if ($c -and (Test-Path $c)) { return $c } }
    return $null
}

$isccPath = Find-Iscc -Explicit $Iscc
if (-not $isccPath) {
    throw "ISCC.exe (Inno Setup 6) not found. Install it from https://jrsoftware.org/isdl.php or pass -Iscc <path>."
}

Write-Host "Inno Setup compiler: $isccPath"
Write-Host "Script:              $iss"
Write-Host "Output directory:    $distDir"

New-Item -ItemType Directory -Force -Path $distDir | Out-Null

$args = @("`"$iss`"")
if ($Version) { $args += "/DAppVersion=$Version" }

& $isccPath @args
if ($LASTEXITCODE -ne 0) { throw "ISCC failed with exit code $LASTEXITCODE." }

$setup = Join-Path $distDir "KSA-eInvoice-Setup.exe"
if (Test-Path $setup) {
    $sizeMb = [math]::Round((Get-Item $setup).Length / 1MB, 2)
    Write-Host ""
    Write-Host "BUILD OK -> $setup ($sizeMb MB)" -ForegroundColor Green
} else {
    throw "ISCC reported success but $setup was not produced."
}
