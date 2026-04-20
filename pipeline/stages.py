from __future__ import annotations

import csv
import io
import math
import shutil
import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.app.config import AppConfig
from pipeline.utils import checksum_file, ensure_dir, haversine_km, read_json, write_json


class StageError(RuntimeError):
    pass


NORMALIZE_STAGE_VERSION = 4
CHARGER_CLUSTER_RADIUS_M = 50.0


def _threshold_token(value: float) -> str:
    return AppConfig.threshold_token(float(value))


def _variant_token(value: float | str) -> str:
    return AppConfig.variant_token(value)


def _suffix_for_variant(variant: float | str | None = None) -> str:
    return f"_{_variant_token(variant)}" if variant is not None else ""


def _intermediate_path(cfg: AppConfig, root: Path, stem: str, variant: float | str | None = None) -> Path:
    suffix = _suffix_for_variant(variant)
    return root / cfg.paths.intermediate_dir / f"{stem}{suffix}.json"


def _processed_geojson_path(cfg: AppConfig, root: Path, stem: str, variant: float | str | None = None) -> Path:
    suffix = _suffix_for_variant(variant)
    return root / cfg.paths.processed_dir / f"{stem}{suffix}.geojson"


def _processed_metadata_path(cfg: AppConfig, root: Path, variant: float | str | None = None) -> Path:
    suffix = _suffix_for_variant(variant)
    return root / cfg.paths.processed_dir / f"run_metadata{suffix}.json"


def _distance_mbtiles_path(cfg: AppConfig, root: Path, variant: float | str | None = None) -> Path:
    if variant is None:
        return root / cfg.tiles.distance_mbtiles_path
    token = _variant_token(variant)
    return root / cfg.paths.processed_dir / f"{cfg.tiles.distance_layer_prefix}_{token}.mbtiles"


def _distance_core_stats_path(cfg: AppConfig, root: Path, variant: float | str) -> Path:
    return _intermediate_path(cfg, root, "04_distance_core_stats", variant)


def _cluster_chargers_within_radius(chargers: list[dict[str, Any]], radius_m: float) -> list[dict[str, Any]]:
    if len(chargers) <= 1:
        out = []
        for c in chargers:
            cp = dict(c)
            cp["site_size"] = 1
            out.append(cp)
        return out

    ordered = sorted(chargers, key=lambda c: str(c["charger_id"]))
    ref_lat = sum(float(c["lat"]) for c in ordered) / len(ordered)
    meters_per_deg_lon = 111_320.0 * max(math.cos(math.radians(ref_lat)), 1e-6)
    meters_per_deg_lat = 110_540.0

    xs: list[float] = []
    ys: list[float] = []
    for c in ordered:
        xs.append(float(c["lon"]) * meters_per_deg_lon)
        ys.append(float(c["lat"]) * meters_per_deg_lat)

    parent = list(range(len(ordered)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a: int, b: int) -> None:
        ra = find(a)
        rb = find(b)
        if ra == rb:
            return
        if ra < rb:
            parent[rb] = ra
        else:
            parent[ra] = rb

    cell_size = max(radius_m, 1.0)
    grid: dict[tuple[int, int], list[int]] = defaultdict(list)

    for i, c in enumerate(ordered):
        x = xs[i]
        y = ys[i]
        cx = int(math.floor(x / cell_size))
        cy = int(math.floor(y / cell_size))
        for nx in range(cx - 1, cx + 2):
            for ny in range(cy - 1, cy + 2):
                for j in grid.get((nx, ny), []):
                    if abs(x - xs[j]) > radius_m or abs(y - ys[j]) > radius_m:
                        continue
                    d_m = haversine_km(float(c["lat"]), float(c["lon"]), float(ordered[j]["lat"]), float(ordered[j]["lon"])) * 1000.0
                    if d_m <= radius_m:
                        union(i, j)
        grid[(cx, cy)].append(i)

    members_by_root: dict[int, list[int]] = defaultdict(list)
    for idx in range(len(ordered)):
        members_by_root[find(idx)].append(idx)

    out: list[dict[str, Any]] = []
    for members in members_by_root.values():
        best_idx = members[0]
        for idx in members[1:]:
            best = ordered[best_idx]
            cand = ordered[idx]
            best_power = float(best.get("power_kw", 0.0))
            cand_power = float(cand.get("power_kw", 0.0))
            if cand_power > best_power:
                best_idx = idx
                continue
            if cand_power == best_power and str(cand["charger_id"]) < str(best["charger_id"]):
                best_idx = idx
        representative = dict(ordered[best_idx])
        representative["site_size"] = len(members)
        out.append(representative)

    out.sort(key=lambda c: str(c["charger_id"]))
    return out


def stage_normalize_chargers(cfg: AppConfig, root: Path, threshold_kw: float) -> tuple[Path, Path]:
    raw_csv = root / cfg.paths.bnetza_csv
    if not raw_csv.exists():
        raise StageError(f"Missing charger CSV: {raw_csv}")

    checksum = checksum_file(raw_csv)
    checksum_path = root / cfg.paths.intermediate_dir / "03_charger_checksum.json"
    out_path = _intermediate_path(cfg, root, "03_eligible_chargers", threshold_kw)

    if checksum_path.exists() and out_path.exists():
        old = read_json(checksum_path)
        if old.get("sha256") == checksum and int(old.get("normalize_stage_version", 0)) == NORMALIZE_STAGE_VERSION:
            existing = read_json(out_path).get("chargers", [])
            if existing:
                return out_path, checksum_path

    text = raw_csv.read_text(encoding="latin-1")
    lines = text.splitlines()
    header_idx = None
    for idx, line in enumerate(lines):
        if line.startswith("Ladeeinrichtungs-ID;"):
            header_idx = idx
            break
    if header_idx is None:
        raise StageError("Could not detect BNetzA CSV header row")

    by_id: dict[str, dict[str, Any]] = {}
    with io.StringIO("\n".join(lines[header_idx:])) as handle:
        reader = csv.DictReader(handle, delimiter=";")
        for row in reader:
            charger_id = (row.get("Ladeeinrichtungs-ID") or "").strip()
            lat_raw = (row.get("Breitengrad") or "").strip().replace(",", ".")
            lon_raw = (row.get("Längengrad") or "").strip().replace(",", ".")
            power_raw = (row.get("Nennleistung Ladeeinrichtung [kW]") or "").strip().replace(",", ".")
            if not charger_id or not lat_raw or not lon_raw or not power_raw:
                continue
            try:
                lat = float(lat_raw)
                lon = float(lon_raw)
                power = float(power_raw)
            except ValueError:
                continue
            if power < threshold_kw:
                continue

            current = {
                "charger_id": charger_id,
                "lat": lat,
                "lon": lon,
                "power_kw": power,
                "operator": (row.get("Betreiber") or "").strip(),
                "status": (row.get("Status") or "").strip(),
            }
            prev = by_id.get(charger_id)
            if prev is None:
                by_id[charger_id] = current
                continue
            if float(current["power_kw"]) > float(prev.get("power_kw", 0.0)):
                by_id[charger_id] = current

    if not by_id:
        raise StageError("No eligible chargers after filtering.")

    clustered = _cluster_chargers_within_radius(list(by_id.values()), CHARGER_CLUSTER_RADIUS_M)

    ensure_dir(out_path.parent)
    write_json(
        out_path,
        {
            "chargers": clustered,
            "stats": {
                "input_records_after_id_dedupe": len(by_id),
                "site_representatives": len(clustered),
                "cluster_radius_m": CHARGER_CLUSTER_RADIUS_M,
            },
        },
    )
    write_json(
        checksum_path,
        {
            "sha256": checksum,
            "normalize_stage_version": NORMALIZE_STAGE_VERSION,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return out_path, checksum_path


def stage_run_distance_core(
    cfg: AppConfig,
    root: Path,
    chargers_path: Path,
    threshold_kw: float,
    variant: float | str,
    max_distance_to_motorway_m: float | None = None,
) -> tuple[Path, Path]:
    segments_path = _processed_geojson_path(cfg, root, "hpc_distance_segments", variant)
    stats_path = _distance_core_stats_path(cfg, root, variant)

    if segments_path.exists() and stats_path.exists():
        existing = read_json(segments_path).get("features", [])
        if existing:
            return segments_path, stats_path

    ensure_dir(segments_path.parent)
    ensure_dir(stats_path.parent)

    command = cfg.distance_core_command()
    if not command:
        raise StageError("distance_core.command must not be empty")

    cmd = [
        *command,
        "--graph-cache",
        str((root / cfg.distance_core.graph_cache_path).resolve()),
        "--chargers-json",
        str(chargers_path.resolve()),
        "--threshold-kw",
        str(float(threshold_kw)),
        "--segment-length-m",
        str(float(cfg.distance_core.segment_length_m)),
        "--road-class",
        str(cfg.distance_core.road_class),
        "--objective",
        str(cfg.distance_core.objective),
        "--drop-unsnappable",
        "true" if cfg.distance_core.drop_unsnappable else "false",
        "--out-segments-geojson",
        str(segments_path.resolve()),
        "--out-stats-json",
        str(stats_path.resolve()),
    ]
    if max_distance_to_motorway_m is not None:
        cmd.extend(["--max-distance-to-motorway-m", str(float(max_distance_to_motorway_m))])

    try:
        proc = subprocess.run(cmd, cwd=root, check=False, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise StageError(
            "Distance-core command not found. Check config.distance_core.command and ensure Java tooling is installed."
        ) from exc

    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        stdout = (proc.stdout or "").strip()
        details = stderr or stdout or "distance-core exited without logs"
        raise StageError(f"distance-core failed (exit {proc.returncode}): {details[:800]}")

    if not segments_path.exists():
        raise StageError(f"distance-core did not emit segments: {segments_path}")
    if not stats_path.exists():
        raise StageError(f"distance-core did not emit stats: {stats_path}")

    return segments_path, stats_path


def stage_build_hpc_points_layer(
    cfg: AppConfig,
    root: Path,
    chargers_path: Path,
    threshold_kw: float,
    variant: float | str,
    allowed_ids: set[str] | None = None,
) -> Path:
    target = _processed_geojson_path(cfg, root, "hpc_sites", variant)
    chargers = read_json(chargers_path).get("chargers", [])
    if allowed_ids is not None:
        chargers = [c for c in chargers if str(c.get("charger_id", "")) in allowed_ids]
    features = [
        {
            "type": "Feature",
            "properties": {
                "charger_id": c["charger_id"],
                "power_kw": c["power_kw"],
                "operator": c.get("operator", ""),
                "status": c.get("status", ""),
                "site_size": c.get("site_size", 1),
                "min_power_kw": threshold_kw,
            },
            "geometry": {"type": "Point", "coordinates": [c["lon"], c["lat"]]},
        }
        for c in chargers
    ]
    ensure_dir(target.parent)
    write_json(target, {"type": "FeatureCollection", "features": features})
    return target


def stage_generate_mbtiles(cfg: AppConfig, root: Path, segments_path: Path, variant: float | str) -> Path:
    mbtiles = _distance_mbtiles_path(cfg, root, variant)
    ensure_dir(mbtiles.parent)
    cmd = [
        "tippecanoe",
        "-f",
        "-o",
        str(mbtiles),
        "-l",
        cfg.tiles.distance_layer_name,
        "-Z4",
        "-z14",
        "--extend-zooms-if-still-dropping",
        "--drop-densest-as-needed",
        str(segments_path),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise StageError(
            "tippecanoe is required to generate distance MBTiles but was not found. "
            "Run the pipeline in Docker (`docker compose run --rm --no-deps pipeline`) "
            "or install tippecanoe locally."
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise StageError(
            f"tippecanoe failed for distance MBTiles ({mbtiles.name}): "
            f"{(exc.stderr or exc.stdout or '').strip()[:400]}"
        ) from exc
    return mbtiles


def write_run_metadata(cfg: AppConfig, root: Path, threshold_kw: float, variant: float | str, stats_path: Path) -> None:
    stats = read_json(stats_path) if stats_path.exists() else {}
    write_json(
        _processed_metadata_path(cfg, root, variant),
        {
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "min_power_kw": threshold_kw,
            "distance_core": {
                "graph_cache_path": cfg.distance_core.graph_cache_path,
                "segment_length_m": cfg.distance_core.segment_length_m,
                "road_class": cfg.distance_core.road_class,
                "objective": cfg.distance_core.objective,
                "drop_unsnappable": cfg.distance_core.drop_unsnappable,
            },
            "compute_stats": stats,
        },
    )


def publish_default_aliases(cfg: AppConfig, root: Path, threshold_kw: float) -> None:
    distance_mbtiles = _distance_mbtiles_path(cfg, root, threshold_kw)
    if distance_mbtiles.exists():
        shutil.copyfile(distance_mbtiles, root / cfg.tiles.distance_mbtiles_path)

    shutil.copyfile(
        _processed_geojson_path(cfg, root, "hpc_sites", threshold_kw),
        _processed_geojson_path(cfg, root, "hpc_sites"),
    )
    shutil.copyfile(
        _processed_geojson_path(cfg, root, "hpc_distance_segments", threshold_kw),
        _processed_geojson_path(cfg, root, "hpc_distance_segments"),
    )
    shutil.copyfile(
        _processed_metadata_path(cfg, root, threshold_kw),
        _processed_metadata_path(cfg, root),
    )


def write_tileserver_config(cfg: AppConfig, root: Path) -> Path:
    out_path = root / cfg.paths.processed_dir / "config.json"
    data: dict[str, dict[str, str]] = {}
    data[cfg.tiles.distance_layer_prefix] = {"mbtiles": Path(cfg.tiles.distance_mbtiles_path).name}
    for threshold_kw in cfg.power_thresholds_kw():
        token = _threshold_token(threshold_kw)
        data[f"{cfg.tiles.distance_layer_prefix}_{token}"] = {
            "mbtiles": _distance_mbtiles_path(cfg, root, threshold_kw).name
        }
    if cfg.analysis.autobahn_direct_hpc.enabled:
        token = "autobahn_direct_hpc"
        data[f"{cfg.tiles.distance_layer_prefix}_{token}"] = {
            "mbtiles": _distance_mbtiles_path(cfg, root, token).name
        }

    write_json(
        out_path,
        {
            "options": {"paths": {"root": "/data"}},
            "data": data,
        },
    )
    return out_path
