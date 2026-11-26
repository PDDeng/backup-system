# Windows PowerShell 2 / .NET 2 compatible. Only the isolated test VM.
param([string]$PlanDirectory,[switch]$Execute,[switch]$PreviewOnly)
Set-StrictMode -Version 2
$ErrorActionPreference='Stop'
$toolDirectory=Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $toolDirectory '..\json-compat.ps1')
if(-not ('OracleRestoreNative' -as [type])){Add-Type -Path (Join-Path $toolDirectory 'native-process.cs')}
$json=New-Object CoreNasJson
$utf8=New-Object Text.UTF8Encoding($false)
function ReadJson([string]$p){return $json.DeserializeObject([IO.File]::ReadAllText($p))}
function Hash([string]$p){
 $s=[IO.File]::Open($p,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
 $h=[Security.Cryptography.SHA256]::Create()
 try{return ([BitConverter]::ToString($h.ComputeHash($s))).Replace('-','').ToLowerInvariant()}
 finally{$s.Close();$h.Clear()}
}
function Log([string]$s){[IO.File]::AppendAllText($script:log,([DateTime]::UtcNow.ToString('o')+' '+$s+"`r`n"),$utf8)}
function Native([string]$exe,[string[]]$arguments,[string]$name){
 $path=Join-Path $script:root $name
 $rc=[OracleRestoreNative]::Run($exe,$arguments,$path)
 if($rc -ne 0){throw ('Native failed '+$name+' rc='+$rc)}
 $text=[IO.File]::ReadAllText($path)
 if($text -match '(ORA-|RMAN-0[0-9]{4}:|SP2-|LRM-|UDI-)'){throw ('Native errors in '+$name)}
 return $text
}
function Phase([string]$name,[string]$file){$script:phases[$name]=@{status='passed';log=$file}}
if(-not $Execute){throw 'PREPARED_ONLY: -Execute required on isolated VM after DBA review'}
if(-not $PlanDirectory){throw 'PlanDirectory required'}
$PlanDirectory=[IO.Path]::GetFullPath($PlanDirectory)
$planFile=Join-Path $PlanDirectory 'plan.json';$p=ReadJson $planFile;$c=$p['config'];$m=$p['manifest']
if($p['production_change'] -ne $false -or $c['target_ip'] -ne '10.10.10.251'){throw 'Wrong isolation contract'}
if($c['id'] -notmatch '^oracle(41|46)$' -or $c['sid'] -ne ('RT'+$c['id'].Substring(6))){throw 'Wrong test SID'}
if($env:COMPUTERNAME.ToUpperInvariant() -ne $c['target_hostname'].ToUpperInvariant()){throw 'Wrong test hostname'}
$ips=@();Get-WmiObject Win32_NetworkAdapterConfiguration -Filter 'IPEnabled=True' | ForEach-Object {$ips+=@($_.IPAddress)}
if($ips -notcontains '10.10.10.251' -or $ips -contains '10.10.10.46' -or $ips -contains '10.10.10.41'){throw 'Wrong local machine IP'}
$root=[IO.Path]::GetFullPath($c['root'])
if($root -notmatch '^D:\\oracle-restore\\[A-Za-z0-9_.\\-]+$' -or $root.Contains('..')){throw 'Unsafe isolation root'}
if($m['id'] -ne $c['id'] -or $m['source'] -ne $c['source'] -or $m['status'] -ne 'complete'){throw 'Wrong manifest'}
$hashes=ReadJson (Join-Path $PlanDirectory 'plan-hashes.json')
foreach($name in $hashes.Keys){if($name -notmatch '^[a-z][a-z0-9.-]+$' -or (Hash (Join-Path $PlanDirectory $name)) -ne $hashes[$name]){throw 'Plan changed: regenerate and review'}}
if(-not $hashes.ContainsKey('plan.json')){throw 'Unbound plan'}
$planDigest=Hash $planFile
$cursor=$root
while($cursor){
 if([IO.Directory]::Exists($cursor) -and (([IO.File]::GetAttributes($cursor) -band [IO.FileAttributes]::ReparsePoint) -ne 0)){throw 'Reparse root refused'}
 $cursor=[IO.Path]::GetDirectoryName($cursor)
}
foreach($child in @('data','fra','network')){
 $childPath=Join-Path $root $child
 if([IO.Directory]::Exists($childPath) -and (([IO.File]::GetAttributes($childPath) -band [IO.FileAttributes]::ReparsePoint) -ne 0)){throw 'Reparse child refused'}
}
$stage=Join-Path $root 'stage'
if([IO.Directory]::Exists($stage) -or [IO.File]::Exists($stage)){throw 'Stage exists: retain evidence and use a fresh reviewed run'}
$sqlplus=Join-Path $c['oracle_home'] 'bin\sqlplus.exe';$impdp=Join-Path $c['oracle_home'] 'bin\impdp.exe';$rman=Join-Path $c['oracle_home'] 'bin\rman.exe'
if(-not [IO.File]::Exists($sqlplus)){throw 'SQLPlus missing'}
if($c['id'] -eq 'oracle41'){
 if($PreviewOnly){throw 'PreviewOnly is Data Pump only'}
 if(-not [IO.File]::Exists($rman)){throw 'RMAN missing'}
 $data=Join-Path $root 'data'
 if([IO.Directory]::Exists($data) -and [IO.Directory]::GetFileSystemEntries($data).Length -ne 0){throw 'RMAN data directory not empty'}
}
if($c['id'] -eq 'oracle46' -and -not [IO.File]::Exists($impdp)){throw 'impdp missing'}
[IO.Directory]::CreateDirectory($root)|Out-Null
$lock=[IO.File]::Open((Join-Path $root 'restore.lock'),[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
$script:root=$root;$script:log=Join-Path $root 'worker.log';$script:phases=@{}
$started=[DateTime]::UtcNow;$status='failed';$errorText='';$receipt=Join-Path $root 'last-result.json'
$oldSid=$env:ORACLE_SID;$oldHome=$env:ORACLE_HOME;$oldLocal=$env:LOCAL;$oldTns=$env:TNS_ADMIN
try{
 $env:ORACLE_SID=$c['sid'];$env:ORACLE_HOME=$c['oracle_home'];$env:LOCAL=$null;$env:TNS_ADMIN=Join-Path $root 'network'
 [IO.Directory]::CreateDirectory($env:TNS_ADMIN)|Out-Null
 [IO.Directory]::CreateDirectory($stage)|Out-Null
 [IO.File]::WriteAllText((Join-Path $env:TNS_ADMIN 'sqlnet.ora'),"SQLNET.AUTHENTICATION_SERVICES=(NTS)`r`n",$utf8)
 Log 'START isolated restore; source NAS read-only input; no cleanup'
 $version=Native $sqlplus @('-V') 'version.log'
 if(-not $version.Contains($m['version'])){throw 'Oracle binary version mismatch'}
 foreach($f in $m['files']){
  $name=$f['name'];if($name -notmatch '^[A-Za-z0-9][A-Za-z0-9_.-]{0,150}$' -or $name -eq '..'){throw 'Unsafe file name'}
  $src=Join-Path $c['source'] $name;$dst=Join-Path $stage $name
  if(([IO.File]::GetAttributes($src) -band [IO.FileAttributes]::ReparsePoint) -ne 0){throw 'Source reparse refused'}
  if((New-Object IO.FileInfo($src)).Length -ne $f['bytes']){throw ('Source size mismatch '+$name)}
  [IO.File]::Copy($src,$dst,$false)
  if((New-Object IO.FileInfo($dst)).Length -ne $f['bytes'] -or (Hash $dst) -ne $f['sha256']){throw ('SHA256 mismatch '+$name)}
  if($f['role'] -eq 'export_log' -or $f['role'] -eq 'rman_log'){if([IO.File]::ReadAllText($dst) -match '(ORA-|RMAN-0[0-9]{4}:|LRM-|UDI-)'){throw 'Source native log not clean'}}
  Log ('SHA256_OK '+$name)
 }
 Phase 'source_sha256' 'worker.log'
 foreach($name in $hashes.Keys){
  if((Hash (Join-Path $PlanDirectory $name)) -ne $hashes[$name]){throw 'Plan changed during source copy'}
  if($name -ne 'plan.json'){
   $localPlan=Join-Path $root $name
   [IO.File]::Copy((Join-Path $PlanDirectory $name),$localPlan,$false)
   if((Hash $localPlan) -ne $hashes[$name]){throw 'Local plan digest mismatch'}
  }
 }
 if($c['id'] -eq 'oracle46'){
  Native $sqlplus @('-L','/ as sysdba',('@'+(Join-Path $root 'guard.sql'))) 'isolation.log'|Out-Null
  Phase 'isolation' 'isolation.log'
  Native $sqlplus @('-L','/ as sysdba',('@'+(Join-Path $root 'prepare.sql'))) 'prepare.log'|Out-Null
  if($PreviewOnly){Native $impdp @(('PARFILE='+(Join-Path $root 'preview.par'))) 'preview-native.log'|Out-Null;$status='partial';Log 'SQLFILE_ONLY: no import; not restore passed'}
  else{
   Native $impdp @(('PARFILE='+(Join-Path $root 'import.par'))) 'import-native.log'|Out-Null
   $importLog=Join-Path $stage 'import.log'
   if(-not [IO.File]::Exists($importLog) -or [IO.File]::ReadAllText($importLog) -match '(ORA-|UDI-|LRM-)'){throw 'Data Pump log missing/errors'}
   Phase 'native_restore' 'import-native.log'
  }
 }else{
  [IO.Directory]::CreateDirectory((Join-Path $root 'data'))|Out-Null
  [IO.Directory]::CreateDirectory((Join-Path $root 'fra'))|Out-Null
  Native $sqlplus @('-L','/ as sysdba',('@'+(Join-Path $root 'startup.sql'))) 'startup.log'|Out-Null
  # No TARGET/CATALOG source connection or filename bypass during duplicate.
  Native $rman @('auxiliary','/','nocatalog',('cmdfile='+(Join-Path $root 'duplicate.rman'))) 'duplicate.log'|Out-Null
  Phase 'native_restore' 'duplicate.log'
  Native $sqlplus @('-L','/ as sysdba',('@'+(Join-Path $root 'guard.sql'))) 'isolation.log'|Out-Null
  Phase 'isolation' 'isolation.log'
  Native $rman @('target','/','nocatalog',('cmdfile='+(Join-Path $root 'validate.rman'))) 'validate.log'|Out-Null
  Phase 'rman_validate' 'validate.log'
 }
 if(-not $PreviewOnly){
  $v=Native $sqlplus @('-L','/ as sysdba',('@'+(Join-Path $root 'verify.sql'))) 'verify.log'
  if($v -notmatch 'RESTORE_VERIFY_OK' -or $v -notmatch 'TABLE\|'){throw 'Verification evidence missing'}
  Phase 'table_checks' 'verify.log';$status='passed'
 }
}catch{$errorText=$_.Exception.Message;Log ('FAILURE '+$errorText)}
finally{
 try{
 $env:ORACLE_SID=$oldSid;$env:ORACLE_HOME=$oldHome;$env:LOCAL=$oldLocal;$env:TNS_ADMIN=$oldTns
 foreach($phase in $script:phases.Values){$phase['sha256']=Hash (Join-Path $root $phase['log'])}
 $r=@{schema_version=1;id=$c['id'];run=$m['run'];status=$status;production_change=$false;target_ip=$c['target_ip'];target_hostname=$c['target_hostname'];plan_sha256=$planDigest;finished=[DateTime]::UtcNow.ToString('o');seconds=([DateTime]::UtcNow-$started).TotalSeconds;scope=('isolated '+$c['mode']+' restore, SHA256, native tool and selected business table checks; business acceptance not performed');phases=$script:phases;error=$errorText;source=$c['source'];business_acceptance='not_tested';deletion='not_performed'}
 $tmp=$receipt+'.tmp';[IO.File]::WriteAllText($tmp,$json.Serialize($r),$utf8);if([IO.File]::Exists($receipt)){[IO.File]::Replace($tmp,$receipt,$null)}else{[IO.File]::Move($tmp,$receipt)}
 }finally{$lock.Close()}
}
if($status -eq 'failed'){throw ('Restore failed; evidence '+$receipt+'; '+$errorText)}
Write-Output ('RESULT '+$status+' '+$receipt)
