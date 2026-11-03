import math
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from core import shell
from core.types import KELVIN, Block
from gem5.result import Gem5Result
from mcpat import inputs, output

GRID_MIN_K, GRID_MAX_K, GRID_STEP_K = 300, 400, 10


class TemperatureOutOfRange(ValueError):
    pass


class McPATPowerModel:
    def __init__(self, binary: Path, template: Path, sim: Gem5Result, tech_nm: int, workdir: Path) -> None:
        self.binary = binary
        self.template = template
        self.sim = sim
        self.tech_nm = tech_nm
        self.workdir = workdir
        self._grid: dict[int, tuple[dict[str, Block], dict[str, Block]]] = {}

    def at(self, temps_c: dict[str, float]) -> dict[str, Block]:
        return {name: self._interpolate(self._blocks, name, temp_c) for name, temp_c in temps_c.items()}

    def at_uniform(self, temp_c: float) -> dict[str, Block]:
        names = self._blocks(_bracket(temp_c + KELVIN)[0])
        return self.at(dict.fromkeys(names, temp_c))

    def l1_caches(self, temps_c: dict[str, float]) -> dict[str, Block]:
        names = self._l1(_bracket(min(temps_c.values()) + KELVIN)[0])
        return {name: self._interpolate(self._l1, name, temps_c[output.l1_host(name)]) for name in names}

    def _interpolate(self, grid: Callable[[int], dict[str, Block]], name: str, temp_c: float) -> Block:
        low, high = _bracket(temp_c + KELVIN)
        cold, hot = grid(low)[name], grid(high)[name]
        fraction = (temp_c + KELVIN - low) / GRID_STEP_K
        return replace(cold, leakage_w=_log_lerp(cold.leakage_w, hot.leakage_w, fraction))

    def _blocks(self, temp_k: int) -> dict[str, Block]:
        return self._at_grid(temp_k)[0]

    def _l1(self, temp_k: int) -> dict[str, Block]:
        return self._at_grid(temp_k)[1]

    def _at_grid(self, temp_k: int) -> tuple[dict[str, Block], dict[str, Block]]:
        if temp_k not in self._grid:
            self._grid[temp_k] = self._run_mcpat(temp_k)
        return self._grid[temp_k]

    def _run_mcpat(self, temp_k: int) -> tuple[dict[str, Block], dict[str, Block]]:
        rundir = self.workdir / f"{temp_k}K"
        rundir.mkdir(parents=True, exist_ok=True)
        xml, out = rundir / "input.xml", rundir / "output.txt"
        rendered = inputs.build(self.template, self.sim, self.tech_nm, temp_k)
        up_to_date = xml.exists() and xml.read_text() == rendered and out.exists() and output.is_complete(out.read_text())
        if not up_to_date:
            xml.write_text(rendered)
            shell.run([self.binary, "-infile", xml, "-print_level", "5"], log=out)
        text = out.read_text()
        return output.parse(text), output.parse_l1(text)


def _bracket(temp_k: float) -> tuple[int, int]:
    if not GRID_MIN_K <= temp_k <= GRID_MAX_K:
        raise TemperatureOutOfRange(
            f"{temp_k - KELVIN:.1f} °C is outside McPAT's leakage tables "
            f"({GRID_MIN_K - KELVIN:.0f}-{GRID_MAX_K - KELVIN:.0f} °C); likely thermal runaway"
        )
    low = GRID_MIN_K + int((temp_k - GRID_MIN_K) // GRID_STEP_K) * GRID_STEP_K
    low = min(low, GRID_MAX_K - GRID_STEP_K)
    return low, low + GRID_STEP_K


def _log_lerp(a: float, b: float, t: float) -> float:
    if a <= 0 or b <= 0:
        return a + (b - a) * t
    return math.exp(math.log(a) + (math.log(b) - math.log(a)) * t)
