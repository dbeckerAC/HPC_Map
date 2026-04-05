from __future__ import annotations

import concurrent.futures
import csv
import hashlib
import io
import json
import math
import subprocess
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
try:
    from shapely.geometry import LineString, shape
except Exception:  # pragma: no cover
    LineString = None
    shape = None
try:
    from sklearn.neighbors import BallTree
except Exception:  # pragma: no cover
    BallTree = None

from backend.app.config import AppConfig
from pipeline.utils import (
    checksum_file,
    ensure_dir,
    haversine_km,
    offset_point_from_heading,
    read_json,
    sample_polyline,
    write_json,
)


class StageError(RuntimeError):
    pass


NORMALIZE_STAGE_VERSION = 2


def _within_bbox(lon: float, lat: float, cfg: AppConfig) -> bool:
    b = cfg.subset_bbox
    return b.min_lon <= lon <= b.max_lon and b.min_lat <= lat <= b.max_lat


def _candidate_bbox(cfg: AppConfig) -> tuple[float, float, float, float]:
    b = cfg.subset_bbox
    expand_km = cfg.preselection.max_air_km
    expand_lat_deg = expand_km / 110.574
    mean_lat = (b.min_lat + b.max_lat) / 2.0
    cos_lat = max(math.cos(math.radians(mean_lat)), 1e-6)
    expand_lon_deg = expand_km / (111.320 * cos_lat)
    return (
        b.min_lon - expand_lon_deg,
        b.min_lat - expand_lat_deg,
        b.max_lon + expand_lon_deg,
        b.max_lat + expand_lat_deg,
    )


def _overpass_query(cfg: AppConfig, bbox: tuple[float, float, float, float] | None = None) -> str:
    if bbox is None:
        b = cfg.subset_bbox
        min_lat, min_lon, max_lat, max_lon = b.min_lat, b.min_lon, b.max_lat, b.max_lon
    else:
        min_lat, min_lon, max_lat, max_lon = bbox
    return f"""
[out:json][timeout:{cfg.overpass.timeout_seconds}];
(
  way["highway"~"motorway|motorway_link"]({min_lat},{min_lon},{max_lat},{max_lon});
);
out geom;
"""


def _iter_bbox_tiles(min_lat: float, min_lon: float, max_lat: float, max_lon: float, tile_deg: float):
    lat = min_lat
    while lat < max_lat:
        next_lat = min(lat + tile_deg, max_lat)
        lon = min_lon
        while lon < max_lon:
            next_lon = min(lon + tile_deg, max_lon)
            yield (lat, lon, next_lat, next_lon)
            lon = next_lon
        lat = next_lat


def stage_extract_motorways(cfg: AppConfig, root: Path) -> Path:
    source = root / cfg.paths.motorway_cache_geojson
    source_meta = source.with_suffix(".meta.json")
    target = root / cfg.paths.intermediate_dir / "01_motorways_clipped.geojson"
    query_signature = hashlib.sha1(_overpass_query(cfg).encode("utf-8")).hexdigest()

    stale = False
    refresh_reason: str | None = None
    if source.exists() and source_meta.exists():
        meta = read_json(source_meta)
        fetched_at = meta.get("fetched_at")
        old_sig = str(meta.get("query_signature") or "")
        if old_sig != query_signature:
            stale = True
            refresh_reason = "query_changed"
        try:
            fetched = datetime.fromisoformat(fetched_at)
            if fetched < (datetime.now(timezone.utc) - timedelta(days=cfg.overpass.cache_max_age_days)):
                stale = True
                refresh_reason = refresh_reason or "cache_age"
        except Exception:
            stale = True
            refresh_reason = refresh_reason or "invalid_meta"
    elif source.exists() and not source_meta.exists():
        # Accept existing cache file as usable; create metadata lazily on next refresh.
        stale = False
    elif not source.exists():
        stale = True
        refresh_reason = "missing_cache"
    if (not source.exists()) or stale:
        # Require explicit confirmation only for age-based refreshes.
        if source.exists() and stale and refresh_reason == "cache_age" and not cfg.overpass.refresh_confirmed:
            raise StageError(
                "Motorway cache older than configured max age. "
                "Set overpass.refresh_confirmed=true to refresh."
            )
        _fetch_motorways_geojson_from_overpass(cfg, source)
        write_json(
            source_meta,
            {
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "cache_max_age_days": cfg.overpass.cache_max_age_days,
                "bbox": cfg.subset_bbox.model_dump(),
                "query_signature": query_signature,
            },
        )

    germany_polygon = _load_germany_country_polygon(root)

    raw = json.loads(source.read_text(encoding="utf-8"))
    features = []
    for f in raw.get("features", []):
        geom = f.get("geometry") or {}
        if geom.get("type") == "LineString":
            coords = geom.get("coordinates", [])
            if any(_within_bbox(c[0], c[1], cfg) for c in coords):
                for clipped in _clip_linestring_to_germany(coords, germany_polygon):
                    if len(clipped) < 2:
                        continue
                    features.append(
                        {
                            "type": "Feature",
                            "properties": f.get("properties") or {},
                            "geometry": {"type": "LineString", "coordinates": clipped},
                        }
                    )
    ensure_dir(target.parent)
    write_json(target, {"type": "FeatureCollection", "features": features})
    return target


def _fetch_motorways_geojson_from_overpass(cfg: AppConfig, out_geojson: Path) -> None:
    b = cfg.subset_bbox
    tiles = list(
        _iter_bbox_tiles(
            b.min_lat,
            b.min_lon,
            b.max_lat,
            b.max_lon,
            max(cfg.overpass.tile_size_deg, 0.2),
        )
    )
    features_by_way: dict[int, dict[str, Any]] = {}
    errors: list[str] = []
    print(f"[pipeline] overpass tiles {len(tiles)} (tile_size_deg={cfg.overpass.tile_size_deg})", flush=True)
    for idx, tile_bbox in enumerate(tiles, start=1):
        query = _overpass_query(cfg, tile_bbox)
        payload = None
        tile_ok = False
        for endpoint in cfg.overpass.endpoints:
            for attempt in range(1, cfg.overpass.retries_per_endpoint + 1):
                try:
                    resp = requests.post(
                        endpoint,
                        data=query,
                        timeout=cfg.overpass.timeout_seconds + 20,
                        headers={
                            "User-Agent": "hpc-map/0.1",
                            "Content-Type": "text/plain; charset=utf-8",
                        },
                    )
                    if resp.status_code >= 400:
                        resp = requests.post(
                            endpoint,
                            data={"data": query},
                            timeout=cfg.overpass.timeout_seconds + 20,
                            headers={"User-Agent": "hpc-map/0.1"},
                        )
                    resp.raise_for_status()
                    payload = resp.json()
                    tile_ok = True
                    break
                except Exception as exc:
                    detail = ""
                    try:
                        text = (resp.text or "").strip()[:240]  # type: ignore[name-defined]
                        if text:
                            detail = f" | body={text}"
                    except Exception:
                        detail = ""
                    errors.append(f"tile {idx}/{len(tiles)} {endpoint} attempt {attempt}: {exc}{detail}")
                    if attempt < cfg.overpass.retries_per_endpoint:
                        time.sleep(cfg.overpass.retry_backoff_seconds * attempt)
            if tile_ok:
                break
        if not tile_ok or payload is None:
            raise StageError(f"Overpass fetch failed: {' | '.join(errors[-8:])}")

        for element in payload.get("elements", []):
            if element.get("type") != "way":
                continue
            way_id = element.get("id")
            if not way_id:
                continue
            geom = element.get("geometry") or []
            if len(geom) < 2:
                continue
            coords = [[p["lon"], p["lat"]] for p in geom if "lon" in p and "lat" in p]
            if len(coords) < 2:
                continue
            feature = {
                "type": "Feature",
                "properties": {
                    "osm_way_id": way_id,
                    "highway": (element.get("tags") or {}).get("highway", ""),
                    "oneway": (element.get("tags") or {}).get("oneway", ""),
                },
                "geometry": {"type": "LineString", "coordinates": coords},
            }
            prev = features_by_way.get(int(way_id))
            if prev is None or len(coords) > len((prev.get("geometry") or {}).get("coordinates", [])):
                features_by_way[int(way_id)] = feature
        if idx % 5 == 0 or idx == len(tiles):
            print(f"[pipeline] overpass progress {idx}/{len(tiles)}", flush=True)

    features = list(features_by_way.values())
    ensure_dir(out_geojson.parent)
    write_json(out_geojson, {"type": "FeatureCollection", "features": features})


def _load_germany_country_polygon(root: Path):
    if LineString is None or shape is None:
        raise StageError("Missing shapely dependency for Germany clipping.")

    cache = root / "data" / "raw" / "osm" / "germany_country_polygon.geojson"
    if cache.exists():
        return shape(read_json(cache))

    countries_path = root / "data" / "raw" / "osm" / "countries.geojson"
    if not countries_path.exists():
        url = "https://raw.githubusercontent.com/datasets/geo-countries/master/data/countries.geojson"
        resp = requests.get(url, timeout=90, headers={"User-Agent": "hpc-map/0.1"})
        resp.raise_for_status()
        ensure_dir(countries_path.parent)
        countries_path.write_text(resp.text, encoding="utf-8")

    fc = read_json(countries_path)
    for f in fc.get("features", []):
        props = f.get("properties") or {}
        a3 = str(props.get("ISO_A3") or props.get("ADM0_A3") or "").upper()
        name = str(props.get("ADMIN") or props.get("name") or "").lower()
        if a3 == "DEU" or name == "germany":
            geom = f.get("geometry")
            if not geom:
                continue
            ensure_dir(cache.parent)
            write_json(cache, geom)
            return shape(geom)
    raise StageError("Germany polygon not found in countries dataset.")


def _clip_linestring_to_germany(coords: list[list[float]], germany_polygon) -> list[list[list[float]]]:
    if not coords or len(coords) < 2:
        return []
    try:
        line = LineString(coords)
        inter = line.intersection(germany_polygon)
    except Exception:
        return []

    out: list[list[list[float]]] = []
    gtype = inter.geom_type
    if gtype == "LineString":
        out.append([[float(x), float(y)] for x, y in inter.coords])
        return out
    if gtype == "MultiLineString":
        for part in inter.geoms:
            out.append([[float(x), float(y)] for x, y in part.coords])
        return out
    if gtype == "GeometryCollection":
        for part in inter.geoms:
            if part.geom_type == "LineString":
                out.append([[float(x), float(y)] for x, y in part.coords])
            elif part.geom_type == "MultiLineString":
                for sub in part.geoms:
                    out.append([[float(x), float(y)] for x, y in sub.coords])
    return out


def stage_sample_points(cfg: AppConfig, root: Path, motorways_path: Path) -> Path:
    target = root / cfg.paths.intermediate_dir / "02_directional_sample_points.json"
    if target.exists():
        existing = read_json(target).get("points", [])
        if existing:
            return target
    data = read_json(motorways_path)
    points: list[dict[str, Any]] = []
    way_index = 0
    for f in data.get("features", []):
        props = f.get("properties") or {}
        line = (f.get("geometry") or {}).get("coordinates", [])
        sampled = sample_polyline(line, cfg.sampling_interval_m)
        if len(sampled) < 2:
            continue
        cumulative_m = [0.0]
        for i in range(1, len(sampled)):
            prev = sampled[i - 1]
            curr = sampled[i]
            cumulative_m.append(cumulative_m[-1] + haversine_km(prev[1], prev[0], curr[1], curr[0]) * 1000.0)
        # Avoid doubling one-way carriageways: use a single geometry.
        # For true bidirectional centerlines we keep both sides.
        oneway_raw = str(props.get("oneway", "")).strip().lower()
        highway = str(props.get("highway", "")).strip().lower()
        is_oneway = (
            oneway_raw in {"yes", "1", "true", "-1"}
            or (highway == "motorway" and oneway_raw not in {"no", "0", "false"})
        )
        sides = [0] if is_oneway else [-1, 1]

        for side in sides:
            for idx, (lon, lat) in enumerate(sampled):
                if idx == 0:
                    prev_lon, prev_lat = sampled[idx]
                    next_lon, next_lat = sampled[idx + 1]
                elif idx == len(sampled) - 1:
                    prev_lon, prev_lat = sampled[idx - 1]
                    next_lon, next_lat = sampled[idx]
                else:
                    prev_lon, prev_lat = sampled[idx - 1]
                    next_lon, next_lat = sampled[idx + 1]
                if side == 0:
                    off_lon, off_lat = lon, lat
                else:
                    off_lon, off_lat = offset_point_from_heading(
                        prev_lon, prev_lat, next_lon, next_lat, lon, lat, cfg.directional_offset_m, side
                    )
                points.append(
                    {
                        "id": f"w{way_index}_s{side}_{idx}",
                        "way_id": f"w{way_index}",
                        "osm_way_id": props.get("osm_way_id"),
                        "oneway": oneway_raw,
                        "side": side,
                        "seq": idx,
                        "along_m": cumulative_m[idx],
                        "lon": off_lon,
                        "lat": off_lat,
                        "centerline_lon": lon,
                        "centerline_lat": lat,
                        "offset_m": cfg.directional_offset_m,
                    }
                )
        way_index += 1
    ensure_dir(target.parent)
    write_json(target, {"points": points})
    return target


def stage_normalize_chargers(cfg: AppConfig, root: Path) -> tuple[Path, Path]:
    raw_csv = root / cfg.paths.bnetza_csv
    if not raw_csv.exists():
        raise StageError(f"Missing charger CSV: {raw_csv}")
    checksum_path = root / cfg.paths.intermediate_dir / "03_charger_checksum.json"
    out_path = root / cfg.paths.intermediate_dir / "03_eligible_chargers.json"

    checksum = checksum_file(raw_csv)
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

    dedup: dict[str, dict[str, Any]] = {}
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
            if power < cfg.min_power_kw:
                continue
            dedup[charger_id] = {
                "charger_id": charger_id,
                "lat": lat,
                "lon": lon,
                "power_kw": power,
                "operator": (row.get("Betreiber") or "").strip(),
                "status": (row.get("Status") or "").strip(),
            }
    if not dedup:
        raise StageError("No eligible chargers after filtering.")
    ensure_dir(out_path.parent)
    write_json(out_path, {"chargers": list(dedup.values())})
    write_json(
        checksum_path,
        {
            "sha256": checksum,
            "normalize_stage_version": NORMALIZE_STAGE_VERSION,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return out_path, checksum_path


def stage_preselect_candidates(cfg: AppConfig, root: Path, points_path: Path, chargers_path: Path) -> Path:
    target = root / cfg.paths.intermediate_dir / "04_preselected_candidates.json"
    if target.exists():
        existing = read_json(target).get("preselected", [])
        if existing:
            return target
    points = read_json(points_path).get("points", [])
    chargers = read_json(chargers_path).get("chargers", [])
    min_lon, min_lat, max_lon, max_lat = _candidate_bbox(cfg)
    chargers = [c for c in chargers if min_lon <= c["lon"] <= max_lon and min_lat <= c["lat"] <= max_lat]
    if not chargers:
        raise StageError("No eligible chargers in candidate bbox.")

    if BallTree is not None:
        rows = _preselect_candidates_balltree(cfg, points, chargers)
        ensure_dir(target.parent)
        write_json(target, {"preselected": rows})
        return target

    print("[pipeline] preselect_candidates using grid fallback (BallTree unavailable)", flush=True)
    rows = _preselect_candidates_grid(cfg, points, chargers)
    ensure_dir(target.parent)
    write_json(target, {"preselected": rows})
    return target


def _preselect_candidates_balltree(cfg: AppConfig, points: list[dict[str, Any]], chargers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    earth_radius_km = 6371.0088
    max_air_km = cfg.preselection.max_air_km
    candidate_count = max(1, min(cfg.preselection.count, len(chargers)))
    batch_size = 20000

    charger_rad = [
        [math.radians(c["lat"]), math.radians(c["lon"])]
        for c in chargers
    ]
    tree = BallTree(charger_rad, metric="haversine")

    rows: list[dict[str, Any]] = []
    total = len(points)
    for start in range(0, total, batch_size):
        end = min(start + batch_size, total)
        point_batch = points[start:end]
        point_rad = [
            [math.radians(p["lat"]), math.radians(p["lon"])]
            for p in point_batch
        ]
        distances_rad, indices = tree.query(point_rad, k=candidate_count)
        for idx, p in enumerate(point_batch):
            candidates = []
            for dist_rad, charger_idx in zip(distances_rad[idx], indices[idx]):
                km = float(dist_rad) * earth_radius_km
                if km <= max_air_km:
                    candidates.append(chargers[int(charger_idx)])
            rows.append({"point_id": p["id"], "candidates": candidates})
        if end % 100000 == 0 or end == total:
            print(f"[pipeline] preselect_candidates progress {end}/{total}", flush=True)
    return rows


def _preselect_candidates_grid(cfg: AppConfig, points: list[dict[str, Any]], chargers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ref_lat = (cfg.subset_bbox.min_lat + cfg.subset_bbox.max_lat) / 2.0
    km_per_lon = 111.320 * max(math.cos(math.radians(ref_lat)), 1e-6)
    km_per_lat = 110.574
    cell_km = cfg.preselection.grid_cell_km

    def grid_key(lat: float, lon: float) -> tuple[int, int]:
        x_km = lon * km_per_lon
        y_km = lat * km_per_lat
        return int(math.floor(x_km / cell_km)), int(math.floor(y_km / cell_km))

    grid: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for c in chargers:
        grid[grid_key(c["lat"], c["lon"])].append(c)

    cell_radius = max(1, int(math.ceil(cfg.preselection.max_air_km / cell_km)))
    rows = []
    for p in points:
        ranked = []
        cx, cy = grid_key(p["lat"], p["lon"])
        for dx in range(-cell_radius, cell_radius + 1):
            for dy in range(-cell_radius, cell_radius + 1):
                for c in grid.get((cx + dx, cy + dy), []):
                    air_km = haversine_km(p["lat"], p["lon"], c["lat"], c["lon"])
                    if air_km <= cfg.preselection.max_air_km:
                        ranked.append((air_km, c))
        ranked.sort(key=lambda t: t[0])
        rows.append({"point_id": p["id"], "candidates": [c for _, c in ranked[: cfg.preselection.count]]})
    return rows


def stage_route_distances(cfg: AppConfig, root: Path, points_path: Path, preselected_path: Path) -> Path:
    target = root / cfg.paths.intermediate_dir / "05_route_distances.json"
    if target.exists():
        existing = read_json(target).get("routes", [])
        if existing:
            return target
    mode = (cfg.routing.distance_mode or "euclidean").lower().strip()
    if mode not in {"euclidean", "graphhopper", "exit_based"}:
        raise StageError("routing.distance_mode must be one of: euclidean, graphhopper, exit_based")
    if mode == "graphhopper":
        if cfg.routing.provider != "graphhopper":
            raise StageError("routing.provider must be graphhopper when routing.distance_mode=graphhopper")
        _ensure_graphhopper_available(cfg)
    if mode == "exit_based":
        return _stage_route_distances_exit_based(cfg, root, points_path)

    points = {p["id"]: p for p in read_json(points_path).get("points", [])}
    preselected = read_json(preselected_path).get("preselected", [])
    out = []
    total = len(preselected)
    completed = 0
    failures = 0

    def process_row(row: dict[str, Any]) -> dict[str, Any] | None:
        pid = row["point_id"]
        point = points.get(pid)
        if not point:
            return None
        best_km = None
        best = None
        for cand in row.get("candidates", []):
            try:
                if mode == "euclidean":
                    km = haversine_km(point["lat"], point["lon"], cand["lat"], cand["lon"])
                else:
                    km = _graphhopper_route_km(cfg, point["lat"], point["lon"], cand["lat"], cand["lon"])
            except Exception:
                continue
            if best_km is None or km < best_km:
                best_km = km
                best = cand
        if best_km is None or best is None:
            return None
        return {
            "point_id": pid,
            "distance_km": best_km,
            "charger_id": best["charger_id"],
            "power_kw": best["power_kw"],
        }

    with concurrent.futures.ThreadPoolExecutor(max_workers=cfg.routing.max_workers) as pool:
        futures = [pool.submit(process_row, row) for row in preselected]
        for fut in concurrent.futures.as_completed(futures):
            completed += 1
            res = fut.result()
            if res is None:
                failures += 1
            else:
                out.append(res)
            if completed % max(cfg.routing.progress_every_points, 1) == 0:
                print(
                    f"[pipeline] route_distances progress {completed}/{total} (ok={len(out)} fail={failures})",
                    flush=True,
                )

    if not out:
        raise StageError("No routes computed from GraphHopper.")
    ensure_dir(target.parent)
    write_json(target, {"routes": out})
    return target


def _stage_route_distances_exit_based(cfg: AppConfig, root: Path, points_path: Path) -> Path:
    if BallTree is None:
        raise StageError("routing.distance_mode=exit_based requires scikit-learn BallTree.")

    target = root / cfg.paths.intermediate_dir / "05_route_distances.json"
    points = read_json(points_path).get("points", [])
    if not points:
        raise StageError("No sampled points available for exit_based mode.")

    exits = _fetch_motorway_exits(cfg, root)
    if not exits:
        raise StageError("No motorway exits found in bbox for exit_based mode.")

    # Assign exits to the nearest sampled motorway point, inheriting way and along-distance.
    point_rad = [[math.radians(p["lat"]), math.radians(p["lon"])] for p in points]
    point_tree = BallTree(point_rad, metric="haversine")
    exit_rad = [[math.radians(e["lat"]), math.radians(e["lon"])] for e in exits]
    _, nearest_idx = point_tree.query(exit_rad, k=1)

    exits_by_way: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e_idx, e in enumerate(exits):
        p = points[int(nearest_idx[e_idx][0])]
        way_id = str(p["way_id"])
        exits_by_way[way_id].append(
            {
                "exit_id": e["id"],
                "lat": e["lat"],
                "lon": e["lon"],
                "along_m": float(p.get("along_m", 0.0)),
            }
        )

    for arr in exits_by_way.values():
        arr.sort(key=lambda x: x["along_m"])

    chargers = read_json(root / cfg.paths.intermediate_dir / "03_eligible_chargers.json").get("chargers", [])
    if not chargers:
        raise StageError("No eligible chargers available for exit_based mode.")
    charger_rad = [[math.radians(c["lat"]), math.radians(c["lon"])] for c in chargers]
    charger_tree = BallTree(charger_rad, metric="haversine")
    exit_rad = [[math.radians(e["lat"]), math.radians(e["lon"])] for e in exits]
    dist_rad, ch_idx = charger_tree.query(exit_rad, k=1)
    exit_tree = BallTree(exit_rad, metric="haversine")
    earth_radius_km = 6371.0088
    exit_to_hpc: dict[str, dict[str, Any]] = {}
    for i, e in enumerate(exits):
        c = chargers[int(ch_idx[i][0])]
        exit_to_hpc[e["id"]] = {
            "distance_km": float(dist_rad[i][0]) * earth_radius_km,
            "charger_id": c["charger_id"],
            "power_kw": c["power_kw"],
        }

    def forward_direction(point: dict[str, Any]) -> bool:
        side = int(point.get("side", 0))
        oneway = str(point.get("oneway", "")).strip().lower()
        if side == 0:
            return oneway != "-1"
        return side == 1

    routes = []
    failures = 0
    fallback_used = 0
    total = len(points)
    for idx, p in enumerate(points, start=1):
        way_exits = exits_by_way.get(str(p["way_id"]), [])
        if not way_exits:
            nearest_exit = _nearest_exit_for_point(exit_tree, exits, p)
            if nearest_exit is None:
                failures += 1
                continue
            hpc = exit_to_hpc.get(nearest_exit["id"])
            if hpc is None:
                failures += 1
                continue
            fallback_used += 1
            routes.append(
                {
                    "point_id": p["id"],
                    "distance_km": haversine_km(p["lat"], p["lon"], nearest_exit["lat"], nearest_exit["lon"])
                    + float(hpc["distance_km"]),
                    "charger_id": hpc["charger_id"],
                    "power_kw": hpc["power_kw"],
                }
            )
            continue
        along = float(p.get("along_m", 0.0))
        exit_choice = None
        if forward_direction(p):
            for ex in way_exits:
                if ex["along_m"] >= along:
                    exit_choice = ex
                    break
        else:
            for ex in reversed(way_exits):
                if ex["along_m"] <= along:
                    exit_choice = ex
                    break
        if exit_choice is None:
            nearest_exit = _nearest_exit_for_point(exit_tree, exits, p)
            if nearest_exit is None:
                failures += 1
                continue
            hpc = exit_to_hpc.get(nearest_exit["id"])
            if hpc is None:
                failures += 1
                continue
            fallback_used += 1
            routes.append(
                {
                    "point_id": p["id"],
                    "distance_km": haversine_km(p["lat"], p["lon"], nearest_exit["lat"], nearest_exit["lon"])
                    + float(hpc["distance_km"]),
                    "charger_id": hpc["charger_id"],
                    "power_kw": hpc["power_kw"],
                }
            )
            continue
        hpc = exit_to_hpc.get(exit_choice["exit_id"])
        if hpc is None:
            nearest_exit = _nearest_exit_for_point(exit_tree, exits, p)
            if nearest_exit is None:
                failures += 1
                continue
            hpc2 = exit_to_hpc.get(nearest_exit["id"])
            if hpc2 is None:
                failures += 1
                continue
            fallback_used += 1
            routes.append(
                {
                    "point_id": p["id"],
                    "distance_km": haversine_km(p["lat"], p["lon"], nearest_exit["lat"], nearest_exit["lon"])
                    + float(hpc2["distance_km"]),
                    "charger_id": hpc2["charger_id"],
                    "power_kw": hpc2["power_kw"],
                }
            )
            continue
        distance_to_exit_km = abs(float(exit_choice["along_m"]) - along) / 1000.0
        routes.append(
            {
                "point_id": p["id"],
                "distance_km": distance_to_exit_km + float(hpc["distance_km"]),
                "charger_id": hpc["charger_id"],
                "power_kw": hpc["power_kw"],
            }
        )
        if idx % max(cfg.routing.progress_every_points, 1) == 0:
            print(
                f"[pipeline] route_distances progress {idx}/{total} (ok={len(routes)} fail={failures} fallback={fallback_used})",
                flush=True,
            )

    if not routes:
        raise StageError("No distances computed for exit_based mode.")
    print(f"[pipeline] exit_based fallback_used={fallback_used}", flush=True)
    ensure_dir(target.parent)
    write_json(target, {"routes": routes})
    return target


def _nearest_exit_for_point(exit_tree: BallTree, exits: list[dict[str, Any]], point: dict[str, Any]) -> dict[str, Any] | None:
    if not exits:
        return None
    p_rad = [[math.radians(point["lat"]), math.radians(point["lon"])]]
    _, idx = exit_tree.query(p_rad, k=1)
    return exits[int(idx[0][0])]


def _overpass_exits_query(cfg: AppConfig) -> str:
    b = cfg.subset_bbox
    return f"""
[out:json][timeout:{cfg.overpass.timeout_seconds}];
node["highway"="motorway_junction"]({b.min_lat},{b.min_lon},{b.max_lat},{b.max_lon});
out body;
"""


def _fetch_motorway_exits(cfg: AppConfig, root: Path) -> list[dict[str, Any]]:
    path = root / cfg.paths.intermediate_dir / "03b_motorway_exits.json"
    if path.exists():
        cached = read_json(path).get("exits", [])
        if cached:
            return cached

    query = _overpass_exits_query(cfg)
    payload = None
    errors: list[str] = []
    for endpoint in cfg.overpass.endpoints:
        for attempt in range(1, cfg.overpass.retries_per_endpoint + 1):
            try:
                resp = requests.post(
                    endpoint,
                    data=query,
                    timeout=cfg.overpass.timeout_seconds + 20,
                    headers={"User-Agent": "hpc-map/0.1", "Content-Type": "text/plain; charset=utf-8"},
                )
                if resp.status_code >= 400:
                    resp = requests.post(
                        endpoint,
                        data={"data": query},
                        timeout=cfg.overpass.timeout_seconds + 20,
                        headers={"User-Agent": "hpc-map/0.1"},
                    )
                resp.raise_for_status()
                payload = resp.json()
                break
            except Exception as exc:
                errors.append(f"{endpoint} attempt {attempt}: {exc}")
                if attempt < cfg.overpass.retries_per_endpoint:
                    time.sleep(cfg.overpass.retry_backoff_seconds * attempt)
        if payload is not None:
            break
    if payload is None:
        raise StageError(f"Motorway exit fetch failed: {' | '.join(errors[-6:])}")

    exits = []
    for el in payload.get("elements", []):
        if el.get("type") != "node":
            continue
        lat = el.get("lat")
        lon = el.get("lon")
        if lat is None or lon is None:
            continue
        exits.append(
            {
                "id": str(el.get("id")),
                "lat": float(lat),
                "lon": float(lon),
                "ref": ((el.get("tags") or {}).get("ref") or ""),
                "name": ((el.get("tags") or {}).get("name") or ""),
            }
        )
    ensure_dir(path.parent)
    write_json(path, {"exits": exits})
    return exits


def _ensure_graphhopper_available(cfg: AppConfig) -> None:
    try:
        url = (
            f"{cfg.routing.graphhopper_base_url}/route?"
            "profile=car&point=52.5000,13.4000&point=52.5200,13.4500"
            "&instructions=false&calc_points=false"
        )
        resp = requests.get(url, timeout=cfg.routing.route_timeout_seconds)
        resp.raise_for_status()
        body = resp.json()
        _ = float(body["paths"][0]["distance"])
    except Exception as exc:
        raise StageError(f"GraphHopper probe failed at {cfg.routing.graphhopper_base_url}: {exc}") from exc


def _graphhopper_route_km(cfg: AppConfig, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    url = (
        f"{cfg.routing.graphhopper_base_url}/route?"
        f"profile=car&point={lat1},{lon1}&point={lat2},{lon2}"
        "&instructions=false&calc_points=false"
    )
    resp = requests.get(url, timeout=cfg.routing.route_timeout_seconds)
    resp.raise_for_status()
    body = resp.json()
    meters = float(body["paths"][0]["distance"])
    return meters / 1000.0


def stage_build_segments(cfg: AppConfig, root: Path, points_path: Path, routes_path: Path) -> Path:
    target = root / cfg.paths.processed_dir / "hpc_distance_segments.geojson"
    points = {p["id"]: p for p in read_json(points_path).get("points", [])}
    routes = {r["point_id"]: r for r in read_json(routes_path).get("routes", [])}
    grouped: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for p in points.values():
        grouped[(p["way_id"], p["side"])].append(p)
    features = []
    for (way_id, side), arr in grouped.items():
        arr.sort(key=lambda x: x["seq"])
        for a, b in zip(arr, arr[1:]):
            ra = routes.get(a["id"])
            rb = routes.get(b["id"])
            if not ra or not rb:
                continue
            features.append(
                {
                    "type": "Feature",
                    "properties": {
                        "way_id": way_id,
                        "side": side,
                        "distance_start_km": ra["distance_km"],
                        "distance_end_km": rb["distance_km"],
                        "min_power_kw": cfg.min_power_kw,
                    },
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[a["lon"], a["lat"]], [b["lon"], b["lat"]]],
                    },
                }
            )
    ensure_dir(target.parent)
    write_json(target, {"type": "FeatureCollection", "features": features})
    return target


def stage_build_hpc_points_layer(cfg: AppConfig, root: Path, chargers_path: Path) -> Path:
    target = root / cfg.paths.processed_dir / "hpc_sites.geojson"
    chargers = read_json(chargers_path).get("chargers", [])
    features = [
        {
            "type": "Feature",
            "properties": {
                "charger_id": c["charger_id"],
                "power_kw": c["power_kw"],
                "operator": c.get("operator", ""),
                "status": c.get("status", ""),
                "min_power_kw": cfg.min_power_kw,
            },
            "geometry": {"type": "Point", "coordinates": [c["lon"], c["lat"]]},
        }
        for c in chargers
    ]
    ensure_dir(target.parent)
    write_json(target, {"type": "FeatureCollection", "features": features})
    return target


def stage_generate_mbtiles(cfg: AppConfig, root: Path, segments_path: Path) -> Path:
    mbtiles = root / cfg.tiles.distance_mbtiles_path
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
    except Exception as exc:
        write_json(
            root / cfg.paths.processed_dir / "mbtiles_generation_note.json",
            {
                "status": "not_generated",
                "reason": str(exc),
                "source_geojson": str(segments_path),
                "target_mbtiles": str(mbtiles),
            },
        )
    return mbtiles


def stage_generate_hpc_sites_mbtiles(cfg: AppConfig, root: Path, hpc_points_path: Path) -> Path:
    mbtiles = root / cfg.tiles.hpc_mbtiles_path
    ensure_dir(mbtiles.parent)
    cmd = [
        "tippecanoe",
        "-f",
        "-o",
        str(mbtiles),
        "-l",
        cfg.tiles.hpc_layer_name,
        "-Z4",
        "-z14",
        "--extend-zooms-if-still-dropping",
        "--drop-densest-as-needed",
        str(hpc_points_path),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except Exception as exc:
        write_json(
            root / cfg.paths.processed_dir / "hpc_sites_mbtiles_generation_note.json",
            {
                "status": "not_generated",
                "reason": str(exc),
                "source_geojson": str(hpc_points_path),
                "target_mbtiles": str(mbtiles),
            },
        )
    return mbtiles


def write_run_metadata(cfg: AppConfig, root: Path) -> None:
    write_json(
        root / cfg.paths.processed_dir / "run_metadata.json",
        {
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "subset_bbox": cfg.subset_bbox.model_dump(),
            "min_power_kw": cfg.min_power_kw,
            "sampling_interval_m": cfg.sampling_interval_m,
            "directional_offset_m": cfg.directional_offset_m,
            "preselection": cfg.preselection.model_dump(),
            "color": cfg.color.model_dump(),
            "routing_distance_mode": cfg.routing.distance_mode,
            "routing_provider": cfg.routing.provider,
        },
    )
