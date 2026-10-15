"""C9 migrations are idempotent on real Hermes (Plan 03 Task 5.1). No model is called."""

import pytest
from integration.test_install import bootstrap

from ide_research_core.runtime import config_migrations

pytestmark = pytest.mark.hermes


def test_fresh_install_bootstrap_leaves_nothing_pending(install):
    bootstrap(install)
    assert config_migrations.pending(install.profile_dir) == []


def test_update_keeps_installer_tuning_and_stays_converged(install):
    profile = install.profile_dir
    bootstrap(install)
    install.hermes("-p", install.profile, "config", "set", "kanban.max_in_progress", "3")
    install.hermes("profile", "update", install.profile, "-y")
    bootstrap(install)
    config = config_migrations.read_config(profile)
    assert config["kanban"]["max_in_progress"] == 3
    assert config_migrations.pending(profile) == []
