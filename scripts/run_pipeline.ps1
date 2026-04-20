# run_pipeline.ps1
# Runs the preprocessing pipeline locally.
# Requires Java 17+ and a prepared GraphHopper graph cache in tools\graphhopper\graph-cache.

$ErrorActionPreference = "Stop"
$Root   = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Config = Join-Path $Root "config\default.yaml"

if (-not (Test-Path $Python)) {
    Write-Error "venv not found. Run: python -m venv .venv && .venv\Scripts\pip install -e ."
    exit 1
}
if (-not (Test-Path $Config)) {
    Write-Error "config\default.yaml not found."
    exit 1
}

$GraphCache = Join-Path $Root "tools\graphhopper\graph-cache"
if (-not (Test-Path $GraphCache)) {
    Write-Error "Graph cache not found at tools\graphhopper\graph-cache. Build it first using scripts\start_graphhopper.ps1"
    exit 1
}

Write-Host "[pipeline] starting pipeline with config\default.yaml ..."
& $Python -m pipeline.run_pipeline --config $Config
if ($LASTEXITCODE -ne 0) {
    Write-Error "Pipeline failed (exit $LASTEXITCODE)."
    exit 1
}

Write-Host ""
Write-Host "Done. Outputs in data/processed/:"
Write-Host "  hpc_distance_segments.geojson"
Write-Host "  hpc_sites.geojson"
Write-Host "  hpc_distance.mbtiles"
