from __future__ import annotations

import argparse
import time
from pathlib import Path

from backend.app.config import AppConfig
from pipeline.stages import (
    StageError,
    stage_build_hpc_points_layer,
    stage_build_segments,
    stage_extract_motorways,
    stage_generate_hpc_sites_mbtiles,
    stage_generate_mbtiles,
    stage_normalize_chargers,
    stage_preselect_candidates,
    stage_route_distances,
    stage_sample_points,
    write_run_metadata,
)


def _log(message: str) -> None:
    print(f"[pipeline] {message}", flush=True)


def _run_stage(name: str, fn):
    start = time.perf_counter()
    _log(f"start {name}")
    result = fn()
    elapsed = time.perf_counter() - start
    _log(f"done  {name} ({elapsed:.1f}s)")
    return result


def _reset_outputs(cfg: AppConfig, root: Path) -> None:
    paths = [
        root / cfg.paths.intermediate_dir / "01_motorways_clipped.geojson",
        root / cfg.paths.intermediate_dir / "02_directional_sample_points.json",
        root / cfg.paths.intermediate_dir / "03_charger_checksum.json",
        root / cfg.paths.intermediate_dir / "03_eligible_chargers.json",
        root / cfg.paths.intermediate_dir / "03b_motorway_exits.json",
        root / cfg.paths.intermediate_dir / "04_preselected_candidates.json",
        root / cfg.paths.intermediate_dir / "05_route_distances.json",
        root / cfg.paths.processed_dir / "hpc_distance_segments.geojson",
        root / cfg.paths.processed_dir / "hpc_sites.geojson",
        root / cfg.tiles.distance_mbtiles_path,
        root / cfg.tiles.hpc_mbtiles_path,
        root / cfg.paths.processed_dir / "mbtiles_generation_note.json",
        root / cfg.paths.processed_dir / "hpc_sites_mbtiles_generation_note.json",
        root / cfg.paths.processed_dir / "run_metadata.json",
    ]
    removed = 0
    for p in paths:
        if p.exists():
            p.unlink()
            removed += 1
    _log(f"fresh mode removed {removed} artifacts")


def run(config_path: Path, fresh: bool = False) -> None:
    root = Path(__file__).resolve().parents[1]
    cfg = AppConfig.from_file(config_path)
    if fresh:
        _reset_outputs(cfg, root)

    _log(f"config {config_path}")
    _log(
        "bbox "
        f"{cfg.subset_bbox.min_lat},{cfg.subset_bbox.min_lon} -> "
        f"{cfg.subset_bbox.max_lat},{cfg.subset_bbox.max_lon}"
    )

    motorways = _run_stage("extract_motorways", lambda: stage_extract_motorways(cfg, root))
    _log(f"artifact {motorways}")
    points = _run_stage("sample_points", lambda: stage_sample_points(cfg, root, motorways))
    _log(f"artifact {points}")
    chargers, _ = _run_stage("normalize_chargers", lambda: stage_normalize_chargers(cfg, root))
    _log(f"artifact {chargers}")
    preselected = None
    mode = (cfg.routing.distance_mode or "euclidean").lower().strip()
    exact_graphhopper = mode == "graphhopper" and cfg.routing.graphhopper_exact.enabled
    if mode not in {"exit_based"} and not exact_graphhopper:
        preselected = _run_stage("preselect_candidates", lambda: stage_preselect_candidates(cfg, root, points, chargers))
        _log(f"artifact {preselected}")
    else:
        _log("skip preselect_candidates (not needed for selected routing mode)")
    routes = _run_stage("route_distances", lambda: stage_route_distances(cfg, root, points, preselected))
    _log(f"artifact {routes}")
    segments = _run_stage("build_segments", lambda: stage_build_segments(cfg, root, points, routes))
    _log(f"artifact {segments}")
    hpc_sites = _run_stage("build_hpc_sites_layer", lambda: stage_build_hpc_points_layer(cfg, root, chargers))
    _log(f"artifact {hpc_sites}")
    dist_mb = _run_stage("generate_distance_mbtiles", lambda: stage_generate_mbtiles(cfg, root, segments))
    _log(f"artifact {dist_mb}")
    hpc_mb = _run_stage("generate_hpc_mbtiles", lambda: stage_generate_hpc_sites_mbtiles(cfg, root, hpc_sites))
    _log(f"artifact {hpc_mb}")
    write_run_metadata(cfg, root)
    _log("pipeline complete")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run HPC preprocessing pipeline")
    parser.add_argument("--config", default="config/default.yaml")
    parser.add_argument("--fresh", action="store_true")
    args = parser.parse_args()
    try:
        run(Path(args.config).resolve(), fresh=args.fresh)
    except StageError as exc:
        raise SystemExit(f"Pipeline failed: {exc}") from exc


if __name__ == "__main__":
    main()
