import shutil
from pathlib import Path

import pytest

from ide_research_core.paths import Layout

KB_SMALL = Path(__file__).resolve().parents[2] / "fixtures" / "workspaces" / "kb_small"


def kb_id(prefix: str, n: int) -> str:
    """Fixed ids used by the kb_small fixture."""
    return f"{prefix}-01KB{n:022d}"


C_GOOD, C_GRADE, C_VERIF, C_CONTRA, C_ORPHAN, C_REFUTED, C_ARCH, C_MISS = (
    kb_id("C", n) for n in range(1, 9)
)
HUB, TOPIC, HYP, H_MISS = kb_id("HUB", 1), kb_id("T", 1), kb_id("H", 1), kb_id("H", 9)


@pytest.fixture
def kb_layout(tmp_path):
    """A writable copy of the kb_small fixture workspace."""
    root = tmp_path / "kb_small"
    shutil.copytree(KB_SMALL, root)
    return Layout(root)
