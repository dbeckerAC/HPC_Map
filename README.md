# HPC Map

Distance map for German motorways to nearest eligible HPC chargers.

## Architecture (current)

- Graph data: GraphHopper cache built from Germany OSM PBF
- Compute core: JVM offline engine (`distance-core`) running multi-source Dijkstra on GraphHopper graph cache
- Orchestration: Python pipeline (`pipeline/run_pipeline.py`)
- Outputs:
  - `hpc_distance_segments*.geojson`
  - `hpc_distance*.mbtiles`
  - `hpc_sites*.geojson`
  - `run_metadata*.json`
- Rendering: MapLibre distance vector tiles + clustered HPC GeoJSON

## Prerequisites

- Python 3.9+
- Java 17+
- GraphHopper `graphhopper-web-11.0.jar` in `tools/graphhopper/`
- Germany PBF at `data/raw/osm/germany-latest.osm.pbf`
- `tippecanoe` for MBTiles generation

## Build Graph Cache (one-time)

Mac/Linux:

```bash
docker compose up graphhopper --remove-orphans
```

Windows:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_graphhopper.ps1
```

This imports once and reuses `tools/graphhopper/graph-cache/` on subsequent starts.

## Run Pipeline

Mac/Linux:

```bash
python -m pipeline.run_pipeline --config config/default.yaml
```

Windows:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_pipeline.ps1
```

Docker:

```bash
docker compose run --rm pipeline
```

## Start App Stack

```bash
docker compose up --build api tileserver frontend
```

- API: `http://localhost:8000`
- Tile server: `http://localhost:8080`
- Frontend: `http://localhost:5173`

## Notes

- Legacy Overpass/sample-point/candidate-preselection route code is removed from pipeline flow.
- HPC MBTiles output is removed; HPC stations are served as GeoJSON for frontend clustering.
- `distance-core` currently runs via `distance-core/run_distance_core.sh` (Linux/macOS) or `scripts/run_distance_core.ps1` (Windows).
- Gradle wrapper files are scaffolded; if you need wrapper-based build/test commands, generate `distance-core/gradle/wrapper/gradle-wrapper.jar` once via local Gradle (`gradle wrapper --gradle-version 8.10.2`).
