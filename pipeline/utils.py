from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Iterable


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")


def read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def checksum_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_km = 6371.0088
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return radius_km * c


def sample_polyline(coords: Iterable[list[float]], step_m: float) -> list[list[float]]:
    points = list(coords)
    if len(points) < 2:
        return []
    out: list[list[float]] = [points[0]]
    step_km = step_m / 1000.0
    acc_km = 0.0
    prev = points[0]
    for curr in points[1:]:
        seg_km = haversine_km(prev[1], prev[0], curr[1], curr[0])
        if seg_km <= 0:
            prev = curr
            continue
        while acc_km + seg_km >= step_km:
            remain = step_km - acc_km
            t = remain / seg_km
            lon = prev[0] + (curr[0] - prev[0]) * t
            lat = prev[1] + (curr[1] - prev[1]) * t
            out.append([lon, lat])
            prev = [lon, lat]
            seg_km = haversine_km(prev[1], prev[0], curr[1], curr[0])
            acc_km = 0.0
        acc_km += seg_km
        prev = curr
    if out[-1] != points[-1]:
        out.append(points[-1])
    return out


def offset_point_from_heading(
    prev_lon: float,
    prev_lat: float,
    next_lon: float,
    next_lat: float,
    base_lon: float,
    base_lat: float,
    offset_m: float,
    side_sign: int,
) -> list[float]:
    mean_lat_rad = math.radians((prev_lat + next_lat) / 2.0)
    meters_per_deg_lon = 111320.0 * max(math.cos(mean_lat_rad), 1e-6)
    meters_per_deg_lat = 110540.0

    dx = (next_lon - prev_lon) * meters_per_deg_lon
    dy = (next_lat - prev_lat) * meters_per_deg_lat
    norm = math.hypot(dx, dy)
    if norm < 1e-6:
        return [base_lon, base_lat]

    nx = -dy / norm
    ny = dx / norm
    signed = offset_m * float(side_sign)
    off_x = nx * signed
    off_y = ny * signed

    lon = base_lon + (off_x / meters_per_deg_lon)
    lat = base_lat + (off_y / meters_per_deg_lat)
    return [lon, lat]

