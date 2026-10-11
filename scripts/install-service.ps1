<#
.SYNOPSIS
  Installs (or updates) the app as a Windows service: starts at boot, runs without a window,
  restarts by itself if it crashes, writes a rotated log to logs\app.log.

.DESCRIPTION
  Run it from an elevated PowerShell (Run as administrator), from anywhere:

      powershell -ExecutionPolicy Bypass -File .\scripts\install-service.ps1

  Needs: Python 3.11 or newer and nssm.exe
  (https://nssm.cc/download, copy win64\nssm.exe into the project folder). Running it again updates
  the service in place, so it is also what you run after changing a parameter.

  A service has no desktop, so the "Alege fisier DBF" dialog in Setari cannot open there. Pass the file
  with -DbfPath instead (or edit settings.json: {"dbf_files": ["C:\\date\\G1.DBF"]}).

  To remove the service:  nssm.exe remove ShopManagement confirm

.EXAMPLE
  .\scripts\install-service.ps1 -DbfPath "C:\date\G1.DBF"

.EXAMPLE
  .\scripts\install-service.ps1 -DryRun        # shows every step, changes nothing
#>
[CmdletBinding()]
param(
    [string]$ServiceName = "ShopManagement",
    [string]$HostAddress = "0.0.0.0",   # 0.0.0.0 = reachable from the network, 127.0.0.1 = this machine only
    [int]$Port = 18766,
    [string]$PythonExe,                 # python.org interpreter used to create the venv (default: python on PATH)
    [string]$NssmPath,                  # default: nssm.exe in the project folder, next to this script, or on PATH
    [string]$DbfPath,                   # DBF file to add to settings.json
    [switch]$DryRun                    # print what would be done, change nothing
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot          # the project folder (this script lives in scripts\)
$venvPy = Join-Path $root "venv\Scripts\python.exe"
$logDir = Join-Path $root "logs"
$logFile = Join-Path $logDir "app.log"
$settingsFile = Join-Path $root "settings.json"

function Step([string]$text) { Write-Host "==> $text" -ForegroundColor Cyan }
function Warn([string]$text) { Write-Host "    ATENTIE: $text" -ForegroundColor Yellow }
function Fail([string]$text) { Write-Host "EROARE: $text" -ForegroundColor Red; exit 1 }

# Runs an external command; with -DryRun it only prints it.
function Run([string]$exe, [string[]]$arguments) {
    $shown = "$exe " + (($arguments | ForEach-Object { if ($_ -match '\s') { '"' + $_ + '"' } else { $_ } }) -join " ")
    if ($DryRun) { Write-Host "    [dry-run] $shown" -ForegroundColor DarkGray; return }
    & $exe @arguments
    if ($LASTEXITCODE -ne 0) { Fail "Comanda a esuat (cod $LASTEXITCODE): $shown" }
}

# Adds a DBF file to settings.json, keeping every other setting. Written as UTF-8 without BOM (Python's json can't read a BOM).
function Add-DbfPath([string]$settingsFile, [string]$dbf) {
    $settings = $null
    if (Test-Path $settingsFile) {
        $raw = Get-Content $settingsFile -Raw -Encoding UTF8
        if ($raw -and $raw.Trim()) { $settings = $raw | ConvertFrom-Json }
    }
    if (-not $settings) { $settings = [pscustomobject]@{} }
    $hasList = $settings.PSObject.Properties.Name -contains "dbf_files"
    $files = @()
    if ($hasList) { $files = @($settings.dbf_files) }
    if ($files -notcontains $dbf) { $files += $dbf }
    if ($hasList) { $settings.dbf_files = $files } else { $settings | Add-Member -NotePropertyName dbf_files -NotePropertyValue $files }
    [System.IO.File]::WriteAllText($settingsFile, ($settings | ConvertTo-Json -Depth 5), (New-Object System.Text.UTF8Encoding($false)))
}

# After a failed start: show the end of the log and, for the usual culprit, a hint. Exits the script.
# A service runs as SYSTEM, which usually cannot start the Microsoft Store Python: the venv's python.exe is only a
# launcher for the interpreter named in venv\pyvenv.cfg, and the log then says "Unable to create process".
function Show-StartFailure([string]$why) {
    Write-Host "EROARE: $why" -ForegroundColor Red
    if (Test-Path $logFile) {
        Write-Host "Ultimele linii din ${logFile}:"
        Get-Content $logFile -Tail 4 | ForEach-Object { Write-Host "    $_" }
    }
    $cfg = Join-Path $root "venv\pyvenv.cfg"
    if ((Test-Path $cfg) -and ((Get-Content $cfg -Raw) -match '\\WindowsApps\\')) {
        Write-Host ""
        Write-Host "Mediul venv foloseste Python din Microsoft Store, iar contul SYSTEM (sub care ruleaza serviciul) de obicei nu-l poate porni." -ForegroundColor Yellow
        Write-Host "Solutie: instaleaza Python de pe python.org pentru toti utilizatorii, sterge folderul venv si ruleaza scriptul cu" -ForegroundColor Yellow
        Write-Host "         -PythonExe ""C:\Program Files\Python311\python.exe""" -ForegroundColor Yellow
    }
    exit 1
}

if ($DryRun) { Write-Host "DRY RUN: nu se modifica nimic, doar se afiseaza pasii." -ForegroundColor Yellow }

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin -and -not $DryRun) { Fail "Ruleaza PowerShell ca Administrator (clic dreapta pe PowerShell, apoi Run as administrator)." }

Write-Host "Proiect: $root"
Write-Host "Serviciu: $ServiceName, asculta pe ${HostAddress}:$Port"

# --- NSSM ---
Step "Caut nssm.exe"
$nssm = $NssmPath
if (-not $nssm) {
    foreach ($candidate in @((Join-Path $root "nssm.exe"), (Join-Path $PSScriptRoot "nssm.exe"))) {
        if (Test-Path $candidate) { $nssm = $candidate; break }
    }
}
if (-not $nssm) {
    $found = Get-Command nssm.exe -ErrorAction SilentlyContinue
    if ($found) { $nssm = $found.Source }
}
if (-not $nssm -or -not (Test-Path $nssm)) {
    $msg = "Nu gasesc nssm.exe. Descarca-l de la https://nssm.cc/download, pune win64\nssm.exe in $root sau da -NssmPath."
    if ($DryRun) { Warn $msg; $nssm = "nssm.exe" } else { Fail $msg }
}
Write-Host "    $nssm"

# --- Python environment ---
Step "Pregatesc mediul Python (venv)"
if (-not (Test-Path $venvPy)) {
    $base = $PythonExe
    if (-not $base) {
        $found = Get-Command python.exe -ErrorAction SilentlyContinue
        if ($found) { $base = $found.Source }
    }
    if (-not $base) { Fail "Nu gasesc Python. Instaleaza-l sau da -PythonExe." }
    Run $base @("-m", "venv", (Join-Path $root "venv"))
}
Run $venvPy @("-m", "pip", "install", "--quiet", "-r", (Join-Path $root "requirements.txt"))

# --- DBF file ---
if ($DbfPath) {
    Step "Adaug fisierul DBF in settings.json"
    if (-not (Test-Path $DbfPath)) { Warn "fisierul nu exista (inca) la $DbfPath" }
    if ($DryRun) { Write-Host "    [dry-run] settings.json: dbf_files += $DbfPath" -ForegroundColor DarkGray }
    else { Add-DbfPath $settingsFile $DbfPath }
}

# --- Service ---
Step "Configurez serviciul $ServiceName"
if (-not $DryRun -and -not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($existing -and $existing.Status -ne "Stopped") { Run $nssm @("stop", $ServiceName) }
if (-not $existing) {
    Run $nssm @("install", $ServiceName, $venvPy, "app.py")
} else {
    Write-Host "    Serviciul exista deja: ii actualizez setarile."
    Run $nssm @("set", $ServiceName, "Application", $venvPy)
    Run $nssm @("set", $ServiceName, "AppParameters", "app.py")
}
Run $nssm @("set", $ServiceName, "AppDirectory", $root)          # products.db is created relative to this folder
Run $nssm @("set", $ServiceName, "DisplayName", "Shop Management")
Run $nssm @("set", $ServiceName, "Description", "Server Configurare Magazin: produse, game, ecrane")
Run $nssm @("set", $ServiceName, "Start", "SERVICE_AUTO_START")
Run $nssm @("set", $ServiceName, "AppStdout", $logFile)
Run $nssm @("set", $ServiceName, "AppStderr", $logFile)
Run $nssm @("set", $ServiceName, "AppRotateFiles", "1")
Run $nssm @("set", $ServiceName, "AppRotateOnline", "1")
Run $nssm @("set", $ServiceName, "AppRotateBytes", "1048576")
Run $nssm @("set", $ServiceName, "AppEnvironmentExtra", "APP_HOST=$HostAddress", "APP_PORT=$Port")

# --- Start ---
Step "Pornesc serviciul"
if ($DryRun) {
    Run $nssm @("start", $ServiceName)
} else {
    & $nssm start $ServiceName
    if ($LASTEXITCODE -ne 0) { Show-StartFailure "Serviciul nu a pornit (nssm start, cod $LASTEXITCODE)." }
    $probeHost = if ($HostAddress -eq "0.0.0.0") { "127.0.0.1" } else { $HostAddress }
    $url = "http://${probeHost}:$Port/api/sync/status"
    $ok = $false
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 1
        try { Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 2 | Out-Null; $ok = $true; break } catch { }
    }
    if (-not $ok) { Show-StartFailure "Serviciul nu raspunde pe $url dupa 30 de secunde." }
}

Write-Host ""
$shownHost = if ($HostAddress -eq "0.0.0.0") { "127.0.0.1" } else { $HostAddress }
Write-Host "Gata. Aplicatia: http://${shownHost}:$Port" -ForegroundColor Green
if ($HostAddress -eq "0.0.0.0") {
    Write-Host "De pe alt calculator: http://<IP-ul-acestui-calculator>:$Port"
    Write-Host "Firewall-ul nu a fost modificat: daca alte calculatoare nu se pot conecta, permite tu portul $Port."
}
Write-Host "Stare:    Get-Service $ServiceName"
Write-Host "Repornire (dupa git pull):  nssm.exe restart $ServiceName"
Write-Host "Log:      $logFile"
if (-not $DbfPath) { Warn "dialogul 'Alege fisier DBF' nu merge intr-un serviciu: pune calea in settings.json sau ruleaza scriptul cu -DbfPath." }
