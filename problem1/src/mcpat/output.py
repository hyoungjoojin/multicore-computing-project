import re

from core.types import UNCORE, Block

SEPARATOR = "*" * 89
CORE_UNITS = {
    "Instruction Fetch Unit": "IFU",
    "Renaming Unit": "RNU",
    "Load Store Unit": "LSU",
    "Memory Management Unit": "MMU",
    "Execution Unit": "EXU",
}
UNCORE_SECTIONS = {"L2": "L2", "BUSES": "NoC", "Memory Controller": "MC"}
L1_CACHES = {"Instruction Cache": "L1I", "Data Cache": "L1D"}
L1_HOSTS = {"L1I": "IFU", "L1D": "LSU"}
VALUE = re.compile(r"^\s*(Area|Peak Dynamic|Subthreshold Leakage|Gate Leakage|Runtime Dynamic) = ([-\d.e+]+)")


class McPATOutputError(ValueError):
    pass


def is_complete(text: str) -> bool:
    return "Processor:" in text


def parse(text: str) -> dict[str, Block]:
    if not is_complete(text):
        raise McPATOutputError(f"McPAT did not produce results:\n{text[-500:]}")
    blocks: list[Block] = []
    cores = 0
    for title, units in _sections(text):
        if title == "Core":
            blocks += _core_blocks(f"core{cores}", units)
            cores += 1
        elif title in UNCORE_SECTIONS:
            blocks.append(_block(UNCORE_SECTIONS[title], UNCORE, units[title]))

    power = {b.name: b for b in blocks}
    missing = set(UNCORE_SECTIONS.values()) - power.keys()
    if missing:
        raise McPATOutputError(f"McPAT output is missing sections {sorted(missing)}")
    return power


def parse_l1(text: str) -> dict[str, Block]:
    cores = [units for title, units in _sections(text) if title == "Core"]
    return {
        f"core{i}_{short}": _block(f"core{i}_{short}", f"core{i}", units[name])
        for i, units in enumerate(cores)
        for name, short in L1_CACHES.items()
    }


def l1_host(name: str) -> str:
    core, short = name.rsplit("_", 1)
    return f"{core}_{L1_HOSTS[short]}"


def _sections(text: str) -> list[tuple[str, dict[str, dict[str, float]]]]:
    sections = []
    for chunk in text.split(SEPARATOR)[3:]:
        lines = [line for line in chunk.splitlines() if line.strip()]
        if not lines:
            continue
        units: dict[str, dict[str, float]] = {}
        path: list[tuple[int, str]] = []
        for line in lines:
            value = VALUE.match(line)
            if value:
                units.setdefault(path[-1][1], {})[value.group(1)] = float(value.group(2))
                continue
            indent = len(line) - len(line.lstrip())
            while path and path[-1][0] >= indent:
                path.pop()
            path.append((indent, line.strip().rstrip(":").strip()))
        sections.append((lines[0].strip().rstrip(":"), units))
    return sections


def _block(name: str, group: str, values: dict[str, float]) -> Block:
    return Block(
        name=name,
        group=group,
        area_mm2=values["Area"],
        dynamic_w=values["Runtime Dynamic"],
        leakage_w=values["Subthreshold Leakage"] + values.get("Gate Leakage", 0.0),
        peak_dynamic_w=values["Peak Dynamic"],
    )


def _core_blocks(core: str, units: dict[str, dict[str, float]]) -> list[Block]:
    blocks = [_block(f"{core}_{short}", core, units[name]) for name, short in CORE_UNITS.items()]
    whole = _block(core, core, units["Core"])
    rest = Block(
        name=f"{core}_other",
        group=core,
        area_mm2=max(whole.area_mm2 - sum(b.area_mm2 for b in blocks), 1e-3),
        dynamic_w=max(whole.dynamic_w - sum(b.dynamic_w for b in blocks), 0.0),
        leakage_w=max(whole.leakage_w - sum(b.leakage_w for b in blocks), 0.0),
        peak_dynamic_w=max(whole.peak_dynamic_w - sum(b.peak_dynamic_w for b in blocks), 0.0),
    )
    return blocks + [rest]
