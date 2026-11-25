# Dedicated LocalSystem SMB session helper. No database or service changes.
param([Parameter(Mandatory=$true)][string]$Config)
$ErrorActionPreference='Stop'
. (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) 'json-compat.ps1')
Add-Type -AssemblyName System.Security
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public class CoreOracleNas {
 [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)]
 public struct Resource {public int scope,type,display,usage; public string local,remote,comment,provider;}
 [DllImport("mpr.dll",CharSet=CharSet.Unicode)]
 public static extern int WNetAddConnection2(ref Resource r,string password,string user,int flags);
 public static int Connect(string share,string user,string password) {
   Resource r=new Resource();r.type=1;r.remote=share;
   return WNetAddConnection2(ref r,password,user,0);
 }
}
'@

$encoding=New-Object Text.UTF8Encoding($false)
$result=$Config+'.result.json'
try {
    $cipher=[Convert]::FromBase64String([IO.File]::ReadAllText($Config))
    $plain=[Security.Cryptography.ProtectedData]::Unprotect($cipher,$null,[Security.Cryptography.DataProtectionScope]::LocalMachine)
    $c=$json.DeserializeObject($encoding.GetString($plain))
    $rc=[CoreOracleNas]::Connect($c.Share,$c.User,$c.Password)
    $reuse=($rc -eq 1219 -and -not [Security.Principal.WindowsIdentity]::GetCurrent().IsSystem)
    if($rc -ne 0 -and -not $reuse){throw ('SMB connection error '+$rc+'; no existing sessions disconnected')}
    if($reuse){Write-Output 'ADMIN_REUSE_EXISTING_SMB_SESSION; existing credentials retained; write/read/delete probe required'}
    [void][IO.Directory]::CreateDirectory($c.AssetRoot)
    $probe=Join-Path $c.AssetRoot ('.system-probe-'+[Guid]::NewGuid().ToString('N'))
    [IO.File]::WriteAllText($probe,'oracle-local-system-test')
    if([IO.File]::ReadAllText($probe) -ne 'oracle-local-system-test'){throw 'SYSTEM readback mismatch'}
    [IO.File]::Delete($probe)
    $r=@{Status='ok';ReusedExistingSession=$reuse;Identity=[Security.Principal.WindowsIdentity]::GetCurrent().Name;Share=$c.Share;AssetRoot=$c.AssetRoot}
    [IO.File]::WriteAllText($result,$json.Serialize($r),$encoding)
}catch{
    [IO.File]::WriteAllText($result,$json.Serialize(@{Status='failed';Error=$_.Exception.Message}),$encoding)
    exit 1
}finally{
    if($plain){[Array]::Clear($plain,0,$plain.Length)}
    $c=$null
}
