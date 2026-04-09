# run_pipeline.ps1
# Step 1 (Windows): runs the preprocessing pipeline against the local GraphHopper instance.
# GraphHopper must already be running (start_graphhopper.ps1) before calling this.
# Produces: data/processed/hpc_distance_segments.geojson + hpc_sites.geojson
# tippecanoe is skipped on Windows - run generate_tiles.sh on Mac for that.

$ErrorActionPreference = "Stop"
$Root   = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Config = Join-Path $Root "config\local.yaml"

if (-not (Test-Path $Python)) {
    Write-Error "venv not found. Run: python -m venv .venv && .venv\Scripts\pip install -e ."
    exit 1
}
if (-not (Test-Path $Config)) {
    Write-Error "config\local.yaml not found."
    exit 1
}

Write-Host "[pipeline] probing GraphHopper at http://localhost:8989 ..."
try {
    $resp = Invoke-WebRequest -Uri "http://localhost:8989/health" -UseBasicParsing -TimeoutSec 5
    Write-Host "[pipeline] GraphHopper is up"
} catch {
    Write-Error "GraphHopper not reachable at http://localhost:8989. Start it first with: .\scripts\start_graphhopper.ps1"
    exit 1
}

Write-Host "[pipeline] starting pipeline with config\local.yaml ..."
& $Python -m pipeline.run_pipeline --config $Config
if ($LASTEXITCODE -ne 0) {
    Write-Error "Pipeline failed (exit $LASTEXITCODE)."
    exit 1
}

Write-Host ""
Write-Host "Done. GeoJSON outputs in data/processed/:"
Write-Host "  hpc_distance_segments.geojson"
Write-Host "  hpc_sites.geojson"
Write-Host ""
Write-Host "Next: copy those two files to your Mac and run ./scripts/generate_tiles.sh"
