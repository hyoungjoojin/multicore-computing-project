from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

WORKLOADS = ("blackscholes", "canneal", "freqmine", "streamcluster")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Variant:
    name: str
    patches: tuple[str, ...]
    params: dict[str, Any] = field(compare=False, hash=False)


@dataclass(frozen=True)
class Run:
    variant: Variant
    workload: str
    cores: int
    width: int

    @property
    def id(self) -> str:
        return f"{self.variant.name}/{self.workload}/c{self.cores}_w{self.width}"

    def axes(self) -> dict[str, Any]:
        return {"variant": self.variant.name, "cores": self.cores, "width": self.width}


@dataclass(frozen=True)
class Experiment:
    name: str
    runs: tuple[Run, ...]
    speedup_baseline: dict[str, Any]

    def baseline_for(self, run: Run) -> Run:
        target = {**run.axes(), **self.speedup_baseline}
        for candidate in self.runs:
            if candidate.workload == run.workload and candidate.axes() == target:
                return candidate
        raise ConfigError(f"experiment '{self.name}' has no baseline run for {run.id}")


@dataclass(frozen=True)
class Config:
    root: Path
    execution: dict[str, Any]
    hardware: dict[str, Any]
    mcpat: dict[str, Any]
    hotspot: dict[str, Any]
    electrothermal: dict[str, Any]
    experiments: tuple[Experiment, ...]

    @property
    def third_party(self) -> Path:
        return self.root / "third_party"

    @property
    def out(self) -> Path:
        return self.root / "out"

    def path(self, relative: str) -> Path:
        return (self.root / relative).resolve()

    def run_dir(self, run: Run) -> Path:
        return self.out / "runs" / run.id

    @property
    def runs(self) -> list[Run]:
        return list({run.id: run for exp in self.experiments for run in exp.runs}.values())


def load(path: Path) -> Config:
    raw = _read(path)
    variants = {
        name: Variant(name, tuple(spec.get("gem5_patches", [])), spec.get("o3_params", {}))
        for name, spec in raw["variants"].items()
    }
    experiments = []
    for name, exp in raw["experiments"].items():
        runs = tuple(
            Run(_find(variants, v, name), workload, cores, width)
            for v, workload, cores, width in itertools.product(exp["variants"], WORKLOADS, exp["core_counts"], exp["widths"])
        )
        experiments.append(Experiment(name, runs, exp.get("speedup_baseline", {})))
    return Config(
        root=path.resolve().parent.parent,
        execution=raw["execution"],
        hardware=raw["hardware"],
        mcpat=raw["mcpat"],
        hotspot=raw["hotspot"],
        electrothermal=raw["electrothermal"],
        experiments=tuple(experiments),
    )


def _read(path: Path) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text())
    base = raw.pop("extends", None)
    return {**_read(path.parent / base), **raw} if base else raw


def _find(variants: dict[str, Variant], name: str, experiment: str) -> Variant:
    if name not in variants:
        raise ConfigError(f"experiment '{experiment}' uses unknown variant '{name}'")
    return variants[name]
