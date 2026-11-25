param([string]$TargetRoot='\\192.168.5.240\资源备份',[string]$RepositoryRoot='D:\Repositories',[string]$SvnBin='C:\Program Files\VisualSVN Server\bin',[string]$PrivateRoot=(Join-Path $env:ProgramData 'CoreSvnBackup'),[int]$KeepCycles=15,[switch]$SetupCredentials,[switch]$CheckOnly,[switch]$SmallTest,[switch]$SkipCleanup)
$ErrorActionPreference='Stop'
$origin=Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $origin 'json-compat.ps1')
. (Join-Path $origin 'svn-core.ps1')
if($TargetRoot -notmatch '^\\\\[^\\/]+\\[^\\/]+\\?$'){throw 'TargetRoot must be an exact UNC share'}
if($KeepCycles -lt 1){throw 'KeepCycles must be positive'}
$null=Assert-SvnPlainPath $PrivateRoot;[void][IO.Directory]::CreateDirectory($PrivateRoot)
# Machine DPAPI requires an ACL restricting access to SYSTEM and Administrators.
$acl=New-Object Security.AccessControl.DirectorySecurity;$acl.SetAccessRuleProtection($true,$false)
foreach($sid in @('S-1-5-18','S-1-5-32-544')){$identity=New-Object Security.Principal.SecurityIdentifier($sid);$rule=New-Object Security.AccessControl.FileSystemAccessRule($identity,'FullControl','ContainerInherit,ObjectInherit','None','Allow');[void]$acl.AddAccessRule($rule)}
Set-Acl -Path $PrivateRoot -AclObject $acl
if($SetupCredentials){$credentialLock=[IO.File]::Open((Join-Path $PrivateRoot 'svn-backup.lock'),'OpenOrCreate','ReadWrite','None');try{$credential=Get-Credential;Save-SvnCredential (Join-Path $PrivateRoot 'nas240.dpapi') $credential;Write-SvnLog $PrivateRoot 'Machine encrypted NAS credential saved; no backup started'}finally{$credentialLock.Close()};exit 0}
if(-not ('SvnNative' -as [type])){Add-Type -TypeDefinition @'
using System;using System.Runtime.InteropServices;
public class SvnNative {
 [StructLayout(LayoutKind.Sequential,CharSet=CharSet.Unicode)] public struct Resource{public int scope,type,display,usage;public string local,remote,comment,provider;}
 [DllImport("mpr.dll",CharSet=CharSet.Unicode)] static extern int WNetAddConnection2(ref Resource r,string p,string u,int flags);
 public static int Connect(string share,string u,string p){Resource r=new Resource();r.type=1;r.remote=share;return WNetAddConnection2(ref r,p,u,0);}
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]public static extern bool GetDiskFreeSpaceEx(string path,out ulong available,out ulong total,out ulong free);
}
'@}
$script:svnBin=$SvnBin;$script:privateForTool=$PrivateRoot
$adapters=@{
 Connect={param($target,$credentialPath) foreach($name in @('svnadmin.exe','svnlook.exe')){if(-not [IO.File]::Exists((Join-Path $script:svnBin $name))){throw ('Missing SVN tool: '+$name)}};Connect-SvnSmb $target $credentialPath}
 Free={param($target) [UInt64]$a=0;[UInt64]$t=0;[UInt64]$f=0;if(-not [SvnNative]::GetDiskFreeSpaceEx($target,[ref]$a,[ref]$t,[ref]$f)){throw 'Cannot read SMB free space'};return $a}
 Tool={param($tool,$arguments) $savedPreference=$ErrorActionPreference;try{$ErrorActionPreference='Continue';$output=& (Join-Path $script:svnBin ($tool+'.exe')) @arguments 2>&1;$rc=$LASTEXITCODE}finally{$ErrorActionPreference=$savedPreference};foreach($line in $output){Write-SvnLog $script:privateForTool ([string]$line)};if($rc -ne 0){throw ('SVN tool failed rc='+$rc+' tool='+$tool)};return @($output | ForEach-Object {[string]$_})}
}
$config=@{TargetRoot=$TargetRoot;RepositoryRoot=$RepositoryRoot;PrivateRoot=$PrivateRoot;Asset='svn01_192.168.0.29_svn代码库';KeepCycles=$KeepCycles;SkipCleanup=[bool]$SkipCleanup;SmallRepository='研发一部';MinFreeBytes=140GB;SmallMinFreeBytes=1GB}
$rc=Invoke-SvnRun $config $adapters ([bool]$CheckOnly) ([bool]$SmallTest)
exit $rc
