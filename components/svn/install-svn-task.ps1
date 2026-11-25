# Explicit local installer. Never run by tests except -PlanOnly.
param([string]$TargetRoot='\\192.168.5.240\资源备份',[string]$RepositoryRoot='D:\Repositories',[string]$SvnBin='C:\Program Files\VisualSVN Server\bin',[string]$PrivateRoot=(Join-Path $env:ProgramData 'CoreSvnBackup'),[int]$KeepCycles=15,[switch]$EnableSchedule,[switch]$PlanOnly,[switch]$SkipCleanup)
$ErrorActionPreference='Stop'
$origin=Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $origin 'json-compat.ps1')
. (Join-Path $origin 'svn-core.ps1')
foreach($path in @($TargetRoot,$RepositoryRoot,$SvnBin,$PrivateRoot)){if($path -match '["\r\n%]'){throw 'Unsupported installer path characters'}}
if($TargetRoot -notmatch '^\\\\[^\\/]+\\[^\\/]+\\?$' -or $KeepCycles -lt 1){throw 'Invalid share or retention'}
$plan=@{TaskName='CoreBackup-SVN29';User='SYSTEM';LogonType=5;RunLevel=1;DailyTime='01:00';Enabled=[bool]$EnableSchedule;MultipleInstances=2;PrivateRoot=$PrivateRoot;TargetRoot=$TargetRoot;KeepCycles=$KeepCycles;SkipCleanup=[bool]$SkipCleanup;StartsBackup=$false}
if($PlanOnly){[Console]::WriteLine($json.Serialize($plan));exit 0}
foreach($name in @('svnadmin.exe','svnlook.exe')){if(-not [IO.File]::Exists((Join-Path $SvnBin $name))){throw 'Wrong server: SVN executable missing'}}
$null=Assert-SvnPlainPath $RepositoryRoot;$null=Assert-SvnPlainPath $PrivateRoot
[void][IO.Directory]::CreateDirectory($PrivateRoot)
$lock=$null
try{
 $lock=[IO.File]::Open((Join-Path $PrivateRoot 'svn-backup.lock'),'OpenOrCreate','ReadWrite','None')
 $acl=New-Object Security.AccessControl.DirectorySecurity;$acl.SetAccessRuleProtection($true,$false)
 foreach($sid in @('S-1-5-18','S-1-5-32-544')){$identity=New-Object Security.Principal.SecurityIdentifier($sid);$rule=New-Object Security.AccessControl.FileSystemAccessRule($identity,'FullControl','ContainerInherit,ObjectInherit','None','Allow');[void]$acl.AddAccessRule($rule)}
 Set-Acl -Path $PrivateRoot -AclObject $acl
 $stamp=(Get-Date -Format yyyyMMddTHHmmss)+'-'+[guid]::NewGuid().ToString('N').Substring(0,6)
 foreach($name in @('svn-backup.ps1','svn-core.ps1','json-compat.ps1')){
  $source=Join-Path $origin $name;$dest=Join-Path $PrivateRoot $name;$null=Assert-SvnPlainPath $dest
  if([IO.File]::Exists($dest)){[IO.File]::Copy($dest,($dest+'.before-'+$stamp),$false)}
  if([IO.Path]::GetFullPath($source) -ne [IO.Path]::GetFullPath($dest)){[IO.File]::Copy($source,$dest,$true)}
 }
 # Already holding the shared lock; credential creation performs no NAS operation.
 $credential=Get-Credential;Save-SvnCredential (Join-Path $PrivateRoot 'nas240.dpapi') $credential
 $invoke='& '+"'"+(Join-Path $PrivateRoot 'svn-backup.ps1').Replace("'","''")+"'"
 foreach($pair in @(@('TargetRoot',$TargetRoot),@('RepositoryRoot',$RepositoryRoot),@('SvnBin',$SvnBin),@('PrivateRoot',$PrivateRoot))){$invoke+=' -'+$pair[0]+" '"+$pair[1].Replace("'","''")+"'"}
 $invoke+=' -KeepCycles '+$KeepCycles+$(if($SkipCleanup){' -SkipCleanup'}else{''})
 # EncodedCommand keeps Chinese share/repository names independent of cmd codepage.
 $encoded=[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($invoke))
 $checkEncoded=[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($invoke+' -CheckOnly'))
 $command='"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand '+$encoded+' >> "%~dp0task-output.log" 2>&1'
 $checkCommand='"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand '+$checkEncoded+' >> "%~dp0task-output.log" 2>&1'
 $launcher=Join-Path $PrivateRoot 'launch-svn.cmd';$null=Assert-SvnPlainPath $launcher
 [IO.File]::WriteAllText($launcher,"@echo off`r`nif /I "+'"%~1"=="-CheckOnly" goto check'+"`r`n"+$command+"`r`nexit /b %errorlevel%`r`n:check`r`n"+$checkCommand+"`r`nexit /b %errorlevel%`r`n",[Text.Encoding]::ASCII)
 $scheduler=New-Object -ComObject Schedule.Service;$scheduler.Connect();$folder=$scheduler.GetFolder('\');$task=$scheduler.NewTask(0)
 $task.Principal.UserId='S-1-5-18';$task.Principal.LogonType=5;$task.Principal.RunLevel=1
 $task.Settings.Enabled=[bool]$EnableSchedule;$task.Settings.MultipleInstances=2;$task.Settings.StartWhenAvailable=$false;$task.Settings.ExecutionTimeLimit='PT24H'
 $trigger=$task.Triggers.Create(2);$trigger.StartBoundary=(Get-Date).Date.AddDays(1).AddHours(1).ToString('yyyy-MM-ddTHH:mm:ss');$trigger.DaysInterval=1
 $action=$task.Actions.Create(0);$action.Path=Join-Path $env:SystemRoot 'System32\cmd.exe';$action.Arguments='/d /c ""'+$launcher+'""';$action.WorkingDirectory=$PrivateRoot
 $old=$null;try{$old=$folder.GetTask('CoreBackup-SVN29')}catch{}
 if($old){if($old.State -eq 4){throw 'Existing task is running; registration refused'};[IO.File]::WriteAllText((Join-Path $PrivateRoot ('task-before-'+$stamp+'.xml')),$old.Xml,(New-Object Text.UTF8Encoding($false)))}
 [void]$folder.RegisterTaskDefinition('CoreBackup-SVN29',$task,6,'SYSTEM',$null,5,$null)
 Write-SvnLog $PrivateRoot ('INSTALLED daily01:00 enabled='+[bool]$EnableSchedule+'; no check or backup started')
}finally{if($lock){$lock.Close()}}
