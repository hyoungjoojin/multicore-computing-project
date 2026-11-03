import copy
import math
import xml.etree.ElementTree as ET
from functools import partial
from pathlib import Path

from gem5.result import Gem5Result

LINE_BYTES = 64
L2 = "board.cache_hierarchy.l2cache"


def build(template: Path, sim: Gem5Result, tech_nm: int, temperature_k: int) -> str:
    root = ET.parse(template).getroot()
    system = _component(root, "system")
    core_template = _component(root, "system.core0")
    index = list(system).index(core_template)
    system.remove(core_template)
    for i in range(sim.cores):
        system.insert(index + i, _clone_core(core_template, i))

    params = [_system_params(sim, tech_nm, temperature_k)] + [_core_params(sim, i) for i in range(sim.cores)]
    stats = [_system_stats(sim)] + [_core_stats(sim, i) for i in range(sim.cores)]
    for kind, groups in (("param", params), ("stat", stats)):
        for group in groups:
            for cid, values in group.items():
                _set(_component(root, cid), kind, values)
    return ET.tostring(root, encoding="unicode")


def _as_list(value) -> list:
    return value if isinstance(value, list) else [value]


def _fu_count(core: dict, op_class: str) -> int:
    return sum(
        fu["count"]
        for iq in _as_list(core["instQueues"])
        for fu in iq["fuPool"]["FUList"]
        if op_class in {op["opClass"] for op in fu["opList"]}
    )


def _cache_config(cache: dict, write_back: int, banks: int = 1) -> str:
    latency = cache["tag_latency"] + cache["data_latency"]
    return f"{cache['size']},{LINE_BYTES},{cache['assoc']},{banks},1,{latency},32,{write_back}"


def _buffers(cache: dict) -> str:
    return f"{cache['mshrs']},{cache['mshrs']},{cache['mshrs']},{cache['write_buffers']}"


def _system_params(sim: Gem5Result, tech_nm: int, temperature_k: int) -> dict[str, dict]:
    l2 = sim.cache_config("l2cache")
    return {
        "system": {
            "number_of_cores": sim.cores,
            "homogeneous_cores": 0,
            "number_of_L2s": 1,
            "Private_L2": 0,
            "number_of_L3s": 0,
            "number_cache_levels": 2,
            "core_tech_node": tech_nm,
            "target_core_clockrate": round(sim.freq_ghz * 1000),
            "temperature": temperature_k,
            "power_gating": 0,
        },
        "system.L20": {
            "L2_config": _cache_config(l2, write_back=1, banks=8),
            "buffer_sizes": _buffers(l2),
            "clockrate": round(sim.freq_ghz * 1000),
        },
        "system.NoC0": {"clockrate": round(sim.freq_ghz * 1000)},
        "system.mc": {
            "number_mcs": 1,
            "memory_channels_per_mc": sim.memory_channels(),
            "mc_clock": 1200,
            "peak_transfer_rate": 19200,
            "databus_width": 64,
        },
    }


def _system_stats(sim: Gem5Result) -> dict[str, dict]:
    cycles = sim.cycles
    reads = sim.stat_sum(r"board\.memory\.mem_ctrl\d*\.readReqs")
    writes = sim.stat_sum(r"board\.memory\.mem_ctrl\d*\.writeReqs")
    return {
        "system": {"total_cycles": cycles, "idle_cycles": 0, "busy_cycles": cycles},
        "system.L20": {
            "read_accesses": sim.stat_sum(rf"{L2}\.Read\w*Req\.accesses::total"),
            "read_misses": sim.stat_sum(rf"{L2}\.Read\w*Req\.misses::total"),
            "write_accesses": sim.stat_sum(rf"{L2}\.Writeback\w*\.accesses::total"),
            "write_misses": sim.stat_sum(rf"{L2}\.Writeback\w*\.misses::total"),
        },
        "system.NoC0": {"total_accesses": sim.stat("board.cache_hierarchy.l2bus.pktCount::total")},
        "system.mc": {"memory_accesses": reads + writes, "memory_reads": reads, "memory_writes": writes},
    }


def _core_params(sim: Gem5Result, i: int) -> dict[str, dict]:
    core = sim.core_config(i)
    l1i, l1d = sim.cache_config("l1icaches", i), sim.cache_config("l1dcaches", i)
    bp = core["branchPred"]
    window = sum(iq["numEntries"] for iq in _as_list(core["instQueues"]))
    cid = f"system.core{i}"
    params = {
        cid: {
            "clock_rate": round(sim.freq_ghz * 1000),
            "number_hardware_threads": core["numThreads"],
            "fetch_width": core["fetchWidth"],
            "decode_width": core["decodeWidth"],
            "issue_width": core["issueWidth"],
            "peak_issue_width": core["issueWidth"],
            "commit_width": core["commitWidth"],
            "fp_issue_width": max(1, core["issueWidth"] // 2),
            "pipeline_depth": "14,14",
            "ALU_per_core": _fu_count(core, "IntAlu"),
            "MUL_per_core": _fu_count(core, "IntMult"),
            "FPU_per_core": _fu_count(core, "FloatAdd"),
            "instruction_buffer_size": core["fetchQueueSize"],
            "instruction_window_size": window,
            "fp_instruction_window_size": window,
            "ROB_size": core["numROBEntries"],
            "phy_Regs_IRF_size": core["numPhysIntRegs"],
            "phy_Regs_FRF_size": core["numPhysFloatRegs"],
            "store_buffer_size": core["SQEntries"],
            "load_buffer_size": core["LQEntries"],
            "memory_ports": core["cacheLoadPorts"],
            "RAS_size": bp["ras"]["numEntries"],
        },
        f"{cid}.itlb": {"number_entries": core["mmu"]["itb"]["size"]},
        f"{cid}.dtlb": {"number_entries": core["mmu"]["dtb"]["size"]},
        f"{cid}.icache": {"icache_config": _cache_config(l1i, write_back=0), "buffer_sizes": _buffers(l1i)},
        f"{cid}.dcache": {"dcache_config": _cache_config(l1d, write_back=1), "buffer_sizes": _buffers(l1d)},
        f"{cid}.BTB": {"BTB_config": f"{bp['btb']['numEntries'] * 4},4,{bp['btb']['associativity']},1,1,1"},
    }
    cond = bp["conditionalBranchPred"]
    if cond["type"] == "TournamentBP":
        params[f"{cid}.predictor"] = {
            "local_predictor_size": f"{int(math.log2(cond['localPredictorSize']))},{cond['localCtrBits']}",
            "local_predictor_entries": cond["localHistoryTableSize"],
            "global_predictor_entries": cond["globalPredictorSize"],
            "global_predictor_bits": cond["globalCtrBits"],
            "chooser_predictor_entries": cond["choicePredictorSize"],
            "chooser_predictor_bits": cond["choiceCtrBits"],
        }
    return params


def _core_stats(sim: Gem5Result, i: int) -> dict[str, dict]:
    cycles = sim.cycles
    stat = partial(sim.core_stat, i)
    l1i, l1d = sim.l1i_prefix(i), sim.l1d_prefix(i)
    idle = cycles - sim.active_cycles(i)
    total = stat("executeStats0.numInsts")
    fp = stat("executeStats0.numFpAluAccesses")
    mul = stat("commitStats0.committedInstType::IntMult") + stat("commitStats0.committedInstType::IntDiv")
    ops = stat("commitStats0.numOps")
    l1d_writes = sim.stat(f"{l1d}.WriteReq.accesses::total", 0.0)
    l1d_write_misses = sim.stat(f"{l1d}.WriteReq.misses::total", 0.0)
    cid = f"system.core{i}"
    return {
        cid: {
            "total_instructions": total,
            "int_instructions": max(total - fp, 0),
            "fp_instructions": fp,
            "branch_instructions": stat("executeStats0.numBranches"),
            "branch_mispredictions": stat("commit.branchMispredicts"),
            "load_instructions": stat("executeStats0.numLoadInsts"),
            "store_instructions": stat("executeStats0.numStoreInsts"),
            "committed_instructions": ops,
            "committed_int_instructions": stat("commitStats0.numIntInsts"),
            "committed_fp_instructions": stat("commitStats0.numFpInsts"),
            "pipeline_duty_cycle": min(1.0, ops / max(cycles, 1) / sim.core_config(i)["commitWidth"]),
            "total_cycles": cycles,
            "idle_cycles": idle,
            "busy_cycles": cycles - idle,
            "ROB_reads": stat("rob.reads"),
            "ROB_writes": stat("rob.writes"),
            "rename_reads": stat("rename.intLookups"),
            "rename_writes": stat("rename.intReturned"),
            "fp_rename_reads": stat("rename.fpLookups"),
            "fp_rename_writes": stat("rename.fpReturned"),
            "inst_window_reads": stat("intInstQueueReads"),
            "inst_window_writes": stat("intInstQueueWrites"),
            "inst_window_wakeup_accesses": stat("intInstQueueWakeupAccesses"),
            "fp_inst_window_reads": stat("fpInstQueueReads"),
            "fp_inst_window_writes": stat("fpInstQueueWrites"),
            "fp_inst_window_wakeup_accesses": stat("fpInstQueueWakeupAccesses"),
            "int_regfile_reads": stat("executeStats0.numIntRegReads"),
            "int_regfile_writes": stat("executeStats0.numIntRegWrites"),
            "float_regfile_reads": stat("executeStats0.numFpRegReads"),
            "float_regfile_writes": stat("executeStats0.numFpRegWrites"),
            "function_calls": stat("commitStats0.numCallsReturns"),
            "context_switches": 0,
            "ialu_accesses": stat("intAluAccesses"),
            "fpu_accesses": stat("fpAluAccesses"),
            "mul_accesses": mul,
            "cdb_alu_accesses": stat("intAluAccesses"),
            "cdb_mul_accesses": mul,
            "cdb_fpu_accesses": stat("fpAluAccesses"),
        },
        f"{cid}.itlb": {
            "total_accesses": stat("mmu.itb.exAccesses") + stat("mmu.itb.rdAccesses"),
            "total_misses": stat("mmu.itb.exMisses") + stat("mmu.itb.rdMisses"),
        },
        f"{cid}.icache": {
            "read_accesses": sim.stat(f"{l1i}.overallAccesses::total"),
            "read_misses": sim.stat(f"{l1i}.overallMisses::total"),
        },
        f"{cid}.dtlb": {
            "total_accesses": stat("mmu.dtb.rdAccesses") + stat("mmu.dtb.wrAccesses"),
            "total_misses": stat("mmu.dtb.rdMisses") + stat("mmu.dtb.wrMisses"),
        },
        f"{cid}.dcache": {
            "read_accesses": sim.stat(f"{l1d}.overallAccesses::total") - l1d_writes,
            "write_accesses": l1d_writes,
            "read_misses": sim.stat(f"{l1d}.overallMisses::total") - l1d_write_misses,
            "write_misses": l1d_write_misses,
        },
        f"{cid}.BTB": {
            "read_accesses": stat("branchPred.btb.lookups::total"),
            "write_accesses": stat("branchPred.btb.updates::total"),
        },
    }


def _component(root: ET.Element, cid: str) -> ET.Element:
    node = root if root.get("id") == cid else root.find(f".//component[@id='{cid}']")
    if node is None:
        raise KeyError(f"McPAT XML has no component '{cid}'")
    return node


def _set(component: ET.Element, kind: str, values: dict) -> None:
    for name, value in values.items():
        node = component.find(f"./{kind}[@name='{name}']")
        if node is None:
            raise KeyError(f"{component.get('id')} has no {kind} '{name}'")
        node.set("value", str(value))


def _clone_core(template: ET.Element, i: int) -> ET.Element:
    core = copy.deepcopy(template)
    for node in core.iter("component"):
        node.set("id", node.get("id").replace("system.core0", f"system.core{i}", 1))
    core.set("name", f"core{i}")
    return core
