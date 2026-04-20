from __future__ import annotations

import argparse
import time
from pathlib import Path

from backend.app.config import AppConfig
from pipeline.stages import (
    StageError,
    publish_default_aliases,
    stage_build_hpc_points_layer,
    stage_generate_mbtiles,
    stage_normalize_chargers,
    stage_run_distance_core,
    write_run_metadata,
    write_tileserver_config,
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
    removed = 0
    purge_patterns = [
        # New artifacts
        "03_eligible_chargers*.json",
        "03_charger_checksum.json",
        "04_distance_core_stats*.json",
        "hpc_distance_segments*.geojson",
        "hpc_sites*.geojson",
        "hpc_distance*.mbtiles",
        "run_metadata*.json",
        # Legacy artifacts to purge
        "01_motorways_clipped.geojson",
        "02_directional_sample_points.json",
        "03b_motorway_exits.json",
        "04_preselected_candidates*.json",
        "05_route_distances*.json",
        "hpc_sites*.mbtiles",
        "mbtiles_generation_note*.json",
        "hpc_sites_mbtiles_generation_note*.json",
        "config.json",
    ]
    for pattern in purge_patterns:
        for p in (root / cfg.paths.intermediate_dir).glob(pattern):
            if p.exists():
                p.unlink()
                removed += 1
        for p in (root / cfg.paths.processed_dir).glob(pattern):
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

    thresholds = cfg.power_thresholds_kw()
    default_threshold = cfg.default_power_threshold_kw()
    default_token = cfg.threshold_token(default_threshold)
    if default_token not in {cfg.threshold_token(value) for value in thresholds}:
        raise StageError("analysis.default_power_threshold_kw must be included in analysis.power_thresholds_kw")

    for threshold_kw in thresholds:
        token = cfg.threshold_token(threshold_kw)
        _log(f"start threshold {token}+ kW")

        chargers, _ = _run_stage(
            f"normalize_chargers_{token}",
            lambda threshold_kw=threshold_kw: stage_normalize_chargers(cfg, root, threshold_kw),
        )
        _log(f"artifact {chargers}")

        segments, stats = _run_stage(
            f"distance_core_{token}",
            lambda threshold_kw=threshold_kw: stage_run_distance_core(cfg, root, chargers, threshold_kw),
        )
        _log(f"artifact {segments}")
        _log(f"artifact {stats}")

        hpc_sites = _run_stage(
            f"build_hpc_sites_layer_{token}",
            lambda threshold_kw=threshold_kw: stage_build_hpc_points_layer(cfg, root, chargers, threshold_kw),
        )
        _log(f"artifact {hpc_sites}")

        dist_mb = _run_stage(
            f"generate_distance_mbtiles_{token}",
            lambda threshold_kw=threshold_kw: stage_generate_mbtiles(cfg, root, segments, threshold_kw),
        )
        _log(f"artifact {dist_mb}")

        write_run_metadata(cfg, root, threshold_kw, stats)
        _log(f"done  threshold {token}+ kW")

    publish_default_aliases(cfg, root, default_threshold)
    write_tileserver_config(cfg, root)
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
