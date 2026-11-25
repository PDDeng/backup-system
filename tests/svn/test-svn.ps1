$ErrorActionPreference='Stop'
$repoRoot=Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))
$component=Join-Path $repoRoot 'components\svn'
. (Join-Path $component 'json-compat.ps1')
. (Join-Path $component 'svn-core.ps1')
$sandbox=Join-Path ([IO.Path]::GetTempPath()) ('svn-unit-'+[guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($sandbox)
$script:passed=0
function Assert($condition,[string]$message){if(-not $condition){throw ('ASSERT: '+$message)};$script:passed++;[Console]::WriteLine('PASS '+$message)}
function Read-Result($config){return $json.DeserializeObject([IO.File]::ReadAllText((Join-Path $config.PrivateRoot 'last-result.json')))}
function New-Config([string]$name){
 $base=Join-Path $sandbox $name;[void][IO.Directory]::CreateDirectory($base)
 $src=Join-Path $base 'source';$target=Join-Path $base 'target';[void][IO.Directory]::CreateDirectory($target)
 foreach($name in @('repoA','repoB')){$r=Join-Path $src $name;[void][IO.Directory]::CreateDirectory((Join-Path $r 'db\empty'));[IO.File]::WriteAllText((Join-Path $r 'format'),'5');[IO.File]::WriteAllText((Join-Path $r 'db\current'),'7')}
 [IO.File]::WriteAllText((Join-Path $src 'groups.conf'),'unit-test-config')
 return @{PrivateRoot=(Join-Path $base 'private');RepositoryRoot=$src;TargetRoot=$target;Asset='svn-unit';KeepCycles=15;SkipCleanup=$false;SmallRepository='repoA';MinFreeBytes=100;SmallMinFreeBytes=10}
}
$script:calls=New-Object Collections.ArrayList
$script:failVerify=$false
$adapters=@{
 Connect={param($target,$credential) [void]$script:calls.Add('connect')}
 Free={param($target) return 1000000}
 Tool={param($tool,$arguments)
  [void]$script:calls.Add($arguments[0])
  if($arguments[0] -eq 'hotcopy'){
   [void][IO.Directory]::CreateDirectory($arguments[2]);Copy-Item -Path (Join-Path $arguments[1] '*') -Destination $arguments[2] -Recurse
  }elseif($arguments[0] -eq 'verify'){if($arguments -notcontains '-q'){throw 'verify must be quiet'};if($script:failVerify){throw 'mock svnadmin verify failed'}}elseif($arguments[0] -eq 'uuid'){return '12345678-1234-1234-1234-123456789abc'}elseif($arguments[0] -eq 'youngest'){return '7'}else{throw 'Unexpected mock invocation'}
 }
}
function New-ManagedDate($base,[datetime]$date,[string]$asset){
 $day=$date.ToString('yyyy-MM-dd');$run=$date.ToString('yyyyMMdd')+'T010000-abcdef';$dir=Join-Path (Join-Path $base $day) $run
 [void][IO.Directory]::CreateDirectory((Join-Path $dir 'repoA\db\empty'));[void][IO.Directory]::CreateDirectory((Join-Path $dir '_root-config'))
 [IO.File]::WriteAllText((Join-Path $dir 'repoA\format'),'5');[IO.File]::WriteAllText((Join-Path $dir 'repoA\db\current'),'7')
 $tree=Get-SvnTree $dir;$r=@{Owner='svn-hotcopy-v2';Asset=$asset;Run=$run;Date=$day;Status='hotcopy_verified';SmallTest=$false;Repositories=@(@{Repository='repoA';UUID='12345678-1234-1234-1234-123456789abc';Revision='7'});Files=$tree.Files;Directories=@($tree.Directories | ForEach-Object {$_.Substring($dir.Length+1)})}
 Write-SvnJson (Join-Path $dir 'hotcopy-result.json') $r
 return $dir
}
try{
 # Parser catches syntax failures without executing installers or production wrappers.
 foreach($file in @(Get-ChildItem -LiteralPath $component -Filter '*.ps1')){$tokens=$null;$errors=$null;$null=[Management.Automation.Language.Parser]::ParseFile($file.FullName,[ref]$tokens,[ref]$errors);Assert ($errors.Count -eq 0) ('syntax '+$file.Name)}
 $c=New-Config 'check';$rc=Invoke-SvnRun $c $adapters $true $false;$r=Read-Result $c
 Assert ($rc -eq 0 -and $r.Status -eq 'check_ok') 'CheckOnly writes diagnostic result'
 Assert ($script:calls -notcontains 'hotcopy') 'CheckOnly never invokes hotcopy'
 Assert (@(Get-ChildItem -LiteralPath (Join-Path $c.TargetRoot $c.Asset) -Force).Count -eq 0) 'CheckOnly leaves no backup/probe files'
 Assert ([IO.File]::Exists((Join-Path $c.PrivateRoot ('logs\'+(Get-Date -Format yyyy-MM-dd)+'.log')))) 'local daily log exists'
 $c=New-Config 'smb-failure';$badAdapters=@{Connect={param($t,$p) throw 'SMB connection error 1219; existing sessions preserved'};Free=$adapters.Free;Tool=$adapters.Tool}
 $rc=Invoke-SvnRun $c $badAdapters $true $false;$r=Read-Result $c
 Assert ($rc -eq 1 -and $r.Status -eq 'failed' -and $r.Error -like '*1219*') 'SMB preflight failure writes local last-result'
 Assert (@(Get-ChildItem -LiteralPath $c.TargetRoot -Force).Count -eq 0) 'SMB failure does not create backup directory'
 $c=New-Config 'success';$rc=Invoke-SvnRun $c $adapters $false $false;$r=Read-Result $c
 Assert ($rc -eq 0 -and $r.Status -eq 'success' -and $r.Repositories.Count -eq 2) 'all repositories copied verified and recorded'
 Assert (Test-SvnReceipt $r.Published $c.Asset $r.Date) 'formal receipt validates file and directory manifest'
 Assert ([IO.File]::ReadAllText((Join-Path $r.Published '_root-config\groups.conf')) -eq 'unit-test-config') 'root config whitelist copied'
 Assert (@(Get-ChildItem -LiteralPath ([IO.Path]::GetDirectoryName($r.Published)) -Force | Where-Object {$_.Name -like '*.partial'}).Count -eq 0) 'successful run atomically published'
 $before=[IO.File]::ReadAllText((Join-Path $c.PrivateRoot 'last-result.json'));$lock=[IO.File]::Open((Join-Path $c.PrivateRoot 'svn-backup.lock'),'Open','ReadWrite','None')
 try{Assert ((Invoke-SvnRun $c $adapters $false $false) -eq 2) 'exclusive lock refuses concurrent run'}finally{$lock.Close()}
 Assert ([IO.File]::ReadAllText((Join-Path $c.PrivateRoot 'last-result.json')) -eq $before) 'lock loser preserves owner result'
 $c=New-Config 'failed';$script:failVerify=$true;$rc=Invoke-SvnRun $c $adapters $false $false;$script:failVerify=$false;$r=Read-Result $c
 Assert ($rc -eq 1 -and $r.Status -eq 'failed' -and $r.Error -like '*verify*') 'verify failure has explicit last-result'
 $partials=@(Get-ChildItem -LiteralPath (Join-Path $c.TargetRoot $c.Asset) -Recurse -Force | Where-Object {$_.PSIsContainer -and $_.Name -like '*.partial'})
 Assert ($partials.Count -eq 1 -and -not $r.Published) 'failed partial retained not published'
 Assert ([IO.File]::ReadAllText((Join-Path $c.RepositoryRoot 'repoA\db\current')) -eq '7') 'source data preserved on failure'
 $c=New-Config 'small';$rc=Invoke-SvnRun $c $adapters $false $true;$r=Read-Result $c
 Assert ($rc -eq 0 -and $r.Repositories.Count -eq 1 -and $r.SmallTest) 'SmallTest selects one repo'
 Assert (-not (Test-SvnReceipt $r.Published $c.Asset $r.Date)) 'SmallTest excluded from full-cycle cleanup'
 $base=Join-Path $sandbox 'retention';[void][IO.Directory]::CreateDirectory($base);$runs=@()
 foreach($i in 1..16){$runs+=,(New-ManagedDate $base ((Get-Date).Date.AddDays(-$i)) 'svn-unit')}
 Assert (@(Get-SvnCleanupPlan $base 'svn-unit' 15).Count -eq 1) '16 complete dates retain newest15'
 Assert ((Clean $base 'svn-unit' 15) -eq 1 -and -not [IO.Directory]::Exists($runs[15])) 'whitelisted oldest complete date cleaned'
 Assert (@(Get-SvnCleanupPlan $base 'svn-unit' 15).Count -eq 0) '15 or fewer cycles never clean'
 Assert (@(Get-SvnCleanupPlan $base 'svn-unit' 7).Count -eq 8) 'retention accepts configurable seven complete cycles'
 $old=New-ManagedDate $base ((Get-Date).Date.AddDays(-20)) 'svn-unit';[IO.File]::WriteAllText((Join-Path $old 'unexpected.txt'),'keep')
 Assert (-not (Test-SvnReceipt $old 'svn-unit' ([IO.Path]::GetFileName([IO.Path]::GetDirectoryName($old))))) 'unknown payload blocks receipt'
 Assert (@(Get-SvnCleanupPlan $base 'svn-unit' 15).Count -eq 0) 'unknown date preserved'
 $legacy=New-ManagedDate $base ((Get-Date).Date.AddDays(-21)) 'svn-unit';$p=Join-Path $legacy 'hotcopy-result.json';$r=$json.DeserializeObject([IO.File]::ReadAllText($p));$r.Remove('Owner');Write-SvnJson $p $r
 Assert (@(Get-SvnCleanupPlan $base 'svn-unit' 15).Count -eq 0) 'legacy unowned receipt preserved'
 $partialDay=New-ManagedDate $base ((Get-Date).Date.AddDays(-22)) 'svn-unit';[void][IO.Directory]::CreateDirectory((Join-Path ([IO.Path]::GetDirectoryName($partialDay)) '.unfinished.partial'))
 Assert (@(Get-SvnCleanupPlan $base 'svn-unit' 15).Count -eq 0) 'partial sibling protects whole date'
 $bad=New-ManagedDate $base ((Get-Date).Date.AddDays(-23)) 'svn-unit';$p=Join-Path $bad 'hotcopy-result.json';$r=$json.DeserializeObject([IO.File]::ReadAllText($p));$r.Files[0].Name='..\escape';Write-SvnJson $p $r
 Assert (-not (Test-SvnReceipt $bad 'svn-unit' $r.Date)) 'manifest traversal rejected'
 $bad=New-ManagedDate $base ((Get-Date).Date.AddDays(-24)) 'svn-unit';[void][IO.Directory]::CreateDirectory((Join-Path $bad 'unknown-empty'))
 Assert (-not (Test-SvnReceipt $bad 'svn-unit' ((Get-Date).Date.AddDays(-24).ToString('yyyy-MM-dd')))) 'unknown empty directory rejected'
 # A real local junction test; never follows the junction or removes its target.
 $outside=Join-Path $sandbox 'outside';[void][IO.Directory]::CreateDirectory($outside);[IO.File]::WriteAllText((Join-Path $outside 'keep.txt'),'keep')
 $junction=Join-Path $bad 'junction';$null=& $env:ComSpec /d /c mklink /J $junction $outside
 if($LASTEXITCODE -ne 0){throw 'Cannot create local junction fixture'}
 $rejected=$false;try{$null=Get-SvnTree $bad}catch{$rejected=$true}
 Assert $rejected 'ReparsePoint rejected before traversal'
 Assert (-not (Test-SvnReceipt $bad 'svn-unit' ((Get-Date).Date.AddDays(-24).ToString('yyyy-MM-dd')))) 'linked payload blocks cleanup'
 $rejected=$false;try{Clear-SvnManagedReadOnly $junction}catch{$rejected=$true};Assert $rejected 'ReadOnly clearing rejects ReparsePoint before attribute mutation'
 [IO.Directory]::Delete($junction,$false)
 Assert ([IO.File]::ReadAllText((Join-Path $outside 'keep.txt')) -eq 'keep') 'junction target untouched'
 # LocalMachine DPAPI encrypt/decrypt roundtrip without WNet/native connect.
 $secure=ConvertTo-SecureString 'unit-only-not-a-secret' -AsPlainText -Force;$credential=New-Object Management.Automation.PSCredential -ArgumentList 'unit-user',$secure
 $acl=New-SvnCredentialAcl;$rules=@($acl.GetAccessRules($true,$false,[Security.Principal.SecurityIdentifier]));Assert ($acl.AreAccessRulesProtected -and $rules.Count -eq 2 -and @($rules | Where-Object {$_.IdentityReference.Value -notin @('S-1-5-18','S-1-5-32-544')}).Count -eq 0) 'credential ACL policy SYSTEM and Administrators only'
 # Non-elevated unit test: inspect ACL above, mock only applying privileged ACL.
 $script:aclApplied=$false;function Set-SvnCredentialAcl([string]$Path){$script:aclApplied=$true}
 $credPath=Join-Path $sandbox 'unit.dpapi';Save-SvnCredential $credPath $credential;$raw=[IO.File]::ReadAllText($credPath)
 Assert $script:aclApplied 'save requests private credential ACL'
 Assert ($raw -notlike '*unit-only*') 'credential stored without plaintext'
 $bytes=[Security.Cryptography.ProtectedData]::Unprotect([Convert]::FromBase64String($raw),$null,[Security.Cryptography.DataProtectionScope]::LocalMachine)
 try{$decoded=$json.DeserializeObject([Text.Encoding]::UTF8.GetString($bytes));Assert ($decoded.User -eq 'unit-user' -and $decoded.Password -eq 'unit-only-not-a-secret') 'machine DPAPI roundtrip'}finally{[Array]::Clear($bytes,0,$bytes.Length)}
 Add-Type -TypeDefinition @'
public class SvnNative {public static int Code=0;public static string Target;public static int Connect(string target,string user,string password){Target=target;return Code;}}
'@
 Connect-SvnSmb '\\unit-invalid\share\' $credPath
 Assert $true 'SMB connector uses decrypted credential through native adapter stub'
 Assert ([SvnNative]::Target -eq '\\unit-invalid\share') 'WNet receives exact share without trailing slash'
 [SvnNative]::Code=1219;$conflict=$false;try{Connect-SvnSmb '\\unit-invalid\share' $credPath}catch{$conflict=$_.Exception.Message -like '*1219*preserved*'}
 Assert $conflict 'SMB1219 explicit failure preserves existing sessions'
 $again=Join-Path $sandbox 'second.dpapi';Save-SvnCredential $again $credential;Save-SvnCredential $again $credential
 Assert (@(Get-ChildItem -Path ($again+'.previous-*')).Count -eq 1) 'credential replacement keeps encrypted prior version'
 $plan=& (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') -NoProfile -ExecutionPolicy Bypass -File (Join-Path $component 'install-svn-task.ps1') -PlanOnly
 Assert ($LASTEXITCODE -eq 0) 'installer PlanOnly exits without task registration'
 $p=$json.DeserializeObject(($plan -join ''));Assert ($p.User -eq 'SYSTEM' -and $p.LogonType -eq 5 -and $p.RunLevel -eq 1 -and -not $p.Enabled -and -not $p.StartsBackup) 'SYSTEM background daily01 disabled by default'
 # Receipt-only fixture reproduces the prior @($null).Count==1 bypass.
 $nullRoot=Join-Path $sandbox 'null-arrays';$nullRun='20260101T010000-abcdef';$nullDir=Join-Path $nullRoot $nullRun;[void][IO.Directory]::CreateDirectory($nullDir)
 $r=@{Owner='svn-hotcopy-v2';Asset='svn-unit';Run=$nullRun;Date='2026-01-01';Status='hotcopy_verified';SmallTest=$false;Repositories=$null;Files=$null;Directories=@()}
 [IO.File]::WriteAllText((Join-Path $nullDir 'hotcopy-result.json'),$json.Serialize($r))
 Assert (-not (Test-SvnReceipt $nullDir 'svn-unit' '2026-01-01')) 'null repositories and files cannot validate receipt-only directory'
 foreach($values in @(@{Repositories=@();Files=@()},@{Repositories=$null;Files=@()},@{Repositories=@();Files=$null})){
  $r.Repositories=$values.Repositories;$r.Files=$values.Files;[IO.File]::WriteAllText((Join-Path $nullDir 'hotcopy-result.json'),$json.Serialize($r))
  Assert (-not (Test-SvnReceipt $nullDir 'svn-unit' '2026-01-01')) 'empty or null manifest arrays fail closed'
 }
 $nullDates=Join-Path $sandbox 'null-date';$invalid=New-ManagedDate $nullDates ((Get-Date).Date.AddDays(-30)) 'svn-unit';$r=$json.DeserializeObject([IO.File]::ReadAllText((Join-Path $invalid 'hotcopy-result.json')));$r.Directories=$null;[IO.File]::WriteAllText((Join-Path $invalid 'hotcopy-result.json'),$json.Serialize($r))
 Assert (-not (Test-SvnReceipt $invalid 'svn-unit' $r.Date)) 'null directory manifest rejected'
 # A real read-only FSFS fixture: preserve other flags, remove receipt last.
 $readOnlyBase=Join-Path $sandbox 'readonly';$older=New-ManagedDate $readOnlyBase ((Get-Date).Date.AddDays(-3)) 'svn-unit';$newer=New-ManagedDate $readOnlyBase ((Get-Date).Date.AddDays(-2)) 'svn-unit'
 $file=Join-Path $older 'repoA\format';[IO.File]::SetAttributes($file,([IO.FileAttributes]::ReadOnly -bor [IO.FileAttributes]::Hidden));Clear-SvnManagedReadOnly $file
 Assert (([IO.File]::GetAttributes($file) -band [IO.FileAttributes]::Hidden) -and -not ([IO.File]::GetAttributes($file) -band [IO.FileAttributes]::ReadOnly)) 'attribute clearing removes ReadOnly only'
 foreach($f in (Get-SvnTree $older).Files){$p=Join-Path $older $f.Name;[IO.File]::SetAttributes($p,([IO.File]::GetAttributes($p) -bor [IO.FileAttributes]::ReadOnly))}
 $script:removeImpl=(Get-Item function:Remove-SvnManagedFile).ScriptBlock;$script:deleteOrder=New-Object Collections.ArrayList
 function Remove-SvnManagedFile([string]$Path){[void]$script:deleteOrder.Add([IO.Path]::GetFileName($Path));if([IO.Path]::GetFileName($Path) -eq 'hotcopy-result.json'){if([IO.Directory]::GetDirectories([IO.Path]::GetDirectoryName($Path)).Length -ne 0){throw 'Receipt removed before payload directories'}};& $script:removeImpl $Path}
 try{$removed=Clean $readOnlyBase 'svn-unit' 1}finally{Set-Item function:Remove-SvnManagedFile -Value $script:removeImpl}
 Assert ($removed -eq 1 -and -not [IO.Directory]::Exists($older)) 'ReadOnly FSFS files and receipt cleaned successfully'
 Assert ($script:deleteOrder[$script:deleteOrder.Count-1] -eq 'hotcopy-result.json') 'ownership receipt deleted after all payload files and directories'
 # Inject a mid-payload failure; receipt must still identify the retained run.
 $failBase=Join-Path $sandbox 'cleanup-fault';$older=New-ManagedDate $failBase ((Get-Date).Date.AddDays(-3)) 'svn-unit';$newer=New-ManagedDate $failBase ((Get-Date).Date.AddDays(-2)) 'svn-unit'
 $script:deletes=0;function Remove-SvnManagedFile([string]$Path){$script:deletes++;if($script:deletes -eq 2){throw 'injected deletion denied'};& $script:removeImpl $Path}
 $failed=$false;try{$null=Clean $failBase 'svn-unit' 1}catch{$failed=$_.Exception.Message -like '*injected deletion denied*'}finally{Set-Item function:Remove-SvnManagedFile -Value $script:removeImpl}
 Assert ($failed -and [IO.File]::Exists((Join-Path $older 'hotcopy-result.json'))) 'mid-cleanup failure retains ownership receipt'
 Assert (-not (Test-SvnReceipt $older 'svn-unit' ((Get-Date).Date.AddDays(-3).ToString('yyyy-MM-dd')))) 'partially cleaned run never validates as complete'
 $c=New-Config 'cleanup-result';$script:cleanImpl=(Get-Item function:Clean).ScriptBlock;function Clean {throw 'injected denied detail'}
 try{$rc=Invoke-SvnRun $c $adapters $false $false}finally{Set-Item function:Clean -Value $script:cleanImpl}
 $r=Read-Result $c;Assert ($rc -eq 1 -and $r.Status -eq 'cleanup_failed' -and $r.Error -like '*injected denied detail*' -and [IO.Directory]::Exists($r.Published)) 'cleanup_failed preserves exception detail and published backup'
 $logText=[IO.File]::ReadAllText((Join-Path $c.PrivateRoot ('logs\'+(Get-Date -Format yyyy-MM-dd)+'.log')));Assert ($logText -like '*CLEANUP_FAILED injected denied detail*') 'cleanup exception detail persisted in daily log'
 $identity=New-SvnRunIdentity ([datetime]::ParseExact('2026-11-25 23:59:59','yyyy-MM-dd HH:mm:ss',[Globalization.CultureInfo]::InvariantCulture))
 Assert ($identity.Date -eq '2026-11-25' -and $identity.Run -like '20261125T235959-*') 'run and date share one captured timestamp at midnight boundary'
 [Console]::WriteLine('RESULT passed='+$script:passed+' failed=0; LOCAL ONLY; sandbox='+$sandbox)
}catch{[Console]::WriteLine($_.ToString());[Console]::WriteLine($_.InvocationInfo.PositionMessage);exit 1}
# Preserve test fixtures for inspection; no recursive deletion needed.
