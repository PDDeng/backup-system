param([switch]$SetupCredentials,[switch]$CheckOnly)
$ErrorActionPreference='Stop'
$root=Split-Path -Parent $MyInvocation.MyCommand.Path
$c=& (Join-Path $root 'config.ps1')
. (Join-Path $root 'json-compat.ps1')
Add-Type -AssemblyName System.Security
$key=Join-Path $root 'nas-credential.xml'
if($SetupCredentials){
 $credential=Get-Credential;if(!$credential){throw 'No credentials entered'}
 $n=$credential.GetNetworkCredential()
 $clear=[Text.Encoding]::UTF8.GetBytes($n.Password)
 $protected=[Security.Cryptography.ProtectedData]::Protect($clear,$null,[Security.Cryptography.DataProtectionScope]::LocalMachine)
 $saved=New-Object PSObject -Property @{UserName=$credential.UserName;Secret=[Convert]::ToBase64String($protected)}
 $saved | Export-Clixml -Path $key
 # The directory must be private because LocalMachine DPAPI alone is not a user ACL.
 $acl=New-Object Security.AccessControl.DirectorySecurity
 $acl.SetAccessRuleProtection($true,$false)
 foreach($sidText in @('S-1-5-18','S-1-5-32-544')){
  $sid=New-Object Security.Principal.SecurityIdentifier($sidText)
  $rule=New-Object Security.AccessControl.FileSystemAccessRule($sid,'FullControl','ContainerInherit,ObjectInherit','None','Allow')
  $acl.AddAccessRule($rule)
 }
 Set-Acl -Path $root -AclObject $acl
 & "$env:SystemRoot\System32\icacls.exe" $key /inheritance:r /grant:r '*S-1-5-18:F' '*S-1-5-32-544:F' | Out-Null
 if($LASTEXITCODE -ne 0){throw 'Credential ACL failed'}
 Write-Host 'Machine encrypted credential saved; directory restricted to SYSTEM and Administrators.';exit 0
}
if([Security.Principal.WindowsIdentity]::GetCurrent().User.Value -ne 'S-1-5-18'){throw 'Run through the supplied SYSTEM scheduled task; do not run backup interactively.'}
Add-Type @'
using System;using System.Runtime.InteropServices;
public class CrmDirectNas {
 [StructLayout(LayoutKind.Sequential,CharSet=CharSet.Unicode)] public struct R{public int a,b,c,d;public string local,remote,comment,provider;}
 [DllImport("mpr.dll",CharSet=CharSet.Unicode)] static extern int WNetAddConnection2(ref R r,string p,string u,int f);
 public static int Connect(string path,string user,string password){R r=new R();r.b=1;r.remote=path;return WNetAddConnection2(ref r,password,user,0);}
}
'@
$logs=Join-Path $root 'logs';New-Item -ItemType Directory -Force -Path $logs | Out-Null
try{$lock=[IO.File]::Open((Join-Path $root 'backup.lock'),'OpenOrCreate','ReadWrite','None')}catch{throw ('Cannot acquire lock: '+$_.Exception.Message)}
$run=Get-Date -Format yyyyMMddTHHmmss;$day=Get-Date -Format yyyy-MM-dd;$start=Get-Date
$log=Join-Path $logs ($run+'.log');$exitCode=1;$summary='';$partial=$null
function Log($s){$line=(Get-Date -Format s)+' '+$s;Write-Host $line;Add-Content -LiteralPath $log -Value $line -Encoding UTF8}
function Sql($query,$name){
 $out=Join-Path $logs ($run+'-'+$name+'.log')
 $err=Join-Path $logs ($run+'-'+$name+'.stderr.log')
 $inputFile=Join-Path $logs ($run+'-'+$name+'.sql')
 [IO.File]::WriteAllText($inputFile,$query,[Text.Encoding]::Unicode)
 # SQLCMD emits progress on stderr too. Bypass PowerShell's native stderr error handling.
 $arguments='-S "'+$c.Instance+'" -E -b -l 15 -t 0 -i "'+$inputFile+'" -o "'+$out+'"'
 $info=New-Object System.Diagnostics.ProcessStartInfo
 $info.FileName=$c.Sqlcmd;$info.Arguments=$arguments
 $info.UseShellExecute=$false;$info.CreateNoWindow=$true;$info.RedirectStandardError=$true
 # SQLCMD writes normal SQL output directly to -o; only stderr is piped, avoiding two-pipe deadlocks.
 $process=New-Object System.Diagnostics.Process
 $process.StartInfo=$info
 try{
  if(!$process.Start()){throw 'SQLCMD failed to start'}
  $errors=$process.StandardError.ReadToEnd()
  $process.WaitForExit()
  [IO.File]::WriteAllText($err,$errors,[Text.Encoding]::UTF8)
  $code=$process.ExitCode
 }finally{$process.Dispose()}
 if($code -ne 0){throw ('SQL '+$name+' failed rc='+$code+'; inspect '+$out+' and '+$err)}
}
function Literal($s){return "N'"+$s.Replace("'","''")+"'"}
function Receipt($dir){
 if($dir.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Linked target directory rejected'}
 $p=Join-Path $dir.FullName 'complete.json';if(!(Test-Path -LiteralPath $p)){return $null}
 $r=$json.DeserializeObject([IO.File]::ReadAllText($p,[Text.Encoding]::UTF8))
 if($r.owner -ne 'crm-direct-v1' -or $r.asset -ne $c.Asset -or $r.date -ne $dir.Name -or $r.database -ne $c.Database){throw 'Unrecognized receipt'}
 if($r.file -notmatch '^YingnaCRM2006_[0-9]{8}T[0-9]{6}\.bak$'){throw 'Invalid backup filename'}
 $f=Get-Item -LiteralPath (Join-Path $dir.FullName $r.file)
 if($f.Length -ne $r.bytes -or ($f.Attributes -band [IO.FileAttributes]::ReparsePoint)){throw 'Backup size or path mismatch'}
 if(@(Get-ChildItem -LiteralPath $dir.FullName -Force).Count -ne 2){throw 'Extra files block cleanup'}
 return $r
}
try{
 if([int]$c.KeepCycles -lt 1 -or $c.Database -ne 'YingnaCRM2006'){throw 'Invalid CRM database/retention configuration'}
 if($c.Share -ne '\\192.168.5.240\资源备份' -or $c.Asset -ne 'crm01_192.168.0.200_crm'){throw 'Unexpected target; review script before changing target'}
 if(!(Test-Path -LiteralPath $c.Sqlcmd)){throw 'SQLCMD executable not found'}
 $svc=Get-WmiObject Win32_Service -Filter "Name='MSSQL`$SQLSERVER2008R2'"
 if($svc.State -ne 'Running' -or $svc.StartName -ne 'LocalSystem'){throw 'Expected running SQL Server LocalSystem service not found; no service changes made'}
 $saved=Import-Clixml -Path $key
 $clear=[Security.Cryptography.ProtectedData]::Unprotect([Convert]::FromBase64String($saved.Secret),$null,[Security.Cryptography.DataProtectionScope]::LocalMachine)
 $rc=[CrmDirectNas]::Connect($c.Share,$saved.UserName,[Text.Encoding]::UTF8.GetString($clear));if($rc -ne 0){throw ('SYSTEM SMB connection failed '+$rc+'; existing sessions preserved')}
 $base=Join-Path $c.Share $c.Asset;New-Item -ItemType Directory -Force -Path $base | Out-Null
 Sql "SET NOCOUNT ON; IF NOT EXISTS(SELECT 1 FROM sys.databases WHERE name=N'YingnaCRM2006' AND state=0) RAISERROR('CRM database not ONLINE',16,1); SELECT @@VERSION;" 'database-check'
 if($CheckOnly){
  # A small master copy-only backup proves SQL service UNC write access, not just SYSTEM task access.
  $probe=Join-Path $base ('.sql-service-probe-'+$run+'.bak')
  Sql ("BACKUP DATABASE [master] TO DISK="+(Literal $probe)+" WITH COPY_ONLY,CHECKSUM; RESTORE VERIFYONLY FROM DISK="+(Literal $probe)+" WITH CHECKSUM;") 'sql-service-nas-probe'
  Remove-Item -LiteralPath $probe
  Log 'CHECK_OK: SQL service NAS backup/write and VERIFYONLY passed; CRM not backed up; no retention cleanup.'
  $exitCode=0
 }else{
  $final=Join-Path $base $day
  if(Test-Path -LiteralPath $final){$r=Receipt (Get-Item -LiteralPath $final);if(!$r){throw 'Existing date directory is unmanaged; preserved'};Log ('Already completed '+$final);$exitCode=0}
  else{
   $partial=Join-Path $base ('.'+$day+'-'+$run+'.partial');New-Item -ItemType Directory -Path $partial | Out-Null
   $filename='YingnaCRM2006_'+$run+'.bak';$bak=Join-Path $partial $filename
   Log ('START direct SQL backup '+$bak)
   Sql ("BACKUP DATABASE [YingnaCRM2006] TO DISK="+(Literal $bak)+" WITH COPY_ONLY,CHECKSUM,STATS=10; RESTORE VERIFYONLY FROM DISK="+(Literal $bak)+" WITH CHECKSUM;") 'backup-and-verify'
   $f=Get-Item -LiteralPath $bak;if($f.Length -le 0){throw 'Empty backup'}
   $r=@{owner='crm-direct-v1';asset=$c.Asset;database=$c.Database;date=$day;file=$filename;bytes=$f.Length;verification='SQL CHECKSUM and RESTORE VERIFYONLY';finished=(Get-Date -Format o)}
   [IO.File]::WriteAllText((Join-Path $partial 'complete.json'),$json.Serialize($r),[Text.Encoding]::UTF8)
   Rename-Item -LiteralPath $partial -NewName $day;$exitCode=0
  }
  $valid=@();foreach($d in @(Get-ChildItem -LiteralPath $base -Force | Where-Object {$_.PSIsContainer -and $_.Name -match '^\d{4}-\d{2}-\d{2}$'})){$r=Receipt $d;if($r){$valid+=@{Dir=$d;R=$r}}}
  $ordered=@($valid | Sort-Object {$_.Dir.Name} -Descending);$deleted=0
  foreach($old in @($ordered | Select-Object -Skip ([int]$c.KeepCycles))){
   $null=Receipt $old.Dir
   Add-Content -LiteralPath (Join-Path $logs 'cleanup-audit.jsonl') -Value ($json.Serialize(@{time=(Get-Date -Format o);path=$old.Dir.FullName;receipt=$old.R})) -Encoding UTF8
   Remove-Item -LiteralPath (Join-Path $old.Dir.FullName $old.R.file) -Force
   Remove-Item -LiteralPath (Join-Path $old.Dir.FullName 'complete.json') -Force
   [IO.Directory]::Delete($old.Dir.FullName);$deleted++
  }
  $summary="CRM数据库备份 | 成功`n数据库：YingnaCRM2006`n目标：$final`n校验：SQL CHECKSUM + VERIFYONLY；未做恢复演练/MD5`n保留：$($ordered.Count-$deleted) 个完整日期；删除：$deleted`n耗时：$([math]::Round(((Get-Date)-$start).TotalMinutes,1))分钟"
 }
}catch{$exitCode=1;$summary='CRM数据库备份 | 失败'+"`n"+$_.Exception.Message+"`n临时目录保留；请查日志："+$log}
finally{if($lock){$lock.Dispose()}}
if($summary){Log $summary}
if($c.Webhook -and $summary){try{
 [Net.ServicePointManager]::SecurityProtocol=[Enum]::ToObject([Net.SecurityProtocolType],3072)
 $bytes=[Text.Encoding]::UTF8.GetBytes($json.Serialize(@{msgtype='text';text=@{content=$summary}}))
 $req=[Net.HttpWebRequest]::Create($c.Webhook);$req.Method='POST';$req.ContentType='application/json; charset=utf-8';$req.Timeout=15000;$req.ReadWriteTimeout=15000;$req.ContentLength=$bytes.Length
 $s=$req.GetRequestStream();try{$s.Write($bytes,0,$bytes.Length)}finally{$s.Close()}
 $response=$req.GetResponse();try{$reader=New-Object IO.StreamReader($response.GetResponseStream());try{$answer=$json.DeserializeObject($reader.ReadToEnd())}finally{$reader.Close()}}finally{$response.Close()}
 if($answer.errcode -ne 0){throw 'Webhook returned error'}
}catch{Log ('NOTIFY_FAILED '+$_.Exception.Message)}}
exit $exitCode
