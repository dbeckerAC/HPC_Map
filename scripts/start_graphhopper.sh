#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GH_DIR="${GH_DIR:-$ROOT_DIR/tools/graphhopper/current}"
PBF_PATH="${PBF_PATH:-$ROOT_DIR/data/raw/osm/germany-latest.osm.pbf}"
PORT="${PORT:-8989}"

if [ ! -x "$GH_DIR/graphhopper.sh" ]; then
  echo "[graphhopper] graphhopper.sh not found at $GH_DIR" >&2
  exit 1
fi

if [ ! -f "$PBF_PATH" ]; then
  echo "[graphhopper] PBF not found: $PBF_PATH" >&2
  exit 1
fi

cd "$GH_DIR"
if [ ! -d "$GH_DIR/graph-cache" ]; then
  echo "[graphhopper] importing graph from $PBF_PATH"
  ./graphhopper.sh import "$PBF_PATH"
fi

echo "[graphhopper] starting web on :$PORT"
./graphhopper.sh web "$PBF_PATH" server.port="$PORT"

