import subprocess
import time
from collections.abc import Callable
from pathlib import Path


class CommandError(RuntimeError):
    pass


class CommandStalled(CommandError):
    pass


def run(
    cmd: list[str | Path],
    cwd: Path | None = None,
    log: Path | None = None,
    timeout_s: float | None = None,
    stalled: Callable[[], bool] = lambda: False,
    poll_s: float = 30.0,
) -> None:
    args = [str(c) for c in cmd]
    if log is None:
        subprocess.run(args, cwd=cwd, check=True, stdin=subprocess.DEVNULL)
        return

    log.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_s if timeout_s else float("inf")
    with log.open("w") as f:
        proc = subprocess.Popen(args, cwd=cwd, stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        while True:
            try:
                proc.wait(timeout=poll_s)
                break
            except subprocess.TimeoutExpired:
                if time.monotonic() > deadline:
                    proc.kill()
                    raise CommandError(f"{args[0]} timed out after {timeout_s:.0f} s (log: {log})") from None
                if stalled():
                    proc.kill()
                    raise CommandStalled(f"{args[0]} stalled (log: {log})") from None

    if proc.returncode != 0:
        tail = "".join(log.read_text(errors="replace").splitlines(keepends=True)[-30:])
        raise CommandError(f"{' '.join(args)} exited with {proc.returncode} (log: {log})\n{tail}")
