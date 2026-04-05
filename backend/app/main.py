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

app = FastAPI(title="HPC Map API", version="0.1.0")
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
        "routing_distance_mode": cfg.routing.distance_mode,
        "routing_provider": cfg.routing.provider,
        "distance_mbtiles_path": cfg.tiles.distance_mbtiles_path,
        "hpc_mbtiles_path": cfg.tiles.hpc_mbtiles_path,
    }
    if RUN_META.exists():
        out["last_pipeline_run"] = json.loads(RUN_META.read_text(encoding="utf-8"))
    return out


@app.get("/layers/status")
def layer_status() -> dict:
    cfg = AppConfig.from_file(CONFIG_PATH)
    distance_mbtiles = ROOT / cfg.tiles.distance_mbtiles_path
    hpc_mbtiles = ROOT / cfg.tiles.hpc_mbtiles_path
    return {
        "distance_mbtiles_exists": distance_mbtiles.exists(),
        "hpc_mbtiles_exists": hpc_mbtiles.exists(),
        "hpc_geojson_exists": HPC_GEOJSON.exists(),
    }


@app.get("/layers/hpc-sites.geojson")
def hpc_sites_geojson():
    if not HPC_GEOJSON.exists():
        return JSONResponse(status_code=404, content={"error": "missing hpc sites layer"})
    return JSONResponse(content=json.loads(HPC_GEOJSON.read_text(encoding="utf-8")))
