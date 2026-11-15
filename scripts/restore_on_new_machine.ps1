<#
.SYNOPSIS
  Set up the project on a new computer from a folder made by pack_for_move.ps1.

.DESCRIPTION
  Run it inside the cloned project folder (git clone ... crop-ai; cd crop-ai). It will:
    1. check the pack (SHA256 of output.tar) and copy .env if this computer has none
    2. extract output.tar into output/
    3. create .venv with Python 3.12 and install requirements.txt
    4. start the database and restore cropai.dump
       (no dump, or -Rebuild: re-create the rows with 07_predict.py --stack, no internet needed)
    5. build and start the whole system, then check http://127.0.0.1:3000
  Needs: Git, Python 3.12, Docker Desktop (running).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\restore_on_new_machine.ps1 -Pack E:\crop-ai-move
#>
param(
    [Parameter(Mandatory = $true)][string]$Pack,   # folder made by pack_for_move.ps1
    [switch]$Rebuild,     # ignore cropai.dump and re-create the database rows with 07_predict.py --stack
    [switch]$SkipVenv,    # do not create .venv / install Python packages
    [switch]$Force        # replace an existing database without asking
)

$Root = Split-Path $PSScriptRoot -Parent
# the script may also be run from the pack folder: then use the current folder as the project
if (-not (Test-Path (Join-Path $Root "docker-compose.yml"))) { $Root = (Get-Location).Path }
Set-Location $Root

function Fail($msg) { Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }
function Step($msg) { Write-Host "`n== $msg" -ForegroundColor Cyan }

function Read-DotEnv($path) {
    $h = @{}
    foreach ($line in [System.IO.File]::ReadAllLines($path, [System.Text.Encoding]::UTF8)) {
        $t = $line.Trim()
        if ($t -and -not $t.StartsWith("#") -and $t.Contains("=")) {
            $i = $t.IndexOf("=")
            $h[$t.Substring(0, $i).Trim()] = $t.Substring($i + 1).Trim()
        }
    }
    return $h
}

function Wait-Db($user, $db) {
    for ($i = 0; $i -lt 40; $i++) {
        docker compose exec -T db pg_isready -U $user -d $db 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { return }
        Start-Sleep -Seconds 2
    }
    Fail "database did not become ready (docker compose logs db)"
}

function Sql($user, $db, $sql) {
    return (docker compose exec -T db psql -U $user -d $db -tAc $sql 2>$null | Out-String).Trim()
}

# ---------------------------------------------------------------- 1. checks
Step "Checking project and pack"
if (-not (Test-Path "docker-compose.yml") -or -not (Test-Path "07_predict.py")) {
    Fail "run this inside the cloned project folder (the one with docker-compose.yml)"
}
$Pack = (Resolve-Path $Pack -ErrorAction SilentlyContinue).Path
if (-not $Pack) { Fail "pack folder not found" }
$tarFile = Join-Path $Pack "output.tar"
$dumpFile = Join-Path $Pack "cropai.dump"
if (-not (Test-Path $tarFile)) { Fail "output.tar not found in $Pack" }

$manifestFile = Join-Path $Pack "manifest.txt"
if (Test-Path $manifestFile) {
    Get-Content $manifestFile | ForEach-Object { Write-Host "   $_" }
    $line = Get-Content $manifestFile | Where-Object { $_ -like "output.tar sha256:*" }
    if ($line) {
        Write-Host "Verifying output.tar (SHA256)..."
        $want = ($line -split ":", 2)[1].Trim()
        $got = (Get-FileHash $tarFile -Algorithm SHA256).Hash
        if ($got -ne $want) { Fail "output.tar is damaged (checksum mismatch) - copy it again" }
        Write-Host "   OK"
    }
}

docker info --format "{{.ServerVersion}}" 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) { Fail "Docker is not running. Open Docker Desktop, wait until it says 'Engine running', then run again." }

# ---------------------------------------------------------------- .env
if (Test-Path ".env") {
    Write-Host ".env already exists - keeping it"
} elseif (Test-Path (Join-Path $Pack ".env")) {
    Copy-Item (Join-Path $Pack ".env") ".env" -ErrorAction Stop
    Write-Host "Copied .env from the pack"
} else {
    Fail "no .env here or in the pack: copy .env.example to .env and set the passwords"
}
$envv = Read-DotEnv (Join-Path $Root ".env")
$DbUser = if ($envv["POSTGRES_USER"]) { $envv["POSTGRES_USER"] } else { "cropai" }
$DbName = if ($envv["POSTGRES_DB"]) { $envv["POSTGRES_DB"] } else { "cropai" }

# ---------------------------------------------------------------- 2. output/
Step "Extracting output.tar (this can take a few minutes)"
& "$env:SystemRoot\System32\tar.exe" -xf $tarFile -C $Root
if ($LASTEXITCODE -ne 0) { Fail "tar extract failed" }
$seasonDirs = Get-ChildItem "output\seasons" -Directory -ErrorAction SilentlyContinue
Write-Host ("Seasons on disk: " + (($seasonDirs | ForEach-Object Name) -join ", "))
if (Test-Path (Join-Path $Pack "data")) {
    Get-ChildItem (Join-Path $Pack "data") -File | Where-Object { -not (Test-Path (Join-Path "data" $_.Name)) } |
        Copy-Item -Destination "data"
}

# ---------------------------------------------------------------- 3. Python
$py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not $SkipVenv) {
    Step "Python environment (.venv)"
    if (-not (Test-Path $py)) {
        $base = $null
        if (Get-Command py -ErrorAction SilentlyContinue) {
            py -3.12 --version 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) { $base = @("py", "-3.12") }
        }
        if (-not $base -and (Get-Command python -ErrorAction SilentlyContinue)) {
            $v = (python --version 2>$null | Out-String)
            if ($v -match "3\.12") { $base = @("python") }
        }
        if (-not $base) { Fail "Python 3.12 not found. Install it from python.org (tick 'Add to PATH'), then run again." }
        $exe = $base[0]; $rest = @($base | Select-Object -Skip 1)
        & $exe @rest -m venv .venv
        if ($LASTEXITCODE -ne 0) { Fail "could not create .venv" }
    }
    & $py -m pip install --upgrade pip --quiet
    & $py -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { Fail "pip install failed" }
}

# ---------------------------------------------------------------- 4. database
Step "Database"
docker compose up -d db
if ($LASTEXITCODE -ne 0) { Fail "could not start the db container" }
Wait-Db $DbUser $DbName

$existing = Sql $DbUser $DbName "SELECT count(*) FROM map_runs"
if ($existing -match "^\d+$" -and [int]$existing -gt 0 -and -not $Force) {
    $ans = Read-Host "The database already has $existing season(s). Replace it with the pack? (y/N)"
    if ($ans -ne "y") { Fail "stopped - nothing in the database was changed" }
}

if ((Test-Path $dumpFile) -and -not $Rebuild) {
    Write-Host "Restoring cropai.dump..."
    # start from an empty database so the restore cannot clash with the schema made by db/init
    # (no quotes around the names: PowerShell 5.1 drops embedded quotes when calling docker)
    docker compose exec -T db psql -U $DbUser -d postgres -q -c "DROP DATABASE IF EXISTS $DbName WITH (FORCE);"
    docker compose exec -T db psql -U $DbUser -d postgres -q -c "CREATE DATABASE $DbName OWNER $DbUser;"
    if ($LASTEXITCODE -ne 0) { Fail "could not re-create the database" }
    docker compose cp $dumpFile db:/tmp/cropai.dump
    if ($LASTEXITCODE -ne 0) { Fail "could not copy the dump into the container" }
    docker compose exec -T db pg_restore -U $DbUser -d $DbName --no-owner --exit-on-error /tmp/cropai.dump
    if ($LASTEXITCODE -ne 0) { Fail "pg_restore failed" }
    docker compose exec -T db rm -f /tmp/cropai.dump | Out-Null
} else {
    Write-Host "No dump used - re-creating the rows with 07_predict.py --stack (no internet needed)"
    if (-not (Test-Path $py)) { Fail ".venv is missing (do not use -SkipVenv with -Rebuild)" }
    if (-not (Test-Path "output\model\rf_model.joblib")) { Fail "output\model\rf_model.joblib missing" }
    foreach ($d in $seasonDirs) {
        $stack = Join-Path $d.FullName "ndvi_stack.tif"
        if ($d.Name -match "^\d{4}$" -and (Test-Path $stack)) {
            Write-Host "-- season $($d.Name)"
            & $py 07_predict.py --season $d.Name --stack $stack
            if ($LASTEXITCODE -ne 0) { Fail "07_predict.py failed for season $($d.Name)" }
        }
    }
}
$runs = Sql $DbUser $DbName "SELECT string_agg(season_label, ', ' ORDER BY season) FROM map_runs"
Write-Host "Seasons in database: $runs"

# ---------------------------------------------------------------- 5. whole system
Step "Building and starting the whole system (first build takes several minutes)"
docker compose up -d --build
if ($LASTEXITCODE -ne 0) { Fail "docker compose up failed" }

$port = if ($envv["WEB_PORT"]) { $envv["WEB_PORT"] } else { "3000" }
$ok = $false
for ($i = 0; $i -lt 60; $i++) {
    try {
        $r = Invoke-RestMethod "http://127.0.0.1:$port/api/seasons" -TimeoutSec 5
        $ok = $true; break
    } catch { Start-Sleep -Seconds 3 }
}
if (-not $ok) { Fail "the web did not answer on port $port - check: docker compose logs web api" }

Step "Done"
Write-Host ("Web is up with {0} season(s): http://localhost:{1}" -f @($r).Count, $port) -ForegroundColor Green
Write-Host "Before running Python scripts:  .venv\Scripts\activate"
