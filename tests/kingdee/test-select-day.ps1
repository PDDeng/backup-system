$ErrorActionPreference='Stop'
. (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) '..\..\components\kingdee\select-day.ps1')
$tmp=Join-Path ([IO.Path]::GetTempPath()) ('kingdee-select-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tmp | Out-Null
$now=Get-Date;$today=$now.Date;$minAge=60;$fail=0
function Day($offset){return $today.AddDays($offset).ToString('yyyy-MM-dd')}
function Item($name,$size,$ageMinutes,$offset){
 $p=Join-Path $tmp $name;[IO.File]::WriteAllBytes($p,(New-Object byte[] $size))
 $f=Get-Item -LiteralPath $p;$f.LastWriteTime=$now.AddMinutes(-$ageMinutes)
 return @{File=$f;Day=(Day $offset)}
}
function Check($name,$cond){if($cond){Write-Host ('PASS '+$name)}else{Write-Host ('FAIL '+$name);$script:fail++}}
try{
 $p=Select-BackupDay @((Item 'a_20261124.ADF' 10 120 -1),(Item 'b_20261124.ADF' 10 120 -1)) $today $minAge $now
 Check 'complete newest day selected' ($p.Day -eq (Day -1) -and $p.Selected.Count -eq 2)
 $p=Select-BackupDay @((Item 'c_20261126.ADF' 10 120 1),(Item 'd_20261124.ADF' 10 120 -1)) $today $minAge $now
 Check 'future date skipped and reported' ($p.Day -eq (Day -1) -and ($p.Skipped -contains ('future '+(Day 1))))
 $p=Select-BackupDay @((Item 'e_20261125.ADF' 10 120 0),(Item 'f_20261125.ADF' 0 120 0),(Item 'g_20261124.ADF' 10 120 -1)) $today $minAge $now
 Check '0-byte file in newest day falls back to previous complete day' ($p.Day -eq (Day -1) -and ($p.Skipped -contains ('unstable '+(Day 0)+' files=1')))
 $p=Select-BackupDay @((Item 'h_20261125.ADF' 10 5 0),(Item 'i_20261124.ADF' 10 120 -1)) $today $minAge $now
 Check 'still-writing newest day falls back' ($p.Day -eq (Day -1))
 $p=Select-BackupDay @((Item 'j_20261125.ADF' 10 120 0),(Item 'k_20261125.ADF' 10 5 0),(Item 'l_20261124.ADF' 10 120 -1)) $today $minAge $now
 Check 'one fresh account file blocks the whole newest day' ($p.Day -eq (Day -1) -and $p.Selected.Count -eq 1)
 $p=Select-BackupDay @((Item 'm_20261124.ADF' 10 120 -3),(Item 'n_20261123.ADF' 10 120 -2),(Item 'o_20261122.ADF' 10 120 -4)) $today $minAge $now
 Check 'newest by date, not by name order' ($p.Day -eq (Day -2) -and $p.Selected.Count -eq 1)
 $p=Select-BackupDay @((Item 'p_20261126.ADF' 10 120 1),(Item 'q_20261125.ADF' 0 120 0)) $today $minAge $now
 Check 'no usable set returns null' ($p -eq $null)
 $p=Select-BackupDay @() $today $minAge $now
 Check 'empty input returns null' ($p -eq $null)
}finally{Remove-Item -LiteralPath $tmp -Recurse -Force}
if($fail -gt 0){Write-Host ('FAILED '+$fail);exit 1}
Write-Host 'select-day checks passed. No NAS, network or production operations performed.'
