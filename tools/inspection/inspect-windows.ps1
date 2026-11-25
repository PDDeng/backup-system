# Read-only preparation for SVN/Nexus. Windows PowerShell 2.0+.
param([string]$RepositoryRoot='', [string]$NexusData='')
$ErrorActionPreference='Continue'
function Safe-Text([string]$value) {
    return [regex]::Replace($value,'(?i)((?:password|passwd|pwd|token|secret)\s*[=:]\s*)("[^"]*"|\S+)','$1[REDACTED]')
}
Write-Output '=== HOST / POWERSHELL ==='
Write-Output $env:COMPUTERNAME
Write-Output $PSVersionTable.PSVersion.ToString()
Write-Output '=== SERVICES ==='
Get-WmiObject Win32_Service | Where-Object { $_.Name -match 'svn|nexus|apache|httpd' -or $_.DisplayName -match 'svn|nexus|apache' } | ForEach-Object {
    Write-Output ('NAME='+$_.Name+' STATE='+$_.State+' ACCOUNT='+$_.StartName)
    Write-Output ('PATH='+ (Safe-Text $_.PathName))
}
Write-Output '=== INSTALLED PRODUCTS ==='
foreach ($key in @('HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*','HKLM:\SOFTWARE\Wow6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*')) {
    Get-ItemProperty $key -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName -match 'Subversion|VisualSVN|Nexus|CollabNet' } | Select-Object DisplayName,DisplayVersion,InstallLocation | Format-List
}
Write-Output '=== VISUALSVN ROOT (if configured) ==='
foreach ($key in @('HKLM:\SOFTWARE\VisualSVN\VisualSVN Server','HKLM:\SOFTWARE\Wow6432Node\VisualSVN\VisualSVN Server')) {
    $v=Get-ItemProperty $key -ErrorAction SilentlyContinue
    if ($v) { $v | Select-Object RepositoriesRoot,InstallDir | Format-List }
}
Write-Output '=== EXECUTABLE LOCATIONS ==='
foreach($tool in @('svnadmin.exe','svnlook.exe','svnserve.exe')) {
    Get-Command $tool -ErrorAction SilentlyContinue | Select-Object Name,Definition | Format-List
}
Write-Output '=== JAVA DATA-DIRECTORY FLAGS ONLY ==='
Get-WmiObject Win32_Process | Where-Object { $_.Name -match '^java(w)?\.exe$' -and $_.CommandLine -match 'nexus|sonatype' } | ForEach-Object {
    Write-Output ('PID='+$_.ProcessId+' EXE='+$_.ExecutablePath)
    $matches=[regex]::Matches($_.CommandLine,'(?i)-D(?:nexus-work|karaf\.data|karaf\.home|nexus\.home|basedir)=(?:"[^"]+"|\S+)')
    foreach($m in $matches) { Write-Output $m.Value }
}
Write-Output '=== DISKS ==='
Get-WmiObject Win32_LogicalDisk -Filter 'DriveType=3' | Select-Object DeviceID,Size,FreeSpace | Format-Table -AutoSize
Write-Output '=== CANDIDATE PATHS (no recursive scan) ==='
foreach($path in @('C:\Repositories','D:\Repositories','D:\svn','C:\sonatype-work','D:\sonatype-work','C:\Nexus','D:\Nexus','D:\data')) {
    if(Test-Path -LiteralPath $path){Write-Output $path;Get-ChildItem -LiteralPath $path | Select-Object Name,Mode,LastWriteTime | Format-Table -AutoSize}
}
if($RepositoryRoot -and (Test-Path -LiteralPath $RepositoryRoot)) {
    Write-Output '=== SVN REPOSITORIES / DIRECT CHILDREN ==='
    $dirs=@(Get-Item -LiteralPath $RepositoryRoot)+@(Get-ChildItem -LiteralPath $RepositoryRoot | Where-Object {$_.PSIsContainer})
    foreach($dir in $dirs) {
        if((Test-Path -LiteralPath (Join-Path $dir.FullName 'format')) -and (Test-Path -LiteralPath (Join-Path $dir.FullName 'db'))) {
            Write-Output ('REPOSITORY='+$dir.FullName)
            Get-Content -LiteralPath (Join-Path $dir.FullName 'format')
        }
    }
}
if($NexusData -and (Test-Path -LiteralPath $NexusData)) {
    Write-Output '=== NEXUS DATA CHILDREN ==='
    Get-ChildItem -LiteralPath $NexusData | Select-Object Name,Mode,LastWriteTime | Format-Table -AutoSize
}
Write-Output 'DONE: no services, tasks or repository data changed; no backup started.'
