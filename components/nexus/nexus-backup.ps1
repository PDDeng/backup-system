# Windows PowerShell 2+. Direct-to-NAS backup with bounded final sync and recovery task.
param([switch]$CheckOnly)
$ErrorActionPreference='Stop'
$private=Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $private 'json-compat.ps1')
$c=& (Join-Path $private 'nexus-config.ps1')
Add-Type -AssemblyName System.Security
Add-Type -TypeDefinition @'
using System;using System.Net;using System.Runtime.InteropServices;
public class NexusNative {
 [StructLayout(LayoutKind.Sequential,CharSet=CharSet.Unicode)]
 public struct Resource{public int scope,type,display,usage;public string local,remote,comment,provider;}
 [DllImport("mpr.dll",CharSet=CharSet.Unicode)] static extern int WNetAddConnection2(ref Resource r,string p,string u,int flags);
 public static int Connect(string share,string u,string p){Resource r=new Resource();r.type=1;r.remote=share;return WNetAddConnection2(ref r,p,u,0);}
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] public static extern bool GetDiskFreeSpaceEx(string p,out ulong a,out ulong t,out ulong f);
}
public class NexusWebClient:WebClient{protected override WebRequest GetWebRequest(Uri u){WebRequest r=base.GetWebRequest(u);r.Timeout=10000;return r;}}
'@
# BEGIN_LONGPATH_HELPER
Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Runtime.InteropServices;
public class NexusPathEntry { public string FullName; public long Length; public long WriteTicks; }
public class NexusLongPaths {
 [StructLayout(LayoutKind.Sequential,CharSet=CharSet.Unicode)] struct FindData {
  public uint attr;public System.Runtime.InteropServices.ComTypes.FILETIME creation,access,write;
  public uint high,low,reserved0,reserved1;
  [MarshalAs(UnmanagedType.ByValTStr,SizeConst=260)]public string name;
  [MarshalAs(UnmanagedType.ByValTStr,SizeConst=14)]public string alt;
 }
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]static extern IntPtr FindFirstFileW(string path,out FindData data);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]static extern bool FindNextFileW(IntPtr handle,out FindData data);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool FindClose(IntPtr handle);
 static string Extended(string p){if(p.StartsWith(@"\\?\"))return p;if(p.StartsWith(@"\\"))return @"\\?\UNC\"+p.Substring(2);return @"\\?\"+p;}
 static void Fail(string p){throw new Win32Exception(Marshal.GetLastWin32Error(),"Path operation failed: "+p);}
 public static IEnumerable<NexusPathEntry> Scan(string root){
  Stack<string> stack=new Stack<string>();stack.Push(root.TrimEnd('\\'));
  while(stack.Count>0){
   string dir=stack.Pop();FindData d;IntPtr h=FindFirstFileW(Extended(dir+@"\*"),out d);
   if(h==new IntPtr(-1)){int e=Marshal.GetLastWin32Error();if(e==2)continue;Fail(dir);}
   try{do{
    if(d.name=="."||d.name=="..")continue;
    string path=dir+@"\"+d.name;
    if((d.attr&1024)!=0)throw new InvalidOperationException("Reparse point rejected: "+path);
    if((d.attr&16)!=0)stack.Push(path);
    else{NexusPathEntry entry=new NexusPathEntry();entry.FullName=path;entry.Length=((long)d.high<<32)|d.low;entry.WriteTicks=((long)d.write.dwHighDateTime<<32)|(long)(uint)d.write.dwLowDateTime;yield return entry;}
   }while(FindNextFileW(h,out d));int error=Marshal.GetLastWin32Error();if(error!=18)throw new Win32Exception(error,"Enumeration failed: "+dir);
   }finally{FindClose(h);}
  }
 }
 public static long[] Summary(string root){long count=0,bytes=0;foreach(NexusPathEntry f in Scan(root)){count++;bytes+=f.Length;}return new long[]{count,bytes};}
 public static System.Collections.ArrayList Manifest(string root){System.Collections.ArrayList list=new System.Collections.ArrayList();foreach(NexusPathEntry f in Scan(root)){System.Collections.Hashtable h=new System.Collections.Hashtable();h["Name"]=f.FullName.Substring(root.TrimEnd('\\').Length+1);h["Size"]=f.Length;list.Add(h);}return list;}
 public static long Size(string path){FindData d;IntPtr h=FindFirstFileW(Extended(path),out d);if(h==new IntPtr(-1))Fail(path);try{if((d.attr&(1024|16))!=0)throw new InvalidOperationException("Not a regular file: "+path);return ((long)d.high<<32)|d.low;}finally{FindClose(h);}}
}

'@
# END_LONGPATH_HELPER
. (Join-Path $private 'pax-tar.ps1')
$utf8=New-Object Text.UTF8Encoding($false)
[void][IO.Directory]::CreateDirectory((Join-Path $private 'logs'))
$logfile=Join-Path $private ('logs\'+(Get-Date -Format 'yyyy-MM-dd')+'.log')
function Log([string]$s){$line=(Get-Date -Format s)+' '+$s;[IO.File]::AppendAllText($logfile,$line+"`r`n",$utf8);try{Write-Host $line}catch{}}
function Notice([string]$event,[string]$message){
 if($message -like '*CRITICAL*'){$event='CRITICAL'}
 if(@('SUMMARY','CRITICAL') -notcontains $event){Log ('NOTICE_LOCAL '+$event+' '+$message);return}
 try{
  if(-not [IO.File]::Exists($c.NotifyConfig)){Log ('NOTIFY_NOT_CONFIGURED '+$event);return}
  $n=$json.DeserializeObject([IO.File]::ReadAllText($c.NotifyConfig));if(-not $n.Enabled){return}
  if($n.WebhookUrl -notlike 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=REPLACE_ME'){throw 'Invalid webhook endpoint'}
  [Net.ServicePointManager]::SecurityProtocol=[Enum]::ToObject([Net.SecurityProtocolType],3072)
  $web=New-Object NexusWebClient;$web.Encoding=$utf8;$web.Headers['Content-Type']='application/json'
  try{$reply=$web.UploadString($n.WebhookUrl,$json.Serialize(@{msgtype='text';text=@{content=('CORE BACKUP | '+$event+' | '+$c.Asset+' | '+$message)}}));if($json.DeserializeObject($reply).errcode -ne 0){throw 'Robot rejected'}}finally{$web.Dispose()}
 }catch{Log ('NOTIFY_FAILED '+$event)}
}
function Free([string]$path){[UInt64]$a=0;[UInt64]$t=0;[UInt64]$f=0;if(-not [NexusNative]::GetDiskFreeSpaceEx($path,[ref]$a,[ref]$t,[ref]$f)){throw 'NAS capacity check failed'};return $a}
function Connect([string]$target,[string]$credential){
 $cipher=[Convert]::FromBase64String([IO.File]::ReadAllText((Join-Path $private $credential)))
 $plain=[Security.Cryptography.ProtectedData]::Unprotect($cipher,$null,[Security.Cryptography.DataProtectionScope]::LocalMachine)
 try{$n=$json.DeserializeObject($utf8.GetString($plain));$rc=[NexusNative]::Connect($target,$n.User,$n.Password);if($rc -ne 0){throw ('SMB connect error '+$rc+'; no existing connections removed')}}finally{[Array]::Clear($plain,0,$plain.Length);$n=$null}
}
function Probe([string]$share){
 $base=Join-Path $share $c.Asset;[void][IO.Directory]::CreateDirectory($base)
 $p=Join-Path $base ('.probe-'+[Guid]::NewGuid().ToString('N'));[IO.File]::WriteAllText($p,'nexus-probe')
 if([IO.File]::ReadAllText($p) -ne 'nexus-probe'){throw 'NAS readback mismatch'};[IO.File]::Delete($p)
 Log ('SMB_OK '+$share+' free_GiB='+[Math]::Round((Free $share)/1GB,1));return $base
}
function StoragePath([string]$url,[string]$work){
 $base=[IO.Path]::GetFullPath($work).TrimEnd('\')
 $value=$url.Trim().Replace('${nexus-work}',$base).Replace('${nexus.work}',$base)
 if($value -match '\$\{'){throw 'Unresolved storage variable'}
 if($value -match '^file:'){
  $uri=New-Object Uri($value)
  if(-not $uri.IsFile -or $uri.IsUnc){throw 'Nonlocal storage URI rejected'}
  $value=$uri.LocalPath
 }
 if($value -notmatch '^[A-Za-z]:[\\/]'){throw 'Unknown or relative storage path rejected'}
 $path=[IO.Path]::GetFullPath($value).TrimEnd('\')
 if(-not $path.Equals($base,[StringComparison]::OrdinalIgnoreCase) -and -not $path.StartsWith($base+'\',[StringComparison]::OrdinalIgnoreCase)){
  throw ('External storage not covered by work backup: '+$path)
 }
 return $path
}
function Files([string]$root){
 $count=0;$tick=[Diagnostics.Stopwatch]::StartNew()
 foreach($entry in [NexusLongPaths]::Scan($root)){
  $count++;if($tick.Elapsed.TotalSeconds -ge 30){if(Get-Command Log -ErrorAction SilentlyContinue){Log ('SCAN_PROGRESS root='+$root+' files='+$count)};$tick.Reset();$tick.Start()};$entry
 }
}
function CopyTree([string]$source,[string]$destination,[bool]$mirror,[int]$timeout){
 # Destination is a unique, private partial run or verified secondary run; never a source directory.
 if(-not $destination.StartsWith('\\') -or ($destination -notlike '*.partial\*' -and $destination -notlike '*.partial')){throw 'Copy destination must be inside a unique partial run'}
 if($source -like '*"*' -or $destination -like '*"*'){throw 'Invalid quote in path'}
 $copyLog=Join-Path $private ('logs\robocopy-'+(Get-Date -Format yyyyMMddTHHmmss)+'-'+[guid]::NewGuid().ToString('N').Substring(0,6)+'.log')
 Log ('COPY_START source='+$source+' destination='+$destination+' copy_log='+$copyLog)
 $args='"'+$source+'" "'+$destination+'" '+$(if($mirror){'/MIR'}else{'/E'})+' /XJ /COPY:DAT /DCOPY:T /R:1 /W:2 /NP /NFL /NDL /LOG:"'+$copyLog+'"'
 $info=New-Object Diagnostics.ProcessStartInfo;$info.FileName=(Join-Path $env:SystemRoot 'System32\robocopy.exe');$info.Arguments=$args;$info.UseShellExecute=$false
 $info.CreateNoWindow=$true;$info.RedirectStandardOutput=$true;$info.RedirectStandardError=$true
 $p=New-Object Diagnostics.Process;$p.StartInfo=$info
 try{[void]$p.Start();$p.BeginOutputReadLine();$p.BeginErrorReadLine();if(-not $p.WaitForExit($timeout*1000)){$p.Kill();$p.WaitForExit();throw ('Copy deadline exceeded; partial retained; log='+$copyLog)};$p.WaitForExit();if($p.ExitCode -ge 8){throw ('Robocopy failed rc='+$p.ExitCode+'; log='+$copyLog)};Log ('COPY_COMPLETE rc='+$p.ExitCode+' log='+$copyLog)}finally{$p.Dispose()}
}
function Manifest([string]$root){
 Log ('MANIFEST_START '+$root)
 $list=[NexusLongPaths]::Manifest($root)
 Log ('MANIFEST_COMPLETE '+$root+' files='+$list.Count)
 return ,($list.ToArray())
}
function Verify([string]$root,$list){foreach($f in $list){$p=Join-Path $root $f.Name;if([NexusLongPaths]::Size($p) -ne $f.Size){throw ('Target missing/size mismatch: '+$f.Name)}}}
function Ready(){
 if((Get-Service $c.Service).Status -ne 'Running'){return $false}
 try{$req=[Net.WebRequest]::Create('http://127.0.0.1:8081/nexus/service/local/status');$req.Timeout=5000;$response=$req.GetResponse();try{$reader=New-Object IO.StreamReader($response.GetResponseStream());$text=$reader.ReadToEnd();return $text -match '<state>STARTED</state>'}finally{$response.Close()}}catch{return $false}
}
function Restore(){
 if((Get-Service $c.Service).Status -ne 'Running'){Start-Service $c.Service}
 for($i=0;$i -lt 60;$i++){if(Ready){return};Start-Sleep -Seconds 2}
 throw 'CRITICAL: Nexus did not pass HTTP health check; inspect service immediately'
}
function ArmWatchdog(){
 $scheduler=New-Object -ComObject 'Schedule.Service';$scheduler.Connect();$folder=$scheduler.GetFolder('\');$task=$scheduler.NewTask(0)
 $task.Principal.UserId='S-1-5-18';$task.Principal.LogonType=5;$task.Principal.RunLevel=1
 $trigger=$task.Triggers.Create(1);$trigger.StartBoundary=(Get-Date).AddMinutes($c.RestartWatchdogMinutes).ToString('yyyy-MM-ddTHH:mm:ss')
 $action=$task.Actions.Create(0);$action.Path=Join-Path $env:SystemRoot 'System32\sc.exe';$action.Arguments='start '+$c.Service
 $task.Settings.StartWhenAvailable=$true;$task.Settings.ExecutionTimeLimit='PT2M'
 [void]$folder.RegisterTaskDefinition('CoreBackup-Nexus-Recover',$task,6,'SYSTEM',$null,5,$null)
 return $folder
}
function Clean([string]$base){
 $dates=@();$runs=@()
 foreach($day in Get-ChildItem -LiteralPath $base){
  if($day.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Cleanup day reparse point rejected'}
  if(-not $day.PSIsContainer -or $day.Name -notmatch '^\d{4}-\d{2}-\d{2}$'){continue}
  foreach($folder in Get-ChildItem -LiteralPath $day.FullName){
   if($folder.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Cleanup run reparse point rejected'}
   if(-not $folder.PSIsContainer -or $folder.Name -notmatch '^\d{8}T\d{6}-[a-f0-9]{6}$'){continue}
   $record=Join-Path $folder.FullName 'backup-result.json';$proof=Join-Path $private ('receipts\'+$folder.Name+'.json')
   if(-not [IO.File]::Exists($record) -or -not [IO.File]::Exists($proof)){continue}
   $text=[IO.File]::ReadAllText($record);if($text -cne [IO.File]::ReadAllText($proof)){throw 'Cleanup blocked: ownership receipt mismatch'}
   $r=$json.DeserializeObject($text)
   if($r.Owner -ne 'nexus-direct-v1' -or $r.Asset -ne $c.Asset -or $r.Run -ne $folder.Name -or $r.Date -ne $day.Name){throw 'Cleanup blocked: path/asset mismatch'}
   $dates+=$day.Name;$runs+=@{Date=$day.Name;Path=$folder.FullName;Receipt=$r}
  }
 }
 $keep=@($dates|Sort-Object -Unique|Sort-Object -Descending|Select-Object -First $c.KeepDates)
 if($keep.Count -lt $c.KeepDates){Log ('CLEANUP_SKIP complete_dates='+$keep.Count);return 0}
 $deleted=0
 foreach($runInfo in $runs){
  if($keep -contains $runInfo.Date){continue}
  $folder=$runInfo.Path;$r=$runInfo.Receipt;$allowed=@{}
  $names=Get-NexusReceiptNames $r
  if($null -eq $names){
   Verify (Join-Path $folder 'work') $r.Work;Verify (Join-Path $folder 'application') $r.Application
   $allowed['backup-result.json']=$true
   foreach($f in $r.Work){$allowed['work\'+$f.Name]=$true};foreach($f in $r.Application){$allowed['application\'+$f.Name]=$true}
  }else{
   Test-NexusArchiveSizes $folder $r.Archives
   foreach($n in $names){$allowed[$n]=$true}
  }
  foreach($f in Files $folder){if(-not $allowed.ContainsKey($f.FullName.Substring($folder.Length+1))){throw 'Cleanup blocked: unknown file'}}
  # Remove only receipt-identified files, then empty directories. Never recursive delete.
  Log ('DELETE_INTENT '+$folder)
  foreach($name in $allowed.Keys){[IO.File]::Delete((Join-Path $folder $name))}
  $dirs=@(Get-ChildItem -LiteralPath $folder -Recurse -Force|Where-Object {$_.PSIsContainer}|Sort-Object {$_.FullName.Length} -Descending)
  foreach($dir in $dirs){if($dir.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Cleanup reparse point rejected'};[IO.Directory]::Delete($dir.FullName,$false)}
  [IO.Directory]::Delete($folder,$false);$deleted++;Log ('DELETED '+$folder)
 }
 return $deleted
}
$lock=$null;$stoppedByUs=$false;$watchdog=$null;$timer=[Diagnostics.Stopwatch]::StartNew();$run=$null
try{
 Log ('VERSION=1.7-pax-tar-v2 mode='+$(if($CheckOnly){'check'}else{'backup'}))
 $lock=[IO.File]::Open((Join-Path $private 'backup.lock'),[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
 if(-not (Ready)){throw 'Nexus is not initially healthy; no stop performed'}
 if(-not [IO.Directory]::Exists($c.Home) -or -not [IO.Directory]::Exists($c.Work)){throw 'Nexus source path missing'}
 # Stop on known external storage declarations; never silently omit outside repositories.
 $xml=Join-Path $c.Work 'conf\nexus.xml'
 if(-not [IO.File]::Exists($xml)){throw 'nexus.xml missing; review work path before backup'}
 if([IO.File]::Exists($xml)){
  [xml]$document=[IO.File]::ReadAllText($xml)
  foreach($node in $document.SelectNodes("//*[local-name()='localStorage']/*[local-name()='url']")){
   $url=$node.InnerText.Trim()
   if($url){$storage=StoragePath $url $c.Work;if(-not [IO.Directory]::Exists($storage)){throw ('Configured storage directory missing: '+$storage)};Log ('STORAGE_INCLUDED '+$storage)}
  }
 }
 $bytes=0
 if(-not $CheckOnly){
  Log 'SCAN_START: inventory before preseed; Nexus remains running'
  $workStats=[NexusLongPaths]::Summary($c.Work);Log ('SCAN_WORK_COMPLETE files='+$workStats[0])
  $homeStats=[NexusLongPaths]::Summary($c.Home);$bytes=$workStats[1]+$homeStats[1]
  Log ('SCAN_COMPLETE files='+($workStats[0]+$homeStats[0])+' source_GiB='+[Math]::Round($bytes/1GB,2))
 }
 Log 'CONNECTING nas243'
 Connect $c.Primary 'nas243.dpapi';$base=Probe $c.Primary
 Log 'CONNECTING win254'
 Connect $c.Secondary 'win254.dpapi';$secondaryBase=Probe $c.Secondary
 Log 'CONNECTING nas240'
 Connect $c.Tertiary 'nas240.dpapi';$tertiaryBase=Probe $c.Tertiary
 if((Free $c.Primary) -lt ($bytes*1.15+2GB) -or (Free $c.Secondary) -lt ($bytes*1.15+2GB) -or (Free $c.Tertiary) -lt ($bytes*1.15+2GB)){throw 'Insufficient NAS free capacity'}
 Log ('CHECK_OK source_GiB='+[Math]::Round($bytes/1GB,2))
 if($CheckOnly){Log 'FAST_CHECK_OK: service/config and three NAS write/read/delete passed; deep inventory and full capacity requirement deferred to backup; no service stop';exit 0}
 $run=(Get-Date -Format 'yyyyMMddTHHmmss')+'-'+[Guid]::NewGuid().ToString('N').Substring(0,6)
 $date=Get-Date -Format 'yyyy-MM-dd';$day=Join-Path $base $date;[void][IO.Directory]::CreateDirectory($day)
 $pending=Join-Path $day ('.'+$run+'.partial');[void][IO.Directory]::CreateDirectory($pending)
 Notice 'START' 'Live streaming archive to NAS243; final stopped delta follows; uploads/downloads briefly unavailable'
 # Phase 1 (hot): streamed PAX archives straight to NAS243 .partial. Not a valid backup by itself.
 $preseed=Write-NexusArchive $c.Work (Join-Path $pending 'work-preseed.tar') 'work/' ([NexusPaxArchiver]::ListFiles($c.Work)) ([DateTime]::UtcNow.AddSeconds(7200).Ticks)
 $application=Write-NexusArchive $c.Home (Join-Path $pending 'application.tar') 'application/' ([NexusPaxArchiver]::ListFiles($c.Home)) ([DateTime]::UtcNow.AddSeconds(3600).Ticks)
 foreach($l in @($preseed.Locked)){Log ('HOT_LOCKED_DEFERRED work/'+$l)}
 $watchdog=ArmWatchdog
 $stoppedByUs=$true;$down=[Diagnostics.Stopwatch]::StartNew();Log 'STOPPING nexus-webapp'
 Stop-Service $c.Service
 (Get-Service $c.Service).WaitForStatus('Stopped',(New-Object TimeSpan(0,1,0)))
 try{
  # Phase 2 (stopped): only changed/new/locked work files and deleted paths, bounded by MaxStoppedCopySeconds.
  $stopDeadline=[DateTime]::UtcNow.AddSeconds($c.MaxStoppedCopySeconds).Ticks
  $changed=New-Object Collections.ArrayList;$deleted=New-Object Collections.ArrayList
  [NexusPaxArchiver]::Delta($c.Work,$preseed.Snapshot,$preseed.Locked,$changed,$deleted)
  Log ('DELTA_COMPUTED changed='+$changed.Count+' deleted='+$deleted.Count)
  $delta=Write-NexusArchive $c.Work (Join-Path $pending 'work-delta.tar') 'work/' $changed $stopDeadline
  if($delta.Locked.Count -gt 0){throw ('Files still locked while Nexus is stopped; backup not published: '+$delta.Locked.Count)}
  [IO.File]::WriteAllText((Join-Path $pending 'deleted-paths.json'),$json.Serialize(@($deleted.ToArray())),$utf8)
  if($down.Elapsed.TotalMinutes -ge ($c.RestartWatchdogMinutes-1) -or (Get-Service $c.Service).Status -ne 'Stopped'){throw 'Stopped snapshot window exceeded or watchdog fired; backup not published'}
 }finally{Restore;$down.Stop();$stoppedByUs=$false;Log ('SERVICE_HEALTHY downtime_seconds='+[Math]::Round($down.Elapsed.TotalSeconds,1))}
 $watchdog.DeleteTask('CoreBackup-Nexus-Recover',0);$watchdog=$null
 # The archives are sealed in NAS243 .partial; record size and writer SHA256 per archive for the receipt.
 $archives=@()
 foreach($a in @($preseed,$application,$delta)){$archives+=@{Name=$a.Name;Bytes=$a.Bytes;Sha256=$a.Sha256;Entries=$a.Entries}}
 $deletedFile=Join-Path $pending 'deleted-paths.json'
 $archives+=@{Name='deleted-paths.json';Bytes=([IO.FileInfo]$deletedFile).Length;Sha256=(Get-NexusSha256 $deletedFile);Entries=0}
 $sourceFiles=[long]($preseed.Entries+$application.Entries);$sourceBytes=[long]0
 foreach($h in @($preseed.Snapshot,$application.Snapshot)){foreach($v in $h.Values){$sourceBytes+=[long]$v[0]}}
 $receipt=@{Owner='nexus-direct-v1';Format='pax-tar-v2';Asset=$c.Asset;Run=$run;Date=$date;Verification='size_and_writer_sha256';StoppedSeconds=[Math]::Round($down.Elapsed.TotalSeconds,1);SourceFileCount=$sourceFiles;SourceBytes=$sourceBytes;DeltaFiles=[long]$delta.Entries;DeltaDeleted=$deleted.Count;Archives=$archives}
 [IO.File]::WriteAllText((Join-Path $pending 'backup-result.json'),$json.Serialize($receipt),$utf8)
 $final=Join-Path $day $run;[IO.Directory]::Move($pending,$final)
 [void][IO.Directory]::CreateDirectory((Join-Path $private 'receipts'))
 [IO.File]::Copy((Join-Path $final 'backup-result.json'),(Join-Path $private ('receipts\'+$run+'.json')),$false)
 Notice 'SUCCESS' ('NAS243 complete; service healthy; run='+$run)
 $secondaryDay=Join-Path $secondaryBase $date;[void][IO.Directory]::CreateDirectory($secondaryDay)
 $secondaryPending=Join-Path $secondaryDay ('.'+$run+'.partial');[void][IO.Directory]::CreateDirectory($secondaryPending)
 CopyTree $final $secondaryPending $false 7200
 Test-NexusArchiveSizes $secondaryPending $archives
 [IO.Directory]::Move($secondaryPending,(Join-Path $secondaryDay $run))
 $tertiaryDay=Join-Path $tertiaryBase $date;[void][IO.Directory]::CreateDirectory($tertiaryDay)
 $tertiaryPending=Join-Path $tertiaryDay ('.'+$run+'.partial');[void][IO.Directory]::CreateDirectory($tertiaryPending)
 CopyTree $final $tertiaryPending $false 7200
 Test-NexusArchiveSizes $tertiaryPending $archives
 [IO.Directory]::Move($tertiaryPending,(Join-Path $tertiaryDay $run))
 $clean243=Clean $base;$clean254=Clean $secondaryBase;$clean240=Clean $tertiaryBase
 $result=@{Status='success';Run=$run;Targets=@{nas243=$true;win254=$true;nas240=$true};Verification='size_and_writer_sha256';Seconds=[Math]::Round($timer.Elapsed.TotalSeconds,1);StoppedSeconds=$receipt.StoppedSeconds}
 [IO.File]::WriteAllText((Join-Path $private 'last-result.json'),$json.Serialize($result),$utf8)
 Log ('SUCCESS '+$run);Notice 'SUMMARY' ("【Nexus备份｜全部成功】`n资产："+$c.Asset+"`n目标：243成功 / 254成功 / 240成功`n校验：仅存在/大小，未做MD5`n清理运行目录：243="+$clean243+' / 254='+$clean254+' / 240='+$clean240+"`n保留："+$c.KeepDates+"个成功日期`n服务：已恢复，HTTP健康检查通过`n批次："+$run+"`n总耗时："+[int]$timer.Elapsed.TotalSeconds+'秒；停机窗口：'+$receipt.StoppedSeconds+'秒')
}catch{
 Log ('FAILURE '+$_.Exception.Message);$backupFailure=$_.Exception.Message
 Log ('ERROR_LOCATION '+$_.InvocationInfo.PositionMessage)
 Log ('ERROR_TARGET '+[string]$_.TargetObject)
 Log ('ERROR_STACK '+[string]$_.ScriptStackTrace)
 if(-not $CheckOnly){[IO.File]::WriteAllText((Join-Path $private 'last-result.json'),$json.Serialize(@{Status='failed';Run=$run;Error=$_.Exception.Message;Seconds=[Math]::Round($timer.Elapsed.TotalSeconds,1)}),$utf8)}
 exit 1
}finally{
 if($stoppedByUs){try{Restore;Log 'SERVICE_RECOVERED_AFTER_FAILURE'}catch{Log ('CRITICAL '+$_.Exception.Message);Notice 'FAILURE' 'CRITICAL: Nexus service recovery failed; use console immediately'}}
 if($backupFailure){Notice 'SUMMARY' ("【Nexus备份｜失败】`n原因："+$backupFailure+"`n服务状态："+[string](Get-Service $c.Service).Status+"`n各端结果需查本次日志，不能按上次成功判断。`n日志：C:\ProgramData\CoreNexusBackup\logs")}
 if($lock){$lock.Dispose()}
}
