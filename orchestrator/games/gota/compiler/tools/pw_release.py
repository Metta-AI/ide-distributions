#!/usr/bin/env python3
# Vendored from personal_labs_paintbot_pw@6b0bffa5085ffc4bf94ecaa8baee463e55599c84: paintbot_pw_lab/tools/pw_release.py
"""The paintbot-pw release the lab tools use: one source of truth, tools/release.env.

Every lab tool that needs an engine build takes its default tag from here, and
build_tools.sh / build_native.sh `source` the same file, so moving the lab to a new
release is one edit (made by `deployed_ref.py --write`) plus a rebuild.

    import pw_release
    pw_release.current_tag()              # 'coworld-v0.3.79'
    pw_release.require_built("pw_trace")  # Path, or raises NotBuilt with the build command

CLI (prints the pins and which binaries exist for the tag):
    uv run python paintbot_pw_lab/tools/pw_release.py
    uv run python paintbot_pw_lab/tools/pw_release.py --json --require all
Exit codes: 0 ok; 2 usage error (unknown tool name); 3 a --require'd binary is not built
(the message names the build command). Reference: paintbot_pw_lab/docs/tools/pw_release.md.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
LAB = TOOLS.parent
REPO = Path(os.environ.get('GOTA_CANDIDATE_REPO', LAB.parents[3])).resolve()
RELEASE_ENV = TOOLS / "release.env"
BUILD_TOOLS = "paintbot_pw_lab/tools/build_tools.sh"
BUILD_NATIVE = "paintbot_pw_lab/tools/build_native.sh"
# Every file under tools/bin/<tag>/ a lab tool depends on, and the script that builds it.
BUILT_BY = {
    "paintbot-headless": BUILD_TOOLS,
    "replay_stats": BUILD_TOOLS,
    "pw_trace": BUILD_TOOLS,
    "pw_map": BUILD_TOOLS,
    "libpw.dylib": BUILD_NATIVE,
    "libpw.build.json": BUILD_NATIVE,
}
ENV_KEYS = ("PW_RELEASE_TAG", "PW_RELEASE_SHA", "PW_DOCS_SHA")


class NotBuilt(RuntimeError):
    """A binary for the requested release is missing. CLIs exit 3 with str(err)."""

    exit_code = 3

    def __init__(self, tool: str, path: Path, fix: str):
        super().__init__(f"{path} is missing: run {fix}")
        self.tool, self.path, self.fix = tool, path, fix


def read_env(path: Path | None = None) -> dict[str, str]:
    """KEY=value lines of release.env; comments and blank lines ignored."""
    path = path or RELEASE_ENV
    values = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    missing = [k for k in ENV_KEYS if not values.get(k)]
    if missing:
        raise ValueError(f"{path} lacks {', '.join(missing)}")
    return values


def write_env(tag: str, sha: str, path: Path | None = None) -> None:
    """Set PW_RELEASE_TAG/PW_RELEASE_SHA in place, keeping comments and PW_DOCS_SHA."""
    path = path or RELEASE_ENV
    text = path.read_text()
    for key, value in (("PW_RELEASE_TAG", tag), ("PW_RELEASE_SHA", sha[:8])):
        text, n = re.subn(rf"^{key}=.*$", f"{key}={value}", text, flags=re.M)
        if n != 1:
            raise ValueError(f"{path} must have exactly one {key}= line")
    path.write_text(text)


def current_tag() -> str:
    return read_env()["PW_RELEASE_TAG"]


def current_sha() -> str:
    return read_env()["PW_RELEASE_SHA"]


def docs_sha() -> str:
    return read_env()["PW_DOCS_SHA"]


def bin_dir(tag: str | None = None) -> Path:
    return TOOLS / "bin" / (tag or current_tag())


def cache_root() -> Path:
    """Where lab tools keep rebuildable caches: $PW_CACHE_DIR when set, else tools/.cache/.

    Holds the per-release source worktree (<tag>/, written by build_tools.sh, which honours the
    same variable), the map rasters (maps/<tag>/) and the shared terrain tables (terrain/<tag>/,
    pw_terrain.py). Read at call time so a caller can point
    it at a scratch directory. Per-episode trace caches are NOT here: they live beside the
    episode (pw_episodes.py)."""
    return Path(os.environ.get("PW_CACHE_DIR") or TOOLS / ".cache").expanduser()


def release_tree(tag: str | None = None) -> Path:
    """The release's source worktree that build_tools.sh leaves: <cache root>/<tag>/."""
    return cache_root() / (tag or current_tag())


def build_command(tool: str, tag: str | None = None) -> str:
    """The exact command that builds `tool` for `tag` (no argument for the current tag)."""
    script = BUILT_BY[tool]
    return script if tag in (None, current_tag()) else f"{script} {tag}"


def require_built(tool: str, tag: str | None = None) -> Path:
    """Path of `tool` in tools/bin/<tag>/, or raise NotBuilt naming the build command."""
    if tool not in BUILT_BY:
        raise ValueError(f"unknown tool {tool!r}; valid: {', '.join(BUILT_BY)}")
    path = bin_dir(tag) / tool
    if not path.is_file():
        raise NotBuilt(tool, path, build_command(tool, tag))
    return path


def require_built_or_exit(tool: str, tag: str | None = None) -> Path:
    """require_built for CLI code paths: print the fix to stderr and exit 3 (agent CLI contract)."""
    try:
        return require_built(tool, tag)
    except NotBuilt as err:
        print(f"ERROR: {err}", file=sys.stderr)
        raise SystemExit(NotBuilt.exit_code) from err


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Print the lab's release pins (tools/release.env) and which binaries are built.",
        epilog="examples:\n"
               "  uv run python paintbot_pw_lab/tools/pw_release.py\n"
               "  uv run python paintbot_pw_lab/tools/pw_release.py --json --require pw_trace libpw.dylib\n"
               "  uv run python paintbot_pw_lab/tools/pw_release.py --tag coworld-v0.3.78 --require all",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tag", help="check this release's bin dir instead of release.env's "
                                      "(e.g. --tag coworld-v0.3.78)")
    parser.add_argument("--require", nargs="+", metavar="TOOL", default=[],
                        help=f"exit 3 unless these are built; 'all' or any of: {', '.join(BUILT_BY)} "
                             "(e.g. --require pw_trace libpw.dylib)")
    parser.add_argument("--json", action="store_true",
                        help="print one JSON object (agent CLI contract) instead of text")
    args = parser.parse_args(argv)

    required = list(BUILT_BY) if "all" in args.require else args.require
    unknown = [t for t in required if t not in BUILT_BY]
    if unknown:
        parser.error(f"unknown --require {', '.join(unknown)}; valid: all, {', '.join(BUILT_BY)}")

    env = read_env()
    tag = args.tag or env["PW_RELEASE_TAG"]
    built = {tool: (bin_dir(tag) / tool).is_file() for tool in BUILT_BY}
    failures = [{"id": tool, "code": "not_built", "message": str(NotBuilt(tool, bin_dir(tag) / tool,
                                                                            build_command(tool, tag)))}
                for tool in required if not built[tool]]
    fixes = sorted({build_command(f["id"], tag) for f in failures})
    result = {"tag": tag, "release_tag": env["PW_RELEASE_TAG"], "release_sha": env["PW_RELEASE_SHA"],
              "docs_sha": env["PW_DOCS_SHA"], "bin_dir": str(bin_dir(tag)), "built": built,
              "release_env": str(RELEASE_ENV)}
    if args.json:
        print(json.dumps({
            "ok": not failures, "tool": "pw_release", "release_tag": env["PW_RELEASE_TAG"],
            "inputs": {"tag": tag, "require": required}, "outputs": [],
            "counts": {"processed": len(required), "failed": len(failures), "excluded": 0},
            "failures": failures, "result": result, "next": fixes}))
    else:
        print(f"release tag: {env['PW_RELEASE_TAG']}  sha {env['PW_RELEASE_SHA']}  ({RELEASE_ENV})")
        print(f"docs sha:    {env['PW_DOCS_SHA']}  (the mechanics/policy docs' line citations)")
        print(f"bin dir:     {bin_dir(tag)}")
        print(f"built:       {' '.join(t for t in BUILT_BY if built[t]) or '(none)'}")
        print(f"missing:     {' '.join(t for t in BUILT_BY if not built[t]) or '(none)'}")
        for failure in failures:
            print(f"ERROR: {failure['message']}", file=sys.stderr)
    return NotBuilt.exit_code if failures else 0


if __name__ == "__main__":
    sys.exit(main())
