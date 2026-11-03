from dataclasses import dataclass
from typing import Protocol

from core.types import Block


class PowerModel(Protocol):
    def at(self, temps_c: dict[str, float]) -> dict[str, Block]: ...

    def at_uniform(self, temp_c: float) -> dict[str, Block]: ...


class ThermalModel(Protocol):
    ambient_c: float

    def solve(self, power: dict[str, Block]) -> dict[str, float]: ...


class ThermalRunaway(RuntimeError):
    pass


@dataclass(frozen=True)
class Solution:
    power: dict[str, Block]
    temps_c: dict[str, float]
    iterations: int


def solve(power_model: PowerModel, thermal_model: ThermalModel, tolerance_c: float, max_iterations: int) -> Solution:
    temps = thermal_model.solve(power_model.at_uniform(thermal_model.ambient_c))
    for iteration in range(1, max_iterations + 1):
        updated = thermal_model.solve(power_model.at(temps))
        if max(abs(updated[name] - temps[name]) for name in temps) < tolerance_c:
            return Solution(power_model.at(updated), updated, iteration)
        temps = updated
    raise ThermalRunaway(f"power and temperature did not converge within {max_iterations} iterations")
