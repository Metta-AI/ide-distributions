"""Builds go to a writable place even when the profile is installed read-only (hosted)."""
import os

import pytest


@pytest.mark.skipif(os.geteuid() == 0, reason='root ignores file permissions')
def test_read_only_install_builds_in_the_profile(tools, tmp_path, monkeypatch):
    storage = tools.storage
    monkeypatch.delenv('GAME_BUILD_ROOT', raising=False)
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / 'profile'))
    install = tmp_path / 'install/tools'
    install.mkdir(parents=True)
    assert storage.build_root(install) == install  # a writable checkout keeps its own folders
    install.chmod(0o555)
    try:
        assert storage.build_root(install) == tmp_path / 'profile/local/game-builds/pudge-wars-basic'
        monkeypatch.delenv('HERMES_HOME')
        with pytest.raises(OSError, match='GAME_BUILD_ROOT'):
            storage.build_root(install)
    finally:
        install.chmod(0o755)
    monkeypatch.setenv('GAME_BUILD_ROOT', str(tmp_path / 'override'))
    assert storage.build_root(install) == tmp_path / 'override/pudge-wars-basic'


def test_native_and_engine_share_the_build_root(tools):
    root = tools.storage.build_root()
    assert tools.native.CACHE == root / '.cache' and tools.native.BIN == root / 'bin'
    assert tools.engine.CACHE == root / '.cache'
