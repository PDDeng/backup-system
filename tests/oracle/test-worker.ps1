# Pure local PS2-compatible checks. Never starts Oracle, WMI, SSH or NAS access.
$ErrorActionPreference='Stop'
$repo=Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))
$tools=Join-Path $repo 'components\oracle\restore'
. (Join-Path $tools '..\json-compat.ps1')
if(-not ('OracleRestoreNative' -as [type])){Add-Type -Path (Join-Path $tools 'native-process.cs')}
$j=New-Object CoreNasJson
$temp=Join-Path ([IO.Path]::GetTempPath()) ('oracle-unit-'+[Guid]::NewGuid().ToString('N'))
[IO.Directory]::CreateDirectory($temp)|Out-Null
$count=0
function Assert($v,[string]$message){if(-not $v){throw $message};$script:count++}
try{
 $a=@{schema_version=1;production_change=$false;phases=@{table_checks=@{status='passed';log='verify.log'}};files=@('a','b')}
 $round=$j.DeserializeObject($j.Serialize($a))
 Assert ($round['production_change'] -eq $false) 'Boolean round trip'
 Assert ($round['files'].Length -eq 2) 'Array round trip'
 Assert ($round['phases']['table_checks']['log'] -eq 'verify.log') 'Nested contract'
 $argvExe=Join-Path $temp 'argv.exe'
 Add-Type -TypeDefinition 'public class ArgsEcho { public static void Main(string[] args) { if(args.Length==1 && args[0]=="--fail") {System.Console.WriteLine("STDOUT");System.Console.Error.WriteLine("STDERR");System.Environment.Exit(7);} foreach(string arg in args)System.Console.WriteLine("ARG="+arg); } }' -OutputAssembly $argvExe -OutputType ConsoleApplication
 $argsToTest=@('plain','two words','D:\tail\','a"b','')
 $log=Join-Path $temp 'args.log'
 $rc=[OracleRestoreNative]::Run($argvExe,$argsToTest,$log)
 Assert ($rc -eq 0) 'Native return code'
 $lines=[IO.File]::ReadAllLines($log)
 Assert ($lines.Length -eq $argsToTest.Length) 'All arguments preserved'
 for($i=0;$i -lt $argsToTest.Length;$i++){Assert ($lines[$i] -eq ('ARG='+$argsToTest[$i])) ('Argument '+$i)}
 $log2=Join-Path $temp 'dual.log'
 $rc=[OracleRestoreNative]::Run($argvExe,@('--fail'),$log2)
 Assert ($rc -eq 7) 'Failure return code retained'
 $output=[IO.File]::ReadAllText($log2)
 Assert ($output.Contains('STDOUT') -and $output.Contains('STDERR')) 'Both native streams logged'
 $failed=$false
 try{& (Join-Path $tools 'invoke-restore.ps1') -PlanDirectory $temp}catch{$failed=$_.Exception.Message.Contains('PREPARED_ONLY')}
 Assert $failed 'No Execute refuses before machine/Oracle access'
 $config=@{production_change=$false;config=@{target_ip='10.10.10.251';id='oracle46';sid='RT46';target_hostname=('WRONG-'+[Guid]::NewGuid().ToString('N'))};manifest=@{}}
 [IO.File]::WriteAllText((Join-Path $temp 'plan.json'),$j.Serialize($config))
 $failed=$false
 try{& (Join-Path $tools 'invoke-restore.ps1') -PlanDirectory $temp -Execute}catch{$failed=$_.Exception.Message.Contains('Wrong test hostname')}
 Assert $failed 'Wrong host refuses before WMI/Oracle/NAS access'
 Write-Output ('PASS assertions='+$count+'; local only; PS='+$PSVersionTable.PSVersion)
}finally{
 # Only delete generated fixtures beneath the verified unique temp directory.
 if($temp.StartsWith([IO.Path]::GetTempPath()) -and [IO.Path]::GetFileName($temp).StartsWith('oracle-unit-')){[IO.Directory]::Delete($temp,$true)}
}
