#!/usr/bin/env python3
# Vendored from personal_labs_paintbot_pw@6b0bffa5085ffc4bf94ecaa8baee463e55599c84: paintbot_pw_lab/tools/pw_terrain.py
"""The shared terrain-table cache of the lab's -d:pwTraining engine builds, kept bounded on disk.

Since coworld-v0.3.89 (#183) the training terrain table computes a whole 64x64 block the first
time any point in it is read, so every short-lived lab process (one pw_trace per tape, pw_map,
each pw_local worker) paid ~20 s for most of the island. The engine can save a computed table
and map it back read-only (topography.nim saveTerrain/loadTerrain; native_env.nim
pw_terrain_cache_save/load). This module and tools/pw_terrain_cache.nim wire that in:

- One file per (release tag, terrain flag set): <cache root>/terrain/<tag>/island-f<bits>.pwterrain,
  ~0.62 GB, built once (whole-world prewarm, ~27 s) by the first process that takes the
  file's flock and then only mapped read-only. Every rules >= 35 tape and every local match
  shares island-f2047. Generated maps are never tabled and never cached. Nothing is written
  per run or per process.
- Every tool run first prunes (and prunes again when it ends): other tags' directories go
  (the pin in tools/release.env and the tag in use stay), orphaned temporary files of
  killed builders go, and least-recently-used files go until the total is under the cap.
- The Nim tools read PW_TERRAIN_CACHE_DIR, which `session`/`env` set for their child process.

Environment:
  PW_TERRAIN_CACHE=0          disable (also off/false/no): no file read, written or pruned
  PW_TERRAIN_CACHE_MAX_GB=N   total cap across tags, least recently used evicted (default 2);
                              a cap under one file (~0.62 GB) means every run rebuilds it
  PW_CACHE_DIR                moves the cache root (pw_release.cache_root)

CLI:
    uv run python paintbot_pw_lab/tools/pw_terrain.py status --json
    uv run python paintbot_pw_lab/tools/pw_terrain.py clear --json
Exit codes: 0 ok; 2 usage (bad subcommand or PW_TERRAIN_CACHE_MAX_GB). Reference:
paintbot_pw_lab/docs/tools/pw_release.md#terrain-cache.
"""
from __future__ import annotations

import contextlib
import fcntl
import os
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pw_cli  # noqa: E402
import pw_release  # noqa: E402

ENV_SWITCH = "PW_TERRAIN_CACHE"
ENV_MAX_GB = "PW_TERRAIN_CACHE_MAX_GB"
ENV_DIR = "PW_TERRAIN_CACHE_DIR"   # read by pw_terrain_cache.nim
DEFAULT_MAX_GB = 2.0
MIN_FREE_BYTES = 1_500_000_000     # below this free space, run uncached rather than fill the disk
SUFFIX = ".pwterrain"
# The rules version at which each terrain flag turns on, in the order topography.nim's
# terrainFlagsKey (and pw_terrain_cache.nim's file name) packs them: wideRamps, wilderness,
# deepWilderness, organicTerrain, islandTerrain, expandedIsland, riverTerrain, curvedRiver,
# fractalRiver, lakeTerrain, symmetricTerrain (sim.nim configureRules). test_pw_terrain.py
# checks this against the release worktree's sim.nim.
FLAG_RULES = (11, 12, 14, 15, 16, 22, 29, 31, 32, 33, 35)


def root() -> Path:
    return pw_release.cache_root() / "terrain"


def cache_dir(tag: str | None = None) -> Path:
    return root() / (tag or pw_release.current_tag())


def enabled() -> bool:
    return os.environ.get(ENV_SWITCH, "1").strip().lower() not in ("0", "off", "false", "no")


def cap_bytes() -> int:
    text = os.environ.get(ENV_MAX_GB, "")
    try:
        gb = float(text) if text.strip() else DEFAULT_MAX_GB
    except ValueError:
        raise pw_cli.UsageError(f"{ENV_MAX_GB}={text!r} is not a number of GB, e.g. 2") from None
    if gb < 0:
        raise pw_cli.UsageError(f"{ENV_MAX_GB}={text!r} must be >= 0")
    return int(gb * 1e9)


def file_name(rules: int) -> str:
    """The cache file of a rules version's island terrain (the same name the Nim tools derive
    from the flags configureRules sets)."""
    bits = sum(1 << i for i, since in enumerate(FLAG_RULES) if rules >= since)
    return f"island-f{bits}{SUFFIX}"


# ---------------------------------------------------------------- locking

@contextlib.contextmanager
def locked(path: Path, blocking: bool = True):
    """Hold the exclusive flock on <path>.lock (the same lock the Nim tools take). Yields True,
    or False when blocking=False and another process holds it. The OS releases the lock of a
    process that dies, so a killed builder never leaves it stuck."""
    fd = os.open(f"{path}.lock", os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            yield False
            return
        yield True
    finally:
        os.close(fd)   # releases the lock


def load_or_build(path: Path, load, build) -> str:
    """The protocol pw_terrain_cache.nim follows, for callers in Python (pw_local's library).

    load(path) -> bool maps the file (False: the engine rejected it); build(path) writes it
    atomically (the engine saves to a temporary name and renames). Returns "loaded", "built"
    or "rejected". A rejected file is left alone, never overwritten: `clear` removes it."""
    if path.is_file():
        return _load(path, load)
    path.parent.mkdir(parents=True, exist_ok=True)
    with locked(path):
        if path.is_file():   # another process built it while we waited
            return _load(path, load)
        for stale in path.parent.glob(path.name + ".tmp-*"):
            stale.unlink(missing_ok=True)
        build(path)
        return "built"


def _load(path: Path, load) -> str:
    if not load(path):
        print(f"WARNING: terrain cache {path} was rejected by this build; running without it "
              "(clear it: uv run python paintbot_pw_lab/tools/pw.py terrain-cache clear)", file=sys.stderr)
        return "rejected"
    with contextlib.suppress(OSError):
        os.utime(path)   # recency for the LRU cap
    return "loaded"


# ---------------------------------------------------------------- bounding disk use

def _files() -> list[Path]:
    return sorted(root().glob(f"*/*{SUFFIX}"), key=lambda p: p.stat().st_mtime) if root().is_dir() else []


def _dir_busy(directory: Path) -> bool:
    """True while some process holds a lock in the directory (it may be building a file there)."""
    for lock in directory.glob(f"*{SUFFIX}.lock"):
        with locked(lock.with_suffix(""), blocking=False) as free:
            if not free:
                return True
    return False


def _remove_orphans() -> list[str]:
    """Temporary files of builders that died; skipped while their lock is held (a live build)."""
    removed = []
    for tmp in list(root().glob(f"*/*{SUFFIX}.tmp-*")):
        target = tmp.with_name(tmp.name.split(".tmp-")[0])
        with locked(target, blocking=False) as free:
            if free and tmp.exists():
                tmp.unlink(missing_ok=True)
                removed.append(str(tmp))
    return removed


def prune(keep_tags: set[str] | None = None, cap: int | None = None) -> list[str]:
    """Delete other tags' directories, orphaned temporary files, and least-recently-used cache
    files until the total is <= cap. Returns the removed paths. Safe while tools run: a process
    that has a file mapped keeps its copy until it exits."""
    if not root().is_dir():
        return []
    keep = {pw_release.current_tag(), *(keep_tags or ())}
    cap = cap_bytes() if cap is None else cap
    removed = []
    for directory in root().iterdir():
        if directory.is_dir() and directory.name not in keep and not _dir_busy(directory):
            shutil.rmtree(directory, ignore_errors=True)
            removed.append(str(directory))
    removed += _remove_orphans()
    files = []
    for path in _files():
        with contextlib.suppress(FileNotFoundError):   # a concurrent prune may have taken it
            files.append((path, path.stat().st_size))
    total = sum(size for _, size in files)
    for path, size in files:   # oldest use first
        if total <= cap:
            break
        path.unlink(missing_ok=True)
        total -= size
        removed.append(str(path))
    return removed


def prepare(tag: str | None) -> Path | None:
    """Prune, then the cache directory a run of `tag` should use, or None to run uncached
    (disabled, an explicit --binary with no tag, or too little free disk)."""
    if tag is None or not enabled():
        return None
    prune({tag})
    directory = cache_dir(tag)
    directory.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(directory).free
    if free < MIN_FREE_BYTES and not any(directory.glob(f"*{SUFFIX}")):
        print(f"WARNING: only {free / 1e9:.1f} GB free; running without the terrain cache "
              f"(a file needs ~0.62 GB)", file=sys.stderr)
        return None
    return directory


@contextlib.contextmanager
def session(tag: str | None):
    """Around one tool run: prune, yield the directory (or None), prune again at the end so a
    file this run built counts against the cap at once."""
    directory = prepare(tag)
    try:
        yield directory
    finally:
        if directory is not None:
            prune({tag})


def env(directory: Path | None) -> dict[str, str]:
    """os.environ for a Nim tool child: PW_TERRAIN_CACHE_DIR set to `directory`, or removed."""
    child = dict(os.environ)
    child.pop(ENV_DIR, None)
    if directory is not None:
        child[ENV_DIR] = str(directory)
    return child


def attach_native(lib, handle, rules: int, directory: Path | None) -> str:
    """Load (or build once, then load) the terrain file for a libpw handle's rules. Returns
    "off" when there is no directory or the library predates the terrain cache ABI."""
    if directory is None or not hasattr(lib, "pw_terrain_cache_load"):
        return "off"
    import ctypes
    for name in ("pw_terrain_prewarm", "pw_terrain_cache_save", "pw_terrain_cache_load"):
        getattr(lib, name).restype = ctypes.c_int
    lib.pw_terrain_prewarm.argtypes = [ctypes.c_void_p]
    lib.pw_terrain_cache_save.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    lib.pw_terrain_cache_load.argtypes = [ctypes.c_void_p, ctypes.c_char_p]

    def build(path: Path) -> None:
        if lib.pw_terrain_prewarm(handle) < 0 or lib.pw_terrain_cache_save(handle, str(path).encode()) < 0:
            raise RuntimeError(f"libpw could not build the terrain cache {path}")
        print(f"terrain cache: built {path}", file=sys.stderr)

    return load_or_build(directory / file_name(rules),
                         lambda path: lib.pw_terrain_cache_load(handle, str(path).encode()) >= 0, build)


# ---------------------------------------------------------------- CLI

def usage() -> dict:
    files = []
    for path in _files():
        with contextlib.suppress(FileNotFoundError):
            stat = path.stat()
            files.append({"tag": path.parent.name, "file": path.name, "path": str(path), "bytes": stat.st_size,
                          "last_used": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(stat.st_mtime))})
    free = shutil.disk_usage(next(p for p in (root(), *root().parents) if p.exists())).free
    return {"root": str(root()), "enabled": enabled(), "cap_bytes": cap_bytes(),
            "total_bytes": sum(f["bytes"] for f in files), "free_bytes": free, "files": files}


def clear() -> list[str]:
    """Remove every cache file and orphaned temporary file (a build in progress keeps its own)."""
    removed = _remove_orphans()
    for path in _files():
        path.unlink(missing_ok=True)
        removed.append(str(path))
    return removed


def main(argv: list[str] | None = None) -> int:
    parser = pw_cli.ArgumentParser("pw_terrain", __doc__, examples=[
        "uv run python paintbot_pw_lab/tools/pw_terrain.py status --json",
        "uv run python paintbot_pw_lab/tools/pw_terrain.py clear --json"],
        exit_codes="0 ok; 2 usage (unknown subcommand, bad PW_TERRAIN_CACHE_MAX_GB)")
    parser.add_argument("action", choices=("status", "clear"),
                        help="status: files, sizes, last use, cap; clear: delete every cache file")

    def run_cli(args, report):
        if args.action == "clear":
            removed = clear()
            report.counts["processed"] = len(removed)
            print(f"removed {len(removed)} terrain cache file(s)", file=sys.stderr)
            return {"removed": removed, **usage()}
        result = usage()
        report.counts["processed"] = len(result["files"])
        print(f"terrain cache {result['root']} ({'on' if result['enabled'] else 'off'}): "
              f"{result['total_bytes'] / 1e9:.2f} GB of {result['cap_bytes'] / 1e9:.2f} GB cap, "
              f"{result['free_bytes'] / 1e9:.1f} GB free on disk")
        for f in result["files"]:
            print(f"  {f['tag']}/{f['file']}  {f['bytes'] / 1e9:.2f} GB  last used {f['last_used']}")
        return result

    return pw_cli.run(parser, run_cli, argv)


if __name__ == "__main__":
    sys.exit(main())
