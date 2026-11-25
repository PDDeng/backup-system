# PowerShell2+. Configure a scoped SYSTEM SMB connection and prove Oracle can write NAS.
# Does NOT start a full export, change OracleService account, restart services, or alter DATA/DATA_DIR.
param([Parameter(Mandatory=$true)][ValidateSet('41','46')][string]$Server)
$ErrorActionPreference='Stop'
$sid='ORCL';$oracleHome='D:\app\administrator\product\11.2.0\dbhome_1'
$asset='oracle01_10.10.10.46_dev研发环境数据库'
if($Server -eq '41'){$sid='YUANQI';$oracleHome='D:\app\administrator\product\11.2.0\dbhome_2';$asset='oracle02_10.10.10.41_uat测试环境数据库'}
$share='\\10.10.11.243\资源备份'
$assetRoot=Join-Path $share $asset
$private=Join-Path $env:ProgramData ('CoreOracleNas'+$Server)
$env:ORACLE_HOME=$oracleHome;$env:ORACLE_SID=$sid
$service=Get-WmiObject Win32_Service -Filter ("Name='OracleService"+$sid+"'")
if(-not $service -or $service.State -ne 'Running' -or $service.StartName -ne 'LocalSystem'){throw 'Expected running LocalSystem Oracle service not found; no identity change attempted'}
if(-not (Test-Path -LiteralPath (Join-Path $oracleHome 'bin\sqlplus.exe'))){throw 'Wrong server / OracleHome'}
. (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) 'json-compat.ps1')
Add-Type -AssemblyName System.Security

$encoding=New-Object Text.UTF8Encoding($false)
function Protect([string]$path,[bool]$directory){
    if($directory){$acl=New-Object Security.AccessControl.DirectorySecurity}else{$acl=New-Object Security.AccessControl.FileSecurity}
    $acl.SetAccessRuleProtection($true,$false)
    foreach($id in @('S-1-5-18','S-1-5-32-544')){
        $identity=New-Object Security.Principal.SecurityIdentifier($id)
        if($directory){$rule=New-Object Security.AccessControl.FileSystemAccessRule($identity,'FullControl','ContainerInherit,ObjectInherit','None','Allow')}
        else{$rule=New-Object Security.AccessControl.FileSystemAccessRule($identity,'FullControl','Allow')}
        [void]$acl.AddAccessRule($rule)
    }
    # All callers use fixed private paths with no wildcard characters.
    Set-Acl -Path $path -AclObject $acl
}
function Sql([string]$text){
    $info=New-Object Diagnostics.ProcessStartInfo
    $info.FileName=Join-Path $oracleHome 'bin\sqlplus.exe';$info.Arguments='-L -S "/ as sysdba"'
    $info.UseShellExecute=$false;$info.CreateNoWindow=$true
    $info.RedirectStandardInput=$true;$info.RedirectStandardOutput=$true;$info.RedirectStandardError=$true
    $p=New-Object Diagnostics.Process;$p.StartInfo=$info
    try{
        [void]$p.Start()
        $p.StandardInput.WriteLine("whenever sqlerror exit failure`nwhenever oserror exit failure`nset pagesize 0 heading off feedback off linesize 1000`n"+$text+"`nexit")
        $p.StandardInput.Close()
        if(-not $p.WaitForExit(30000)){$p.Kill();throw 'Oracle probe client timed out; no database service stopped'}
        $out=$p.StandardOutput.ReadToEnd();$err=$p.StandardError.ReadToEnd()
        if($p.ExitCode -ne 0){throw ('Oracle NAS probe failed: '+$out+' '+$err)}
        return $out.Trim()
    }finally{$p.Dispose()}
}
if(-not (Test-Path -LiteralPath $private)){[void](New-Item -ItemType Directory -Path $private)}
if((Get-Item -LiteralPath $private).Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Private directory cannot be junction/link'}
Protect $private $true
$helper=Join-Path $private 'connect-system.ps1'
$origin=Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) 'connect-system.ps1'
Copy-Item -LiteralPath $origin -Destination $helper -Force
$compat=Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) 'json-compat.ps1'
Copy-Item -LiteralPath $compat -Destination (Join-Path $private 'json-compat.ps1') -Force
Protect (Join-Path $private 'json-compat.ps1') $false
Protect $helper $false
$cred=Get-Credential -Credential 'databak'
if(-not $cred){throw 'NAS credentials cancelled'}
$network=$cred.GetNetworkCredential()
$user=$cred.UserName
$plain=$encoding.GetBytes($json.Serialize(@{Share=$share;AssetRoot=$assetRoot;User=$user;Password=$network.Password}))
$cipher=[Security.Cryptography.ProtectedData]::Protect($plain,$null,[Security.Cryptography.DataProtectionScope]::LocalMachine)
$config=Join-Path $private 'nas-connection.dpapi'
if(Test-Path -LiteralPath $config){Copy-Item -LiteralPath $config -Destination ($config+'.before-'+(Get-Date -Format 'yyyyMMddHHmmss'))}
[IO.File]::WriteAllText($config,[Convert]::ToBase64String($cipher),$encoding)
Protect $config $false
[Array]::Clear($plain,0,$plain.Length)
$network=$null;$cred=$null
$taskName='CoreOracleNas'+$Server+'-243'
$scheduler=New-Object -ComObject 'Schedule.Service';$scheduler.Connect()
$folder=$scheduler.GetFolder('\')
$definition=$scheduler.NewTask(0)
$definition.RegistrationInfo.Description='Connect LocalSystem to NAS243 for Oracle backup; no Oracle service changes'
$definition.Principal.UserId='S-1-5-18';$definition.Principal.LogonType=5;$definition.Principal.RunLevel=1
$definition.Settings.Enabled=$true;$definition.Settings.StartWhenAvailable=$true
$definition.Settings.ExecutionTimeLimit='PT2M';$definition.Settings.MultipleInstances=2
$trigger=$definition.Triggers.Create(8);$trigger.Delay='PT1M'
$action=$definition.Actions.Create(0)
$action.Path=Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$action.Arguments='-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "'+$helper+'" -Config "'+$config+'"'
$action.WorkingDirectory=$private
# Preserve existing task XML for rollback; update only our dedicated task.
try{$old=$folder.GetTask($taskName);[IO.File]::WriteAllText((Join-Path $private ('task-before-'+(Get-Date -Format 'yyyyMMddHHmmss')+'.xml')),$old.Xml,$encoding)}catch{}
$result=$config+'.result.json'
if(Test-Path -LiteralPath $result){Remove-Item -LiteralPath $result}
$task=$folder.RegisterTaskDefinition($taskName,$definition,6,'SYSTEM',$null,5)
[void]$task.Run($null)
for($i=0;$i -lt 45;$i++){if(Test-Path -LiteralPath $result){break};Start-Sleep -Seconds 2}
if(-not (Test-Path -LiteralPath $result)){throw 'SYSTEM NAS helper did not return; inspect dedicated scheduled task, do not restart Oracle'}
$r=$json.DeserializeObject([IO.File]::ReadAllText($result))
if($r.Status -ne 'ok'){throw $r.Error}
Write-Output ('SYSTEM_SMB_OK '+$r.Identity+' '+$assetRoot)
# Make the same connection available to this administrator for folder/metadata operations.
# Credential is read only from the protected file, never placed in command-line arguments.
& $helper -Config $config
$adminResult=$json.DeserializeObject([IO.File]::ReadAllText($result))
if($adminResult.Status -ne 'ok'){throw 'Administrator NAS session failed'}
$run=(Get-Date -Format 'yyyyMMdd_HHmmss')+'-'+[Guid]::NewGuid().ToString('N').Substring(0,4)
$day=Join-Path $assetRoot (Get-Date -Format 'yyyy-MM-dd')
$pending=Join-Path $day ('.'+$run+'.partial')
[void][IO.Directory]::CreateDirectory($pending)
$directory=('CORE'+$Server+'_'+$run.Replace('-','_')).ToUpperInvariant()
$encoded=New-Object Text.StringBuilder
foreach($ch in $pending.ToCharArray()){[void]$encoded.Append(('\{0:X4}' -f [int]$ch))}
# New, uniquely named DIRECTORY only; original DATA/DATA_DIR unchanged.
$create=@"
declare
  v_path varchar2(4000);
  v_sql varchar2(32767);
begin
  v_path := unistr('$($encoded.ToString())');
  v_sql := 'CREATE DIRECTORY $directory AS ' || chr(39) || replace(v_path,chr(39),chr(39)||chr(39)) || chr(39);
  execute immediate v_sql;
end;
/
"@
[void](Sql $create)
$pathCheck=Sql ("select case when count(*)=0 then 'DIRECTORY_NOT_FOUND' when sum(case when directory_path=unistr('"+$encoded.ToString()+"') then 1 else 0 end)=1 then 'DIRECTORY_PATH_OK' else 'DIRECTORY_PATH_MISMATCH' end from dba_directories where directory_name='"+$directory+"';")
if($pathCheck -notmatch 'DIRECTORY_PATH_OK'){throw ('Directory readback failed: '+$pathCheck+'; object='+$directory+'; no export started')}
$probe='oracle_probe_'+[Guid]::NewGuid().ToString('N')+'.tmp'
$test="declare f utl_file.file_type; begin f:=utl_file.fopen('$directory','$probe','w'); utl_file.put_line(f,'ORACLE_NAS_WRITE_OK'); utl_file.fclose(f); utl_file.fremove('$directory','$probe'); end;`n/"
[void](Sql $test)
Write-Output ('ORACLE_SERVICE_NAS_WRITE_OK directory='+$directory)
$local=Join-Path 'C:\core-backup' ('nas'+$Server+'-'+$run)
[void][IO.Directory]::CreateDirectory($local)
Protect $local $true
$par=Join-Path $local 'export.par'
@('USERID="/ as sysdba"','FULL=YES',('DIRECTORY='+$directory),'DUMPFILE=full_%U.dmp','LOGFILE=export.log',('JOB_NAME=EXP'+$Server+'_'+$run.Replace('-','_')),'FILESIZE=10G','PARALLEL=1','FLASHBACK_TIME=SYSTIMESTAMP') | Set-Content -LiteralPath $par -Encoding ASCII
$meta=@{Server=$Server;Sid=$sid;OracleHome=$oracleHome;DirectoryObject=$directory;Pending=$pending;Run=$run;ParameterFile=$par;Task=$taskName;Status='prepared_only_not_exported'}
[IO.File]::WriteAllText((Join-Path $local 'prepared.json'),$json.Serialize($meta),$encoding)
Write-Output ('PREPARED_ONLY '+$local)
Write-Output ('NAS_TEMPORARY_DIRECTORY='+$pending)
Write-Output 'No full export started. Use prepared.json/export.par after checking Oracle component health.'
