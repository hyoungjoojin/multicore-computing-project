import hashlib
import logging
import os
from pathlib import Path

from core import shell
from core.config import Config

log = logging.getLogger(__name__)

TARGET = "build/X86/gem5.opt"


def binary(config: Config, patches: tuple[str, ...] = ()) -> Path:
    return _source_tree(config, patches) / TARGET


def build_variants(config: Config) -> None:
    if not binary(config).exists():
        raise FileNotFoundError(f"{binary(config)} is missing; run scripts/install.sh (make setup) first")
    for patches in {run.variant.patches for run in config.runs if run.variant.patches}:
        _build(config, patches)


def _patch_hash(config: Config, patches: tuple[str, ...]) -> str:
    h = hashlib.sha256()
    for patch in patches:
        h.update(config.path(patch).read_bytes())
    return h.hexdigest()[:12]


def _source_tree(config: Config, patches: tuple[str, ...] = ()) -> Path:
    if not patches:
        return config.third_party / "gem5"
    return config.third_party / f"gem5-{_patch_hash(config, patches)}"


def _build(config: Config, patches: tuple[str, ...]) -> None:
    tree = _source_tree(config, patches)
    if not tree.exists():
        log.info("creating gem5 worktree %s with %s", tree.name, list(patches))
        shell.run(["git", "worktree", "add", "-q", "--detach", tree, "HEAD"], cwd=_source_tree(config))
        for patch in patches:
            shell.run(["git", "apply", config.path(patch)], cwd=tree)

    stamp, key = tree / ".build-stamp", _patch_hash(config, patches)
    if stamp.exists() and stamp.read_text() == key:
        return
    log.info("building gem5 in %s (about 20 minutes)", tree.name)
    shell.run(["scons", TARGET, f"-j{os.cpu_count()}", "--ignore-style"], cwd=tree, log=tree / "build.log")
    stamp.write_text(key)
