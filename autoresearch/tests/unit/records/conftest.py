import pytest

from autoresearch.paths import Layout
from autoresearch.records.store import RecordStore


@pytest.fixture
def layout(tmp_path):
    return Layout(tmp_path)


@pytest.fixture
def store(layout):
    return RecordStore(layout)
