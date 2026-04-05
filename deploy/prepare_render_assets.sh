#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RENDER_TILE_DIR="${ROOT_DIR}/deploy/render/tileserver/data"
DIST_MB="${ROOT_DIR}/data/processed/hpc_distance.mbtiles"
HPC_MB="${ROOT_DIR}/data/processed/hpc_sites.mbtiles"

if [ ! -f "${DIST_MB}" ] || [ ! -f "${HPC_MB}" ]; then
  echo "Missing MBTiles in data/processed. Run pipeline first." >&2
  exit 1
fi

mkdir -p "${RENDER_TILE_DIR}"
cp "${DIST_MB}" "${RENDER_TILE_DIR}/hpc_distance.mbtiles"
cp "${HPC_MB}" "${RENDER_TILE_DIR}/hpc_sites.mbtiles"

echo "[render] staged MBTiles into deploy/render/tileserver/data"
echo "[render] commit these files before pushing to Render:"
echo "  deploy/render/tileserver/data/hpc_distance.mbtiles"
echo "  deploy/render/tileserver/data/hpc_sites.mbtiles"
