#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC_DIR="${ROOT_DIR}/distance-core/src/main/java"
BUILD_DIR="${ROOT_DIR}/distance-core/build/classes/java/main"

shopt -s nullglob
GH_JARS=("${ROOT_DIR}/tools/graphhopper"/graphhopper-web-*.jar)
shopt -u nullglob
if [ ${#GH_JARS[@]} -eq 0 ]; then
  echo "No GraphHopper jar found in ${ROOT_DIR}/tools/graphhopper (expected graphhopper-web-*.jar)" >&2
  exit 1
fi
if [ ${#GH_JARS[@]} -gt 1 ]; then
  echo "Multiple GraphHopper jars found; keep only one graphhopper-web-*.jar" >&2
  exit 1
fi
GH_JAR="${GH_JARS[0]}"

mkdir -p "${BUILD_DIR}"

JAVA_FILES=()
while IFS= read -r file; do
  JAVA_FILES+=("$file")
done < <(find "${SRC_DIR}" -name '*.java' | sort)

if [ ${#JAVA_FILES[@]} -eq 0 ]; then
  echo "No Java source files found under ${SRC_DIR}" >&2
  exit 1
fi

javac -cp "${GH_JAR}" -d "${BUILD_DIR}" "${JAVA_FILES[@]}"

JAVA_OPTS_DEFAULT="-Xms1g -Xmx6g -XX:+UseG1GC"
JAVA_OPTS_RAW="${DISTANCE_CORE_JAVA_OPTS:-${JAVA_OPTS_DEFAULT}}"
read -r -a JAVA_OPTS <<< "${JAVA_OPTS_RAW}"

exec java "${JAVA_OPTS[@]}" -cp "${BUILD_DIR}:${GH_JAR}" com.hpcmap.distance.DistanceCoreMain "$@"
