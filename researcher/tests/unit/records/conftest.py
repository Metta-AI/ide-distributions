import pytest

from ide_research_core.paths import Layout
from ide_research_core.records.store import RecordStore


@pytest.fixture
def layout(tmp_path):
    return Layout(tmp_path)


@pytest.fixture
def store(layout):
    return RecordStore(layout)
