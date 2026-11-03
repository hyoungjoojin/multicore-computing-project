from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

from core.types import CorePerformance, Performance

CORE = r"board\.processor\.switch\d*\.core"
STATS_BEGIN = "---------- Begin Simulation Statistics ----------"


@dataclass(frozen=True)
class Gem5Result:
    stats: dict[str, float]
    config: dict
    cores: int
    freq_ghz: float

    @classmethod
    def load(cls, outdir: Path, cores: int, freq_ghz: float) -> Gem5Result:
        return cls(
            stats=_parse_first_dump(outdir / "stats.txt"),
            config=json.loads((outdir / "config.json").read_text()),
            cores=cores,
            freq_ghz=freq_ghz,
        )

    def stat(self, key: str, default: float | None = None) -> float:
        if key in self.stats:
            return self.stats[key]
        if default is None:
            raise KeyError(f"stat '{key}' not found in gem5 output")
        return default

    def stat_sum(self, pattern: str) -> float:
        regex = re.compile(pattern)
        return sum(v for k, v in self.stats.items() if regex.fullmatch(k))

    def core_prefix(self, i: int) -> str:
        return f"board.processor.switch{self._index(i)}.core"

    def l1i_prefix(self, i: int) -> str:
        return f"board.cache_hierarchy.l1icaches{self._index(i)}"

    def l1d_prefix(self, i: int) -> str:
        return f"board.cache_hierarchy.l1dcaches{self._index(i)}"

    def core_stat(self, i: int, name: str, optional: bool = False) -> float:
        key = f"{self.core_prefix(i)}.{name}"
        if key in self.stats:
            return self.stats[key]
        reported_by_another_core = any(f"{self.core_prefix(j)}.{name}" in self.stats for j in range(self.cores))
        if optional or reported_by_another_core:
            return 0.0
        return self.stat(key)

    def core_config(self, i: int) -> dict:
        return self.config["board"]["processor"]["switch"][i]["core"]

    def cache_config(self, name: str, i: int | None = None) -> dict:
        node = self.config["board"]["cache_hierarchy"][name]
        return node if i is None else node[i]

    def memory_channels(self) -> int:
        controllers = self.config["board"]["memory"]["mem_ctrl"]
        return len(controllers) if isinstance(controllers, list) else 1

    @property
    def cycles(self) -> int:
        return round(self.stat("simSeconds") * self.freq_ghz * 1e9)

    @property
    def performance(self) -> Performance:
        instructions = self._core_sum("commitStats0.numInsts")
        kilo_instructions = max(instructions, 1) / 1e3
        active_cycles = self._core_sum("numCycles")
        busy = max(active_cycles, 1)
        reads = self.stat_sum(r"board\.memory\.mem_ctrl\d*\.dram\.bytesRead::total")
        writes = self.stat_sum(r"board\.memory\.mem_ctrl\d*\.dram\.bytesWritten::total")
        seconds = self.stat("simSeconds")
        return Performance(
            sim_seconds=seconds,
            cycles=self.cycles,
            instructions=instructions,
            ipc=instructions / max(self.cycles, 1),
            throughput_mips=instructions / seconds / 1e6,
            active_fraction=active_cycles / max(self.cycles * self.cores, 1),
            active_ipc=instructions / busy,
            l1i_miss_rate=self._miss_rate(r"l1icaches\d*"),
            l1d_miss_rate=self._miss_rate(r"l1dcaches\d*"),
            l2_miss_rate=self._miss_rate("l2cache"),
            l1d_mpki=self._cache_stat(r"l1dcaches\d*", "overallMisses") / kilo_instructions,
            l2_mpki=self._cache_stat("l2cache", "overallMisses") / kilo_instructions,
            branch_mispredict_rate=self._core_sum("commit.branchMispredicts")
            / max(self._core_sum("commitStats0.committedControl::IsControl"), 1),
            branch_mpki=self._core_sum("commit.branchMispredicts") / kilo_instructions,
            squashed_pki=self._core_sum("commit.commitSquashedInsts") / kilo_instructions,
            no_commit_fraction=self._core_sum("commit.numCommittedDist::0") / busy,
            icache_stall_fraction=self._core_sum("fetchStats0.icacheStallCycles") / busy,
            rename_blocked_fraction=self._core_sum("rename.status::Blocked") / busy,
            serialize_stall_fraction=self._core_sum("rename.status::SerializeStall") / busy,
            rob_full_pki=self._core_sum("rename.ROBFullEvents") / kilo_instructions,
            iq_full_pki=self._core_sum("rename.IQFullEvents") / kilo_instructions,
            lq_full_pki=self._core_sum("rename.LQFullEvents") / kilo_instructions,
            sq_full_pki=self._core_sum("rename.SQFullEvents") / kilo_instructions,
            noc_packets_pki=self.stat("board.cache_hierarchy.l2bus.pktCount::total", 0.0) / kilo_instructions,
            dram_read_bytes=reads,
            dram_write_bytes=writes,
            dram_bandwidth_gbps=(reads + writes) / seconds / 1e9,
            per_core=tuple(self._core_performance(i) for i in range(self.cores)),
        )

    def active_cycles(self, i: int) -> float:
        return min(self.core_stat(i, "numCycles"), self.cycles)

    def _core_performance(self, i: int) -> CorePerformance:
        instructions = self.core_stat(i, "commitStats0.numInsts")
        active = self.active_cycles(i)
        return CorePerformance(
            instructions=instructions,
            active_cycles=active,
            active_fraction=active / max(self.cycles, 1),
            ipc=instructions / max(active, 1),
        )

    def _core_sum(self, name: str) -> float:
        return self.stat_sum(rf"{CORE}\.{re.escape(name)}")

    def _cache_stat(self, cache: str, name: str) -> float:
        return self.stat_sum(rf"board\.cache_hierarchy\.{cache}\.{name}::total")

    def _miss_rate(self, cache: str) -> float:
        return self._cache_stat(cache, "overallMisses") / max(self._cache_stat(cache, "overallAccesses"), 1)

    def _index(self, i: int) -> str:
        return "" if self.cores == 1 else str(i)


def _parse_first_dump(path: Path) -> dict[str, float]:
    dumps = path.read_text().split(STATS_BEGIN)
    if len(dumps) < 2:
        raise ValueError(f"no stats dump in {path}")
    stats = {}
    for line in dumps[1].splitlines():
        fields = line.split("#")[0].split()
        if len(fields) < 2:
            continue
        try:
            value = float(fields[1])
        except ValueError:
            continue
        stats[fields[0]] = value if math.isfinite(value) else 0.0
    return stats
