# Functional Specification Document

## Title

HPC Routing Distance Map Germany

## Status

Draft v2.2 (Exit-based active, routing-ready)

## Goal

Build a web app that visualizes route distance from German motorway directions to eligible HPC chargers.

The app must avoid loading large raw point datasets in the browser and must use standard map-layer delivery.

## Locked Technical Path

- Motorway extraction: Overpass API (cached locally as GeoJSON)
- Directional sampling: points every configurable interval, offset by configurable lateral distance from centerline using heading-derived normal vectors
- Distance mode default: exit-based approximation without routing engine
- Optional routing engine: local GraphHopper Java server
- Charger source: Bundesnetzagentur CSV
- Layer output: precomputed line segments and HPC points; tiled delivery via MBTiles
- Frontend rendering: MapLibre GL JS

## Data Sources

### Motorways

- Source: Overpass API (`motorway` and `motorway_link`)
- Country scope: Germany bbox query with post-extraction geometric clipping to Germany polygon
- Cache artifact: `data/raw/osm/motorways.geojson`
- Cache invalidation: cache is automatically refreshed when the Overpass query signature changes
- Cache policy:
  - use cache if age < 90 days
  - if age >= 90 days, refresh only when explicit confirmation flag is enabled

### Chargers

- Source: latest CSV from Bundesnetzagentur
- Local file: `data/raw/bnetza/Ladesaeulenregister_BNetzA_2026-03-25.csv`
- Eligibility field: `Nennleistung Ladeeinrichtung [kW]`
- Default minimum power threshold: `150 kW`

## Pipeline

1. Fetch or reuse motorway cache from Overpass.
2. Clip/prepare motorway geometries for selected bbox.
3. Sample points at `sampling_interval_m` on each motorway line.
4. Create two directional point sets using lateral offset (`directional_offset_m`) from centerline.
   - Exception: for one-way motorway carriageways, use a single directional geometry to avoid duplicate visual lanes.
5. Normalize and filter eligible chargers from CSV.
6. Preselect nearby charger candidates per sampled point (air-distance filter).
   - Implementation uses BallTree nearest-neighbor search (haversine metric) for fast candidate lookup.
   - Grid-based lookup remains as fallback when BallTree runtime is unavailable.
7. Compute sampled-point to charger distance using configured mode:
   - `exit_based` (active default): distance to next motorway exit in driving direction + Euclidean exit-to-nearest-HPC
   - `euclidean`: direct air-distance fallback
   - `graphhopper`: full routing mode
8. Keep nearest candidate by computed distance.
9. Build motorway line segments between consecutive sampled points and attach endpoint route distances.
10. Build HPC points layer from filtered chargers.
11. Generate MBTiles for distance and HPC layers when `tippecanoe` is available.

## Core Defaults

- Subset bbox:
  - `min_lat=47.2`
  - `min_lon=5.8`
  - `max_lat=55.1`
  - `max_lon=15.1`
- Sampling interval: `2000 m`
- Directional offset: `10 m`
- Candidate count: `8`
- Candidate radius: `75 km` (air distance)
- Distance color range: `0..20 km`, green to red, clamp above max
- Distance mode default: `exit_based`
- Routing provider (when enabled): `graphhopper`
- GraphHopper endpoint default: `http://localhost:8989`

## Output Layers

### Distance Layer

- Geometry: line segments derived from consecutive directional sampled points
- Attributes:
  - `distance_start_km`
  - `distance_end_km`
  - `min_power_kw`
- Visualization: gradient between endpoint values on each segment

### HPC Sites Layer (Placeholder)

- Geometry: points
- Source: filtered chargers above configurable threshold
- Purpose: additional effective-layer pattern for map stack and hover inspection
- UI: visible as point layer; hover shows charger id, power, and status

## Frontend Behavior

- Render base map and precomputed distance layer.
- Do not render raw sampled point set.
- Hover on distance segments displays value in km and active minimum power threshold.
- Show clustered HPC sites layer by default with a checkbox to hide/show stations.
- Layer loading mode:
  - preferred: vector tiles from MBTiles
  - fallback: GeoJSON from API endpoints when MBTiles are not available

## Performance Rules

- Heavy computations happen offline in preprocessing.
- Browser consumes tiled layer output instead of raw dense analysis datasets.
- Pipeline uses restartable artifacts and can run in fresh-recompute mode.
- Route stage runs in parallel and logs progress.

## Operational Notes

- Full Germany runtime depends mainly on routing throughput and candidate strategy.
- Overpass cache and staged outputs reduce repeated expensive work.
- GraphHopper import is one-time per PBF snapshot; routing server then reused for repeated runs.
