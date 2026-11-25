$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$backupScript = Join-Path $repoRoot 'components\kingdee\backup.ps1'

# 1. Verify PSParser syntax of backup.ps1 and select-day.ps1 (0 errors)
foreach ($f in @($backupScript, (Join-Path $repoRoot 'components\kingdee\select-day.ps1'))) {
    $errs = $null
    [void][System.Management.Automation.PSParser]::Tokenize([IO.File]::ReadAllText($f, [Text.Encoding]::UTF8), [ref]$errs)
    if ($errs.Count -ne 0) { throw "PSParser error in $f" }
}

# 2. Verify backup.ps1 isolates robocopy stdout from inherited redirected stdout handle and guards Write-Host
$txt = [IO.File]::ReadAllText($backupScript, [Text.Encoding]::UTF8)
if ($txt -notmatch '\$null\s*=\s*&\s*\$robocopy[^\r\n]*2>&1') {
    throw 'Expected robocopy invocation to capture stdout/stderr via $null = & $robocopy ... 2>&1'
}
if ($txt -notmatch 'Add-Content -LiteralPath \$log -Value \$line -Encoding UTF8;try\{Write-Host \$line\}catch\{\}') {
    throw 'Expected Log() to write $log first and guard Write-Host'
}

# 3. Real end-to-end reproduction under cmd.exe >> redirected.log 2>&1 with 2 files + read-shared handles + robocopy /LOG+:
$tmp = Join-Path ([IO.Path]::GetTempPath()) ('kd-redir-test-' + [guid]::NewGuid().ToString('N'))
$src = Join-Path $tmp 'src'
$dst = Join-Path $tmp 'dst'
$log = Join-Path $tmp 'run.log'
$out = Join-Path $tmp 'task-output.log'
$runner = Join-Path $tmp 'runner.ps1'
New-Item -ItemType Directory -Force -Path $src, $dst | Out-Null
try {
    [IO.File]::WriteAllText((Join-Path $src 'A_20261126.ADF'), 'alpha-payload')
    [IO.File]::WriteAllText((Join-Path $src 'B_20261126.ADF'), 'beta-payload-2')
    $runnerCode = @"
`$ErrorActionPreference = 'Stop'
`$log = '$log'
`$robocopy = Join-Path `$env:SystemRoot 'System32\robocopy.exe'
function Log(`$s){`$line=(Get-Date -Format s)+' '+`$s;Add-Content -LiteralPath `$log -Value `$line -Encoding UTF8;try{Write-Host `$line}catch{}}
`$files = @('A_20261126.ADF','B_20261126.ADF')
`$handles = @()
try {
    foreach (`$n in `$files) { `$handles += [IO.File]::Open((Join-Path '$src' `$n), 'Open', 'Read', 'Read') }
    foreach (`$n in `$files) {
        Log ('COPY ' + `$n)
        `$null = & `$robocopy '$src' '$dst' `$n /COPY:DAT /R:1 /W:2 /NP /NFL /NDL ('/LOG+:' + `$log) 2>&1
        if (`$LASTEXITCODE -ge 8) { throw ('Robocopy failed ' + `$LASTEXITCODE) }
    }
    Log 'ALL_COPIED_OK'
} finally {
    foreach (`$h in `$handles) { `$h.Dispose() }
}
"@
    [IO.File]::WriteAllText($runner, $runnerCode, [Text.Encoding]::UTF8)
    $cmdLine = "`"$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe`" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$runner`" >> `"$out`" 2>&1"
    & cmd.exe /d /c $cmdLine
    if ($LASTEXITCODE -ne 0) { throw "Redirected robocopy runner failed with exit code $LASTEXITCODE" }
    foreach ($n in @('A_20261126.ADF', 'B_20261126.ADF')) {
        if (-not (Test-Path -LiteralPath (Join-Path $dst $n))) { throw "Missing copied file $n" }
    }
    $logText = [IO.File]::ReadAllText($log, [Text.Encoding]::UTF8)
    if ($logText -notmatch 'ALL_COPIED_OK') { throw 'Missing ALL_COPIED_OK in run.log' }
    Write-Output 'PASS Kingdee redirected robocopy + read-lock test (0 errors; no NAS or production changes).'
} finally {
    Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
}
