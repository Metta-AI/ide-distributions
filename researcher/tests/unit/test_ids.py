import time

import pytest

from ide_research_core.ids import ENTITY_PREFIXES, new_id, parse_id, short


def test_new_id_has_prefix_and_ulid_length():
    for entity, prefix in ENTITY_PREFIXES.items():
        value = new_id(entity)
        assert value.startswith(prefix + "-")
        assert len(value) == len(prefix) + 1 + 26


def test_new_id_sorts_by_creation_time():
    first = new_id("claim")
    time.sleep(0.005)
    second = new_id("claim")
    assert first < second


def test_new_id_rejects_unknown_entity():
    with pytest.raises(ValueError):
        new_id("nonsense")


def test_parse_id_roundtrip_including_multi_letter_prefix():
    assert parse_id(new_id("hub"))[0] == "HUB"
    prefix, ulid = parse_id("C-01K6Z3V8QXABCDEFGHJKMNPQRS")
    assert (prefix, ulid) == ("C", "01K6Z3V8QXABCDEFGHJKMNPQRS")


def test_parse_id_rejects_unknown_prefix_and_bad_ulid():
    with pytest.raises(ValueError):
        parse_id("ZZ-01K6Z3V8QXABCDEFGHJKMNPQRS")
    with pytest.raises(ValueError):
        parse_id("C-short")
    with pytest.raises(ValueError):
        parse_id("nodash")


def test_short():
    assert short("C-01K6Z3V8QXABCDEFGHJKMNPQRS") == "C-MNPQRS"
