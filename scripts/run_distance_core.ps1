param(
  [Parameter(ValueFromRemainingArguments = $true)]
  [string[]]$ArgsFromPython
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$SrcDir = Join-Path $Root "distance-core\src\main\java"
$BuildDir = Join-Path $Root "distance-core\build\classes\java\main"

$GhJars = Get-ChildItem -Path (Join-Path $Root "tools\graphhopper") -Filter "graphhopper-web-*.jar" -File | Sort-Object Name
if ($GhJars.Count -eq 0) {
  Write-Error "No GraphHopper jar found in tools\graphhopper (expected graphhopper-web-*.jar)."
  exit 1
}
if ($GhJars.Count -gt 1) {
  Write-Error "Multiple GraphHopper jars found in tools\graphhopper. Keep only one graphhopper-web-*.jar file."
  exit 1
}
$GhJar = $GhJars[0].FullName

if (-not (Test-Path $BuildDir)) {
  New-Item -ItemType Directory -Path $BuildDir -Force | Out-Null
}

$JavaFiles = Get-ChildItem -Path $SrcDir -Filter "*.java" -Recurse | ForEach-Object { $_.FullName }
if ($JavaFiles.Count -eq 0) {
  Write-Error "No Java source files found under $SrcDir"
  exit 1
}

& javac -cp $GhJar -d $BuildDir @JavaFiles
if ($LASTEXITCODE -ne 0) {
  Write-Error "javac failed with exit $LASTEXITCODE"
  exit $LASTEXITCODE
}

$Cp = "$BuildDir;$GhJar"
$JavaOptsRaw = $env:DISTANCE_CORE_JAVA_OPTS
if (-not $JavaOptsRaw -or [string]::IsNullOrWhiteSpace($JavaOptsRaw)) {
  $JavaOptsRaw = "-Xms1g -Xmx6g -XX:+UseG1GC"
}
$JavaOpts = $JavaOptsRaw -split "\s+" | Where-Object { $_ -ne "" }
& java @JavaOpts -cp $Cp com.hpcmap.distance.DistanceCoreMain @ArgsFromPython
exit $LASTEXITCODE
