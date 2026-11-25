# PowerShell 2 / .NET 2 compatible functions. Loading this file performs no I/O.
function Assert-SvnPlainPath([string]$Path) {
 $full=[IO.Path]::GetFullPath($Path);$p=$full
 while($p){if(Test-Path -LiteralPath $p){$item=Get-Item -LiteralPath $p -Force;if($item.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'ReparsePoint rejected'}};$parent=[IO.Path]::GetDirectoryName($p.TrimEnd('\'));if($parent -eq $p){break};$p=$parent}
 return $full
}
function Get-SvnTree([string]$Root){
 $rootPath=Assert-SvnPlainPath $Root;$files=@();$dirs=@();$todo=New-Object Collections.Stack;$todo.Push($rootPath)
 while($todo.Count){$dir=$todo.Pop();foreach($f in @(Get-ChildItem -LiteralPath $dir -Force -ErrorAction Stop)){
  if($f.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'ReparsePoint rejected in payload'}
  if($f.PSIsContainer){$dirs+=,$f.FullName;$todo.Push($f.FullName)}else{$files+=,@{Name=$f.FullName.Substring($rootPath.TrimEnd('\').Length+1);Bytes=[long]$f.Length}}
 }}
 return @{Files=@($files);Directories=@($dirs)}
}
function Write-SvnJson([string]$Path,$Value){
 $null=Assert-SvnPlainPath $Path;$temp=$Path+'.'+[guid]::NewGuid().ToString('N')+'.tmp';$utf8=New-Object Text.UTF8Encoding($false)
 [IO.File]::WriteAllText($temp,$script:json.Serialize($Value),$utf8)
 if([IO.File]::Exists($Path)){$old=$Path+'.previous';if([IO.File]::Exists($old)){$null=Assert-SvnPlainPath $old;[IO.File]::Delete($old)};[IO.File]::Replace($temp,$Path,$old)}else{[IO.File]::Move($temp,$Path)}
}
function Write-SvnLog([string]$Private,[string]$Message){
 $dir=Join-Path $Private 'logs';$null=Assert-SvnPlainPath $dir;[void][IO.Directory]::CreateDirectory($dir)
 $line=(Get-Date -Format s)+' '+$Message+[Environment]::NewLine
 [IO.File]::AppendAllText((Join-Path $dir ((Get-Date -Format yyyy-MM-dd)+'.log')),$line,(New-Object Text.UTF8Encoding($false)))
 # Scheduled stdout may be redirected; console failure must not abort a backup.
 try{[Console]::Write($line)}catch{}
}
function Test-SvnSegment([string]$Name){return ($Name -and $Name -notmatch '[\\/:*?"<>|\x00-\x1f]' -and $Name -ne '.' -and $Name -ne '..' -and $Name -notmatch '[. ]$')}
function Test-SvnReceipt([string]$Directory,[string]$Asset,[string]$Date){
 try{
  $null=Assert-SvnPlainPath $Directory;$run=[IO.Path]::GetFileName($Directory)
  if($run -notmatch '^\d{8}T\d{6}-[0-9a-f]{6}$' -or $Date -notmatch '^\d{4}-\d{2}-\d{2}$' -or $run.Substring(0,8) -ne $Date.Replace('-','')){return $false}
  $receipt=Join-Path $Directory 'hotcopy-result.json';$null=Assert-SvnPlainPath $receipt
  if(-not [IO.File]::Exists($receipt)){return $false}
  $r=$script:json.DeserializeObject([IO.File]::ReadAllText($receipt,[Text.Encoding]::UTF8))
  if($r.Owner -ne 'svn-hotcopy-v2' -or $r.Asset -ne $Asset -or $r.Run -ne $run -or $r.Date -ne $Date -or $r.Status -ne 'hotcopy_verified' -or $r.SmallTest -isnot [bool] -or $r.SmallTest){return $false}
  if($null -eq $r.Repositories -or $r.Repositories -isnot [Array] -or @($r.Repositories).Count -eq 0 -or $null -eq $r.Files -or $r.Files -isnot [Array] -or @($r.Files).Count -eq 0 -or $null -eq $r.Directories -or $r.Directories -isnot [Array]){return $false}
  $repos=@{};foreach($repo in $r.Repositories){if(-not (Test-SvnSegment $repo.Repository) -or $repo.Repository -eq '_root-config' -or $repos.ContainsKey($repo.Repository) -or $repo.UUID -notmatch '^[0-9a-fA-F-]{36}$' -or [string]$repo.Revision -notmatch '^\d+$'){return $false};$repos[$repo.Repository]=$true
   if(-not [IO.File]::Exists((Join-Path $Directory ($repo.Repository+'\format'))) -or -not [IO.Directory]::Exists((Join-Path $Directory ($repo.Repository+'\db')))){return $false}
  }
  $tree=Get-SvnTree $Directory;$actual=@{};foreach($f in $tree.Files){$actual[$f.Name]=[long]$f.Bytes}
  $expected=@{};foreach($f in $r.Files){$parts=@(([string]$f.Name).Split('\'));if($parts.Count -lt 2){return $false};foreach($part in $parts){if(-not (Test-SvnSegment $part)){return $false}}
   if($parts[0] -eq '_root-config'){if($parts.Count -ne 2 -or @('groups.conf','htpasswd','VisualSVN-GlobalWinAuthz.ini','VisualSVN_GlobalWinAuthz.ini') -notcontains $parts[1]){return $false}}elseif(-not $repos.ContainsKey($parts[0])){return $false}
   if($expected.ContainsKey($f.Name) -or [string]$f.Bytes -notmatch '^\d+$' -or -not $actual.ContainsKey($f.Name) -or $actual[$f.Name] -ne [long]$f.Bytes){return $false};$expected[$f.Name]=$true
  }
  # Unknown files or empty unknown directories block the whole date's cleanup.
  if($actual.Count -ne $expected.Count+1){return $false}
  $expectedDirs=@{};foreach($rel in @($r.Directories)){
   if(-not $rel -or $expectedDirs.ContainsKey($rel)){return $false};$parts=@(([string]$rel).Split('\'));foreach($part in $parts){if(-not (Test-SvnSegment $part)){return $false}}
   if($parts[0] -ne '_root-config' -and -not $repos.ContainsKey($parts[0])){return $false};if($parts[0] -eq '_root-config' -and $parts.Count -ne 1){return $false};$expectedDirs[$rel]=$true
  }
  if($tree.Directories.Count -ne $expectedDirs.Count){return $false}
  foreach($dir in $tree.Directories){$rel=$dir.Substring($Directory.TrimEnd('\').Length+1);if(-not $expectedDirs.ContainsKey($rel)){return $false}}
  return $true
 }catch{return $false}
}
function Get-SvnCleanupPlan([string]$Base,[string]$Asset,[int]$Keep){
 if($Keep -lt 1){throw 'KeepCycles must be positive'};$null=Assert-SvnPlainPath $Base;$valid=@()
 foreach($day in @(Get-ChildItem -LiteralPath $Base -Force)){
  if(-not $day.PSIsContainer -or $day.Name -notmatch '^\d{4}-\d{2}-\d{2}$' -or ($day.Attributes -band [IO.FileAttributes]::ReparsePoint)){continue}
  try{$parsed=[datetime]::ParseExact($day.Name,'yyyy-MM-dd',[Globalization.CultureInfo]::InvariantCulture)}catch{continue}
  if($parsed.Date -gt (Get-Date).Date){continue}
  $runs=@(Get-ChildItem -LiteralPath $day.FullName -Force);$ok=$runs.Count -gt 0
  foreach($run in $runs){if(-not $run.PSIsContainer -or -not (Test-SvnReceipt $run.FullName $Asset $day.Name)){$ok=$false;break}}
  if($ok){$valid+=,$day}
 }
 $ordered=@($valid | Sort-Object Name -Descending);if($ordered.Count -le $Keep){return @()}
 return @($ordered | Select-Object -Skip $Keep | ForEach-Object {$_.FullName})
}
function Clear-SvnManagedReadOnly([string]$Path){
 # Validate again immediately before changing attributes; never follow junctions.
 $null=Assert-SvnPlainPath $Path;$attributes=[IO.File]::GetAttributes($Path)
 if($attributes -band [IO.FileAttributes]::ReparsePoint){throw 'ReparsePoint rejected before attribute change'}
 if($attributes -band [IO.FileAttributes]::ReadOnly){[IO.File]::SetAttributes($Path,($attributes -band (-bnot [IO.FileAttributes]::ReadOnly)))}
}
function Remove-SvnManagedFile([string]$Path){Clear-SvnManagedReadOnly $Path;[IO.File]::Delete($Path)}
function Clean([string]$Base,[string]$Asset,[int]$Keep){
 $removed=0
 foreach($day in @(Get-SvnCleanupPlan $Base $Asset $Keep)){
  $null=Assert-SvnPlainPath $day;$runs=@(Get-ChildItem -LiteralPath $day -Force);$date=[IO.Path]::GetFileName($day)
  foreach($run in $runs){if(-not $run.PSIsContainer -or -not (Test-SvnReceipt $run.FullName $Asset $date)){throw 'Cleanup revalidation failed; preserved'}}
  foreach($run in $runs){$tree=Get-SvnTree $run.FullName
   # Keep ownership evidence until every payload file and directory is removed.
   foreach($file in $tree.Files){if($file.Name -eq 'hotcopy-result.json'){continue};Remove-SvnManagedFile (Join-Path $run.FullName $file.Name)}
   foreach($dir in @($tree.Directories | Sort-Object Length -Descending)){Clear-SvnManagedReadOnly $dir;[IO.Directory]::Delete($dir,$false)}
   Clear-SvnManagedReadOnly $run.FullName
   Remove-SvnManagedFile (Join-Path $run.FullName 'hotcopy-result.json')
   [IO.Directory]::Delete($run.FullName,$false)
  };Clear-SvnManagedReadOnly $day;[IO.Directory]::Delete($day,$false);$removed++
 };return $removed
}
function New-SvnCredentialAcl {
 $acl=New-Object Security.AccessControl.FileSecurity;$acl.SetAccessRuleProtection($true,$false)
 foreach($sid in @('S-1-5-18','S-1-5-32-544')){$identity=New-Object Security.Principal.SecurityIdentifier($sid);$rule=New-Object Security.AccessControl.FileSystemAccessRule($identity,'FullControl','Allow');[void]$acl.AddAccessRule($rule)}
 return $acl
}
function Set-SvnCredentialAcl([string]$Path){Set-Acl -Path $Path -AclObject (New-SvnCredentialAcl)}
function Save-SvnCredential([string]$Path,$Credential){
 if(-not $Credential){throw 'Credential entry cancelled'};Add-Type -AssemblyName System.Security
 $network=$Credential.GetNetworkCredential();$plain=[Text.Encoding]::UTF8.GetBytes($script:json.Serialize(@{User=$Credential.UserName;Password=$network.Password}))
 try{
  $null=Assert-SvnPlainPath $Path;$cipher=[Security.Cryptography.ProtectedData]::Protect($plain,$null,[Security.Cryptography.DataProtectionScope]::LocalMachine)
  $temp=$Path+'.'+[guid]::NewGuid().ToString('N')+'.tmp';[IO.File]::WriteAllText($temp,[Convert]::ToBase64String($cipher),(New-Object Text.UTF8Encoding($false)))
  if([IO.File]::Exists($Path)){[IO.File]::Replace($temp,$Path,($Path+'.previous-'+[guid]::NewGuid().ToString('N')))}else{[IO.File]::Move($temp,$Path)}
  Set-SvnCredentialAcl $Path
 }finally{[Array]::Clear($plain,0,$plain.Length);$network=$null}
}
function Connect-SvnSmb([string]$Target,[string]$CredentialPath){
 Add-Type -AssemblyName System.Security;$null=Assert-SvnPlainPath $CredentialPath
 $plain=[Security.Cryptography.ProtectedData]::Unprotect([Convert]::FromBase64String([IO.File]::ReadAllText($CredentialPath)),$null,[Security.Cryptography.DataProtectionScope]::LocalMachine)
 try{$cred=$script:json.DeserializeObject([Text.Encoding]::UTF8.GetString($plain));$rc=[SvnNative]::Connect($Target.TrimEnd('\'),$cred.User,$cred.Password);if($rc -ne 0){throw ('SMB connection error '+$rc+'; existing sessions preserved')}}finally{[Array]::Clear($plain,0,$plain.Length);$cred=$null}
}
function New-SvnRunIdentity([datetime]$Started){return @{Run=($Started.ToString('yyyyMMddTHHmmss')+'-'+[guid]::NewGuid().ToString('N').Substring(0,6));Date=$Started.ToString('yyyy-MM-dd')}}
function Invoke-SvnRun($Config,$Adapters,[bool]$CheckOnly,[bool]$SmallTest){
 $timer=[Diagnostics.Stopwatch]::StartNew();$lock=$null;$pending=$null;$published=$null;$results=@();$exitCode=1
 $started=Get-Date;$identity=New-SvnRunIdentity $started;$run=$identity.Run;$date=$identity.Date
 $record=@{Status='failed';Run=$run;Target=$Config.TargetRoot;Asset=$Config.Asset;Date=$date;Seconds=0;Repositories=@();Error=$null;SmallTest=$SmallTest;Mode=$(if($CheckOnly){'check'}else{'backup'});Published=$null;Cleanup=0}
 try{
  $null=Assert-SvnPlainPath $Config.PrivateRoot;[void][IO.Directory]::CreateDirectory($Config.PrivateRoot)
  try{$lock=[IO.File]::Open((Join-Path $Config.PrivateRoot 'svn-backup.lock'),'OpenOrCreate','ReadWrite','None')}catch{return 2}
  Write-SvnLog $Config.PrivateRoot ('START '+$run+' mode='+$record.Mode)
  $null=Assert-SvnPlainPath $Config.RepositoryRoot
  & $Adapters.Connect $Config.TargetRoot (Join-Path $Config.PrivateRoot 'nas240.dpapi')
  $null=Assert-SvnPlainPath $Config.TargetRoot
  $free=& $Adapters.Free $Config.TargetRoot
  if($free -lt $(if($SmallTest){$Config.SmallMinFreeBytes}else{$Config.MinFreeBytes})){throw 'Insufficient target free space'}
  $repos=@(Get-ChildItem -LiteralPath $Config.RepositoryRoot -Force | Where-Object {$_.PSIsContainer -and [IO.File]::Exists((Join-Path $_.FullName 'format')) -and [IO.Directory]::Exists((Join-Path $_.FullName 'db'))})
  if($SmallTest){$repos=@($repos | Where-Object {$_.Name -eq $Config.SmallRepository})};if($repos.Count -eq 0){throw 'No repositories found'}
  foreach($repo in $repos){$null=Assert-SvnPlainPath $repo.FullName;$null=Get-SvnTree $repo.FullName}
  $base=Join-Path $Config.TargetRoot $Config.Asset;$null=Assert-SvnPlainPath $base;[void][IO.Directory]::CreateDirectory($base)
  $probe=Join-Path $base ('.write-probe-'+[guid]::NewGuid().ToString('N'));[IO.File]::WriteAllText($probe,'svn-probe');if([IO.File]::ReadAllText($probe) -ne 'svn-probe'){throw 'SMB readback failed'};[IO.File]::Delete($probe)
  if($CheckOnly){$record.Status='check_ok';Write-SvnLog $Config.PrivateRoot ('CHECK_OK repositories='+$repos.Count+'; no backup or cleanup');$exitCode=0}
  else{
   $day=Join-Path $base $date;$null=Assert-SvnPlainPath $day;[void][IO.Directory]::CreateDirectory($day);$pending=Join-Path $day ('.'+$run+'.partial');[void][IO.Directory]::CreateDirectory($pending)
   foreach($repo in $repos){$dest=Join-Path $pending $repo.Name;$tick=[Diagnostics.Stopwatch]::StartNew()
    Write-SvnLog $Config.PrivateRoot ('HOTCOPY_START '+$repo.Name)
    $null=& $Adapters.Tool 'svnadmin' @('hotcopy',$repo.FullName,$dest)
    $null=Assert-SvnPlainPath $dest;$null=Get-SvnTree $dest
    $null=& $Adapters.Tool 'svnadmin' @('verify',$dest,'-q')
    $uuid=((& $Adapters.Tool 'svnlook' @('uuid',$dest)) -join '').Trim();$revision=((& $Adapters.Tool 'svnlook' @('youngest',$dest)) -join '').Trim()
    if($uuid -notmatch '^[0-9a-fA-F-]{36}$' -or $revision -notmatch '^\d+$'){throw 'Invalid copied UUID/revision'}
    $results+=,@{Repository=$repo.Name;UUID=$uuid;Revision=$revision;Seconds=[Math]::Round($tick.Elapsed.TotalSeconds,1)}
    Write-SvnLog $Config.PrivateRoot ('REPOSITORY_OK '+$repo.Name)
   }
   $configDir=Join-Path $pending '_root-config';[void][IO.Directory]::CreateDirectory($configDir)
   foreach($name in @('groups.conf','htpasswd','VisualSVN-GlobalWinAuthz.ini','VisualSVN_GlobalWinAuthz.ini')){$p=Join-Path $Config.RepositoryRoot $name;if([IO.File]::Exists($p)){$null=Assert-SvnPlainPath $p;[IO.File]::Copy($p,(Join-Path $configDir $name),$false)}}
   $tree=Get-SvnTree $pending;$dirNames=@($tree.Directories | ForEach-Object {$_.Substring($pending.TrimEnd('\').Length+1)});$receipt=@{Owner='svn-hotcopy-v2';Asset=$Config.Asset;Run=$run;Date=$date;Status='hotcopy_verified';SmallTest=$SmallTest;Repositories=@($results);Files=@($tree.Files);Directories=$dirNames;Seconds=[Math]::Round($timer.Elapsed.TotalSeconds,1)}
   Write-SvnJson (Join-Path $pending 'hotcopy-result.json') $receipt
   $final=Join-Path $day $run;[IO.Directory]::Move($pending,$final);$published=$final;$record.Published=$final
   $record.Status='success';$exitCode=0
   if(-not $SmallTest -and -not $Config.SkipCleanup){try{$record.Cleanup=Clean $base $Config.Asset $Config.KeepCycles}catch{$record.Status='cleanup_failed';$record.Error='Backup published; cleanup failed safely: '+$_.Exception.Message;Write-SvnLog $Config.PrivateRoot ('CLEANUP_FAILED '+$_.Exception.Message);$exitCode=1}}
   Write-SvnLog $Config.PrivateRoot ('PUBLISHED '+$run+' status='+$record.Status)
  }
 }catch{$record.Error=$_.Exception.Message;Write-SvnLog $Config.PrivateRoot ('FAILURE '+$record.Error)}
 finally{
  if($lock){try{$record.Repositories=@($results);$record.Seconds=[Math]::Round($timer.Elapsed.TotalSeconds,1);$record.Finished=(Get-Date).ToString('o');Write-SvnJson (Join-Path $Config.PrivateRoot 'last-result.json') $record}finally{$lock.Close()}}
 }
 return $exitCode
}
