# Compile embedded production C# using the installed .NET 2 compiler; no network.
$ErrorActionPreference='Stop'
$root=Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))
$compiler=Join-Path $env:SystemRoot 'Microsoft.NET\Framework\v2.0.50727\csc.exe'
if(-not [IO.File]::Exists($compiler)){throw 'NET2 compiler unavailable; compatibility not verified'}
$temp=Join-Path ([IO.Path]::GetTempPath()) ('svn-csc20-'+[guid]::NewGuid().ToString('N'));[void][IO.Directory]::CreateDirectory($temp)
$count=0
foreach($file in @('json-compat.ps1','svn-backup.ps1')){
 $source=[IO.File]::ReadAllText((Join-Path $root ('components\svn\'+$file)))
 foreach($match in [regex]::Matches($source,"(?s)@'\r?\n(.*?)\r?\n'@")){
  $cs=Join-Path $temp ($count.ToString()+'.cs');$dll=Join-Path $temp ($count.ToString()+'.dll')
  [IO.File]::WriteAllText($cs,$match.Groups[1].Value,(New-Object Text.UTF8Encoding($false)))
  & $compiler /nologo /target:library /warnaserror+ ('/out:'+$dll) $cs
  if($LASTEXITCODE -ne 0){throw ('NET2 compilation failed: '+$file)};$count++
 }
}
if($count -ne 2){throw 'Expected two embedded C# production helpers'}
[Console]::WriteLine('PASS csc20 compile helpers=2 rc=0; no production calls')
