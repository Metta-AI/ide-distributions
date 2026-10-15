"""Fixtures for fresh-install integration tests against real Hermes (marker ``hermes``).

Each install is a throwaway ``HERMES_HOME`` under pytest's tmp dir, never ``~/.hermes``. The
distribution is installed from a ``git archive`` of ``HEAD`` extracted outside the source tree,
so the tests see exactly what a user would install (committed files only, no ``.venv``).
A test's tmp dir is deleted when the test passes and kept when it fails, for inspection.
"""

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest


DISTRIBUTION_ROOT = Path(__file__).resolve().parents[2]  # .../researcher
REPO_ROOT = DISTRIBUTION_ROOT.parent
BOARD = "autoresearch"  # the Kanban board slug: fixed, whatever the install name
PROFILE = "researcher-1"
FAKE_CREDENTIAL = '{"token": "ply_fake", "expires_at": "2099-01-01T00:00:00Z"}'

# Inherited variables that would leak the developer's setup into the install under test.
_STRIPPED_ENV = ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT", "PYTHONHOME", "HERMES_KANBAN_HOME")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    setattr(item, f"report_{report.when}", report)


def _test_failed(item) -> bool:
    return any(
        getattr(item, f"report_{when}", None) is not None and getattr(item, f"report_{when}").failed
        for when in ("setup", "call")
    )


def uv_cache_dir() -> str:
    """The default uv cache: the launcher's ``uv sync`` must reuse it (no second cache)."""
    return subprocess.run(
        ["uv", "cache", "dir"], check=True, capture_output=True, text=True
    ).stdout.strip()


@dataclass
class Install:
    root: Path  # the tmp dir that holds everything below
    hermes_home: Path  # host root (HERMES_HOME for `hermes` commands)
    profile: str  # installed profile name (the card assignee)
    profile_dir: Path  # installed profile home
    source: Path  # extracted distribution copy (the recorded install source)
    remote: Path  # bare repo used as RESEARCH_CANONICAL
    base_env: dict
    board: str = BOARD  # the Kanban board slug: fixed, whatever the install name

    def env(self, **overrides: str) -> dict:
        return {**self.base_env, **overrides}

    def hermes(self, *args: str, check: bool = True, timeout: float = 120):
        """``hermes <args>`` against the throwaway host root."""
        result = subprocess.run(
            ["hermes", *args],
            check=False,
            env=self.env(),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if check and result.returncode != 0:
            raise AssertionError(
                f"hermes {' '.join(args)} exited {result.returncode}\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        return result

    def profile_env(self, **overrides: str) -> dict:
        """The profile's environment: ``HERMES_HOME`` is the profile, plus its .env names."""
        return self.env(
            HERMES_HOME=str(self.profile_dir),
            RESEARCH_CANONICAL=str(self.remote),
            RESEARCH_PLAYER_ID="ply_fake",
            RESEARCH_GAME="fakegame",
            RESEARCH_SOFTMAX="fake",
            SOFTMAX_PLAYER_CREDENTIAL=FAKE_CREDENTIAL,
        ) | overrides



def extract_distribution(dest: Path) -> Path:
    """``git archive HEAD researcher`` extracted under ``dest``; returns the copy's root."""
    dest.mkdir(parents=True)
    archive = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "archive", "HEAD", "researcher"],
        check=True,
        capture_output=True,
    )
    subprocess.run(["tar", "-x", "-C", str(dest)], input=archive.stdout, check=True)
    return dest / "researcher"


def write_profile_env(profile_dir: Path, remote: Path) -> None:
    lines = {
        "SOFTMAX_PLAYER_CREDENTIAL": f"'{FAKE_CREDENTIAL}'",
        "RESEARCH_PLAYER_ID": "ply_fake",
        "RESEARCH_CANONICAL": str(remote),
        "RESEARCH_GAME": "fakegame",
        "RESEARCH_SOFTMAX": "fake",
        "OPENROUTER_API_KEY": os.environ.get("OPENROUTER_API_KEY") or "sk-or-fake",
    }
    env_file = profile_dir / ".env"
    env_file.write_text("".join(f"{key}={value}\n" for key, value in lines.items()))
    env_file.chmod(0o600)


def make_install(root: Path, name: str = PROFILE) -> Install:
    if shutil.which("hermes") is None:
        pytest.skip("hermes not installed")
    hermes_home = root / "hh"
    hermes_home.mkdir(parents=True)
    base_env = {k: v for k, v in os.environ.items() if k not in _STRIPPED_ENV}
    base_env = {k: v for k, v in base_env.items() if not k.startswith("AUTORESEARCH_")}
    base_env.update(
        HERMES_HOME=str(hermes_home),
        PYTHONPATH="",
        UV_CACHE_DIR=uv_cache_dir(),
    )
    base_env.pop("OPENROUTER_API_KEY", None)  # workers read it from the profile .env only
    install = Install(
        root=root,
        hermes_home=hermes_home,
        profile=name,
        profile_dir=hermes_home / "profiles" / name,
        source=extract_distribution(root / "src-copy"),
        remote=root / "remote.git",
        base_env=base_env,
    )
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(install.remote)], check=True)
    install.hermes("profile", "install", str(install.source), "--name", name, "-y")
    write_profile_env(install.profile_dir, install.remote)
    return install


def _cleanup(root: Path, keep: bool) -> None:
    if keep:
        print(f"\nkept failed test dir: {root}", file=sys.stderr)
    else:
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def install(request, tmp_path):
    """A fresh install of the distribution as profile ``researcher-1`` in a throwaway host root."""
    yield make_install(tmp_path / "it")
    _cleanup(tmp_path, keep=_test_failed(request.node))
