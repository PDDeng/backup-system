# Direct export to the NAS directory prepared/proven by prepare-direct-nas.ps1.
param([Parameter(Mandatory=$true)][string]$Prepared)
$ErrorActionPreference='Stop'
. (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) 'json-compat.ps1')

$utf8=New-Object Text.UTF8Encoding($false)
$c=$json.DeserializeObject([IO.File]::ReadAllText($Prepared))
if(@('41','46') -notcontains $c.Server){throw 'Invalid server'}
if($c.Run -notmatch '^[0-9]{8}_[0-9]{6}-[a-f0-9]{4}$' -or $c.DirectoryObject -notmatch '^CORE(41|46)_[0-9_]+[a-f0-9]{4}$'){throw 'Invalid prepared run'}
$expectedHome='D:\app\administrator\product\11.2.0\dbhome_1';$expectedSid='ORCL'
if($c.Server -eq '41'){$expectedHome='D:\app\administrator\product\11.2.0\dbhome_2';$expectedSid='YUANQI'}
if($c.OracleHome -ine $expectedHome -or $c.Sid -ine $expectedSid){throw 'Prepared instance mismatch'}
$env:ORACLE_HOME=$expectedHome;$env:ORACLE_SID=$expectedSid
function Query([string]$sql){
    $info=New-Object Diagnostics.ProcessStartInfo
    $info.FileName=Join-Path $expectedHome 'bin\sqlplus.exe';$info.Arguments='-L -S "/ as sysdba"'
    $info.UseShellExecute=$false;$info.RedirectStandardInput=$true;$info.RedirectStandardOutput=$true;$info.RedirectStandardError=$true;$info.CreateNoWindow=$true
    $p=New-Object Diagnostics.Process;$p.StartInfo=$info
    try{
        [void]$p.Start();$p.StandardInput.WriteLine("whenever sqlerror exit failure`nset heading off feedback off pagesize 0`n"+$sql+"`nexit");$p.StandardInput.Close()
        if(-not $p.WaitForExit(30000)){$p.Kill();throw 'Read-only query client timeout'}
        $out=$p.StandardOutput.ReadToEnd()
        if($p.ExitCode -ne 0){throw ('Read-only query failed: '+$out)}
        return $out.Trim()
    }finally{$p.Dispose()}
}
Add-Type -TypeDefinition @'
using System;
using System.Net;
using System.Runtime.InteropServices;
public class CoreDirectNasSpace {
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]
 public static extern bool GetDiskFreeSpaceEx(string path,out ulong available,out ulong total,out ulong free);
}
public class CoreDirectWeb : WebClient {
 protected override WebRequest GetWebRequest(Uri u) { WebRequest r=base.GetWebRequest(u); r.Timeout=10000; return r; }
}
'@
function Notice([string]$event,[string]$text){
    if(@('SUMMARY','CRITICAL') -notcontains $event){Write-Output ('NOTICE_LOCAL '+$event+' '+$text);return}
    Write-Output ((Get-Date -Format o)+' '+$event+' '+$text)
    try{
        $n=$json.DeserializeObject([IO.File]::ReadAllText('C:\core-backup\notify.json'))
        if($n.Enabled -eq $false){return}
        if(-not $n.WebhookUrl.StartsWith('https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=')){throw 'Invalid webhook'}
        [Net.ServicePointManager]::SecurityProtocol=[Net.ServicePointManager]::SecurityProtocol -bor 3072
        $web=New-Object CoreDirectWeb
        try{
            $web.Headers['Content-Type']='application/json; charset=utf-8'
            $reply=$web.UploadData($n.WebhookUrl,'POST',$utf8.GetBytes($json.Serialize(@{msgtype='text';text=@{content=('ORACLE NAS '+$c.Server+' '+$event+' '+$text)}})))
            $answer=$json.DeserializeObject($utf8.GetString($reply))
            if(-not $answer.ContainsKey('errcode') -or $answer.errcode -ne 0){throw 'Webhook rejected'}
        }finally{$web.Dispose()}
    }catch{Write-Warning 'NOTIFY_FAILED; direct NAS backup result must be checked locally'}
}
$timer=[Diagnostics.Stopwatch]::StartNew()
$lock=$null
try{
    $lock=[IO.File]::Open((Join-Path (Split-Path -Parent $Prepared) 'export.lock'),'OpenOrCreate','ReadWrite','None')
    $invalid=Query "select count(*) from dba_objects where owner='EXFSYS' and object_name in ('DBMS_EXPFIL_EXP','DBMS_EXPFIL_DEPASEXP') and status <> 'VALID';"
    if([int]$invalid -gt 0){throw 'EXFSYS export packages invalid; refused full export; no packages changed'}
    $busy=Query "select count(*) from dba_datapump_jobs where operation='EXPORT' and state='EXECUTING';"
    if([int]$busy -gt 0){throw 'Another export is active; no duplicate started'}
    if(-not (Test-Path -LiteralPath $c.Pending)){throw 'Prepared NAS folder unavailable'}
    if(@(Get-ChildItem -LiteralPath $c.Pending -Filter '*.dmp').Count -gt 0){throw 'Dump files already present; do not overwrite/restart blindly'}
    [UInt64]$available=0;[UInt64]$total=0;[UInt64]$free=0
    if(-not [CoreDirectNasSpace]::GetDiskFreeSpaceEx(($c.Pending+'\'),[ref]$available,[ref]$total,[ref]$free)){throw 'Cannot query NAS free space'}
    $need=200GB;if($c.Server -eq '41'){$need=100GB}
    if($available -lt $need){throw 'NAS free space below first-backup threshold'}
    Notice 'START' ('Direct NAS export; run='+$c.Run+'; no local dump staging')
    & (Join-Path $expectedHome 'bin\expdp.exe') ('PARFILE='+$c.ParameterFile)
    if($LASTEXITCODE -ne 0){throw ('Data Pump errors rc='+$LASTEXITCODE+'; partial files retained, no successful publication')}
    $files=@(Get-ChildItem -LiteralPath $c.Pending -Filter 'full_*.dmp')
    if($files.Count -eq 0 -or @($files|Where-Object {$_.Length -le 0}).Count -gt 0){throw 'Missing/empty dump files'}
    $receipt=@{Type='oracle-full-logical';Server=$c.Server;Sid=$c.Sid;Run=$c.Run;Verification='pending_md5';Files=@();Seconds=$timer.Elapsed.TotalSeconds}
    foreach($f in $files){$receipt.Files+=@{Name=$f.Name;Size=$f.Length}}
    $meta=Join-Path $c.Pending 'pending.json'
    [IO.File]::WriteAllText($meta,$json.Serialize($receipt),$utf8)
    $final=Join-Path (Split-Path -Parent $c.Pending) $c.Run
    if(Test-Path -LiteralPath $final){throw 'Final destination already exists'}
    Move-Item -LiteralPath $c.Pending -Destination $final
    Notice 'SUMMARY' ('【Oracle'+$c.Server+"全库逻辑备份｜成功】`n243：成功；240/254：尚未分发`n导出：退出码0；MD5：未执行`n清理：未启用`n耗时："+[int]$timer.Elapsed.TotalSeconds+"秒`n目录："+$final)
}catch{
    Notice 'SUMMARY' ('【Oracle'+$c.Server+"备份｜失败】`n原因："+$_.Exception.Message+"`n未发布完整备份，临时文件保留。")
    Write-Error $_ -ErrorAction Continue
    exit 1
}finally{if($lock){$lock.Dispose()}}
