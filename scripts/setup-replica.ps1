<#
.SYNOPSIS
    Provision the Phase 6 corpus replica in one command.

.DESCRIPTION
    Bootstrap, load and verify, in that order, stopping on the first failure.

    This exists because provisioning by hand cost an hour, and all of it went
    to paste-and-shell problems rather than to code:

      * a database name truncated to ".../ra" instead of ".../railway",
        which surfaced as an authentication-looking connection failure
      * LOCAL_DATABASE_URL unset in the shell, so --load had no source and
        the error named the flag rather than the cause
      * a tunnel port that changes every session, pasted from last time

    Each of those is now refused by name before anything connects, rather
    than written down in a runbook. The URL checks live in
    agent_tools.replica.validate_target_url (called here via --check-url) so
    they are covered by tests/agent/test_replica_setup.py; a validator whose
    only exercise is a PowerShell script nobody reruns is a comment.

    Idempotent, per the same requirement as Phase 0's loader: --bootstrap is
    CREATE IF NOT EXISTS throughout, --load TRUNCATEs each table before
    copying into it, and --verify is read-only. A partial failure is fixed by
    running this again, not by cleaning up first.

    NOTE that a re-run ROTATES the agent_ro password, because the whole point
    is that the password is generated here and stored nowhere. If you have
    already pasted AGENT_SQL_ROLE_DB_URL into api/.env, re-running this means
    pasting the new one.

    That is the wrong behaviour once this is deployed and right while it is
    not, so it is deliberately left alone rather than fixed early. Local-only,
    a rotation costs one paste into a file you already have open. Against a
    deployed service it would silently invalidate a credential a running
    process is holding, and re-running after a partial failure - which this
    script is built to make safe - would take the agent down. When the hosting
    decision is made, this should accept an existing password and only
    generate one when none is supplied.

.PARAMETER TargetUrl
    Where to build the replica. Two targets are supported and they take the
    same code path: the local docker Postgres of docker-compose.yml, and a
    Railway Postgres reached through a local tunnel. Both are localhost - a
    *.railway.internal host only resolves inside Railway's network, and
    *.rlwy.net is the public TCP proxy, neither of which this drives.

    The corpus database itself is refused: --load truncates its target.

.EXAMPLE
    Local docker, which is where the agent is built while the hosting
    decision is deferred:

    .\scripts\setup-replica.ps1 "postgresql://postgres:postgres@localhost:5433/cricket_agent_replica"

.EXAMPLE
    Railway, through a tunnel started by `railway connect <svc> --tunnel-only`:

    .\scripts\setup-replica.ps1 "postgresql://postgres:PASSWORD@127.0.0.1:54321/railway"
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string] $TargetUrl,

    # Catches an unquoted URL that PowerShell split on a space, which would
    # otherwise fail as "a positional parameter cannot be found" - a message
    # about argument binding, not about the actual mistake.
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $ExtraArguments
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
$EnvPath = Join-Path $RepoRoot 'api\.env'
$SrcPath = Join-Path $RepoRoot 'api\src'
$RoleName = 'agent_ro'


function Invoke-Native {
    <#
        Runs an executable with $ErrorActionPreference relaxed, and nothing
        else.

        Windows PowerShell 5.1 turns ANY stderr output from a native command
        into a terminating NativeCommandError while the preference is 'Stop'.
        replica.py printing a rejection to stderr is its entire job, so
        without this the script fails with a PowerShell error quoting the
        first line of the message instead of letting the message be read -
        which is the "confusing failure instead of the actual cause" problem
        this whole script exists to remove, reproduced in the tool meant to
        fix it.

        Exit codes are what this script decides on, and every caller checks
        $LASTEXITCODE immediately after.
    #>
    param([string] $Exe, [string[]] $Arguments)

    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Exe @Arguments
    } finally {
        $ErrorActionPreference = $previous
    }
}

function Format-Redacted {
    <# Keeps the username, which is diagnostic, and drops the password. #>
    param([string] $Url)
    return ($Url -replace '://([^:/@]+):[^@]*@', '://$1:***@')
}

function Read-DotEnvValue {
    <#
        Reads ONE key out of api/.env, deliberately ignoring the process
        environment. The unset-LOCAL_DATABASE_URL half of the lost hour was
        a shell that did not have what the file did, so falling back to
        $env: here would reintroduce exactly the bug this removes.
    #>
    param([string] $Path, [string] $Key)

    if (-not (Test-Path -LiteralPath $Path)) {
        throw "$Path does not exist. It holds LOCAL_DATABASE_URL, the corpus this script copies FROM."
    }
    foreach ($line in Get-Content -LiteralPath $Path) {
        $trimmed = $line.Trim()
        if ($trimmed -eq '' -or $trimmed.StartsWith('#')) { continue }
        $split = $trimmed.IndexOf('=')
        if ($split -lt 1) { continue }
        if ($trimmed.Substring(0, $split).Trim() -ne $Key) { continue }
        $value = $trimmed.Substring($split + 1).Trim()
        if ($value.Length -ge 2) {
            $first = $value[0]
            $last = $value[$value.Length - 1]
            if (($first -eq '"' -and $last -eq '"') -or ($first -eq "'" -and $last -eq "'")) {
                $value = $value.Substring(1, $value.Length - 2)
            }
        }
        return $value
    }
    return $null
}

function New-RolePassword {
    <#
        Base62 on purpose. The password is pasted into a connection STRING
        and into Railway's dashboard, so a '%', '@', '/' or '#' in it is a
        URL-encoding bug waiting to be blamed on the grants. 32 characters of
        base62 is ~190 bits, nowhere near the margin.

        Modulo over 256 favours the first eight letters very slightly. Said
        plainly rather than left for a reader to spot: it costs a fraction of
        a bit per character and nothing here depends on the other 189.
    #>
    $alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return (-join ($bytes | ForEach-Object { $alphabet[$_ % $alphabet.Length] }))
}

function Resolve-Python {
    <#
        api/.venv first, then PATH - but only if the interpreter can actually
        import psycopg. A venv that exists and is missing a dependency
        otherwise fails four steps later with a traceback about the wrong
        thing.
    #>
    $candidates = @(Join-Path $RepoRoot 'api\.venv\Scripts\python.exe')
    try {
        $onPath = (Get-Command python -ErrorAction Stop).Source
        if ($onPath) { $candidates += $onPath }
    } catch {
        # Nothing on PATH; the venv candidate may still work.
    }

    foreach ($candidate in $candidates) {
        if (-not (Test-Path -LiteralPath $candidate)) { continue }
        Invoke-Native -Exe $candidate -Arguments @('-c', 'import psycopg') 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { return $candidate }
    }
    $venvPython = Join-Path $RepoRoot 'api\.venv\Scripts\python.exe'
    throw ("No interpreter on this machine can import psycopg. Tried: " +
        ($candidates -join ', ') + ". Fix with: " + $venvPython + " -m pip install -e " +
        (Join-Path $RepoRoot 'api'))
}

function Invoke-Step {
    <# Runs one replica.py mode and stops the script if it fails. #>
    param([string] $Name, [string] $Python, [string[]] $ReplicaArgs)

    Write-Host ''
    Write-Host "== $Name" -ForegroundColor Cyan
    $stepWatch = [System.Diagnostics.Stopwatch]::StartNew()
    # Out-Host, not the pipeline: this function's return value is the
    # elapsed TimeSpan, and letting replica.py's own output join it would
    # both hide that output and hand the caller an array to take
    # .TotalSeconds off.
    Invoke-Native -Exe $Python -Arguments $ReplicaArgs | Out-Host
    $exit = $LASTEXITCODE
    $stepWatch.Stop()
    if ($exit -ne 0) {
        throw ("$Name failed (exit $exit) after " +
            ('{0:n1}' -f $stepWatch.Elapsed.TotalSeconds) +
            "s. Nothing after it ran. This script is idempotent: fix the cause and run it again.")
    }
    Write-Host ('   {0} took {1:n1}s' -f $Name, $stepWatch.Elapsed.TotalSeconds) -ForegroundColor DarkGray
    return $stepWatch.Elapsed
}


$totalWatch = [System.Diagnostics.Stopwatch]::StartNew()
$originalLocalUrl = $env:LOCAL_DATABASE_URL
$rows = $null
$loadElapsed = [TimeSpan]::Zero

try {
    # --- 0. an unquoted paste, caught as itself ---------------------------
    if ($ExtraArguments) {
        throw ('The target URL was split into ' + ($ExtraArguments.Count + 1) +
            ' arguments, which means it contains a space and was not quoted. Wrap it in double quotes. Extra: ' +
            ($ExtraArguments -join ' '))
    }

    # --- 1. interpreter ---------------------------------------------------
    $python = Resolve-Python
    Write-Host "python:  $python" -ForegroundColor DarkGray

    Push-Location $SrcPath
    try {
        # --- 2. the URL, refused before anything connects -----------------
        # First, so a mis-pasted URL costs nothing and, in particular, does
        # not burn a generated password on a run that cannot start.
        Invoke-Native -Exe $python -Arguments @('-m', 'agent_tools.replica', '--check-url', $TargetUrl)
        if ($LASTEXITCODE -ne 0) {
            throw 'The target URL was rejected (see above). Nothing connected and nothing changed.'
        }

        # --- 3. the source corpus, from the file and only the file --------
        $localUrl = Read-DotEnvValue -Path $EnvPath -Key 'LOCAL_DATABASE_URL'
        if (-not $localUrl) {
            throw "LOCAL_DATABASE_URL is missing or empty in $EnvPath. That file is the only place this script will look for it."
        }
        if ($originalLocalUrl -and $originalLocalUrl -ne $localUrl) {
            Write-Host 'note:    ignoring the LOCAL_DATABASE_URL already in this shell; api/.env wins' -ForegroundColor Yellow
        }
        Write-Host ('source:  ' + (Format-Redacted $localUrl) + '  (from api/.env)') -ForegroundColor DarkGray
        Write-Host ('target:  ' + (Format-Redacted $TargetUrl)) -ForegroundColor DarkGray

        # --- 4. the role password -----------------------------------------
        $password = New-RolePassword
        $target = [uri] $TargetUrl
        if (-not $target.Host) { throw 'Could not parse a host out of the target URL.' }
        $roleUrl = ('postgresql://{0}:{1}@{2}:{3}{4}{5}' -f
            $RoleName, $password, $target.Host, $target.Port, $target.AbsolutePath, $target.Query)

        # Set here rather than exported by hand: these four are the entire
        # shell-state surface, they live only for this process, and the
        # finally block below takes them away again.
        $env:REPLICA_ADMIN_DB_URL = $TargetUrl
        $env:AGENT_RO_PASSWORD = $password
        $env:AGENT_SQL_ROLE_DB_URL = $roleUrl
        $env:LOCAL_DATABASE_URL = $localUrl

        # --- 5. the three steps -------------------------------------------
        Invoke-Step -Name 'bootstrap' -Python $python -ReplicaArgs @('-m', 'agent_tools.replica', '--bootstrap') | Out-Null
        $loadElapsed = Invoke-Step -Name 'load' -Python $python -ReplicaArgs @('-m', 'agent_tools.replica', '--load')

        Write-Host ''
        Write-Host '== verify' -ForegroundColor Cyan
        $verifyOutput = Invoke-Native -Exe $python -Arguments @('-m', 'agent_tools.replica', '--verify')
        $verifyExit = $LASTEXITCODE
        $verifyOutput | ForEach-Object { Write-Host $_ }
        if ($verifyExit -ne 0) {
            throw "verify failed (exit $verifyExit). The replica exists but does not hold the guarantees layer 1 claims - do not point the agent at it."
        }

        # The one line _report_load_stamp prints in key=value form, for
        # exactly this. Keep the two in step if you edit either.
        foreach ($line in $verifyOutput) {
            if ($line -match '\brows=(\d+)\b') { $rows = [long] $Matches[1]; break }
        }
    } finally {
        Pop-Location
    }

    $totalWatch.Stop()
    Write-Host ''
    Write-Host '-------------------------------------------------------------' -ForegroundColor Green
    Write-Host 'replica provisioned' -ForegroundColor Green
    if ($null -ne $rows) {
        Write-Host ('  rows      {0:n0}  (loaded in {1:n1}s)' -f $rows, $loadElapsed.TotalSeconds)
    } else {
        Write-Host '  rows      not reported - verify printed no load stamp' -ForegroundColor Yellow
    }
    Write-Host ('  elapsed   {0:n1}s total' -f $totalWatch.Elapsed.TotalSeconds)
    Write-Host ''
    Write-Host '  AGENT_RO_PASSWORD (shown once, written nowhere):' -ForegroundColor Yellow
    Write-Host "    $password"
    Write-Host ''
    Write-Host '  Put this line in api/.env. Nothing goes into Railway service'
    Write-Host '  variables this round - the replica is local and so is its role:'
    Write-Host ('    AGENT_SQL_ROLE_DB_URL=' + $roleUrl)
    Write-Host '-------------------------------------------------------------' -ForegroundColor Green
} finally {
    # The password must not outlive the run, and REPLICA_ADMIN_DB_URL is the
    # owner credential - neither belongs in a shell you keep typing in.
    Remove-Item Env:\AGENT_RO_PASSWORD -ErrorAction SilentlyContinue
    Remove-Item Env:\REPLICA_ADMIN_DB_URL -ErrorAction SilentlyContinue
    Remove-Item Env:\AGENT_SQL_ROLE_DB_URL -ErrorAction SilentlyContinue
    if ($null -eq $originalLocalUrl) {
        Remove-Item Env:\LOCAL_DATABASE_URL -ErrorAction SilentlyContinue
    } else {
        $env:LOCAL_DATABASE_URL = $originalLocalUrl
    }
}
