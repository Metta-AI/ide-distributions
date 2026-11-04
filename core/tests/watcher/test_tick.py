"""Cron is an observation request, never a second scheduler."""

import json
import sqlite3

import pytest

from research_core.store.db import Store
from research_core.watcher import tick as tick_module


def test_main_persists_an_observation_request_without_running_a_tick(tmp_path, capsys):
    store_path = tmp_path / "ws" / ".state" / "store.db"
    assert tick_module.main([], env={"IDE_WORKSPACE": str(tmp_path / "ws")}) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["wakeAgent"] is False and payload["queued"] is True
    with Store.open(store_path, kind="workspace") as store:
        assert store.events_after(0)[-1].kind == "coordinator.observation_requested"
        assert store.get_meta("heartbeat") is None


def test_cron_does_not_independently_repair_or_execute_on_a_mismatched_store(tmp_path):
    from research_core.store.errors import SchemaMismatch

    store_path = tmp_path / "ws" / ".state" / "store.db"
    Store.open(store_path, kind="workspace").close()
    with sqlite3.connect(store_path) as conn:
        conn.execute("ALTER TABLE directives ADD COLUMN junk TEXT")
    with pytest.raises(SchemaMismatch):
        tick_module.main([], env={"IDE_WORKSPACE": str(tmp_path / "ws")})
