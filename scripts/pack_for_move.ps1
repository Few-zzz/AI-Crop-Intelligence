<#
.SYNOPSIS
  Pack everything that is NOT in Git so the project can be moved to another computer.

.DESCRIPTION
  Code travels through GitHub (git clone). This script packs the rest into one folder:
    cropai.dump    PostGIS database (pg_dump custom format)
    output.tar     output/seasons (without monthly/) + output/model   (-Full: the whole output/)
    .env           database passwords  -> keep this folder private, never upload it
    manifest.txt   date, git commit, SHA256 of output.tar (checked by the restore script)
    restore_on_new_machine.ps1   copy of the restore script
  Copy the folder to an external drive, then on the new computer run restore_on_new_machine.ps1.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\pack_for_move.ps1
  powershell -ExecutionPolicy Bypass -File scripts\pack_for_move.ps1 -Dest E:\crop-ai-move -Full
#>
param(
    [string]$Dest = "",      # default: a folder "crop-ai-move" next to the project folder
    [switch]$Full,           # include all of output/ (monthly NDVI, stacks) - needed to retrain the model
    [switch]$NoDb            # skip the database dump (restore can rebuild it with 07_predict.py --stack)
)

$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root
if (-not $Dest) { $Dest = Join-Path (Split-Path $Root -Parent) "crop-ai-move" }

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

# ---------------------------------------------------------------- checks
Step "Checking project"
if (-not (Test-Path ".env")) { Fail ".env not found in $Root" }
if (-not (Test-Path "output\model\rf_model.joblib")) { Fail "output\model\rf_model.joblib not found (train with 05_train_rf.py first)" }
if (-not (Test-Path "output\seasons")) { Fail "output\seasons not found (run 07_predict.py first)" }
$envv = Read-DotEnv (Join-Path $Root ".env")
$DbUser = if ($envv["POSTGRES_USER"]) { $envv["POSTGRES_USER"] } else { "cropai" }
$DbName = if ($envv["POSTGRES_DB"]) { $envv["POSTGRES_DB"] } else { "cropai" }

# code moves through GitHub, so warn about work that is not pushed yet
$dirty = git status --porcelain 2>$null
if ($dirty) { Write-Host "WARNING: uncommitted changes - commit and push them, or they will not reach the new computer:" -ForegroundColor Yellow; $dirty | Select-Object -First 10 | ForEach-Object { Write-Host "   $_" } }
$ahead = git rev-list --count "@{u}..HEAD" 2>$null
if ($LASTEXITCODE -eq 0 -and [int]$ahead -gt 0) { Write-Host "WARNING: $ahead commit(s) not pushed to GitHub yet - run: git push" -ForegroundColor Yellow }
$commit = git rev-parse --short HEAD 2>$null

try { New-Item -ItemType Directory -Force -Path $Dest -ErrorAction Stop | Out-Null }
catch { Fail "cannot create $Dest - choose another place with -Dest, e.g. -Dest E:\crop-ai-move" }
Write-Host "Pack folder: $Dest"

# ---------------------------------------------------------------- database
$runs = "skipped"
if (-not $NoDb) {
    Step "Dumping database"
    docker info --format "{{.ServerVersion}}" 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) { Fail "Docker is not running. Open Docker Desktop and try again (or use -NoDb)." }

    $services = docker compose ps --status running --services 2>$null
    if (-not ($services -contains "db")) {
        Write-Host "Starting the db container..."
        docker compose up -d db
        if ($LASTEXITCODE -ne 0) { Fail "could not start the db container" }
    }
    $ready = $false
    for ($i = 0; $i -lt 30; $i++) {
        docker compose exec -T db pg_isready -U $DbUser -d $DbName 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { $ready = $true; break }
        Start-Sleep -Seconds 2
    }
    if (-not $ready) { Fail "database did not become ready" }

    $runs = (docker compose exec -T db psql -U $DbUser -d $DbName -tAc "SELECT count(*) FROM map_runs" 2>$null | Out-String).Trim()
    Write-Host "Seasons in database (map_runs): $runs"

    docker compose exec -T db pg_dump -U $DbUser -d $DbName -Fc -f /tmp/cropai.dump
    if ($LASTEXITCODE -ne 0) { Fail "pg_dump failed" }
    docker compose cp db:/tmp/cropai.dump (Join-Path $Dest "cropai.dump")
    if ($LASTEXITCODE -ne 0) { Fail "could not copy the dump out of the container" }
    docker compose exec -T db rm -f /tmp/cropai.dump | Out-Null
}

# ---------------------------------------------------------------- .env
Step "Copying .env"
Copy-Item ".env" (Join-Path $Dest ".env") -Force -ErrorAction Stop

# ---------------------------------------------------------------- output/
Step "Archiving output/ (this can take a few minutes)"
$tarFile = Join-Path $Dest "output.tar"
# .tif files are already compressed, so a plain (uncompressed) tar is just as small and much faster
$tarArgs = @("-cf", $tarFile, "--exclude", "*.tmp.tif")
if ($Full) {
    $tarArgs += "output"
} else {
    $tarArgs += @("--exclude", "output/seasons/*/monthly", "output/seasons", "output/model")
}
& "$env:SystemRoot\System32\tar.exe" @tarArgs
if ($LASTEXITCODE -ne 0) { Fail "tar failed" }

# QGIS project files are not in Git either
$qgz = Get-ChildItem "data" -Include "*.qgz", "*.qgs" -Recurse -ErrorAction SilentlyContinue
if ($qgz) {
    New-Item -ItemType Directory -Force -Path (Join-Path $Dest "data") | Out-Null
    $qgz | Copy-Item -Destination (Join-Path $Dest "data") -Force
    Write-Host "Copied $($qgz.Count) QGIS project file(s) to data\ (the restore script puts them back)"
}

Step "Computing checksum"
$hash = (Get-FileHash $tarFile -Algorithm SHA256).Hash
$mode = if ($Full) { "full" } else { "web" }
$manifest = @(
    "created: $(Get-Date -Format 'yyyy-MM-dd HH:mm')",
    "git_commit: $commit",
    "mode: $mode",
    "map_runs: $runs",
    "output.tar sha256: $hash"
)
[System.IO.File]::WriteAllLines((Join-Path $Dest "manifest.txt"), $manifest)
Copy-Item (Join-Path $PSScriptRoot "restore_on_new_machine.ps1") $Dest -Force -ErrorAction SilentlyContinue

# ---------------------------------------------------------------- summary
Step "Done"
Get-ChildItem $Dest -File -Force | ForEach-Object { "{0,-30} {1,10:N1} MB" -f $_.Name, ($_.Length / 1MB) } | Write-Host
$total = (Get-ChildItem $Dest -File -Force -Recurse | Measure-Object Length -Sum).Sum / 1GB
Write-Host ("Total: {0:N2} GB" -f $total)
Write-Host ""
Write-Host "Next: copy the folder $Dest to an external drive."
Write-Host "On the new computer:  git clone https://github.com/Few-zzz/AI-Crop-Intelligence.git crop-ai"
Write-Host "                      cd crop-ai"
Write-Host "                      powershell -ExecutionPolicy Bypass -File scripts\restore_on_new_machine.ps1 -Pack <drive>\crop-ai-move"
Write-Host "The folder contains .env (passwords): keep it private, do not upload it." -ForegroundColor Yellow
