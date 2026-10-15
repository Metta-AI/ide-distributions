"""metric-noise.yaml game_version is read as a string (Task 3.9 carry fix): a hand-written
``game_version: 65`` is the integer 65 in YAML."""

import pytest

from autoresearch.paths import Layout
from autoresearch.stats.noise import load_noise


@pytest.mark.parametrize("written", ["65", "'65'"])
def test_numeric_game_version_loads_as_a_string(tmp_path, written):
    layout = Layout(tmp_path)
    layout.stats.mkdir()
    (layout.stats / "metric-noise.yaml").write_text(
        f"game_version: {written}\ncomputed_at: '2026-10-07T00:00:00+00:00'\n"
        "evidence: []\nmetrics: {}\n"
    )
    assert load_noise(layout, "65")["game_version"] == "65"
