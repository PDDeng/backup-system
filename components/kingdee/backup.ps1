param([switch]$SetupCredentials, [switch]$CheckOnly)
$ErrorActionPreference='Stop'
$root=Split-Path -Parent $MyInvocation.MyCommand.Path
$c=& (Join-Path $root 'config.ps1')
. (Join-Path $root 'json-compat.ps1')
$credPath=Join-Path $root 'nas-credential.xml'
if($SetupCredentials){
 $credential=Get-Credential
 if(!$credential){throw 'No credentials entered'}
 $saved=New-Object PSObject -Property @{UserName=$credential.UserName;EncryptedPassword=($credential.Password | ConvertFrom-SecureString)}
 $saved | Export-Clixml -Path $credPath
 Write-Host 'Encrypted credentials saved for this Windows user.'
 exit 0
}
Add-Type @'
using System;using System.Runtime.InteropServices;
public class KingdeeNas {
 [StructLayout(LayoutKind.Sequential,CharSet=CharSet.Unicode)] public struct R {public int a,b,c,d;public string local,remote,comment,provider;}
 [DllImport("mpr.dll",CharSet=CharSet.Unicode)] static extern int WNetAddConnection2(ref R r,string password,string user,int flags);
 public static int Connect(string path,string user,string password){R r=new R();r.b=1;r.remote=path;return WNetAddConnection2(ref r,password,user,0);}
}
'@
$logs=Join-Path $root 'logs'; New-Item -ItemType Directory -Force -Path $logs | Out-Null
try{$lock=[IO.File]::Open((Join-Path $root 'backup.lock'),'OpenOrCreate','ReadWrite','None')}catch{Write-Host ('Cannot acquire backup lock (another process or permissions): '+$_.Exception.Message);exit 2}
$start=Get-Date;$log=Join-Path $logs ((Get-Date -Format yyyyMMddTHHmmss)+'.log')
function Log($s){$line=(Get-Date -Format s)+' '+$s;Write-Host $line;Add-Content -LiteralPath $log -Value $line -Encoding UTF8}
function Snapshot {
 $list=@(Get-ChildItem -LiteralPath $c.Source -Force | Where-Object {!$_.PSIsContainer -and $_.Name -match '_([0-9]{8})\.ADF$'})
 $found=@()
 foreach($f in $list){$null=$f.Name -match '_([0-9]{8})\.ADF$';$d=[datetime]::ParseExact($Matches[1],'yyyyMMdd',[Globalization.CultureInfo]::InvariantCulture);$found+=@{File=$f;Day=$d.ToString('yyyy-MM-dd')}}
 return $found
}
function ValidReceipt($dir){
 if($dir.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Target reparse point rejected'}
 $p=Join-Path $dir.FullName 'complete.json';if(!(Test-Path -LiteralPath $p -PathType Leaf)){return $null}
 $r=$json.DeserializeObject([IO.File]::ReadAllText($p,[Text.Encoding]::UTF8))
 if($r.owner -ne 'kingdee-nas-v1' -or $r.asset -ne $c.Asset -or $r.date -ne $dir.Name){throw ('Unrecognized receipt '+$dir.FullName)}
 if(@($r.files).Count -eq 0){throw 'Empty receipt'}
 foreach($f in $r.files){if([IO.Path]::GetFileName($f.name) -ne $f.name -or $f.name -notmatch '_[0-9]{8}\.ADF$'){throw 'Invalid receipt filename'};$item=Get-Item -LiteralPath (Join-Path $dir.FullName $f.name);if($item.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Linked file rejected'};if($item.Length -ne $f.bytes){throw ('Backup size mismatch '+$item.FullName)}}
 $actual=@(Get-ChildItem -LiteralPath $dir.FullName -Force)
 if($actual.Count -ne (@($r.files).Count+1)){throw ('Extra files block cleanup '+$dir.FullName)}
 return $r
}
$message='';$exitCode=1
try{
 if([int]$c.KeepCycles -lt 1){throw 'KeepCycles must be >= 1'}
 if($c.Asset -notmatch '^[\p{L}\p{N}_.-]+$' -or $c.Asset -match '^\.+$|\.$'){throw 'Invalid asset name'}
 $all=@(Snapshot);if(!$all.Count){throw 'No dated ADF backup files found'}
 $day=$all | ForEach-Object { [string]$_.Day } | Sort-Object -Descending | Select-Object -First 1
 if([datetime]$day -gt (Get-Date).Date){throw 'Future backup date rejected'}
 $selected=@($all | Where-Object {$_.Day -eq $day})
 foreach($x in $selected){if($x.File.Length -le 0 -or $x.File.LastWriteTime -gt (Get-Date).AddMinutes(-[int]$c.MinimumAgeMinutes)){throw 'Latest dated backup is empty or too recent; retry after backup finishes'};if($x.File.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Source linked file rejected'}}
 Log ('Selected '+$day+' files='+$selected.Count+'; stability check')
 Start-Sleep -Seconds ([int]$c.StableSeconds)
 $again=@(Snapshot | Where-Object {$_.Day -eq $day})
 if($again.Count -ne $selected.Count){throw 'Source file set changed'}
 foreach($x in $selected){$now=Get-Item -LiteralPath $x.File.FullName;if($now.Length -ne $x.File.Length -or $now.LastWriteTimeUtc -ne $x.File.LastWriteTimeUtc){throw 'Source changed during stability check'}}
 $saved=Import-Clixml -Path $credPath
 if(!$saved.EncryptedPassword){throw 'Old credential format: rerun -SetupCredentials with this version'}
 $secure=ConvertTo-SecureString $saved.EncryptedPassword
 $cred=New-Object System.Management.Automation.PSCredential -ArgumentList $saved.UserName,$secure
 $n=$cred.GetNetworkCredential();$rc=[KingdeeNas]::Connect($c.Share,$cred.UserName,$n.Password)
 if($rc -ne 0){throw ('SMB connection error '+$rc+'; no sessions disconnected')}
 $robocopy=Join-Path $env:SystemRoot 'System32\robocopy.exe'
 if(!(Test-Path -LiteralPath $robocopy)){throw ('Robocopy not found: '+$robocopy)}
 $base=Join-Path $c.Share $c.Asset;New-Item -ItemType Directory -Path $base -Force | Out-Null
 if($CheckOnly){
  $probe=Join-Path $base ('.probe-'+[guid]::NewGuid().ToString('N'));[IO.File]::WriteAllText($probe,'kingdee-probe');if([IO.File]::ReadAllText($probe) -ne 'kingdee-probe'){throw 'NAS probe mismatch'};Remove-Item -LiteralPath $probe;Log ('CHECK_OK selected_date='+$day+' files='+$selected.Count+'; no upload or cleanup');exit 0
 }
 $final=Join-Path $base $day;$action='已上传'
 $manifest=@($selected | ForEach-Object {@{name=$_.File.Name;bytes=$_.File.Length;mtime=$_.File.LastWriteTimeUtc.Ticks.ToString()}})
 if(Test-Path -LiteralPath $final){
  $r=ValidReceipt (Get-Item -LiteralPath $final);if(!$r){throw 'Existing date directory has no managed receipt; preserved'}
  if(@($r.files).Count -ne $manifest.Count){throw 'Same date source set differs; existing backup preserved'}
  foreach($f in $manifest){$match=@($r.files | Where-Object {$_.name -eq $f.name -and $_.bytes -eq $f.bytes -and $_.mtime -eq $f.mtime});if($match.Count -ne 1){throw 'Same date source differs; existing backup preserved'}}
  $action='已存在，大小复核通过'
 }else{
  $partial=Join-Path $base ('.'+$day+'-'+[guid]::NewGuid().ToString('N')+'.partial');New-Item -ItemType Directory -Path $partial | Out-Null
  # Keep all source files open without write sharing until publication; never lock a live database directory.
  $handles=@()
  try{
   foreach($x in $selected){$handles += [IO.File]::Open($x.File.FullName,'Open','Read','Read')}
   foreach($x in $selected){Log ('COPY '+$x.File.Name);& $robocopy $c.Source $partial $x.File.Name /COPY:DAT /R:1 /W:2 /NP /NFL /NDL ('/LOG+:'+$log);if($LASTEXITCODE -ge 8){throw ('Robocopy failed '+$LASTEXITCODE)};$f=Get-Item -LiteralPath (Join-Path $partial $x.File.Name);if($f.Length -ne $x.File.Length){throw 'Uploaded size mismatch'}}
   $fresh=@(Snapshot | Where-Object {$_.Day -eq $day});if($fresh.Count -ne $selected.Count){throw 'Source set changed during copy'}
   foreach($x in $selected){$f=Get-Item -LiteralPath $x.File.FullName;if($f.Length -ne $x.File.Length -or $f.LastWriteTimeUtc -ne $x.File.LastWriteTimeUtc){throw 'Source changed during copy'}}
   $receipt=@{owner='kingdee-nas-v1';asset=$c.Asset;date=$day;verification='size_only';finished=(Get-Date -Format o);files=$manifest}
   [IO.File]::WriteAllText((Join-Path $partial 'complete.json'),$json.Serialize($receipt),[Text.Encoding]::UTF8)
   Rename-Item -LiteralPath $partial -NewName $day
  }finally{foreach($h in $handles){$h.Dispose()}}
 }
 $valid=@();foreach($d in @(Get-ChildItem -LiteralPath $base -Force | Where-Object {$_.PSIsContainer -and $_.Name -match '^\d{4}-\d{2}-\d{2}$'})){$r=ValidReceipt $d;if($r){$valid+=@{Dir=$d;Receipt=$r}}}
 $ordered=@($valid | Sort-Object {$_.Dir.Name} -Descending);$deleted=0
 foreach($old in @($ordered | Select-Object -Skip ([int]$c.KeepCycles))){
  $null=ValidReceipt $old.Dir
  $audit=@{time=(Get-Date -Format o);action='delete_cycle';path=$old.Dir.FullName;receipt=$old.Receipt}
  Add-Content -LiteralPath (Join-Path $logs 'cleanup-audit.jsonl') -Value $json.Serialize($audit) -Encoding UTF8
  foreach($f in $old.Receipt.files){Remove-Item -LiteralPath (Join-Path $old.Dir.FullName $f.name) -Force}
  Remove-Item -LiteralPath (Join-Path $old.Dir.FullName 'complete.json') -Force
  [IO.Directory]::Delete($old.Dir.FullName);$deleted++
 }
 $stale=if($day -ne (Get-Date -Format yyyy-MM-dd)){'【注意：最新文件日期不是今天，请检查金蝶备份计划】'}else{''}
 $message="金蝶备份 | 成功 $stale`n备份日期：$day`n结果：$action；文件数：$($selected.Count)`n目标：$final`n保留：$($ordered.Count-$deleted) 个完整日期；本轮删除：$deleted`n校验：文件大小，未做 MD5`n耗时：$([math]::Round(((Get-Date)-$start).TotalMinutes,1)) 分钟"
 $exitCode=0
}catch{$message='金蝶备份 | 失败（可能已上传，但后续检查/清理失败，请查日志）'+"`n"+$_.Exception.Message+"`n日志："+$log}
finally{if($lock){$lock.Dispose()}}
Log $message
if($c.Webhook){try{
 try{[Net.ServicePointManager]::SecurityProtocol=[Enum]::ToObject([Net.SecurityProtocolType],3072)}catch{}
 $body=$json.Serialize(@{msgtype='text';text=@{content=$message}})
 $bytes=[Text.Encoding]::UTF8.GetBytes($body)
 $req=[Net.HttpWebRequest]::Create($c.Webhook);$req.Method='POST';$req.ContentType='application/json; charset=utf-8';$req.Timeout=15000;$req.ReadWriteTimeout=15000;$req.ContentLength=$bytes.Length
 $stream=$req.GetRequestStream();try{$stream.Write($bytes,0,$bytes.Length)}finally{$stream.Close()}
 $response=$req.GetResponse();try{$reader=New-Object IO.StreamReader($response.GetResponseStream());try{$answer=$json.DeserializeObject($reader.ReadToEnd())}finally{$reader.Close()}}finally{$response.Close()}
 if($answer.errcode -ne 0){throw 'Webhook returned error'}
}catch{Log ('NOTIFY_FAILED '+$_.Exception.Message)}}
exit $exitCode
