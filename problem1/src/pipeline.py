import json
import logging
from collections.abc import Iterable
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path

from core.config import Config, Run
from core.types import UNCORE, Block, Performance
from gem5 import build
from gem5.simulation import simulate
from hotspot import electrothermal
from hotspot.hotspot import HotSpot
from mcpat.model import McPATPowerModel

log = logging.getLogger(__name__)


def run_all(config: Config) -> list[str]:
    build.build_variants(config)
    runs = config.runs
    failed = []
    with ProcessPoolExecutor(max_workers=config.execution["parallel_runs"]) as pool:
        futures = {pool.submit(run_one, config, run): run for run in runs}
        for done, future in enumerate(as_completed(futures), 1):
            run = futures[future]
            try:
                r = future.result()
            except Exception:
                failed.append(run.id)
                log.exception("[%d/%d] %s failed", done, len(runs), run.id)
                continue
            log.info(
                "[%d/%d] %s: %.3f ms, %.1f W, peak %.1f °C (%d electrothermal iterations)",
                done, len(runs), run.id, r["sim_seconds"] * 1e3, r["power_total_w"],
                r["temp_peak_c"], r["electrothermal_iterations"],
            )
    return failed


def run_one(config: Config, run: Run) -> dict:
    run_dir = config.run_dir(run)
    sim = simulate(config, run)
    power_model = McPATPowerModel(
        binary=config.third_party / "mcpat" / "mcpat",
        template=config.path(config.mcpat["xml_template"]),
        sim=sim,
        tech_nm=config.mcpat["technology_nm"],
        workdir=run_dir / "mcpat",
    )
    thermal_model = HotSpot(
        binary=config.third_party / "hotspot" / "hotspot",
        config=config.path(config.hotspot["package_config"]),
        grid=config.hotspot["grid_size"],
        workdir=run_dir / "hotspot",
    )
    solution = electrothermal.solve(
        power_model, thermal_model, config.electrothermal["tolerance_c"], config.electrothermal["max_iterations"]
    )
    result = _summarize(run, sim.performance, solution, power_model.l1_caches(solution.temps_c), thermal_model)
    result_path(config, run).write_text(json.dumps(result, indent=2))
    return result


def result_path(config: Config, run: Run) -> Path:
    return config.run_dir(run) / "result.json"


def _summarize(
    run: Run, performance: Performance, solution: electrothermal.Solution, l1: dict[str, Block], thermal: HotSpot
) -> dict:
    power, temps = solution.power, solution.temps_c
    area = sum(b.area_mm2 for b in power.values())
    total_w = sum(b.total_w for b in power.values())
    leakage_w = sum(b.leakage_w for b in power.values())
    energy_j = total_w * performance.sim_seconds
    hottest = max(temps, key=temps.get)
    thermal_map = thermal.thermal_map()
    grid_temps = [t for row in thermal_map for t in row]
    return {
        "variant": run.variant.name,
        "workload": run.workload,
        "cores": run.cores,
        "width": run.width,
        **asdict(performance),
        "power_total_w": total_w,
        "power_dynamic_w": total_w - leakage_w,
        "power_leakage_w": leakage_w,
        "power_leakage_share": leakage_w / total_w,
        "power_peak_w": sum(b.peak_dynamic_w + b.leakage_w for b in power.values()),
        "power_per_core_w": total_w / run.cores,
        **_components(power, l1),
        "energy_j": energy_j,
        "energy_per_instruction_nj": energy_j / max(performance.instructions, 1) * 1e9,
        "mips_per_watt": performance.throughput_mips / total_w,
        "edp_js": energy_j * performance.sim_seconds,
        "area_mm2": area,
        "power_density_w_mm2": total_w / area,
        "temp_ambient_c": thermal.ambient_c,
        "temp_peak_c": temps[hottest],
        "temp_peak_block": hottest,
        "temp_rise_c": temps[hottest] - thermal.ambient_c,
        "temp_avg_c": sum(temps[name] * block.area_mm2 for name, block in power.items()) / area,
        "temp_grid_peak_c": max(grid_temps),
        "temp_grid_min_c": min(grid_temps),
        "temp_gradient_c": max(grid_temps) - min(grid_temps),
        "electrothermal_iterations": solution.iterations,
        "blocks": {name: {**asdict(block), "temp_c": temps[name]} for name, block in power.items()},
        "l1_caches": {name: asdict(block) for name, block in l1.items()},
        "thermal_map_c": [[round(t, 2) for t in row] for row in thermal_map],
        "floorplan_mm": [
            {"name": r.name, "left": r.left * 1e3, "bottom": r.bottom * 1e3, "width": r.width * 1e3, "height": r.height * 1e3}
            for r in thermal.floorplan
        ],
    }


def _components(power: dict[str, Block], l1: dict[str, Block]) -> dict[str, float]:
    core_dynamic, core_leakage = _split(b for b in power.values() if b.group != UNCORE)
    l1_dynamic, l1_leakage = _split(l1.values())
    uncore = {b.name.lower(): (b.dynamic_w, b.leakage_w) for b in power.values() if b.group == UNCORE}
    split = {
        "core": (core_dynamic - l1_dynamic, core_leakage - l1_leakage),
        "l1": (l1_dynamic, l1_leakage),
        **uncore,
        "cache": (l1_dynamic + uncore["l2"][0], l1_leakage + uncore["l2"][1]),
        "interconnect": uncore["noc"],
    }
    columns = {}
    for component, (dynamic, leakage) in split.items():
        columns[f"power_{component}_w"] = dynamic + leakage
        columns[f"power_{component}_dynamic_w"] = dynamic
        columns[f"power_{component}_leakage_w"] = leakage
    return columns


def _split(blocks: Iterable[Block]) -> tuple[float, float]:
    blocks = list(blocks)
    return sum(b.dynamic_w for b in blocks), sum(b.leakage_w for b in blocks)
