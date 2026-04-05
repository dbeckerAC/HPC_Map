#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RENDER_TILE_DIR="${ROOT_DIR}/deploy/render/tileserver/data"
RENDER_API_DATA_DIR="${ROOT_DIR}/deploy/render/api-data"
DIST_MB="${ROOT_DIR}/data/processed/hpc_distance.mbtiles"
HPC_MB="${ROOT_DIR}/data/processed/hpc_sites.mbtiles"
HPC_GEOJSON="${ROOT_DIR}/data/processed/hpc_sites.geojson"
RUN_META="${ROOT_DIR}/data/processed/run_metadata.json"

if [ ! -f "${DIST_MB}" ] || [ ! -f "${HPC_MB}" ] || [ ! -f "${HPC_GEOJSON}" ]; then
  echo "Missing required processed outputs in data/processed. Run pipeline first." >&2
  exit 1
fi

mkdir -p "${RENDER_TILE_DIR}"
mkdir -p "${RENDER_API_DATA_DIR}"
cp "${DIST_MB}" "${RENDER_TILE_DIR}/hpc_distance.mbtiles"
cp "${HPC_MB}" "${RENDER_TILE_DIR}/hpc_sites.mbtiles"
cp "${HPC_GEOJSON}" "${RENDER_API_DATA_DIR}/hpc_sites.geojson"
if [ -f "${RUN_META}" ]; then
  cp "${RUN_META}" "${RENDER_API_DATA_DIR}/run_metadata.json"
fi

echo "[render] staged MBTiles into deploy/render/tileserver/data"
echo "[render] staged API data into deploy/render/api-data"
echo "[render] commit these files before pushing to Render:"
echo "  deploy/render/tileserver/data/hpc_distance.mbtiles"
echo "  deploy/render/tileserver/data/hpc_sites.mbtiles"
echo "  deploy/render/api-data/hpc_sites.geojson"
if [ -f "${RUN_META}" ]; then
  echo "  deploy/render/api-data/run_metadata.json"
fi
