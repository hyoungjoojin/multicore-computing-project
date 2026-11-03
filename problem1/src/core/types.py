from dataclasses import dataclass

KELVIN = 273.15
UNCORE = "uncore"


@dataclass(frozen=True)
class CorePerformance:
    instructions: float
    active_cycles: float
    active_fraction: float
    ipc: float


@dataclass(frozen=True)
class Performance:
    sim_seconds: float
    cycles: int
    instructions: float
    ipc: float
    throughput_mips: float
    active_fraction: float
    active_ipc: float
    l1i_miss_rate: float
    l1d_miss_rate: float
    l2_miss_rate: float
    l1d_mpki: float
    l2_mpki: float
    branch_mispredict_rate: float
    branch_mpki: float
    squashed_pki: float
    no_commit_fraction: float
    icache_stall_fraction: float
    rename_blocked_fraction: float
    serialize_stall_fraction: float
    rob_full_pki: float
    iq_full_pki: float
    lq_full_pki: float
    sq_full_pki: float
    noc_packets_pki: float
    dram_read_bytes: float
    dram_write_bytes: float
    dram_bandwidth_gbps: float
    per_core: tuple[CorePerformance, ...]


@dataclass(frozen=True)
class Block:
    name: str
    group: str
    area_mm2: float
    dynamic_w: float
    leakage_w: float
    peak_dynamic_w: float

    @property
    def total_w(self) -> float:
        return self.dynamic_w + self.leakage_w
