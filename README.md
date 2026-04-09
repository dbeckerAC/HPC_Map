# HPC Map
*This is a vibe coding app based on a functional specification document (FSD).*

GraphHopper-based prototype for visualizing motorway distance to eligible HPC chargers.

## Locked Architecture

- Motorways: Overpass API (single query, cached locally)
- Distance computation: configurable (`graphhopper` exact default, `exit_based` optional)
- Preprocessing: Python pipeline with restartable artifacts
- Map delivery: MBTiles + tile server + MapLibre GL JS

## Prerequisites

- Python 3.9+
- Docker Desktop with Compose (Mac/Linux pipeline + local dev)
- Internet access for Overpass API

### Required data files

**BNetzA charging station register** (`data/raw/bnetza/`)
- Download the latest CSV from the [Bundesnetzagentur Ladesäulenkarte](https://www.bundesnetzagentur.de/DE/Fachthemen/ElektrizitaetundGas/E-Mobilitaet/Ladesaeulenkarte/start.html)
- Place in `data/raw/bnetza/`
- Update the filename in `config/default.yaml` → `paths.charger_csv` if your date differs

**Germany OSM PBF** (`data/raw/osm/germany-latest.osm.pbf`) — only needed for GraphHopper import
- Download from [Geofabrik](https://download.geofabrik.de/europe/germany-latest.osm.pbf) (~4.4 GB)

**GraphHopper JAR** (`tools/graphhopper/graphhopper-web-*.jar`) — only needed when running GraphHopper natively (without Docker)
- Download the latest `graphhopper-web-*.jar` from the [GraphHopper releases page](https://github.com/graphhopper/graphhopper/releases/)
- Place it in `tools/graphhopper/` with its original filename

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
python3 -m pip install --upgrade pip
python3 -m pip install -e .
```

Only for local frontend (optional):

```bash
cd frontend && npm install && cd ..
```

## Run Pipeline (Mac / Linux — recommended)

Requires Docker Desktop. GraphHopper runs inside the compose network.

```bash
docker compose up graphhopper --remove-orphans   # first run imports graph (~10 min), then serves on :8989
docker compose run --rm pipeline                 # runs all stages; skips already-completed artifacts
```

Import completion is persisted at `tools/graphhopper/.import-complete` — restarts do not re-import.
For Germany import set Docker Desktop memory to at least 10 GB (16 GB recommended).

Reset and re-import if needed:

```bash
rm -f tools/graphhopper/.import-complete
rm -rf tools/graphhopper/graph-cache
docker compose up graphhopper --remove-orphans
```

Pipeline outputs:

- `data/intermediate/` — stage artifacts (cached between runs)
- `data/processed/hpc_distance_segments.geojson`
- `data/processed/hpc_sites.geojson`
- `data/processed/hpc_distance.mbtiles`
- `data/processed/hpc_sites.mbtiles`
- `data/processed/run_metadata.json`

## Run Pipeline (Windows — native Java, no Docker)

Requires Java 17+ and the GraphHopper JAR + Germany PBF (see Prerequisites above).

**One-time graph import + start server:**

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_graphhopper.ps1
```

First run imports the graph (~10 min for Germany). Subsequent runs start the server immediately.
Uses `config/graphhopper.yml` and writes graph cache to `tools/graphhopper/graph-cache/`.

**Run the pipeline:**

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_pipeline.ps1
```

Uses `config/local.yaml` (points to `http://127.0.0.1:8989`). Tippecanoe is skipped on Windows — copy the GeoJSON outputs to a Mac and run the Mac pipeline step to produce mbtiles.

**Reset graph cache:**

```powershell
Remove-Item -Recurse -Force tools\graphhopper\graph-cache
Remove-Item tools\graphhopper\.import-complete
```

## Start API + Tile Server

```bash
docker compose up --build api tileserver
```

- API: `http://localhost:8000`
- Tile server: `http://localhost:8080`

## Start Frontend

Docker (recommended):

```bash
docker compose up --build frontend api tileserver
```

Local:

```bash
cd frontend
npm run dev
```

Open `http://localhost:5173`.

## Notes

- Route stage is parallelized (`routing.max_workers`) and logs progress with ETA every 30 s.
- GraphHopper exact mode uses BallTree + adaptive lower-bound pruning (provably optimal result, no fixed top-K approximation). Requires Contraction Hierarchies enabled in `config/graphhopper.yml`.
- Motorways are fetched from Overpass in a single query and clipped to the Germany polygon before sampling.
- Overpass cache refresh requires confirmation when older than 90 days (config-driven).
- An HPC stations layer (`hpc_sites`) is generated from the BNetzA CSV.
- Frontend uses vector tiles for distance lines and clustered GeoJSON for HPC stations.

## Windows → Mac handoff (produce mbtiles)

After the Windows pipeline finishes, copy these intermediate artifacts to the Mac
(same paths under `data/intermediate/`):

```
data/intermediate/01_motorways_clipped.geojson
data/intermediate/02_directional_sample_points.json
data/intermediate/03_charger_checksum.json
data/intermediate/03_eligible_chargers.json
data/intermediate/05_route_distances.json
```

These files form a coupled cache set. Copying only `05_route_distances.json` is not
safe because stages 2 and 3 must match the point ids and charger set used when routing.
With the full set present, the pipeline reuses stages 1-5 and regenerates only the
processed GeoJSON, run metadata, and mbtiles outputs.

Then on Mac, run tippecanoe only (no GH needed, no PBF needed):

```bash
docker compose run --rm --no-deps pipeline
```

`--no-deps` skips starting the graphhopper container. With the intermediate cache set
in place, the pipeline reuses stages 1-5 and only rebuilds downstream processed
outputs and mbtiles.

## Render.com Deployment

Run the full pipeline on Mac first (produces mbtiles). Then:

```bash
./deploy/prepare_render_assets.sh
```

This stages mbtiles into `deploy/render/tileserver/data/` and `hpc_sites.geojson` into `deploy/render/api-data/`.

Commit and push:

```bash
git add deploy/render
git commit -m "chore: update pipeline outputs"
git push
```

Render builds Docker images from your repo on every push (configured via `render.yaml` Blueprint).
If service names differ from defaults, adjust `VITE_TILESERVER_BASE` in `render.yaml`.
