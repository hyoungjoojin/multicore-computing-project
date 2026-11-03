import math
from dataclasses import dataclass

from core.types import UNCORE, Block


@dataclass
class Rect:
    name: str
    width: float
    height: float
    left: float
    bottom: float


def build(power: dict[str, Block]) -> list[Rect]:
    uncore = [b for b in power.values() if b.group == UNCORE]
    cores: dict[str, list[Block]] = {}
    for block in power.values():
        if block.group != UNCORE:
            cores.setdefault(block.group, []).append(block)

    cols = next(d for d in range(math.ceil(math.sqrt(len(cores))), len(cores) + 1) if len(cores) % d == 0)
    tile = max(math.sqrt(sum(b.area_mm2 for b in blocks)) for blocks in cores.values()) * 1e-3

    rects = _stack(uncore, left=0.0, bottom=0.0, width=cols * tile)
    base = sum(r.height for r in rects)
    for i, blocks in enumerate(cores.values()):
        tile_rects = _stack(blocks, left=(i % cols) * tile, bottom=base + (i // cols) * tile, width=tile)
        tile_rects[-1].height += tile - sum(r.height for r in tile_rects)
        rects += tile_rects
    return rects


def render(rects: list[Rect]) -> str:
    return "".join(f"{r.name}\t{r.width:.9f}\t{r.height:.9f}\t{r.left:.9f}\t{r.bottom:.9f}\n" for r in rects)


def _stack(blocks: list[Block], left: float, bottom: float, width: float) -> list[Rect]:
    rects = []
    for block in blocks:
        height = block.area_mm2 * 1e-6 / width
        rects.append(Rect(block.name, width, height, left, bottom))
        bottom += height
    return rects
