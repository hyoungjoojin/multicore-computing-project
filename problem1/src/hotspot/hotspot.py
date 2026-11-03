from pathlib import Path

from core import shell
from core.types import KELVIN, Block
from hotspot import floorplan


class HotSpot:
    def __init__(self, binary: Path, config: Path, grid: int, workdir: Path) -> None:
        self.binary = binary
        self.config = config
        self.grid = grid
        self.workdir = workdir
        self.ambient_c = _config_value(config, "-ambient") - KELVIN
        self.floorplan: list[floorplan.Rect] = []

    def solve(self, power: dict[str, Block]) -> dict[str, float]:
        self.workdir.mkdir(parents=True, exist_ok=True)
        flp, ptrace, steady, grid = (
            self.workdir / f"chip.{ext}" for ext in ("flp", "ptrace", "steady", "grid.steady")
        )
        names = list(power)
        self.floorplan = floorplan.build(power)
        flp.write_text(floorplan.render(self.floorplan))
        ptrace.write_text("\t".join(names) + "\n" + "\t".join(f"{power[n].total_w:.6f}" for n in names) + "\n")
        shell.run(
            [
                self.binary,
                "-c", self.config,
                "-f", flp,
                "-p", ptrace,
                "-model_type", "grid",
                "-grid_rows", self.grid,
                "-grid_cols", self.grid,
                "-leakage_used", "0",
                "-steady_file", steady,
                "-grid_steady_file", grid,
            ],
            cwd=self.workdir,
            log=self.workdir / "hotspot.log",
        )
        temps = {}
        for line in steady.read_text().splitlines():
            name, kelvin = line.split()
            if name in power:
                temps[name] = float(kelvin) - KELVIN
        return temps

    def thermal_map(self) -> list[list[float]]:
        lines = (self.workdir / "chip.grid.steady").read_text().splitlines()
        silicon = [float(line.split()[1]) - KELVIN for line in lines[1 : 1 + self.grid * self.grid]]
        return [silicon[row * self.grid : (row + 1) * self.grid] for row in range(self.grid)]


def _config_value(config: Path, key: str) -> float:
    for line in config.read_text().splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[0] == key:
            return float(fields[1])
    raise KeyError(f"{config} does not set {key}")
