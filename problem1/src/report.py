import csv
import html
import io
import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from core.config import Config, Experiment
from pipeline import result_path

NAN = float("nan")
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
INK, MUTED, GRID = "#1f1f1e", "#6b6a64", "#e4e3de"
KEYS = ["experiment", "variant", "workload", "cores", "width"]
COMPONENTS = [("core", "Core (w/o L1)"), ("l1", "L1 caches"), ("l2", "L2"), ("noc", "NoC"), ("mc", "Memory controller")]
GROUPS = [("core", "Core"), ("cache", "Cache (L1+L2)"), ("interconnect", "Interconnect (NoC)"), ("mc", "Memory controller")]
NESTED = {"blocks", "l1_caches", "per_core", "thermal_map_c", "floorplan_mm"}


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    reference: str | None = None


REQUIRED = [
    Metric("speedup", "Normalized speedup", "scale"),
    Metric("power_total_w", "Average power, dynamic + static (W)"),
    Metric("temp_peak_c", "Steady-state peak temperature (°C)"),
]
SPEEDUP = [
    Metric("speedup", "Normalized speedup", "scale"),
    Metric("efficiency", "Scaling efficiency (speedup / scale)", "one"),
    Metric("ipc", "Chip IPC (instructions / cycle)"),
    Metric("active_ipc", "IPC per active core cycle"),
    Metric("active_fraction", "Core active fraction (not quiesced)"),
    Metric("throughput_mips", "Throughput (MIPS)"),
]
BOTTLENECK = [
    Metric("no_commit_fraction", "Cycles with zero commits"),
    Metric("icache_stall_fraction", "Fetch I-cache stall cycles"),
    Metric("rename_blocked_fraction", "Rename blocked cycles"),
    Metric("serialize_stall_fraction", "Rename serialize-stall cycles"),
    Metric("rob_full_pki", "ROB-full events / kilo-inst"),
    Metric("iq_full_pki", "IQ-full events / kilo-inst"),
    Metric("lq_full_pki", "LQ-full events / kilo-inst"),
    Metric("sq_full_pki", "SQ-full events / kilo-inst"),
    Metric("branch_mpki", "Branch mispredicts / kilo-inst"),
    Metric("squashed_pki", "Squashed insts / kilo-inst"),
    Metric("l1d_mpki", "L1D misses / kilo-inst"),
    Metric("l2_mpki", "L2 misses / kilo-inst"),
    Metric("l2_miss_rate", "L2 miss rate"),
    Metric("noc_packets_pki", "NoC packets / kilo-inst"),
    Metric("dram_bandwidth_gbps", "DRAM bandwidth (GB/s)"),
]
POWER = [
    Metric("power_total_w", "Average power (W)"),
    Metric("power_scaling", "Power / baseline power", "scale"),
    Metric("power_linearity", "Power scaling / config scale", "one"),
    Metric("power_per_core_w", "Average power per core (W)"),
    Metric("power_dynamic_w", "Dynamic power (W)"),
    Metric("power_leakage_w", "Static (leakage) power (W)"),
    Metric("power_leakage_share", "Static share of total power"),
    Metric("power_peak_w", "McPAT peak power (W)"),
    Metric("energy_j", "ROI energy (J)"),
    Metric("energy_per_instruction_nj", "Energy per instruction (nJ)"),
    Metric("mips_per_watt", "Performance per watt (MIPS/W)"),
    Metric("edp_js", "Energy-delay product (J·s)"),
]
THERMAL = [
    Metric("temp_peak_c", "Peak block temperature (°C)"),
    Metric("temp_grid_peak_c", "Peak grid-cell temperature (°C)"),
    Metric("temp_avg_c", "Area-weighted average temperature (°C)"),
    Metric("temp_rise_c", "Peak rise over ambient (°C)"),
    Metric("temp_gradient_c", "On-die gradient, max - min (°C)"),
    Metric("power_density_w_mm2", "Power density (W/mm²)"),
    Metric("area_mm2", "Die area (mm²)"),
]
DERIVED = ["config_scale", "speedup", "efficiency", "power_scaling", "power_linearity", "energy_scaling", "temp_delta_c"]


def write(config: Config) -> Path:
    plots = config.out / "plots"
    plots.mkdir(parents=True, exist_ok=True)
    rows, missing, sections = [], [], []
    for exp in config.experiments:
        exp_rows, exp_missing = _rows(config, exp)
        rows += exp_rows
        missing += exp_missing
        if exp_rows:
            sections.append(_experiment_section(exp, exp_rows, plots))

    columns = _columns(rows)
    _write_csv(config.out / "results.csv", columns, rows)
    block_rows = [
        {**_keys(row), **block}
        for row in rows
        for block in [*row.get("blocks", {}).values(), *row.get("l1_caches", {}).values()]
    ]
    _write_csv(config.out / "blocks.csv", _columns(block_rows), block_rows)
    core_rows = [{**_keys(row), "core": i, **core} for row in rows for i, core in enumerate(row.get("per_core", []))]
    _write_csv(config.out / "cores.csv", _columns(core_rows), core_rows)

    page = config.out / "report.html"
    page.write_text(_page(config, rows, missing, sections, columns, block_rows, core_rows))
    return page


def _rows(config: Config, exp: Experiment) -> tuple[list[dict], list[str]]:
    results, missing = {}, []
    for run in exp.runs:
        path = result_path(config, run)
        if path.exists():
            results[run.id] = json.loads(path.read_text())
        else:
            missing.append(run.id)

    rows = []
    for run in exp.runs:
        if run.id not in results:
            continue
        result = results[run.id]
        base_run = exp.baseline_for(run)
        base = results.get(base_run.id)
        scale = (run.cores / base_run.cores) * (run.width / base_run.width)
        derived = dict.fromkeys(DERIVED, NAN) | {"config_scale": scale}
        if base:
            speedup = base["sim_seconds"] / result["sim_seconds"]
            power_scaling = result["power_total_w"] / base["power_total_w"]
            derived |= {
                "speedup": speedup,
                "efficiency": speedup / scale,
                "power_scaling": power_scaling,
                "power_linearity": power_scaling / scale,
                "energy_scaling": result["energy_j"] / base["energy_j"],
                "temp_delta_c": result["temp_peak_c"] - base["temp_peak_c"],
            }
        rows.append({"experiment": exp.name, "run_id": run.id, **result, **derived})
    return rows, missing


def _experiment_section(exp: Experiment, rows: list[dict], plots: Path) -> str:
    name = exp.name
    label = _labeler(rows)
    base = ", ".join(f"{k} = {v}" for k, v in exp.speedup_baseline.items()) or "itself"
    parts = [
        f'<section id="{_esc(name)}"><h2>{_esc(name)}</h2>',
        f'<p class="muted">Configurations: {_esc(", ".join(_configs(rows, label)))} · '
        f"Workloads: {_esc(', '.join(_workloads(rows)))} · Speedup and scaling are relative to {_esc(base)} "
        f"(config scale = cores × width relative to that baseline).</p>",
        "<h3>Required metrics (per configuration, per workload)</h3>",
        _figure(_panels(f"{name}: required metrics", rows, REQUIRED, 3), plots / f"{name}_required"),
        _pivot(rows, label, [(m.key, m.label) for m in REQUIRED]),
        "<h3>Speedup: does it scale?</h3>",
        _figure(_panels(f"{name}: speedup and IPC", rows, SPEEDUP, 3), plots / f"{name}_speedup"),
        _pivot(rows, label, [(m.key, m.label) for m in SPEEDUP]),
        "<h3>Performance bottlenecks</h3>",
        _figure(_panels(f"{name}: bottlenecks", rows, BOTTLENECK, 5), plots / f"{name}_bottlenecks"),
        _pivot(rows, label, [(m.key, m.label) for m in BOTTLENECK]),
        "<h3>Power: where does it go?</h3>",
        _figure(_breakdown(f"{name}: power breakdown", rows), plots / f"{name}_power_breakdown"),
        _figure(_shares(f"{name}: power share by group", rows), plots / f"{name}_power_share"),
        _share_table(rows, label),
        "<h3>Power: does it grow linearly?</h3>",
        _figure(_panels(f"{name}: power scaling", rows, POWER, 4), plots / f"{name}_power"),
        _pivot(rows, label, [(m.key, m.label) for m in POWER] + [("config_scale", "Config scale")]),
        "<h3>Temperature</h3>",
        _figure(_panels(f"{name}: temperature", rows, THERMAL, 4), plots / f"{name}_temperature"),
        _pivot(rows, label, [(m.key, m.label) for m in THERMAL] + [("temp_delta_c", "Peak ΔT vs baseline (°C)"),
                                                                  ("temp_peak_block", "Hottest block")]),
        _figure(_thermal_maps(f"{name}: steady-state thermal maps (silicon layer)", rows), plots / f"{name}_thermal_maps"),
        "</section>",
    ]
    return "\n".join(parts)


def _panels(title: str, rows: list[dict], metrics: list[Metric], ncols: int) -> plt.Figure:
    label = _labeler(rows)
    configs, workloads = _configs(rows, label), _workloads(rows)
    lines = _numeric_sweep(rows)
    nrows = math.ceil(len(metrics) / ncols)
    fig, axes = plt.subplots(nrows, ncols, figsize=(4.2 * ncols, 3.3 * nrows), squeeze=False)
    xs = list(range(len(configs)))
    bar = 0.8 / len(workloads)
    for ax, metric in zip(axes.flat, metrics):
        for i, workload in enumerate(workloads):
            by_config = {label(r): r.get(metric.key, NAN) for r in rows if r["workload"] == workload}
            ys = [by_config.get(c, NAN) for c in configs]
            if lines:
                ax.plot(xs, ys, marker="o", color=SERIES[i], label=workload, linewidth=1.8)
            else:
                ax.bar([x + (i - (len(workloads) - 1) / 2) * bar for x in xs], ys, width=bar * 0.9,
                       color=SERIES[i], label=workload)
        if metric.reference:
            scale = {label(r): r["config_scale"] for r in rows}
            ref = [scale[c] if metric.reference == "scale" else 1.0 for c in configs]
            ax.plot(xs, ref, linestyle="--", color=MUTED, linewidth=1, label="linear" if metric.reference == "scale" else "1.0")
        _style(ax, metric.label, configs)
    for ax in list(axes.flat)[len(metrics):]:
        ax.axis("off")
    handles, labels = axes.flat[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=len(labels), frameon=False, fontsize=9, labelcolor=INK)
    fig.suptitle(title, color=INK, x=0.01, ha="left", fontsize=12)
    fig.tight_layout(rect=(0, 0.06 / nrows, 1, 1))
    return fig


def _breakdown(title: str, rows: list[dict]) -> plt.Figure:
    label = _labeler(rows)
    configs, workloads = _configs(rows, label), _workloads(rows)
    fig, axes = plt.subplots(1, len(workloads), figsize=(4 * len(workloads), 4.4), sharey=True, squeeze=False)
    for ax, workload in zip(axes[0], workloads):
        by_config = {label(r): r for r in rows if r["workload"] == workload}
        bottom = [0.0] * len(configs)
        for i, (component, name) in enumerate(COMPONENTS):
            for kind, hatch, suffix in (("dynamic", None, "dynamic"), ("leakage", "////", "static")):
                values = [by_config[c].get(f"power_{component}_{kind}_w", NAN) if c in by_config else 0.0 for c in configs]
                ax.bar(range(len(configs)), values, bottom=bottom, width=0.7, color=SERIES[i], hatch=hatch,
                       edgecolor="white", linewidth=0.5, label=f"{name} ({suffix})")
                bottom = [b + v for b, v in zip(bottom, values)]
        _style(ax, workload, configs)
    axes[0][0].set_ylabel("Average power (W)", color=MUTED)
    handles, labels = axes[0][0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=len(COMPONENTS), frameon=False, fontsize=8, labelcolor=INK)
    fig.suptitle(title, color=INK, x=0.01, ha="left", fontsize=12)
    fig.tight_layout(rect=(0, 0.14, 1, 1))
    return fig


def _shares(title: str, rows: list[dict]) -> plt.Figure:
    label = _labeler(rows)
    configs, workloads = _configs(rows, label), _workloads(rows)
    fig, axes = plt.subplots(1, len(workloads), figsize=(4 * len(workloads), 4.0), sharey=True, squeeze=False)
    for ax, workload in zip(axes[0], workloads):
        by_config = {label(r): r for r in rows if r["workload"] == workload}
        bottom = [0.0] * len(configs)
        for i, (group, name) in enumerate(GROUPS):
            values = [_share(by_config[c], f"power_{group}_w") if c in by_config else 0.0 for c in configs]
            ax.bar(range(len(configs)), values, bottom=bottom, width=0.7, color=SERIES[i], label=name)
            bottom = [b + v for b, v in zip(bottom, values)]
        _style(ax, workload, configs)
        ax.set_ylim(0, 100)
    axes[0][0].set_ylabel("Share of total power (%)", color=MUTED)
    handles, labels = axes[0][0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=len(GROUPS), frameon=False, fontsize=9, labelcolor=INK)
    fig.suptitle(title, color=INK, x=0.01, ha="left", fontsize=12)
    fig.tight_layout(rect=(0, 0.08, 1, 1))
    return fig


def _thermal_maps(title: str, rows: list[dict]) -> plt.Figure | None:
    rows = [r for r in rows if r.get("thermal_map_c")]
    if not rows:
        return None
    label = _labeler(rows)
    configs, workloads = _configs(rows, label), _workloads(rows)
    temps = [t for r in rows for line in r["thermal_map_c"] for t in line]
    vmin, vmax = min(temps), max(temps)
    fig, axes = plt.subplots(len(workloads), len(configs), figsize=(3.2 * len(configs), 3.0 * len(workloads)),
                             squeeze=False)
    image = None
    for i, workload in enumerate(workloads):
        by_config = {label(r): r for r in rows if r["workload"] == workload}
        for j, cfg in enumerate(configs):
            ax = axes[i][j]
            ax.set_xticks([])
            ax.set_yticks([])
            if cfg not in by_config:
                ax.axis("off")
                continue
            r = by_config[cfg]
            rects = r["floorplan_mm"]
            width = max(x["left"] + x["width"] for x in rects)
            height = max(x["bottom"] + x["height"] for x in rects)
            image = ax.imshow(r["thermal_map_c"], cmap="inferno", vmin=vmin, vmax=vmax,
                              extent=(0, width, 0, height), origin="upper", interpolation="bilinear")
            for x in rects:
                ax.add_patch(Rectangle((x["left"], x["bottom"]), x["width"], x["height"], fill=False,
                                       edgecolor="white", linewidth=0.3, alpha=0.6))
            ax.set_title(f"{workload} · {cfg}\npeak {r['temp_grid_peak_c']:.1f} °C", fontsize=9, color=INK)
    fig.suptitle(title, color=INK, x=0.01, ha="left", fontsize=12)
    fig.tight_layout(rect=(0, 0, 0.92, 1))
    bar = fig.colorbar(image, ax=axes.ravel().tolist(), fraction=0.02, pad=0.02)
    bar.set_label("°C", color=MUTED)
    return fig


def _figure(fig: plt.Figure | None, path: Path) -> str:
    if fig is None:
        return ""
    fig.savefig(path.with_suffix(".png"), dpi=150)
    buffer = io.StringIO()
    fig.savefig(buffer, format="svg")
    plt.close(fig)
    svg = buffer.getvalue()
    return f'<figure>{svg[svg.index("<svg"):]}<figcaption>plots/{_esc(path.name)}.png</figcaption></figure>'


def _pivot(rows: list[dict], label: Callable[[dict], str], metrics: list[tuple[str, str]]) -> str:
    configs, workloads = _configs(rows, label), _workloads(rows)
    head = "".join(f"<th>{_esc(c)}</th>" for c in configs)
    body = []
    for key, title in metrics:
        body.append(f'<tr class="group"><th colspan="{len(configs) + 1}">{_esc(title)} <code>{key}</code></th></tr>')
        for workload in workloads:
            by_config = {label(r): r.get(key, NAN) for r in rows if r["workload"] == workload}
            cells = "".join(f"<td>{_fmt(by_config.get(c, NAN))}</td>" for c in configs)
            body.append(f"<tr><th>{_esc(workload)}</th>{cells}</tr>")
        if all(isinstance(r.get(key), (int, float)) for r in rows):
            means = []
            for c in configs:
                values = [r[key] for r in rows if label(r) == c and not math.isnan(r[key])]
                means.append(_geomean(values) if values and min(values) > 0 else NAN)
            body.append(f'<tr class="mean"><th>geomean</th>{"".join(f"<td>{_fmt(m)}</td>" for m in means)}</tr>')
    return f'<div class="scroll"><table class="pivot"><thead><tr><th></th>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


def _share_table(rows: list[dict], label: Callable[[dict], str]) -> str:
    columns = [(f"power_{g}_w", f"{n} %") for g, n in GROUPS] + [
        ("power_dynamic_w", "Dynamic %"), ("power_leakage_w", "Static %")]
    columns += [(f"power_{g}_{k}_w", f"{n} {k} %") for g, n in GROUPS for k in ("dynamic", "leakage")]
    head = "".join(f"<th>{_esc(t)}</th>" for _, t in columns)
    body = "".join(
        f"<tr><th>{_esc(r['workload'])}</th><th>{_esc(label(r))}</th><td>{_fmt(r['power_total_w'])}</td>"
        + "".join(f"<td>{_fmt(_share(r, key))}</td>" for key, _ in columns)
        + "</tr>"
        for r in sorted(rows, key=lambda r: (r["workload"], r["cores"], r["width"], r["variant"]))
    )
    return (f'<div class="scroll"><table><thead><tr><th>workload</th><th>config</th><th>Total W</th>{head}</tr></thead>'
            f"<tbody>{body}</tbody></table></div>")


def _flat_table(table_id: str, columns: list[str], rows: list[dict]) -> str:
    head = "".join(f"<th>{_esc(c)}</th>" for c in columns)
    body = "".join("<tr>" + "".join(f"<td>{_fmt(r.get(c, ''))}</td>" for c in columns) + "</tr>" for r in rows)
    return (f'<input class="filter" placeholder="filter rows…" data-table="{table_id}">'
            f'<div class="scroll tall"><table id="{table_id}" class="sortable"><thead><tr>{head}</tr></thead>'
            f"<tbody>{body}</tbody></table></div>")


def _page(config: Config, rows, missing, sections, columns, block_rows, core_rows) -> str:
    expected = sum(len(exp.runs) for exp in config.experiments)
    settings = {"hardware": config.hardware, "mcpat": config.mcpat, "hotspot": config.hotspot,
                "electrothermal": config.electrothermal, "execution": config.execution}
    settings_rows = "".join(
        f"<tr><th>{_esc(section)}</th><td>{_esc(key)}</td><td>{_esc(value)}</td></tr>"
        for section, values in settings.items() for key, value in values.items()
    )
    status = f"{len(rows)} of {expected} experiment runs have results."
    if missing:
        status += " Missing: " + ", ".join(f"<code>{_esc(m)}</code>" for m in missing)
    nav = "".join(f'<a href="#{_esc(exp.name)}">{_esc(exp.name)}</a>' for exp in config.experiments)
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>Problem 1 report</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>{STYLE}</style></head><body>
<header><h1>gem5 → McPAT → HotSpot: Problem 1 results</h1>
<p class="muted">Generated {datetime.now():%Y-%m-%d %H:%M} · <span class="{"warn" if missing else ""}">{status}</span></p>
<nav>{nav}<a href="#all">all metrics</a><a href="#blocks">blocks</a><a href="#cores">cores</a><a href="#setup">setup</a></nav></header>
<main>
{"".join(sections)}
<section id="all"><h2>All recorded metrics (one row per run and experiment)</h2>
<p class="muted">Also in <code>results.csv</code>. Click a header to sort.</p>{_flat_table("t-all", columns, rows)}</section>
<section id="blocks"><h2>Per-block power, area and temperature</h2>
<p class="muted">Floorplan blocks from McPAT (core units, L2, NoC, MC) with their steady-state temperature; L1 rows are
contained in IFU/LSU and have no own temperature. Also in <code>blocks.csv</code>.</p>{_flat_table("t-blocks", _columns(block_rows), block_rows)}</section>
<section id="cores"><h2>Per-core performance</h2><p class="muted">Also in <code>cores.csv</code>.</p>
{_flat_table("t-cores", _columns(core_rows), core_rows)}</section>
<section id="setup"><h2>Setup</h2><table>{settings_rows}</table>
<p class="muted">Power from McPAT is best read relatively. Temperatures are steady state for average ROI power
with leakage–temperature feedback. Speedup = baseline ROI time / ROI time.</p></section>
</main><script>{SCRIPT}</script></body></html>"""


def _columns(rows: list[dict]) -> list[str]:
    columns = list(KEYS) if rows and "experiment" in rows[0] else []
    for row in rows:
        for key, value in row.items():
            if key not in columns and key not in NESTED and not isinstance(value, (dict, list)):
                columns.append(key)
    return columns


def _write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _keys(row: dict) -> dict:
    return {key: row[key] for key in KEYS}


def _numeric_sweep(rows: list[dict]) -> bool:
    return len({r["variant"] for r in rows}) == 1


def _labeler(rows: list[dict]) -> Callable[[dict], str]:
    swept = [axis for axis in ("variant", "cores", "width") if len({row[axis] for row in rows}) > 1] or ["cores"]
    suffix = {"variant": "", "cores": "c", "width": "w"}
    return lambda row: " ".join(f"{row[axis]}{suffix[axis]}" for axis in swept)


def _configs(rows: list[dict], label: Callable[[dict], str]) -> list[str]:
    ordered = sorted(rows, key=lambda r: (r["cores"], r["width"]))
    return list(dict.fromkeys(label(r) for r in ordered))


def _workloads(rows: list[dict]) -> list[str]:
    return list(dict.fromkeys(r["workload"] for r in rows))


def _share(row: dict, key: str) -> float:
    return 100 * row.get(key, NAN) / row["power_total_w"]


def _geomean(values: list[float]) -> float:
    return math.exp(sum(math.log(v) for v in values) / len(values))


def _fmt(value) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return _esc(value)
    if isinstance(value, int):
        return f"{value:,}"
    if math.isnan(value):
        return '<span class="muted">–</span>'
    if value == 0:
        return "0"
    if abs(value) >= 1e4:
        return f"{value:,.0f}"
    if abs(value) >= 1:
        return f"{value:,.3f}".rstrip("0").rstrip(".")
    return f"{value:.4g}"


def _esc(value) -> str:
    return html.escape(str(value))


def _style(ax: plt.Axes, title: str, configs: list[str]) -> None:
    ax.set_title(title, color=INK, fontsize=10, loc="left")
    ax.set_xticks(range(len(configs)), configs, color=MUTED)
    ax.tick_params(colors=MUTED, length=0)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)


STYLE = """
:root { --ink:#1f1f1e; --muted:#6b6a64; --line:#e4e3de; --bg:#fbfbf9; --accent:#2a78d6; }
* { box-sizing: border-box; }
body { margin: 0; font: 14px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; color: var(--ink); background: var(--bg); }
header { padding: 20px 32px 8px; border-bottom: 1px solid var(--line); background: #fff; position: sticky; top: 0; z-index: 2; }
h1 { font-size: 20px; margin: 0 0 4px; } h2 { font-size: 18px; margin: 32px 0 8px; } h3 { font-size: 15px; margin: 24px 0 8px; }
nav a { margin-right: 16px; color: var(--accent); text-decoration: none; }
main { padding: 0 32px 64px; max-width: 1800px; }
section { border-bottom: 1px solid var(--line); padding-bottom: 24px; }
.muted { color: var(--muted); } .warn { color: #b3261e; }
figure { margin: 12px 0; } figure svg { max-width: 100%; height: auto; }
figcaption { color: var(--muted); font-size: 12px; }
.scroll { overflow: auto; } .tall { max-height: 70vh; }
table { border-collapse: collapse; font-variant-numeric: tabular-nums; background: #fff; margin: 8px 0; }
th, td { border: 1px solid var(--line); padding: 3px 8px; text-align: right; white-space: nowrap; }
th { text-align: left; font-weight: 600; } thead th { position: sticky; top: 0; background: #f3f2ee; cursor: pointer; }
tr.group th { background: #f3f2ee; } tr.mean th, tr.mean td { color: var(--muted); }
code { font-size: 12px; color: var(--muted); }
.filter { margin: 8px 0; padding: 4px 8px; width: 320px; border: 1px solid var(--line); border-radius: 4px; }
"""

SCRIPT = """
document.querySelectorAll('table.sortable thead th').forEach((th, i) => th.addEventListener('click', () => {
  const body = th.closest('table').tBodies[0];
  const dir = th.dataset.dir === 'asc' ? -1 : 1; th.dataset.dir = dir === 1 ? 'asc' : 'desc';
  const key = r => { const t = r.cells[i].textContent.replace(/,/g, ''); const n = parseFloat(t); return isNaN(n) ? t : n; };
  [...body.rows].sort((a, b) => { const x = key(a), y = key(b); return (x > y ? 1 : x < y ? -1 : 0) * dir; })
    .forEach(r => body.appendChild(r));
}));
document.querySelectorAll('input.filter').forEach(input => input.addEventListener('input', () => {
  const q = input.value.toLowerCase();
  for (const r of document.getElementById(input.dataset.table).tBodies[0].rows)
    r.style.display = r.textContent.toLowerCase().includes(q) ? '' : 'none';
}));
"""
