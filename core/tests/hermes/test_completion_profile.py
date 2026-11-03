"""Completion preflight must use the API conversation's owning profile database."""

import os
import subprocess
from pathlib import Path


def test_completion_uses_parent_profile(hermes_bin, tmp_path):
    result = subprocess.run(
        [str(hermes_bin.parent / "python"), str(Path(__file__).resolve())],
        env={
            **os.environ,
            "HERMES_HOME": str(tmp_path),
            "HERMES_REAL_HOME": str(tmp_path),
        },
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


if __name__ == "__main__":
    import asyncio

    from gateway.config import GatewayConfig
    from gateway.run import GatewayRunner
    from hermes_constants import get_hermes_home
    from hermes_state import SessionDB

    home = Path(os.environ["HERMES_HOME"])
    for profile in ("orchestrator", "researcher"):
        profile_home = home / "profiles" / profile
        profile_home.mkdir(parents=True)
        (profile_home / "config.yaml").write_text("{}\n")
        db = SessionDB(profile_home / "state.db")
        db.create_session(f"api_completion_{profile}", source="api_server", profile_name=profile)
        db.close()

    runner = GatewayRunner(config=GatewayConfig(multiplex_profiles=True))

    async def check():
        for profile in ("orchestrator", "researcher"):
            session_id = f"api_completion_{profile}"
            event = {
                "type": "async_delegation",
                "platform": "api_server",
                "session_key": session_id,
                "origin_session_id": session_id,
                "parent_session_id": session_id,
            }
            async with runner._completion_event_scope(event):
                verdict = await runner._classify_completion_target(session_id)
                assert verdict == "deliver", (profile, verdict, str(get_hermes_home()))
                assert get_hermes_home() == home / "profiles" / profile
        print("Both served-profile parents remain deliverable")

    try:
        asyncio.run(check())
    finally:
        runner.close_all_session_db_handles()
