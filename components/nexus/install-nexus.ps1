# Install as Administrator on SERVER-51. No Nexus stop and no backup start.
param([switch]$EnableSchedule)
$ErrorActionPreference='Stop'
$running=@(Get-WmiObject Win32_Process -Filter "Name='powershell.exe'" | Where-Object {$_.CommandLine -match 'nexus-backup\.ps1'})
if($running.Count){throw 'Nexus backup/check is running; existing processes preserved. Retry installer after completion.'}
$origin=Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $origin 'json-compat.ps1')
Add-Type -AssemblyName System.Security
$c=& (Join-Path $origin 'nexus-config.ps1')
if(-not (Test-Path -LiteralPath $c.Work) -or -not (Get-Service $c.Service -ErrorAction SilentlyContinue)){throw 'Wrong server/path'}
$private=Join-Path $env:ProgramData 'CoreNexusBackup'
[void][IO.Directory]::CreateDirectory($private)
if((Get-Item -LiteralPath $private).Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Private directory cannot be junction'}
$acl=New-Object Security.AccessControl.DirectorySecurity;$acl.SetAccessRuleProtection($true,$false)
foreach($sid in @('S-1-5-18','S-1-5-32-544')){
 $identity=New-Object Security.Principal.SecurityIdentifier($sid)
 $rule=New-Object Security.AccessControl.FileSystemAccessRule($identity,'FullControl','ContainerInherit,ObjectInherit','None','Allow');[void]$acl.AddAccessRule($rule)
}
Set-Acl -Path $private -AclObject $acl
$stamp=Get-Date -Format 'yyyyMMdd-HHmmss'
foreach($name in @('nexus-backup.ps1','nexus-config.ps1','json-compat.ps1','pax-tar.ps1')){
 $dest=Join-Path $private $name
 if(Test-Path -LiteralPath $dest){Copy-Item -LiteralPath $dest -Destination ($dest+'.before-'+$stamp)}
 Copy-Item -LiteralPath (Join-Path $origin $name) -Destination $dest -Force
}
$utf8=New-Object Text.UTF8Encoding($false)
[void][IO.Directory]::CreateDirectory((Join-Path $private 'logs'))
# Capture errors that occur before nexus-backup.ps1 can open its own log.
$launcher=Join-Path $private 'launch-nexus.cmd'
$launchText="@echo off`r`n"+'"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0nexus-backup.ps1" %* >> "%~dp0task-output.log" 2>&1'+"`r`nexit /b %errorlevel%`r`n"
[IO.File]::WriteAllText($launcher,$launchText,[Text.Encoding]::ASCII)
foreach($target in @(@{Label='NAS243';File='nas243.dpapi'},@{Label='WIN254';File='win254.dpapi'},@{Label='NAS240';File='nas240.dpapi'})){
 Write-Host ('Enter '+$target.Label+' SMB credentials (not Windows login password)')
 $cred=Get-Credential -Credential 'databak';if(-not $cred){throw 'Credential entry cancelled'}
 $network=$cred.GetNetworkCredential();$plain=$utf8.GetBytes($json.Serialize(@{User=$cred.UserName;Password=$network.Password}))
 try{$cipher=[Security.Cryptography.ProtectedData]::Protect($plain,$null,[Security.Cryptography.DataProtectionScope]::LocalMachine);[IO.File]::WriteAllText((Join-Path $private $target.File),[Convert]::ToBase64String($cipher),$utf8)}finally{[Array]::Clear($plain,0,$plain.Length);$network=$null;$cred=$null}
}
$scheduler=New-Object -ComObject 'Schedule.Service';$scheduler.Connect();$folder=$scheduler.GetFolder('\');$task=$scheduler.NewTask(0)
$task.Principal.UserId='S-1-5-18';$task.Principal.LogonType=5;$task.Principal.RunLevel=1
$task.Settings.Enabled=[bool]$EnableSchedule;$task.Settings.MultipleInstances=2
$task.Settings.StartWhenAvailable=$false;$task.Settings.ExecutionTimeLimit='PT6H'
$trigger=$task.Triggers.Create(2);$trigger.StartBoundary=(Get-Date).Date.AddDays(1).AddHours(3).ToString('yyyy-MM-ddTHH:mm:ss');$trigger.DaysInterval=1
$action=$task.Actions.Create(0);$action.Path=Join-Path $env:SystemRoot 'System32\cmd.exe'
$action.Arguments='/d /c ""'+$launcher+'""';$action.WorkingDirectory=$private
try{$old=$folder.GetTask('CoreBackup-Nexus51');[IO.File]::WriteAllText((Join-Path $private ('task-before-'+$stamp+'.xml')),$old.Xml,$utf8)}catch{}
[void]$folder.RegisterTaskDefinition('CoreBackup-Nexus51',$task,6,'SYSTEM',$null,5,$null)
# Separate one-off SYSTEM preflight verifies the actual scheduled identity without stopping Nexus.
$check=$scheduler.NewTask(0);$check.Principal.UserId='S-1-5-18';$check.Principal.LogonType=5;$check.Principal.RunLevel=1
$check.Settings.ExecutionTimeLimit='PT10M';$check.Settings.MultipleInstances=2
$a=$check.Actions.Create(0);$a.Path=$action.Path;$a.Arguments='/d /c ""'+$launcher+'" -CheckOnly"';$a.WorkingDirectory=$private
$registered=$folder.RegisterTaskDefinition('CoreBackup-Nexus51-Check',$check,6,'SYSTEM',$null,5,$null);[void]$registered.Run($null)
Write-Host ('Installed. Daily03:00 enabled='+[bool]$EnableSchedule+'. SYSTEM preflight started; no service stop.')
Write-Host ('Logs: '+$private+'\logs ; inspect scheduled check Last Run Result and CHECK_OK before enabling daily task.')
