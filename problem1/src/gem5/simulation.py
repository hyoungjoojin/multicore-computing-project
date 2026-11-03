import hashlib
import json
import logging
import time
from collections.abc import Callable
from pathlib import Path

from core import shell
from core.config import Config, Run
from gem5 import build
from gem5.result import Gem5Result

log = logging.getLogger(__name__)

GUEST_SCRIPT = Path(__file__).parent / "guest" / "simulate.py"
KERNEL = "vmlinux-4.19.83"
DISK = "parsec.img"
ROOT_PARTITION = "1"
COMMAND = (
    "cd /home/gem5/parsec-benchmark; "
    "source env.sh; "
    "parsecmgmt -a run -p {benchmark} -c gcc-hooks -i simsmall -n {cores}; "
    "sleep 5; "
    "m5 exit;"
)


def simulate(config: Config, run: Run) -> Gem5Result:
    outdir = config.run_dir(run) / "gem5"
    spec = _guest_spec(config, run)
    key = _cache_key(config, run, spec)
    stamp = outdir / "inputs.sha256"
    if not stamp.exists() or stamp.read_text() != key:
        _simulate_with_retries(config, run, spec, outdir)
        stamp.write_text(key)
    return Gem5Result.load(outdir, run.cores, config.hardware["clock_ghz"])


def _guest_spec(config: Config, run: Run) -> dict:
    resources = config.third_party / "resources"
    return {
        "cores": run.cores,
        "width": run.width,
        "params": run.variant.params,
        "hardware": config.hardware,
        "command": COMMAND.format(benchmark=run.workload, cores=run.cores),
        "kernel": str(resources / KERNEL),
        "disk": str(resources / DISK),
        "root_partition": ROOT_PARTITION,
    }


def _cache_key(config: Config, run: Run, spec: dict) -> str:
    h = hashlib.sha256()
    h.update(json.dumps(spec, sort_keys=True).encode())
    h.update((config.third_party / "gem5" / ".git" / "HEAD").read_bytes())
    h.update(GUEST_SCRIPT.read_bytes())
    for patch in run.variant.patches:
        h.update(config.path(patch).read_bytes())
    return h.hexdigest()


def _simulate_with_retries(config: Config, run: Run, spec: dict, outdir: Path) -> None:
    attempts = 1 + config.execution["boot_retries"]
    for attempt in range(1, attempts + 1):
        try:
            _simulate_once(config, run, spec, outdir)
            return
        except shell.CommandStalled:
            log.warning("%s: guest boot stalled (attempt %d/%d)", run.id, attempt, attempts)
    raise RuntimeError(f"{run.id}: guest boot stalled {attempts} times")


def _simulate_once(config: Config, run: Run, spec: dict, outdir: Path) -> None:
    spec_file = outdir.parent / "gem5-spec.json"
    outdir.mkdir(parents=True, exist_ok=True)
    spec_file.write_text(json.dumps(spec, indent=2))
    for marker in ("roi_started", "roi_complete"):
        (outdir / marker).unlink(missing_ok=True)

    log.info("%s: simulating", run.id)
    shell.run(
        [build.binary(config, run.variant.patches), "-d", outdir, GUEST_SCRIPT, spec_file],
        log=outdir.parent / "gem5.log",
        timeout_s=config.execution["run_timeout_hours"] * 3600,
        stalled=_boot_watchdog(outdir, config.execution["boot_stall_minutes"] * 60),
    )
    if not (outdir / "roi_complete").exists():
        raise RuntimeError(f"{run.id}: gem5 exited before the ROI finished (see {outdir.parent / 'gem5.log'})")


def _boot_watchdog(outdir: Path, limit_s: float) -> Callable[[], bool]:
    console = outdir / "board.pc.com_1.device"
    started = time.time()

    def stalled() -> bool:
        if (outdir / "roi_started").exists():
            return False
        last_output = console.stat().st_mtime if console.exists() else started
        return time.time() - max(last_output, started) > limit_s

    return stalled
