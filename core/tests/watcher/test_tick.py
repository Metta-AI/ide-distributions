"""Observation belongs to the lifetime service; no cron endpoint remains."""

import pytest

from research_core import cli


def test_watcher_tick_command_is_removed():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["watcher", "tick"])
