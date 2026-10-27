"""Static binding checks, never execution or proof of a successful build."""

import shlex
import shutil
from pathlib import Path

from ide_research_core.games.loader import BINDING_NAMES


def binding_readiness(game, root, env):
    missing_env = [
        name for name in game.host_env
        if not env.get(name) or (Path(env[name]).is_absolute() and not Path(env[name]).exists())
    ]
    missing_tools = [name for name in game.host_tools if not shutil.which(name, path=env.get("PATH", ""))]
    result = {}
    for name in BINDING_NAMES:
        command = game.bindings.get(name, "")
        reasons = []
        try:
            argv = shlex.split(command)
        except ValueError:
            argv = []
            reasons.append("invalid_command")
        if not argv:
            reasons.append("missing_binding")
        else:
            executable = argv[0]
            # Python bindings use the distribution interpreter (binding_argv).
            if (executable != "python3" and not executable.endswith(".py")
                    and not shutil.which(executable, path=env.get("PATH", ""))):
                reasons.append("missing_executable")
            scripts = [arg for arg in argv if arg.endswith(".py") and not arg.startswith("-")]
            for script in scripts:
                path = (Path(root) / script).resolve()
                if not path.is_relative_to(Path(root).resolve()):
                    reasons.append("script_outside_distribution")
                elif not path.is_file():
                    reasons.append("missing_script")
                elif path.stat().st_size <= 256_000:
                    source = path.read_text(errors="replace")
                    if any(marker in source for marker in ("NotImplementedError", "not_implemented", "template stub", "research-template-stub")):
                        reasons.append("stub_marker_requires_review")
        if missing_env or missing_tools:
            reasons.append("missing_host_requirements")
        result[name] = {"status": "blocked" if reasons else "configured", "reasons": sorted(set(reasons))}
    return {
        "operations": result,
        "missing_environment": missing_env,
        "missing_tools": missing_tools,
        "execution_verified": False,
        "scope": "current profile only; configured does not prove the worker can execute the binding",
    }
