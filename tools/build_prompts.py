"""Build, check and sync the generated SOULs and plugin manifests.

    uv run python tools/build_prompts.py --write       write both SOUL.md files
    uv run python tools/build_prompts.py --manifests   sync provides_tools in both plugin.yaml
    uv run python tools/build_prompts.py --check       exit 1 when a committed file is stale or a
                                                       SOUL fails its checks (budgets, leak test)

Without a flag it prints the word counts and the tool lists. ``--root`` points at another
checkout (tests use a copy). The parts live in ``core/src/research_core/prompts/``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
try:
    from research_core.prompts import build
except ModuleNotFoundError:  # a plain `python3`, outside the workspace environment
    sys.path.insert(0, str(REPO / "core" / "src"))
    from research_core.prompts import build


def soul_path(root: Path, role: str) -> Path:
    return root / build.SOUL_PATH.format(role=role)


def manifest_path(root: Path, role: str) -> Path:
    return root / build.MANIFEST_PATH.format(role=role)


def write_souls(root: Path) -> None:
    for role in build.ROLES:
        path = soul_path(root, role)
        path.write_text(build.render(role), encoding="utf-8")
        print(f"wrote {path.relative_to(root)} ({build.word_count(role)} words)")


def write_manifests(root: Path) -> None:
    for role, names in build.manifests().items():
        path = manifest_path(root, role)
        text = build.set_provides_tools(path.read_text(encoding="utf-8"), names)
        _assert_manifest_parses(text, names, path)
        path.write_text(text, encoding="utf-8")
        print(f"wrote {path.relative_to(root)} ({len(names)} tools)")


def _assert_manifest_parses(text: str, names: list[str], path: Path) -> None:
    try:
        import yaml
    except ModuleNotFoundError:
        return  # outside the workspace environment the textual edit stands on its own
    loaded = yaml.safe_load(text)
    if loaded.get("provides_tools", []) != names:
        raise SystemExit(f"{path}: the rewritten provides_tools does not parse back to the list")


def stale(root: Path) -> list[str]:
    """Generated files whose committed text differs from the render."""
    problems = []
    for role in build.ROLES:
        path = soul_path(root, role)
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        if current != build.render(role):
            problems.append(f"{path.relative_to(root)} differs from the render; run --write")
    for role, names in build.manifests().items():
        path = manifest_path(root, role)
        text = path.read_text(encoding="utf-8")
        if build.set_provides_tools(text, names) != text:
            problems.append(f"{path.relative_to(root)} provides_tools is stale; run --manifests")
    return problems


def check(root: Path) -> int:
    problems = [problem for role in build.ROLES for problem in build.check(role)]
    problems += stale(root)
    for problem in problems:
        print(problem)
    if problems:
        return 1
    print("SOULs and manifests are current and within budget")
    return 0


def report() -> None:
    for role in build.ROLES:
        print(
            f"{role}: {build.word_count(role)} words (budget {build.BUDGETS[role]}), "
            f"Records section {build.records_words(role)} words, "
            f"tools: {', '.join(build.manifests()[role])}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help="write both SOUL.md files")
    parser.add_argument("--check", action="store_true", help="fail when a generated file is stale")
    parser.add_argument("--manifests", action="store_true", help="sync provides_tools")
    parser.add_argument("--root", type=Path, default=REPO, help="the checkout to act on")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if args.write:
        write_souls(root)
    if args.manifests:
        write_manifests(root)
    if args.check:
        return check(root)
    if not (args.write or args.manifests):
        report()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
