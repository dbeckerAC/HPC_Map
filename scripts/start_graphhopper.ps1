# start_graphhopper.ps1
# Imports the OSM graph (first run only) and starts GraphHopper on localhost:8989.
#
# Usage:
#   .\scripts\start_graphhopper.ps1
#   .\scripts\start_graphhopper.ps1 -MaxHeapGb 10
#   .\scripts\start_graphhopper.ps1 -SkipImport

param(
    [int]   $MaxHeapGb  = 12,
    [switch]$SkipImport
)

$ErrorActionPreference = "Stop"
$Root       = Split-Path -Parent $PSScriptRoot
$GhJarDir   = Join-Path $Root "tools\graphhopper"
$GhJars     = @(Get-ChildItem -Path $GhJarDir -Filter "graphhopper-web-*.jar" -File | Sort-Object Name)
$PbfPath    = Join-Path $Root "data\raw\osm\germany-latest.osm.pbf"
$ConfigYml  = Join-Path $Root "config\graphhopper.yml"
$GraphCache = Join-Path $Root "tools\graphhopper\graph-cache"
$ImportFlag = Join-Path $Root "tools\graphhopper\.import-complete"

if ($GhJars.Count -eq 0) {
    Write-Error "No GraphHopper JAR found in $GhJarDir. Download graphhopper-web-*.jar and place it there."
    exit 1
}
if ($GhJars.Count -gt 1) {
    $JarList = ($GhJars | ForEach-Object { $_.Name }) -join ", "
    Write-Error "Multiple GraphHopper JARs found in $GhJarDir: $JarList. Keep only one graphhopper-web-*.jar file."
    exit 1
}
$GhJar = $GhJars[0].FullName
if (-not (Test-Path $PbfPath)) {
    Write-Error "OSM PBF not found at $PbfPath. Run .\scripts\fetch_assets.ps1 first."
    exit 1
}

$DpbfArg = "-Ddw.graphhopper.datareader.file=$PbfPath"

# ---------------------------------------------------------------------------
# Import phase (one-time)
# ---------------------------------------------------------------------------
if ((-not (Test-Path $ImportFlag)) -and (-not $SkipImport)) {
    Write-Host "[graphhopper] importing graph from PBF (this takes 40-90 min, uses ~$MaxHeapGb GB RAM)..."
    if (Test-Path $GraphCache) {
        Remove-Item -Recurse -Force $GraphCache
    }
    & java "-Xms1g" "-Xmx${MaxHeapGb}g" "-XX:+UseG1GC" $DpbfArg "-jar" $GhJar "import" $ConfigYml
    if ($LASTEXITCODE -ne 0) {
        Write-Error "GraphHopper import failed (exit $LASTEXITCODE)."
        exit 1
    }
    New-Item -ItemType File -Force -Path $ImportFlag | Out-Null
    Write-Host "[graphhopper] import complete"
} else {
    Write-Host "[graphhopper] graph already imported - skipping import phase"
}

# ---------------------------------------------------------------------------
# Server phase
# ---------------------------------------------------------------------------
Write-Host "[graphhopper] starting server on http://localhost:8989 ..."
& java "-Xms1g" "-Xmx${MaxHeapGb}g" "-XX:+UseG1GC" $DpbfArg "-jar" $GhJar "server" $ConfigYml
