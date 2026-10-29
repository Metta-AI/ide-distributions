"""Hermes plugin entry. STDLIB ONLY: this runs inside the Hermes process.

It puts `<distribution root>/src` on sys.path and registers the shared `capabilities` tool
(`ide_research_core.session_catalog`, also registered by the orchestrator) in the `researcher`
toolset. On a hosted workspace that `src` is the agent's writable copy; if it fails to import,
the image copy is loaded instead (`_import_with_fallback`).
"""

import os
import sys
from pathlib import Path


def _distribution_root() -> Path:
    # Inside the gateway, os.environ["HERMES_HOME"] is the host root, not this profile, so
    # prefer Hermes' own resolver (as the orchestrator's plugin does).
    try:
        from hermes_constants import get_hermes_home

        return Path(get_hermes_home())
    except ImportError:
        pass
    env_home = os.environ.get("HERMES_HOME")
    if env_home:
        return Path(env_home)
    return Path(__file__).resolve().parents[2]


def _image_src(root: Path):
    """The read-only image copy of this distribution's ``src``, recorded by Softmax hosting."""
    try:
        source = Path((root / "local/tooling-source.txt").read_text().strip()) / "src"
    except OSError:
        return None
    return source if source.is_dir() and source.resolve() != (root / "src").resolve() else None


def _import_with_fallback(root: Path, *modules: str):
    """Import from the profile's writable ``src``; if the agent's edits break it, load the image
    copy and record why, so the agent keeps its tools and ``status`` shows the error."""
    import importlib
    import json
    import traceback

    record = root / "local/tooling-fallback.json"
    try:
        loaded = [importlib.import_module(module) for module in modules]
        record.unlink(missing_ok=True)
        return loaded
    except Exception as error:
        image = _image_src(root)
        if image is None:
            raise
        packages = {module.split(".")[0] for module in modules} | {"ide_research_core"}
        for name in [name for name in sys.modules if name.split(".")[0] in packages]:
            del sys.modules[name]
        if str(root / "src") in sys.path:
            sys.path.remove(str(root / "src"))
        sys.path.insert(0, str(image))
        record.parent.mkdir(parents=True, exist_ok=True)
        record.write_text(json.dumps({
            "error": f"{type(error).__name__}: {error}",
            "traceback": traceback.format_exc()[-4000:],
            "using": str(image),
            "message": "Your tooling in src failed to import, so the image copy is loaded. "
            "Fix the error in your profile's src, commit, and restart.",
        }, indent=2) + "\n")
        return [importlib.import_module(module) for module in modules]


_root = _distribution_root()
_src = str(_root / "src")
if _src not in sys.path:
    sys.path.insert(0, _src)

session_catalog, host_capabilities = _import_with_fallback(
    _root, "ide_research_core.session_catalog", "ide_research_core.host_capabilities"
)


def register(ctx) -> None:
    session_catalog.register(ctx, "researcher")
    host_capabilities.register(ctx, "researcher")
