from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import AppConfig

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config" / "default.yaml"
RUN_META = ROOT / "data" / "processed" / "run_metadata.json"
HPC_GEOJSON = ROOT / "data" / "processed" / "hpc_sites.geojson"
PROCESSED_DIR = ROOT / "data" / "processed"

app = FastAPI(title="HPC Map API", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/config")
def config() -> dict:
    cfg = AppConfig.from_file(CONFIG_PATH)
    return cfg.model_dump()


@app.get("/metadata")
def metadata() -> dict:
    cfg = AppConfig.from_file(CONFIG_PATH)
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "distance_objective": cfg.distance_core.objective,
        "distance_road_class": cfg.distance_core.road_class,
        "distance_mbtiles_path": cfg.tiles.distance_mbtiles_path,
        "hpc_geojson_layer_prefix": cfg.tiles.hpc_layer_prefix,
    }
    if RUN_META.exists():
        out["last_pipeline_run"] = json.loads(RUN_META.read_text(encoding="utf-8"))
    return out


@app.get("/layers/status")
def layer_status() -> dict:
    cfg = AppConfig.from_file(CONFIG_PATH)
    distance_mbtiles = ROOT / cfg.tiles.distance_mbtiles_path
    return {
        "distance_mbtiles_exists": distance_mbtiles.exists(),
        "hpc_geojson_exists": HPC_GEOJSON.exists(),
    }


@app.get("/layers/hpc-sites.geojson")
def hpc_sites_geojson():
    if not HPC_GEOJSON.exists():
        return JSONResponse(status_code=404, content={"error": "missing hpc sites layer"})
    return JSONResponse(content=json.loads(HPC_GEOJSON.read_text(encoding="utf-8")))


@app.get("/layers/{layer_name}.geojson")
def named_geojson_layer(layer_name: str):
    if "/" in layer_name or ".." in layer_name:
        return JSONResponse(status_code=404, content={"error": "unknown layer"})
    path = PROCESSED_DIR / f"{layer_name}.geojson"
    if not path.exists():
        return JSONResponse(status_code=404, content={"error": "missing layer"})
    return JSONResponse(content=json.loads(path.read_text(encoding="utf-8")))
