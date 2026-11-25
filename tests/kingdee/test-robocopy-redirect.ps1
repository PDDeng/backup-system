$ErrorActionPreference = 'Stop'
try {
    $here = Split-Path -Parent $MyInvocation.MyCommand.Path
    $repoRoot = Split-Path -Parent (Split-Path -Parent $here)
    $backupScript = Join-Path $repoRoot 'components\kingdee\backup.ps1'
    $selectScript = Join-Path $repoRoot 'components\kingdee\select-day.ps1'

    # 1. Verify UTF-8 BOM (EF BB BF) on backup.ps1 (required for Chinese literals in PS 2.0)
    $rawBytes = [IO.File]::ReadAllBytes($backupScript)
    if ($rawBytes.Length -lt 3 -or $rawBytes[0] -ne 0xEF -or $rawBytes[1] -ne 0xBB -or $rawBytes[2] -ne 0xBF) {
        throw 'components/kingdee/backup.ps1 must have a UTF-8 BOM (EF BB BF) for PowerShell 2.0'
    }

    # 2. Verify PSParser syntax of backup.ps1 and select-day.ps1 (0 errors)
    foreach ($f in @($backupScript, $selectScript)) {
        $errs = $null
        [void][System.Management.Automation.PSParser]::Tokenize([IO.File]::ReadAllText($f, [Text.Encoding]::UTF8), [ref]$errs)
        if ($errs.Count -ne 0) { throw "PSParser error in $f" }
    }

    # 3. Verify backup.ps1 guards 2>&1 with ErrorActionPreference='Continue' / finally restore and hardens Log()
    $txt = [IO.File]::ReadAllText($backupScript, [Text.Encoding]::UTF8)
    if ($txt -notmatch "\`$prevEap=\`$ErrorActionPreference;\`$ErrorActionPreference='Continue';try\{\`$null\s*=\s*&\s*\`$robocopy[^\r\n]*2>&1\}finally\{\`$ErrorActionPreference=\`$prevEap\}") {
        throw 'Expected robocopy 2>&1 invocation to lower/restore ErrorActionPreference in try/finally'
    }
    if ($txt -notmatch 'Add-Content -LiteralPath \$log -Value \$line -Encoding UTF8;try\{Write-Host \$line\}catch\{\}') {
        throw 'Expected Log() to write $log first and guard Write-Host'
    }
    if ($txt -match 'Rename-Item\s+-LiteralPath') {
        throw 'Rename-Item -LiteralPath is unsupported in PowerShell 2.0; use [IO.Directory]::Move'
    }
    if ($txt -notmatch '\[IO\.Directory\]::Move\(\$partial,\$final\)') {
        throw 'Expected atomic [IO.Directory]::Move($partial,$final) publication in backup.ps1'
    }
    # Audit every cmdlet invoked with -LiteralPath in backup.ps1 against current PS runtime parameters
    foreach ($mLp in [regex]::Matches($txt, '([A-Za-z]+-[A-Za-z]+)\s+-LiteralPath')) {
        $cmdName = $mLp.Groups[1].Value
        if (-not (Get-Command $cmdName).Parameters.ContainsKey('LiteralPath')) {
            throw "Cmdlet $cmdName does not support -LiteralPath in PowerShell $($PSVersionTable.PSVersion)"
        }
    }

    # 4. Verify stderr-emitting native command under EAP=Stop wrapped with Continue/finally does NOT throw and preserves $LASTEXITCODE
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $null = & cmd.exe /d /c "echo benign-stderr 1>&2 & exit /b 3" 2>&1
    } finally {
        $ErrorActionPreference = $prevEap
    }
    if ($LASTEXITCODE -ne 3) { throw "Expected preserved LASTEXITCODE=3, got $LASTEXITCODE" }
    if ($ErrorActionPreference -ne 'Stop') { throw "Expected ErrorActionPreference restored to Stop, got $ErrorActionPreference" }

    # 5. Real end-to-end reproduction under cmd.exe >> redirected.log 2>&1 (WITHOUT swallowing Write-Host errors!)
    # Runs under default PS and also under real PS 2.0 (-Version 2 -InputFormat None) when available.
    $psExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $modes = @(@{ Name = 'default'; Extra = '' })
    $ps2Check = $false
    try {
        $null = & cmd.exe /d /c "`"$psExe`" -Version 2 -InputFormat None -NoProfile -NonInteractive -Command `"exit 0`" < NUL" 2>&1
        if ($LASTEXITCODE -eq 0) { $ps2Check = $true }
    } catch {}
    if ($ps2Check) {
        $modes += @{ Name = 'ps2'; Extra = '-Version 2 -InputFormat None ' }
    }

    foreach ($m in $modes) {
        $tmp = Join-Path ([IO.Path]::GetTempPath()) ('kd-redir-' + $m.Name + '-' + [guid]::NewGuid().ToString('N'))
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
try {
    `$log = '$log'
    `$robocopy = Join-Path `$env:SystemRoot 'System32\robocopy.exe'
    # Deliberately do NOT swallow Write-Host errors here so handle corruption fails loudly
    function Log(`$s){`$line=(Get-Date -Format s)+' '+`$s;Add-Content -LiteralPath `$log -Value `$line -Encoding UTF8;Write-Host `$line}
    `$files = @('A_20261126.ADF','B_20261126.ADF')
    `$handles = @()
    try {
        foreach (`$n in `$files) { `$handles += [IO.File]::Open((Join-Path '$src' `$n), 'Open', 'Read', 'Read') }
        foreach (`$n in `$files) {
            Log ('COPY ' + `$n)
            `$prev = `$ErrorActionPreference; `$ErrorActionPreference = 'Continue'
            try { `$null = & `$robocopy '$src' '$dst' `$n /COPY:DAT /R:1 /W:2 /NP /NFL /NDL ('/LOG+:' + `$log) 2>&1 } finally { `$ErrorActionPreference = `$prev }
            if (`$LASTEXITCODE -ge 8) { throw ('Robocopy failed ' + `$LASTEXITCODE) }
        }
        Log 'ALL_COPIED_OK'
    } finally {
        foreach (`$h in `$handles) { `$h.Dispose() }
    }
} catch {
    Write-Output ('RUNNER_ERR ' + `$_.Exception.Message)
    exit 1
}
"@
            [IO.File]::WriteAllText($runner, $runnerCode, [Text.Encoding]::UTF8)
            $cmdLine = '""' + $psExe + '" ' + $m.Extra + '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $runner + '" >> "' + $out + '" 2>&1 < NUL"'
            $prevCmdEap = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
            try { $null = & cmd.exe /d /c $cmdLine 2>&1 } finally { $ErrorActionPreference = $prevCmdEap }
            if ($LASTEXITCODE -ne 0) {
                $outDump = if (Test-Path -LiteralPath $out) { [IO.File]::ReadAllText($out) } else { '' }
                throw "Redirected robocopy runner ($($m.Name)) failed rc=$LASTEXITCODE out=$outDump"
            }
            foreach ($n in @('A_20261126.ADF', 'B_20261126.ADF')) {
                if (-not (Test-Path -LiteralPath (Join-Path $dst $n))) { throw "Missing copied file $n ($($m.Name))" }
            }
            $logText = [IO.File]::ReadAllText($log, [Text.Encoding]::UTF8)
            if ($logText -notmatch 'ALL_COPIED_OK') { throw "Missing ALL_COPIED_OK in run.log ($($m.Name))" }
            $outText = [IO.File]::ReadAllText($out)
            if ($outText -notmatch 'ALL_COPIED_OK') { throw "Missing ALL_COPIED_OK in task-output.log ($($m.Name))" }
        } finally {
            Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
        }
    }

    Write-Output ('PASS Kingdee redirected robocopy + BOM + EAP/stderr test (modes=' + (($modes | ForEach-Object { $_.Name }) -join ',') + '; no NAS or production changes).')
} catch {
    Write-Output ('FAIL ' + $_.Exception.Message)
    exit 1
}
