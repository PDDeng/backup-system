$ErrorActionPreference='Stop'
$s=[IO.File]::ReadAllText((Join-Path $PSScriptRoot 'nexus-backup.ps1'))
$helper=[regex]::Match($s,"(?s)# BEGIN_LONGPATH_HELPER.*?@'\r?\n(.*?)\r?\n'@").Groups[1].Value
Add-Type -TypeDefinition $helper
Add-Type -TypeDefinition @'
using System;using System.Runtime.InteropServices;
public class NexusFixture {
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode)]public static extern bool CreateDirectoryW(string p,IntPtr a);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode)]public static extern bool RemoveDirectoryW(string p);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode)]public static extern bool DeleteFileW(string p);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode)]public static extern IntPtr CreateFileW(string p,uint a,uint share,IntPtr sa,uint creation,uint attrs,IntPtr template);
 [DllImport("kernel32.dll")]public static extern bool CloseHandle(IntPtr h);
}
'@
$root=Join-Path $env:TEMP ('nexus-longpath-'+[guid]::NewGuid().ToString('N'))
$dirs=New-Object Collections.ArrayList;$path=$root;$file=$null
try{
 for($i=0;$i -lt 16;$i++){
  if(-not [NexusFixture]::CreateDirectoryW(('\\?\'+$path),[IntPtr]::Zero)){throw 'Fixture directory creation failed'}
  [void]$dirs.Add($path);$path+='\segment1234567890'
 }
 $file=$dirs[$dirs.Count-1]+'\artifact.jar'
 if($file.Length -le 260){throw 'Fixture too short'}
 $h=[NexusFixture]::CreateFileW(('\\?\'+$file),1073741824,0,[IntPtr]::Zero,1,128,[IntPtr]::Zero)
 if($h -eq [IntPtr](-1)){throw 'Fixture file creation failed'}
 [void][NexusFixture]::CloseHandle($h)
 $entries=@([NexusLongPaths]::Scan($root))
 if($entries.Count -ne 1 -or $entries[0].FullName -ne $file -or [NexusLongPaths]::Size($file) -ne 0){throw 'Long path enumeration/size failed'}
 Write-Output ('Long path enumeration/size passed: '+$file.Length+' characters. No service or SMB operations.')
}finally{
 if($file){[void][NexusFixture]::DeleteFileW(('\\?\'+$file))}
 for($i=$dirs.Count-1;$i -ge 0;$i--){[void][NexusFixture]::RemoveDirectoryW(('\\?\'+$dirs[$i]))}
}
