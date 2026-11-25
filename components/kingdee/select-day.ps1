# PowerShell 2.0 compatible. Picks the newest dated ADF set that is complete and stable.
# Returns @{Day; Selected; Skipped} or $null. Future, empty and too-recent sets are skipped, not fatal.
function Select-BackupDay($found,$today,$minAgeMinutes,$now){
 $skipped=@()
 $days=@($found | ForEach-Object {[string]$_.Day} | Sort-Object -Descending -Unique)
 foreach($d in $days){
  if([datetime]$d -gt $today){$skipped+=('future '+$d);continue}
  $set=@($found | Where-Object {$_.Day -eq $d})
  $bad=@($set | Where-Object {$_.File.Length -le 0 -or $_.File.LastWriteTime -gt $now.AddMinutes(-[int]$minAgeMinutes)})
  if($bad.Count -gt 0){$skipped+=('unstable '+$d+' files='+$bad.Count);continue}
  return @{Day=$d;Selected=$set;Skipped=$skipped}
 }
 return $null
}
