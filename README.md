# HPC Map
* This is a vibe coding app based on a functional specificatio document (FSD).*

GraphHopper-based prototype for visualizing motorway distance to eligible HPC chargers.

## Locked Architecture

- Motorways: Overpass API (cached locally)
- Distance computation: configurable (`graphhopper` exact default, `exit_based` optional)
- Preprocessing: Python pipeline with restartable artifacts
- Map delivery: MBTiles + tile server + MapLibre GL JS

## Prerequisites

- Python 3.9+
- Docker Desktop (for tileserver/api containers)
- internet access for Overpass and initial GraphHopper download
- HPC CSV at `data/raw/bnetza/Ladesaeulenregister_BNetzA_2026-03-25.csv`
- OSM PBF at `data/raw/osm/germany-latest.osm.pbf` (for GraphHopper import)

## Setup

```bash
cd /Users/Daniel/Documents/Skripte/HPC_Map
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -e .
```

Only for local frontend (optional):

```bash
cd frontend && npm install && cd ..
```

## GraphHopper (one-time install)

```bash
./scripts/fetch_graphhopper.sh
```

If you specifically want the release JAR instead of ZIP:

```bash
./scripts/fetch_graphhopper.sh 11.0 --artifact jar
```

## Start GraphHopper

```bash
./scripts/start_graphhopper.sh
```

This imports graph data on first run and starts the server on `http://localhost:8989`.

## Start GraphHopper With Docker Compose (recommended)

Prerequisites for this mode:

- run `./scripts/fetch_graphhopper.sh 11.0 --artifact jar` once (provides `tools/graphhopper/current.jar`)
- place `germany-latest.osm.pbf` in `data/raw/osm/`

Then run:

```bash
docker compose up graphhopper --remove-orphans
```

This imports on first run and then serves GraphHopper on `http://localhost:8989`.
Import completion is persisted at `tools/graphhopper/.import-complete`, so normal container restarts do not trigger a re-import.
For Germany import, set Docker Desktop memory high enough (recommended at least 10-12 GB; 16 GB is safer).

If import failed earlier (OOM or interrupted), reset and retry:

```bash
rm -f tools/graphhopper/.import-complete
rm -rf tools/graphhopper/graph-cache
docker compose up graphhopper --remove-orphans
```

Windows note:
- This setup also works on Windows with Docker Desktop + Compose.
- For full Germany import, use a machine with at least 16 GB RAM (32 GB preferred).

## Run Pipeline

```bash
docker compose run --rm pipeline
```

Current default in `config/default.yaml`:
- `routing.distance_mode: graphhopper`
- `routing.graphhopper_base_url: http://graphhopper:8989` (inside compose network)
- exact adaptive search enabled via `routing.graphhopper_exact.*` (provably best route distance with heading-constrained start)

Outputs:

- `data/intermediate/` stage artifacts
- `data/processed/hpc_distance_segments.geojson`
- `data/processed/hpc_sites.geojson`
- `data/processed/hpc_distance.mbtiles` (if `tippecanoe` installed)
- `data/processed/hpc_sites.mbtiles` (if `tippecanoe` installed)
- `data/processed/run_metadata.json`

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

- Route stage is parallelized and logs progress.
- GraphHopper exact mode uses BallTree + adaptive lower-bound pruning (no fixed top-K approximation).
- Motorway extraction uses Overpass bbox queries (internally tiled for reliability), then geometries are clipped to Germany polygon before sampling.
- Overpass cache refresh requires confirmation when older than 90 days (config-driven).
- An HPC stations layer (`hpc_sites`) is generated from CSV-filtered chargers.
- Frontend uses vector tiles for distance lines and clustered GeoJSON for HPC stations.

## Render.com Deployment

Prerequisites:
- pipeline already finished
- `data/processed/hpc_distance.mbtiles` and `data/processed/hpc_sites.mbtiles` exist

1. Stage MBTiles for Render tileserver image:

```bash
./deploy/prepare_render_assets.sh
```

This also stages `hpc_sites.geojson` for the Render API image at `deploy/render/api-data/`.

2. Commit and push to GitHub:

```bash
git add render.yaml deploy/render deploy/prepare_render_assets.sh frontend/src/main.js README.md
git commit -m "Prepare Render deployment"
git push
```

3. In Render, create a new **Blueprint** from your GitHub repo (uses `render.yaml`).

4. If service names differ, adjust in `render.yaml`:
- `VITE_TILESERVER_BASE`

to your actual Render service URLs if names differ.

Notes:
- Render builds Docker images from your repo contents and Dockerfiles during deploy.
- Images are not stored in GitHub; only source files and Dockerfiles are in GitHub.
