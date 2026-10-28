"""``host_capabilities`` reports the host without running anything or returning secrets."""
import os

import pytest

from ide_research_core.host_capabilities import host_capabilities, register


def test_reports_presence_without_executing_or_returning_secrets(tmp_path):
    executable = tmp_path / "bin/coworld"
    executable.parent.mkdir()
    marker = tmp_path / "executed"
    executable.write_text(f"#!/bin/sh\ntouch {marker}\n")
    executable.chmod(0o755)
    env = {"PATH": str(executable.parent), "RESEARCH_CLI": str(executable),
           "SOFTMAX_PLAYER_CREDENTIAL": "cga_private", "HOME": str(tmp_path)}
    result = host_capabilities(tmp_path / "profile", env)
    assert result["executables"]["coworld"] is True
    assert result["executables"]["softmax"] is False
    assert result["research_cli_available"] is True
    assert not marker.exists()
    assert "cga_private" not in str(result)


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permissions")
def test_reports_read_only_install_and_game_requirements(tmp_path):
    profile = tmp_path / "profile"
    (profile / "games").mkdir(parents=True)
    (profile / "games").chmod(0o555)
    try:
        result = host_capabilities(profile, {"PATH": "", "HOME": str(tmp_path), "TMPDIR": str(tmp_path)})
    finally:
        (profile / "games").chmod(0o755)
    assert result["writable"]["installed_games"] is False
    assert result["writable"]["game_cache"] is True and result["writable"]["tmpdir"] is True


def test_registers_in_the_given_toolset():
    calls = []

    class Context:
        def register_tool(self, **kwargs):
            calls.append(kwargs)

    register(Context(), "researcher")
    assert calls[0]["name"] == "host_capabilities" and calls[0]["toolset"] == "researcher"


def test_reports_the_installed_distribution_commit(tmp_path):
    from ide_research_core.host_capabilities import installed_commit

    image = tmp_path / "opt/ide-distributions"
    (image / "researcher/src").mkdir(parents=True)
    (image / "researcher.commit").write_text("f4840ec9086983379f8447ebbfefe5889d6cf25c\n")
    profile = tmp_path / "profiles/researcher-1"
    profile.mkdir(parents=True)
    (profile / "src").symlink_to(image / "researcher/src")
    assert installed_commit(profile) == "f4840ec9086983379f8447ebbfefe5889d6cf25c"
    # A checkout install (no image record) reports nothing rather than guessing.
    (tmp_path / "checkout/src").mkdir(parents=True)
    assert installed_commit(tmp_path / "checkout") is None
