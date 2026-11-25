$ErrorActionPreference='Stop'
. (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) 'json-compat.ps1')
$sample=@{Share='\\server\share';Password="test`"\line`n";Files=@(@{Size=47314135040;Name='backup.tar'});Enabled=$true;Empty=$null}
$sample.Wrapped=[System.Management.Automation.PSObject]::AsPSObject('wrapped-value')
$text=$json.Serialize($sample)
$round=$json.DeserializeObject($text)
if($round.Wrapped -cne 'wrapped-value' -or $round.Password -cne $sample.Password -or $round.Files[0].Size -ne 47314135040 -or -not $round.Enabled){throw 'JSON round trip failed'}
try {$json.DeserializeObject('{"a":1,"a":2}');throw 'Duplicate accepted'}catch{if($_.Exception.Message -eq 'Duplicate accepted'){throw}}
Write-Host 'JSON compatibility checks passed. No network or Oracle operations performed.'
