#!/usr/bin/env bash
set -euo pipefail

# Downloads a GraphHopper release asset with curl.
# Usage:
#   ./scripts/fetch_graphhopper.sh
#   ./scripts/fetch_graphhopper.sh 11.0
#   ./scripts/fetch_graphhopper.sh --artifact jar
#   ./scripts/fetch_graphhopper.sh 11.0 --artifact jar
#   GH_VERSION=11.0 GH_INSTALL_DIR=tools/graphhopper ./scripts/fetch_graphhopper.sh

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GH_VERSION="${GH_VERSION:-11.0}"
GH_ARTIFACT="${GH_ARTIFACT:-zip}" # zip or jar
GH_INSTALL_DIR="${GH_INSTALL_DIR:-$ROOT_DIR/tools/graphhopper}"
GH_TMP_DIR="${GH_TMP_DIR:-$ROOT_DIR/data/cache/graphhopper}"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --artifact)
      shift
      GH_ARTIFACT="${1:-}"
      ;;
    --artifact=*)
      GH_ARTIFACT="${1#*=}"
      ;;
    -*)
      echo "[graphhopper] unknown option: $1" >&2
      exit 1
      ;;
    *)
      GH_VERSION="$1"
      ;;
  esac
  shift
done

if [ "$GH_ARTIFACT" != "zip" ] && [ "$GH_ARTIFACT" != "jar" ]; then
  echo "[graphhopper] invalid artifact '$GH_ARTIFACT' (expected: zip or jar)" >&2
  exit 1
fi

GH_ARCHIVE="graphhopper-web-${GH_VERSION}.${GH_ARTIFACT}"
GH_URL="https://github.com/graphhopper/graphhopper/releases/download/${GH_VERSION}/${GH_ARCHIVE}"

mkdir -p "$GH_INSTALL_DIR" "$GH_TMP_DIR"

echo "[graphhopper] version: ${GH_VERSION}"
echo "[graphhopper] artifact: ${GH_ARTIFACT}"
echo "[graphhopper] url: ${GH_URL}"

ARCHIVE_PATH="${GH_TMP_DIR}/${GH_ARCHIVE}"

if [ ! -f "${ARCHIVE_PATH}" ]; then
  echo "[graphhopper] downloading..."
  if ! curl -fL --retry 3 --retry-delay 2 -o "${ARCHIVE_PATH}" "${GH_URL}"; then
    echo "[graphhopper] failed to download ${GH_ARTIFACT} release from ${GH_URL}" >&2
    exit 1
  fi
else
  echo "[graphhopper] using cached archive ${ARCHIVE_PATH}"
fi

if [ "$GH_ARTIFACT" = "jar" ]; then
  cp -f "${ARCHIVE_PATH}" "${GH_INSTALL_DIR}/${GH_ARCHIVE}"
  ln -sfn "${GH_ARCHIVE}" "${GH_INSTALL_DIR}/current.jar"
  echo "[graphhopper] jar ready: ${GH_INSTALL_DIR}/${GH_ARCHIVE}"
  echo "[graphhopper] symlink: ${GH_INSTALL_DIR}/current.jar"
else
  EXTRACT_DIR="${GH_INSTALL_DIR}/graphhopper-web-${GH_VERSION}"
  if [ -d "${EXTRACT_DIR}" ]; then
    echo "[graphhopper] already extracted at ${EXTRACT_DIR}"
  else
    echo "[graphhopper] extracting..."
    unzip -q "${ARCHIVE_PATH}" -d "${GH_INSTALL_DIR}"
  fi
  ln -sfn "${EXTRACT_DIR}" "${GH_INSTALL_DIR}/current"
  echo "[graphhopper] zip ready"
  echo "[graphhopper] current -> ${GH_INSTALL_DIR}/current"
  echo "[graphhopper] run with: ${ROOT_DIR}/scripts/start_graphhopper.sh"
fi
