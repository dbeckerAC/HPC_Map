from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, Field


class AnalysisConfig(BaseModel):
    default_power_threshold_kw: Optional[float] = None
    power_thresholds_kw: list[float] = Field(default_factory=list)


class DistanceCoreConfig(BaseModel):
    command: list[str] = Field(default_factory=list)
    graph_cache_path: str = "tools/graphhopper/graph-cache"
    segment_length_m: float = Field(default=250.0, gt=0)
    road_class: str = "MOTORWAY"
    objective: str = "distance"
    drop_unsnappable: bool = True


class PathsConfig(BaseModel):
    bnetza_csv: str
    intermediate_dir: str
    processed_dir: str


class TilesConfig(BaseModel):
    distance_layer_prefix: str = "hpc_distance"
    distance_layer_name: str = "hpc_distance"
    distance_mbtiles_path: str = "data/processed/hpc_distance.mbtiles"
    hpc_layer_prefix: str = "hpc_sites"


class AppConfig(BaseModel):
    min_power_kw: float = 150
    analysis: AnalysisConfig = Field(default_factory=AnalysisConfig)
    distance_core: DistanceCoreConfig = Field(default_factory=DistanceCoreConfig)
    paths: PathsConfig
    tiles: TilesConfig = Field(default_factory=TilesConfig)

    @classmethod
    def from_file(cls, path: str | Path) -> "AppConfig":
        with Path(path).open("r", encoding="utf-8") as handle:
            data: dict[str, Any] = yaml.safe_load(handle) or {}
        return cls.model_validate(data)

    def power_thresholds_kw(self) -> list[float]:
        values = self.analysis.power_thresholds_kw or [self.min_power_kw]
        out: list[float] = []
        seen: set[str] = set()
        for value in values:
            token = self.threshold_token(float(value))
            if token in seen:
                continue
            seen.add(token)
            out.append(float(value))
        return out

    def default_power_threshold_kw(self) -> float:
        if self.analysis.default_power_threshold_kw is not None:
            return float(self.analysis.default_power_threshold_kw)
        return float(self.min_power_kw)

    def distance_core_command(self) -> list[str]:
        if self.distance_core.command:
            return list(self.distance_core.command)
        if os.name == "nt":
            return ["powershell", "-ExecutionPolicy", "Bypass", "-File", "scripts/run_distance_core.ps1"]
        return ["bash", "distance-core/run_distance_core.sh"]

    @staticmethod
    def threshold_token(value: float) -> str:
        if float(value).is_integer():
            return str(int(value))
        return str(value).replace(".", "p")
