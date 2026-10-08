"""Read-only lookups over version records: the platform id a version was uploaded as, and the
bootstrap baseline (amendment A14).

A baseline version is a version record with ``role: baseline`` and no experiment, written by
``upload.upload_baseline`` on a fresh install that has no champion. A baseline that a later
baseline names in ``supersedes`` is retired. The current baseline is the earliest
(lowest-id, ids are time ordered) baseline that no other baseline supersedes: normally there
is exactly one, but two cards that bootstrap at the same time can each merge one, and the
earliest one then wins deterministically.

This module imports only the record store, so ``runtime.promotion`` (the champion) and
``softmax_tool.upload`` (the writer) can both use it.
"""

from dataclasses import dataclass

from autoresearch.records.store import RecordStore

BASELINE_ROLE = "baseline"


@dataclass(frozen=True)
class Baseline:
    version_id: str
    platform_policy_version_id: str | None  # None until its ``uploaded`` event exists


def uploaded_policy(store: RecordStore, version_id: str) -> str | None:
    """The platform policy version id of ``version_id``'s ``uploaded`` event, if any."""
    for event_id in store.find("version_event", version=version_id, kind="uploaded"):
        return store.load(event_id)[0]["data"]["policy_version_id"]
    return None


def current_baseline(store: RecordStore) -> Baseline | None:
    """The current baseline version (module docstring), or None when there is none."""
    baselines = store.find("version", role=BASELINE_ROLE)
    superseded = {store.load(version_id)[0].get("supersedes") for version_id in baselines}
    heads = [version_id for version_id in baselines if version_id not in superseded]
    if not heads:
        return None
    return Baseline(heads[0], uploaded_policy(store, heads[0]))
