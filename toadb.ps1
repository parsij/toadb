# toadb.ps1 — Windows installer (run elevated; it self-elevates if needed)
$ErrorActionPreference = "Stop"

function Require-Admin {
  $id=[Security.Principal.WindowsIdentity]::GetCurrent()
  $p = New-Object Security.Principal.WindowsPrincipal($id)
  if (-not $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Start-Process powershell -Verb runAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    exit
  }
}
Require-Admin

function Have($n){ $null -ne (Get-Command $n -ErrorAction SilentlyContinue) }
$RAW_BASE = "https://github.com/parsij/toadb/blob/main/main.py"
$Base = "C:\ProgramData\PhoneTimeSync"
$DstPy = Join-Path $Base "main.py"
$Task = "PhoneTimeSyncDaemon"

Write-Host "[*] Ensuring winget..."
if (-not (Have winget)) { throw "winget not found. Install 'App Installer' from Microsoft Store." }

Write-Host "[*] Ensuring ADB..."
if (-not (Have adb)) {
  winget install -e --id AndroidPlatformTools | Out-Null
  if (-not (Have adb)) { throw "ADB install failed via winget." }
}

Write-Host "[*] Ensuring Python..."
$py = (Have py) ? "py -3" : ((Have python) ? "python" : "")
if ($py -eq "") {
  winget install -e --id Python.Python.3 | Out-Null
  $py = (Have py) ? "py -3" : ((Have python) ? "python" : "")
  if ($py -eq "") { throw "Python install failed via winget." }
}

Write-Host "[*] Preparing $Base ..."
New-Item -Force -ItemType Directory $Base | Out-Null

# main.py: prefer local beside installer, else download
$LocalMain = Join-Path (Split-Path -Parent $PSCommandPath) "main.py"
if (Test-Path $LocalMain) { Copy-Item $LocalMain $DstPy -Force }
else { Invoke-WebRequest -UseBasicParsing -Uri "$RAW_BASE/main.py" -OutFile $DstPy }

# Put a PATH shim in System32 so 'toadb' works anywhere
$Shim = "C:\Windows\System32\toadb.cmd"
$ShimContent = @'
@echo off
setlocal
set SCRIPT=C:\ProgramData\PhoneTimeSync\main.py
where py >NUL 2>&1 && (py -3 "%SCRIPT%" %* & exit /b)
where python >NUL 2>&1 && (python "%SCRIPT%" %* & exit /b)
echo Python not found in PATH.
exit /b 1
'@
Set-Content -NoNewline -Encoding ASCII $Shim $ShimContent

# Runner for scheduled task: wait ~7s then run daemon mode
$Runner = Join-Path $Base "run_daemon.cmd"
$RunnerContent = @'
@echo off
setlocal
ping -n 8 127.0.0.1 >NUL
where py >NUL 2>&1 && (py -3 "C:\ProgramData\PhoneTimeSync\main.py" & exit /b)
where python >NUL 2>&1 && (python "C:\ProgramData\PhoneTimeSync\main.py" & exit /b)
exit /b 1
'@
Set-Content -NoNewline -Encoding ASCII $Runner $RunnerContent

Write-Host "[*] Creating scheduled task..."
schtasks /Delete /TN $Task /F 2>$null | Out-Null
schtasks /Create /TN $Task /SC ONLOGON /RL HIGHEST /TR "`"$Runner`"" /F | Out-Null

Write-Host "[✓] Installed."
Write-Host "   • CLI: toadb | toadb resync | toadb list | toadb device N | toadb reset"
Write-Host "   • It runs at logon in daemon mode; open a terminal and type 'toadb list' to see devices."
