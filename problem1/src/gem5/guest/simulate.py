import json
import sys

import m5
from gem5.components.boards.x86_board import X86Board
from gem5.components.cachehierarchies.classic.private_l1_shared_l2_cache_hierarchy import (
    PrivateL1SharedL2CacheHierarchy,
)
from gem5.components.memory import DualChannelDDR4_2400
from gem5.components.processors.cpu_types import CPUTypes
from gem5.components.processors.simple_switchable_processor import SimpleSwitchableProcessor
from gem5.isas import ISA
from gem5.resources.resource import DiskImageResource, KernelResource
from gem5.simulate.exit_event import ExitEvent
from gem5.simulate.simulator import Simulator

WIDTH_PARAMS = ("fetchWidth", "decodeWidth", "renameWidth", "dispatchWidth", "issueWidth", "commitWidth")


def configure_o3(core, spec: dict) -> None:
    for name in WIDTH_PARAMS:
        setattr(core, name, spec["width"])
    core.cacheLoadPorts = spec["hardware"]["cache_load_ports"]
    core.cacheStorePorts = spec["hardware"]["cache_store_ports"]
    for name, value in spec["params"].items():
        setattr(core, name, value)


def board_parts(spec: dict) -> dict:
    hardware = spec["hardware"]
    return {
        "clk_freq": f"{hardware['clock_ghz']}GHz",
        "memory": DualChannelDDR4_2400(size=hardware["memory_size"]),
        "cache_hierarchy": PrivateL1SharedL2CacheHierarchy(
            l1d_size=f"{hardware['l1d_size_kb']}KiB",
            l1i_size=f"{hardware['l1i_size_kb']}KiB",
            l2_size=f"{hardware['l2_size_kb_per_core'] * spec['cores']}KiB",
        ),
    }


def run_full_system(spec: dict) -> None:
    processor = SimpleSwitchableProcessor(
        starting_core_type=CPUTypes.KVM,
        switch_core_type=CPUTypes.O3,
        isa=ISA.X86,
        num_cores=spec["cores"],
    )
    for core in processor._switchable_cores["start"]:
        core.core.usePerf = False
    for core in processor._switchable_cores["switch"]:
        configure_o3(core.core, spec)
    board = X86Board(processor=processor, **board_parts(spec))
    board.set_kernel_disk_workload(
        kernel=KernelResource(local_path=spec["kernel"]),
        disk_image=DiskImageResource(local_path=spec["disk"], root_partition=spec["root_partition"]),
        readfile_contents=spec["command"],
    )

    def roi_begin():
        open(f"{m5.options.outdir}/roi_started", "w").close()
        m5.stats.reset()
        processor.switch()
        yield False

    def roi_end():
        m5.stats.dump()
        open(f"{m5.options.outdir}/roi_complete", "w").close()
        yield True

    Simulator(board=board, on_exit_event={ExitEvent.WORKBEGIN: roi_begin(), ExitEvent.WORKEND: roi_end()}).run()


def main(spec_path: str) -> None:
    with open(spec_path) as f:
        spec = json.load(f)
    run_full_system(spec)


if __name__ == "__m5_main__":
    main(sys.argv[1])
