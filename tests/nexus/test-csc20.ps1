$ErrorActionPreference='Stop'
# Compiles the embedded C# of components/nexus/pax-tar.ps1 with the .NET 2.0 compiler (C# 2.0 language level).
$component=Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) '..\..\components\nexus'
$source=[IO.File]::ReadAllText((Join-Path $component 'pax-tar.ps1'))
$code=[regex]::Match($source,"(?s)Add-Type -TypeDefinition @'\r?\n(.*?)\r?\n'@").Groups[1].Value
if(-not $code){Write-Output 'FAIL embedded C# block not found';exit 1}
$work=Join-Path $env:TEMP ('nexus-csc20-'+[Guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($work)
try{
 $cs=Join-Path $work 'NexusPax.cs';$dll=Join-Path $work 'NexusPax.dll'
 [IO.File]::WriteAllText($cs,$code,(New-Object Text.UTF8Encoding($false)))
 $csc=Join-Path $env:SystemRoot 'Microsoft.NET\Framework\v2.0.50727\csc.exe'
 if(-not (Test-Path -LiteralPath $csc)){$csc=Join-Path $env:SystemRoot 'Microsoft.NET\Framework64\v2.0.50727\csc.exe'}
 if(-not (Test-Path -LiteralPath $csc)){Write-Output 'FAIL .NET 2.0 csc.exe not found';exit 1}
 $out=& $csc /nologo /target:library /warnaserror+ /nowarn:1591 "/out:$dll" $cs 2>&1
 $rc=$LASTEXITCODE
 $out | ForEach-Object { Write-Output ('csc: '+$_) }
 if($rc -ne 0 -or -not (Test-Path -LiteralPath $dll)){Write-Output ('FAIL csc20 compile rc='+$rc);exit 1}
 Write-Output ('PASS csc20 compile rc=0 using '+$csc)
}finally{
 if([IO.File]::Exists((Join-Path $work 'NexusPax.dll'))){[IO.File]::Delete((Join-Path $work 'NexusPax.dll'))}
 if([IO.File]::Exists((Join-Path $work 'NexusPax.cs'))){[IO.File]::Delete((Join-Path $work 'NexusPax.cs'))}
 [IO.Directory]::Delete($work,$false)
}
