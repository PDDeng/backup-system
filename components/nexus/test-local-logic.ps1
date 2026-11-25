# Developer test: Windows PowerShell 3+ AST only. Never stops a service or contacts NAS.
$ErrorActionPreference='Stop'
$source=[IO.File]::ReadAllText((Join-Path $PSScriptRoot 'nexus-backup.ps1'))
$helper=[regex]::Match($source,"(?s)# BEGIN_LONGPATH_HELPER.*?@'\r?\n(.*?)\r?\n'@").Groups[1].Value
Add-Type -TypeDefinition $helper
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot 'nexus-backup.ps1'),[ref]$tokens,[ref]$errors)
if($errors){throw 'Parse failed'}
foreach($name in @('Files','Manifest','Verify','CopyTree','StoragePath')){
 $def=$ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name},$true)
 . ([scriptblock]::Create($def.Extent.Text))
}
function Log([string]$s){}
$root=Join-Path $env:TEMP ('nexus-backup-test-'+[Guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory((Join-Path $root 'source\nested'))
try{
 [IO.File]::WriteAllText((Join-Path $root 'source\nested\a.txt'),'abc')
 $manifest=Manifest (Join-Path $root 'source')
 if($manifest.Count -ne 1 -or $manifest[0].Size -ne 3){throw 'Manifest wrong'}
 Verify (Join-Path $root 'source') $manifest
 [IO.File]::WriteAllText((Join-Path $root 'source\nested\a.txt'),'truncated')
 $rejected=$false;try{Verify (Join-Path $root 'source') $manifest}catch{$rejected=$true}
 if(-not $rejected){throw 'Size mismatch accepted'}
 $rejected=$false;try{CopyTree 'D:\source' 'D:\source' $true 1}catch{$rejected=$true}
 if(-not $rejected){throw 'Local destination accepted'}
 $rejected=$false;try{CopyTree 'D:\source' '\\server\share\completed\work' $true 1}catch{$rejected=$true}
 if(-not $rejected){throw 'Completed destination accepted'}
 $work='D:\nexus\nexus-2.14.11-01-bundle\sonatype-work\nexus'
 $actual=StoragePath 'file:/D:/nexus/nexus-2.14.11-01-bundle/sonatype-work/nexus/storage/gradle-plugin' $work
 if($actual -ne ($work+'\storage\gradle-plugin')){throw 'Internal file URI rejected'}
 $actual=StoragePath '${nexus-work}/storage/releases' $work
 if($actual -ne ($work+'\storage\releases')){throw 'Work variable resolution failed'}
 foreach($bad in @('D:\outside','D:\nexus\nexus-2.14.11-01-bundle\sonatype-work\nexus-other','${nexus-work}/../outside','file://server/share/repo')){
  $rejected=$false;try{$null=StoragePath $bad $work}catch{$rejected=$true}
  if(-not $rejected){throw ('External path accepted: '+$bad)}
 }
 Write-Output '10 local logic checks passed; no service, SMB or backup operations.'
}finally{
 # Exact developer-created temporary files only, no recursive removal.
 [IO.File]::Delete((Join-Path $root 'source\nested\a.txt'))
 [IO.Directory]::Delete((Join-Path $root 'source\nested'))
 [IO.Directory]::Delete((Join-Path $root 'source'))
 [IO.Directory]::Delete($root)
}
