$ErrorActionPreference='Stop'
$root=Split-Path -Parent $MyInvocation.MyCommand.Path
if($root -ne 'C:\core-crm-direct'){throw 'Extract package into C:\core-crm-direct first'}
$powershell=Join-Path $PSHOME 'powershell.exe'
& $powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'backup.ps1') -SetupCredentials
if($LASTEXITCODE -ne 0){throw 'Credential setup failed'}
New-Item -ItemType Directory -Force -Path (Join-Path $root 'logs') | Out-Null
$cmd='@echo off'+"`r`n"+'"'+$powershell+'" -NoProfile -ExecutionPolicy Bypass -File "'+$root+'\backup.ps1" %* >> "'+$root+'\logs\task-output.log" 2>&1'+"`r`n"+'exit /b %errorlevel%'+"`r`n"
[IO.File]::WriteAllText((Join-Path $root 'launch.cmd'),$cmd,[Text.Encoding]::ASCII)
$schtasks=Join-Path $env:SystemRoot 'System32\schtasks.exe'
$check='cmd.exe /d /c C:\core-crm-direct\launch.cmd -CheckOnly'
$daily='cmd.exe /d /c C:\core-crm-direct\launch.cmd'
# Refuse overwriting existing schedules; user can inspect previous tasks safely.
& "$env:SystemRoot\System32\cmd.exe" /d /c "schtasks.exe /Query /TN CoreBackup-CRM200 >nul 2>&1"
if($LASTEXITCODE -eq 0){throw 'CoreBackup-CRM200 already exists; existing schedule preserved'}
& $schtasks /Create /TN CoreBackup-CRM200 /SC DAILY /ST 03:00 /RU SYSTEM /RL HIGHEST /TR $daily
if($LASTEXITCODE -ne 0){throw 'Daily task creation failed'}
& $schtasks /Change /TN CoreBackup-CRM200 /DISABLE
if($LASTEXITCODE -ne 0){throw 'Could not disable task; inspect schedule immediately'}
& $schtasks /Create /TN CoreBackup-CRM200-Check /SC ONCE /ST 23:59 /RU SYSTEM /RL HIGHEST /TR $check
if($LASTEXITCODE -ne 0){throw 'Check task creation failed; inspect existing check task'}
& $schtasks /Run /TN CoreBackup-CRM200-Check
if($LASTEXITCODE -ne 0){throw 'Check task launch failed'}
Write-Host 'Installed: daily03:00 DISABLED. SYSTEM SQL/NAS probe started; check logs\task-output.log. No CRM backup started.'
