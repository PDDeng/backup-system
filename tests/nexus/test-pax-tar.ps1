$ErrorActionPreference='Stop'
$component=Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) '..\..\components\nexus'
. (Join-Path $component 'pax-tar.ps1')
Add-Type -TypeDefinition @'
using System;using System.Runtime.InteropServices;
public class PaxFixture {
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode)]public static extern bool CreateDirectoryW(string p,IntPtr a);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode)]public static extern bool RemoveDirectoryW(string p);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode)]public static extern bool DeleteFileW(string p);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode)]public static extern IntPtr CreateFileW(string p,uint a,uint share,IntPtr sa,uint creation,uint attrs,IntPtr template);
 [DllImport("kernel32.dll")]public static extern bool WriteFile(IntPtr h,byte[] b,uint n,out uint written,IntPtr ov);
 [DllImport("kernel32.dll")]public static extern bool CloseHandle(IntPtr h);
}
'@
$script:pass=0;$script:fail=0
function Check([string]$name,[bool]$cond){if($cond){Write-Output ('PASS '+$name);$script:pass++}else{Write-Output ('FAIL '+$name);$script:fail++}}
$root=Join-Path $env:TEMP ('nexus-pax-test-'+[Guid]::NewGuid().ToString('N'))
$src=Join-Path $root 'src';$src2=Join-Path $root 'src2';$out=Join-Path $root 'out';$ext=Join-Path $root 'extract'
$files=New-Object Collections.ArrayList;$dirs=New-Object Collections.ArrayList;$longDirs=New-Object Collections.ArrayList
$longFile=$null;$lockFs=$null
$cn=-join [char[]](0x4E2D,0x6587)
function MakeFile([string]$path,[byte[]]$bytes){[IO.File]::WriteAllBytes($path,$bytes);[void]$files.Add($path)}
try{
 [void][IO.Directory]::CreateDirectory($src);[void]$dirs.Add($src)
 [void][IO.Directory]::CreateDirectory($out);[void]$dirs.Add($out)
 [void][IO.Directory]::CreateDirectory($src2);[void]$dirs.Add($src2)
 MakeFile (Join-Path $src 'small.txt') ([Text.Encoding]::ASCII.GetBytes('abc'))
 MakeFile (Join-Path $src ($cn+'.txt')) ([Text.Encoding]::UTF8.GetBytes('chinese name'))
 MakeFile (Join-Path $src 'empty.bin') (New-Object byte[] 0)
 $rnd=New-Object Random 42;$big=New-Object byte[] (1MB);$rnd.NextBytes($big)
 MakeFile (Join-Path $src 'bin.dat') $big
 MakeFile (Join-Path $src 'locked.txt') ([Text.Encoding]::ASCII.GetBytes('lock'))
 # Long path (> 260 chars) fixture, written through \\?\ as in test-longpath.ps1.
 $path=$src
 for($i=0;$i -lt 16;$i++){
  $path+='\segment1234567890'
  if(-not [PaxFixture]::CreateDirectoryW(('\\?\'+$path),[IntPtr]::Zero)){throw 'Long path directory creation failed'}
  [void]$longDirs.Add($path)
 }
 $longFile=$path+'\artifact.jar'
 $h=[PaxFixture]::CreateFileW(('\\?\'+$longFile),0x40000000,0,[IntPtr]::Zero,1,0x80,[IntPtr]::Zero)
 if($h -eq [IntPtr](-1)){throw 'Long path file creation failed'}
 $content=[Text.Encoding]::ASCII.GetBytes('long-path-content');$written=[uint32]0
 [void][PaxFixture]::WriteFile($h,$content,[uint32]$content.Length,[ref]$written,[IntPtr]::Zero)
 [void][PaxFixture]::CloseHandle($h)
 $rels=@([NexusPaxArchiver]::ListFiles($src))
 Check 'fixture has six files including long path' ($rels.Count -eq 6 -and (@($rels | Where-Object {$_.Length -gt 240}).Count -eq 1))

 # A: archive, reader, writer checksum, snapshot
 $tarA=Join-Path $out 'work-preseed.tar'
 $pre=Write-NexusArchive $src $tarA 'work/' $rels 0 $false
 [void]$files.Add($tarA)
 Check 'writer counts every file' ($pre.Entries -eq 6)
 Check 'writer snapshot covers every file' ($pre.Snapshot.Count -eq 6)
 Check 'writer size equals tar file length' ($pre.Bytes -eq (Get-Item -LiteralPath $tarA).Length)
 Check 'writer sha256 matches written file' ($pre.Sha256 -eq (Get-NexusSha256 $tarA))
 $entries=@([NexusPaxReader]::List($tarA))
 $got=@($entries | ForEach-Object {[string]$_.Name}) | Sort-Object
 $want=@($rels | ForEach-Object {'work/'+$_}) | Sort-Object
 Check 'reader lists exact names (pax path for long/unicode names)' (($got -join '|') -eq ($want -join '|'))
 $prefixOk=$true;foreach($e in $entries){if($e.Name -notlike 'work/*'){$prefixOk=$false}}
 Check 'reader prefix is work/' $prefixOk
 $utf8Name=[Text.Encoding]::UTF8.GetBytes('work/'+$cn+'.txt');$tarBytes=[IO.File]::ReadAllBytes($tarA);$utf8Found=$false
 for($i=0;$i -le $tarBytes.Length-$utf8Name.Length;$i++){$m=$true;for($j=0;$j -lt $utf8Name.Length;$j++){if($tarBytes[$i+$j] -ne $utf8Name[$j]){$m=$false;break}};if($m){$utf8Found=$true;break}}
 Check 'unicode name stored as UTF-8 bytes' $utf8Found
 $longEntry=@($entries | Where-Object {$_.Name.Length -gt 260})
 Check 'long entry name longer than 260 characters' ($longEntry.Count -eq 1 -and [long]$longEntry[0].Size -eq $content.Length)

 # B: interoperability with Windows bsdtar (same format family as Linux tar -tf)
 $tar=Join-Path $env:SystemRoot 'System32\tar.exe'
 if(Test-Path -LiteralPath $tar){
  $listing=@(& $tar -tf $tarA)
  Check 'tar.exe lists all six entries' ($listing.Count -eq 6)
  New-Item -ItemType Directory -Path $ext -Force | Out-Null;[void]$dirs.Add($ext)
  & $tar -xf $tarA -C $ext 2>$null
  $shortOk=((Get-NexusSha256 (Join-Path $ext 'work\small.txt')) -eq (Get-NexusSha256 (Join-Path $src 'small.txt'))) -and ((Get-NexusSha256 (Join-Path $ext 'work\bin.dat')) -eq (Get-NexusSha256 (Join-Path $src 'bin.dat')))
  Check 'tar.exe extraction reproduces binary and text content' $shortOk
  $unicodeExtracted=Join-Path $ext ('work\'+$cn+'.txt')
  Check 'tar.exe extraction restores unicode file name with identical content' ((Test-Path -LiteralPath $unicodeExtracted) -and ((Get-NexusSha256 $unicodeExtracted) -eq (Get-NexusSha256 (Join-Path $src ($cn+'.txt')))))
 }else{Check 'tar.exe present for interoperability check' $false}

 # C: corrupted header rejected
 $bytes=[IO.File]::ReadAllBytes($tarA);$bytes[1]=[byte]($bytes[1] -bxor 0x55)
 $corrupt=Join-Path $out 'corrupt.tar';[IO.File]::WriteAllBytes($corrupt,$bytes);[void]$files.Add($corrupt)
 $rejected=$false;try{[void][NexusPaxReader]::List($corrupt)}catch{$rejected=$true}
 Check 'corrupted header checksum rejected' $rejected

 # D: deadline rejects writes past the budget
 $deadlineTar=Join-Path $out 'deadline.tar'
 $rejected=$false;try{[void](Write-NexusArchive $src $deadlineTar 'work/' $rels 1 $false)}catch{$rejected=$true}
 [void]$files.Add($deadlineTar)
 Check 'deadline exceeded rejected' $rejected

 # E: locked file recorded and picked up by the stopped delta
 MakeFile (Join-Path $src2 'small.txt') ([Text.Encoding]::ASCII.GetBytes('abc'))
 MakeFile (Join-Path $src2 'del.txt') ([Text.Encoding]::ASCII.GetBytes('gone'))
 MakeFile (Join-Path $src2 'keep.bin') ([Text.Encoding]::ASCII.GetBytes('keep'))
 MakeFile (Join-Path $src2 'locked.txt') ([Text.Encoding]::ASCII.GetBytes('lock'))
 $lockFs=[IO.File]::Open((Join-Path $src2 'locked.txt'),[IO.FileMode]::Open,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
 $tarPre2=Join-Path $out 'pre2.tar';[void]$files.Add($tarPre2)
 $pre2=Write-NexusArchive $src2 $tarPre2 'work/' ([NexusPaxArchiver]::ListFiles($src2)) 0 $true
 Check 'hot pass records locked file instead of skipping silently' ($pre2.Locked -contains 'locked.txt' -and $pre2.Entries -eq 3)
 $lockFs.Dispose();$lockFs=$null
 [IO.File]::WriteAllBytes((Join-Path $src2 'small.txt'),[Text.Encoding]::ASCII.GetBytes('abcdef'))
 [IO.File]::WriteAllBytes((Join-Path $src2 'new.txt'),[Text.Encoding]::ASCII.GetBytes('new'));[void]$files.Add((Join-Path $src2 'new.txt'))
 [IO.File]::Delete((Join-Path $src2 'del.txt'))
 $changed=New-Object Collections.ArrayList;$deleted=New-Object Collections.ArrayList
 [NexusPaxArchiver]::Delta($src2,$pre2.Snapshot,$pre2.Locked,$changed,$deleted,0)
 Check 'delta picks modified, new and forced locked files' (($changed -contains 'small.txt') -and ($changed -contains 'new.txt') -and ($changed -contains 'locked.txt') -and -not ($changed -contains 'keep.bin'))
 Check 'delta lists only deleted paths' (($deleted.Count -eq 1) -and ($deleted[0] -eq 'del.txt'))
 $tarDelta=Join-Path $out 'work-delta.tar';[void]$files.Add($tarDelta)
 $delta=Write-NexusArchive $src2 $tarDelta 'work/' $changed 0 $false
 $dn=@([NexusPaxReader]::List($tarDelta) | ForEach-Object {[string]$_.Name}) | Sort-Object
 $dw=@($changed | ForEach-Object {'work/'+$_}) | Sort-Object
 Check 'delta archive contains exactly the changed set' (($dn -join '|') -eq ($dw -join '|'))
 Check 'delta archive has no unchanged keep.bin' (-not ($dn -contains 'work/keep.bin'))

 # F: receipt whitelist for pax-tar-v2 and legacy receipts
 $names=@(Get-NexusReceiptNames @{Format='pax-tar-v2';Archives=@(@{Name='work-preseed.tar'},@{Name='deleted-paths.json'})})
 Check 'receipt whitelist lists backup-result and archives' (($names -contains 'backup-result.json') -and ($names -contains 'work-preseed.tar') -and ($names -contains 'deleted-paths.json') -and $names.Count -eq 3)
 $rejected=$false;try{[void](Get-NexusReceiptNames @{Format='pax-tar-v2';Archives=@(@{Name='..\x.tar'})})}catch{$rejected=$true}
 Check 'receipt rejects path traversal archive name' $rejected
 $rejected=$false;try{[void](Get-NexusReceiptNames @{Format='pax-tar-v9';Archives=@()})}catch{$rejected=$true}
 Check 'receipt rejects unknown format' $rejected
 Check 'legacy receipt without format is not handled by pax whitelist' ($null -eq (Get-NexusReceiptNames @{Owner='nexus-direct-v1';Work=@()}))

 # H: hot transient handling, truncated source padding, mtime clamp, deadline in delta, '..' receipt rejection
 $ghostLocked=New-Object Collections.ArrayList;$ghostMs=New-Object IO.MemoryStream;$ghostW=New-Object NexusPaxWriter -ArgumentList $ghostMs
 $ghostSnap=[NexusPaxArchiver]::ArchiveNames($src,'work/',$ghostW,@('ghost.txt'),$ghostLocked,$true)
 Check 'hot pass skips a vanished file without failing' (($ghostSnap.Count -eq 0) -and ($ghostLocked.Count -eq 0))
 $rejected=$false;try{[void][NexusPaxArchiver]::ArchiveNames($src,'work/',$ghostW,@('ghost.txt'),$ghostLocked,$false)}catch{$rejected=$true}
 Check 'stopped pass rejects a vanished file' $rejected
 $ghostW.Dispose();$ghostMs.Dispose()
 $shortMs=New-Object IO.MemoryStream;$shortW=New-Object NexusPaxWriter -ArgumentList $shortMs
 $shortSrc=New-Object IO.MemoryStream;$shortSrc.Write([byte[]](1,2,3),0,3);$shortSrc.Position=0
 $padded=$shortW.AddFile('short.bin',$shortSrc,10,-5)
 [void]$shortW.AddFile('late.bin',(New-Object IO.MemoryStream),0,9999999999999)
 $shortW.Finish()
 $shortTar=Join-Path $out 'short.tar';[IO.File]::WriteAllBytes($shortTar,$shortMs.ToArray());[void]$files.Add($shortTar)
 $shortEntries=@([NexusPaxReader]::List($shortTar))
 Check 'truncated source is zero-padded to the declared length and reported' (($padded -eq 7) -and ($shortEntries[0].Size -eq 10) -and ($shortEntries[0].Mtime -eq 0))
 Check 'mtime above the tar range is clamped' ($shortEntries[1].Mtime -eq 8589934591)
 $shortW.Dispose();$shortMs.Dispose();$shortSrc.Dispose()
 $rejected=$false;try{[void](Get-NexusReceiptNames @{Format='pax-tar-v2';Archives=@(@{Name='..'})})}catch{$rejected=$true}
 Check 'receipt rejects dot-dot archive name' $rejected
 $rejected=$false;try{[void][NexusPaxArchiver]::Delta($src2,$pre2.Snapshot,$pre2.Locked,(New-Object Collections.ArrayList),(New-Object Collections.ArrayList),1)}catch{$rejected=$true}
 Check 'stopped delta honours its deadline' $rejected

 # G: archive size gate used before publishing and by Clean
 $sizeDir=Join-Path $out 'sizes';[void][IO.Directory]::CreateDirectory($sizeDir);[void]$dirs.Add($sizeDir)
 MakeFile (Join-Path $sizeDir 'a.tar') ([Text.Encoding]::ASCII.GetBytes('12345'))
 $rejected=$false;try{Test-NexusArchiveSizes $sizeDir @(@{Name='a.tar';Bytes=5})}catch{$rejected=$true}
 Check 'archive size gate accepts matching size' (-not $rejected)
 $rejected=$false;try{Test-NexusArchiveSizes $sizeDir @(@{Name='a.tar';Bytes=6})}catch{$rejected=$true}
 Check 'archive size gate rejects size mismatch' $rejected
 $rejected=$false;try{Test-NexusArchiveSizes $sizeDir @(@{Name='missing.tar';Bytes=1})}catch{$rejected=$true}
 Check 'archive size gate rejects missing archive' $rejected

 # I: Win32 byte-range lock (ERROR_LOCK_VIOLATION=0x80070021) during FileStream.Read, mid-file lock, non-Win32 IOException, and vanished-lock deletion
 $brSrc=Join-Path $root 'br-src';[void][IO.Directory]::CreateDirectory($brSrc);[void]$dirs.Add($brSrc)
 $brFile=Join-Path $brSrc 'range.lock';MakeFile $brFile ([Text.Encoding]::ASCII.GetBytes('LOCKED-PAYLOAD-1234'))
 $brMid=Join-Path $brSrc 'mid.lock';$midBytes=New-Object byte[] 1200000;for($i=0;$i -lt 1200000;$i++){$midBytes[$i]=[byte](65+($i%26))};MakeFile $brMid $midBytes
 $brVanish=Join-Path $brSrc 'vanish.lock';MakeFile $brVanish ([Text.Encoding]::ASCII.GetBytes('TRANSIENT-LOCK'))
 $brOk=Join-Path $brSrc 'plain.txt';MakeFile $brOk ([Text.Encoding]::ASCII.GetBytes('ok-after-lock'))
 $brFs=New-Object IO.FileStream -ArgumentList $brFile,([IO.FileMode]::Open),([IO.FileAccess]::ReadWrite),([IO.FileShare]::ReadWrite)
 $brMidFs=New-Object IO.FileStream -ArgumentList $brMid,([IO.FileMode]::Open),([IO.FileAccess]::ReadWrite),([IO.FileShare]::ReadWrite)
 $brVanishFs=New-Object IO.FileStream -ArgumentList $brVanish,([IO.FileMode]::Open),([IO.FileAccess]::ReadWrite),([IO.FileShare]::ReadWrite)
 $brTar=Join-Path $out 'br-hot.tar'
 try{
  $brFs.Lock(0,19)
  $brMidFs.Lock(1050000,1000)
  $brVanishFs.Lock(0,14)
  $brRes=Write-NexusArchive $brSrc $brTar 'work/' @('range.lock','mid.lock','vanish.lock','plain.txt') 0 $true
  [void]$files.Add($brTar)
  $brEntries=@([NexusPaxReader]::List($brTar))
  Check 'hot pass defers byte-0 and mid-file byte-range locks (Win32 33) and keeps tar valid' (($brRes.Locked -contains 'range.lock') -and ($brRes.Locked -contains 'mid.lock') -and ($brRes.Locked -contains 'vanish.lock') -and (-not $brRes.Snapshot.ContainsKey('range.lock')) -and (-not $brRes.Snapshot.ContainsKey('mid.lock')) -and $brRes.Snapshot.ContainsKey('plain.txt') -and ($brEntries.Count -eq 4) -and ($brEntries[0].Size -eq 19) -and ($brEntries[1].Size -eq 1200000) -and ($brEntries[3].Name -eq 'work/plain.txt'))
  Check 'zero-filled hot tar bytes and sha256 match file on disk' (($brRes.Bytes -eq ([IO.FileInfo]$brTar).Length) -and ($brRes.Sha256 -eq (Get-NexusSha256 $brTar)))
  $brStoppedTar=Join-Path $out 'br-stop.tar'
  $rejected=$false;try{[void](Write-NexusArchive $brSrc $brStoppedTar 'work/' @('range.lock') 0 $false)}catch{$rejected=$true}
  if([IO.File]::Exists($brStoppedTar)){[void]$files.Add($brStoppedTar)}
  Check 'stopped pass rejects byte-range locked file instead of zero-padding' $rejected
  $brFs.Unlock(0,19);$brMidFs.Unlock(1050000,1000);$brVanishFs.Unlock(0,14)
 }finally{$brFs.Dispose();$brMidFs.Dispose();$brVanishFs.Dispose()}
 [IO.File]::Delete($brVanish)
 $brChanged=New-Object Collections.ArrayList;$brDeleted=New-Object Collections.ArrayList
 [NexusPaxArchiver]::Delta($brSrc,$brRes.Snapshot,$brRes.Locked,$brChanged,$brDeleted,0)
 Check 'stopped delta picks up unlocked files and marks vanished locked file deleted' (($brChanged -contains 'range.lock') -and ($brChanged -contains 'mid.lock') -and (-not ($brChanged -contains 'vanish.lock')) -and ($brChanged.Count -eq 2) -and ($brDeleted -contains 'vanish.lock') -and ($brDeleted.Count -eq 1))
 $brDeltaTar=Join-Path $out 'br-delta.tar'
 $brDelta=Write-NexusArchive $brSrc $brDeltaTar 'work/' $brChanged 0 $false
 [void]$files.Add($brDeltaTar)
 $brDeltaEntries=@([NexusPaxReader]::List($brDeltaTar))
 $brDeltaText=[Text.Encoding]::ASCII.GetString([IO.File]::ReadAllBytes($brDeltaTar))
 Check 'stopped delta archives unlocked byte-range file cleanly with real payload' (($brDelta.Locked.Count -eq 0) -and ($brDeltaEntries.Count -eq 2) -and ($brDeltaText.Contains('LOCKED-PAYLOAD-1234')))
 if(-not ('NonWin32ErrStream' -as [type])){
  Add-Type -TypeDefinition 'using System;using System.IO;public class NonWin32ErrStream:Stream{public override bool CanRead{get{return true;}}public override bool CanSeek{get{return false;}}public override bool CanWrite{get{return false;}}public override long Length{get{return 10;}}public override long Position{get{return 0;}set{}}public override void Flush(){}public override long Seek(long o,SeekOrigin r){return 0;}public override void SetLength(long v){}public override void Write(byte[] b,int o,int c){}public override int Read(byte[] b,int o,int c){throw new IOException("non-win32",unchecked((int)0x80130021));}}'
 }
 $nwMs=New-Object IO.MemoryStream;$nwW=New-Object NexusPaxWriter -ArgumentList $nwMs
 $rejected=$false;try{[void]$nwW.AddFile('bad.bin',(New-Object NonWin32ErrStream),10,0,$true)}catch{$rejected=$true}
 Check 'hot AddFile rejects non-FACILITY_WIN32 IOException with low word 33' $rejected
 $nwW.Dispose();$nwMs.Dispose()
}finally{
 if($lockFs){$lockFs.Dispose()}
 if($longFile){[void][PaxFixture]::DeleteFileW(('\\?\'+$longFile))}
 for($i=$longDirs.Count-1;$i -ge 0;$i--){[void][PaxFixture]::RemoveDirectoryW(('\\?\'+$longDirs[$i]))}
 foreach($f in $files){if([IO.File]::Exists($f)){[IO.File]::Delete($f)}}
 if([IO.Directory]::Exists($ext)){
  # bsdtar may create paths longer than 260 characters that .NET cannot delete; robocopy /MIR from an empty directory removes only this test's extract tree.
  $empty=Join-Path $root 'empty';[void][IO.Directory]::CreateDirectory($empty)
  & "$env:SystemRoot\System32\robocopy.exe" $empty $ext /MIR /NFL /NDL /NJH /NJS | Out-Null
  [IO.Directory]::Delete($ext,$false);[IO.Directory]::Delete($empty,$false)
 }
 for($i=$dirs.Count-1;$i -ge 0;$i--){if([IO.Directory]::Exists($dirs[$i])){[IO.Directory]::Delete($dirs[$i],$false)}}
 if([IO.Directory]::Exists($root)){[IO.Directory]::Delete($root,$false)}
}
Write-Output ('pax-tar checks: pass='+$script:pass+' fail='+$script:fail+'. No service, SMB or NAS operations.')
if($script:fail -gt 0){exit 1}
