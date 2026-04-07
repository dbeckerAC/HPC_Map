from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class BBox(BaseModel):
    min_lat: float
    min_lon: float
    max_lat: float
    max_lon: float


class PreselectionConfig(BaseModel):
    count: int = Field(default=8, ge=1)
    max_air_km: float = Field(default=75, gt=0)
    grid_cell_km: float = Field(default=25, gt=0)


class ColorConfig(BaseModel):
    min_km: float = 0
    max_km: float = 20
    clamp_above_max: bool = True


class OverpassConfig(BaseModel):
    endpoints: list[str]
    timeout_seconds: int = 240
    retries_per_endpoint: int = 2
    retry_backoff_seconds: int = 3
    tile_size_deg: float = 1.2
    cache_max_age_days: int = 90
    refresh_confirmed: bool = False


class RoutingConfig(BaseModel):
    distance_mode: str = "euclidean"  # euclidean | graphhopper | exit_based
    provider: str = "graphhopper"
    graphhopper_base_url: str = "http://localhost:8989"
    route_timeout_seconds: int = 8
    max_workers: int = 12
    progress_every_points: int = 250
    graphhopper_exact: "GraphHopperExactConfig" = Field(default_factory=lambda: GraphHopperExactConfig())


class GraphHopperExactConfig(BaseModel):
    enabled: bool = True
    request_retries: int = Field(default=2, ge=0)
    request_backoff_seconds: float = Field(default=0.4, ge=0)
    max_candidates_per_point: int = Field(default=5000, ge=1)
    initial_candidate_batch: int = Field(default=16, ge=1)
    max_heading_deviation_deg: float = Field(default=95.0, gt=0, le=180)
    heading_penalty_relaxed: int = Field(default=60, ge=0)
    heading_penalty_strict: int = Field(default=300, ge=0)


class PathsConfig(BaseModel):
    motorway_cache_geojson: str
    bnetza_csv: str
    intermediate_dir: str
    processed_dir: str


class TilesConfig(BaseModel):
    distance_layer_name: str = "hpc_distance"
    distance_mbtiles_path: str = "data/processed/hpc_distance.mbtiles"
    hpc_layer_name: str = "hpc_sites"
    hpc_mbtiles_path: str = "data/processed/hpc_sites.mbtiles"


class AppConfig(BaseModel):
    subset_bbox: BBox
    sampling_interval_m: float = 2000
    directional_offset_m: float = 10
    min_power_kw: float = 150
    preselection: PreselectionConfig
    color: ColorConfig
    overpass: OverpassConfig
    routing: RoutingConfig
    paths: PathsConfig
    tiles: TilesConfig = TilesConfig()

    @classmethod
    def from_file(cls, path: str | Path) -> "AppConfig":
        with Path(path).open("r", encoding="utf-8") as handle:
            data: dict[str, Any] = yaml.safe_load(handle) or {}
        return cls.model_validate(data)
