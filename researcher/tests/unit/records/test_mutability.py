import pytest

from ide_research_core.records.mutability import check_change

from .fixtures import CLAIM, EXP_DESIGNED, HYP, JOURNAL, JOURNAL_BODY, TOPIC

CID = "C-01K6Z3V8QX0000000000000000"
EID = "E-01K6Z3V8QX0000000000000000"
HID = "H-01K6Z3V8QX0000000000000000"
JID = "J-01K6Z3V8QX0000000000000000"
TID = "T-01K6Z3V8QX0000000000000000"
BINDING = {"version_id": "V-1", "platform_policy_version_id": "pv_1"}


# --- brief tests (verbatim, reformatted onto separate lines) ---
def test_design_frozen_after_build(store):
    eid = store.create("experiment", EXP_DESIGNED)
    store.update(eid, {"status": "built"})
    with pytest.raises(ValueError, match="evaluation_design"):
        store.update(eid, {"evaluation_design": {**EXP_DESIGNED["evaluation_design"], "n_min": 5}})


def test_candidate_binding_set_once(store):
    eid = store.create("experiment", {**EXP_DESIGNED, "status": "uploaded"})
    store.update(
        eid, {"candidate_binding": {"version_id": "V-1", "platform_policy_version_id": "pv_1"}}
    )
    with pytest.raises(ValueError, match="candidate_binding"):
        store.update(
            eid, {"candidate_binding": {"version_id": "V-2", "platform_policy_version_id": "pv_2"}}
        )


def test_evaluating_to_awaiting_credits_allowed(store):
    eid = store.create("experiment", {**EXP_DESIGNED, "status": "evaluating"})
    store.update(eid, {"status": "awaiting-credits"})
    assert store.load(eid)[0]["status"] == "awaiting-credits"


def test_results_append_only(store):
    eid = store.create("experiment", EXP_DESIGNED)
    store.update(eid, {"results": ["R-1"]})
    with pytest.raises(ValueError, match="append-only"):
        store.update(eid, {"results": ["R-2"]})


def test_contradiction_resolution_is_appended(store):
    cid = store.create(
        "claim",
        {
            **CLAIM,
            "evidence": [
                {
                    "ref": "EV-1",
                    "tier": "B",
                    "direct": True,
                    "stance": "contradicts",
                    "source_group": "g1",
                }
            ],
        },
        "x",
    )
    store.update(
        cid,
        {
            "evidence": [
                *store.load(cid)[0]["evidence"],
                {
                    "ref": "R-9",
                    "stance": "resolves",
                    "target": "EV-1",
                    "tier": "B",
                    "direct": True,
                    "source_group": "g2",
                },
            ]
        },
    )
    evidence = store.load(cid)[0]["evidence"]
    assert [(e["ref"], e["stance"]) for e in evidence] == [
        ("EV-1", "contradicts"),
        ("R-9", "resolves"),
    ]
    assert evidence[1]["target"] == "EV-1"


# --- direct check_change tests ---
def doc(meta, body=""):
    return (meta, body)


def exp(**overrides):
    return doc({**EXP_DESIGNED, "id": EID, **overrides})


def test_deletion_of_mutable_record_is_a_problem():
    problems = check_change("topic", doc({**TOPIC, "id": TID}), None)
    assert any("deleted" in p for p in problems)


def test_no_change_is_fine():
    assert check_change("experiment", exp(), exp()) == []


def test_design_change_allowed_while_designed():
    assert check_change("experiment", exp(), exp(baseline="V-9")) == []


def test_illegal_experiment_transition():
    problems = check_change("experiment", exp(), exp(status="reviewed"))
    assert any("designed -> reviewed" in p for p in problems)


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("designed", "built"),
        ("built", "uploaded"),
        ("uploaded", "evaluating"),
        ("evaluating", "reviewed"),
        ("uploaded", "awaiting-credits"),
        ("awaiting-credits", "evaluating"),
    ],
)
def test_legal_experiment_transitions(before, after):
    bound = {"candidate_binding": BINDING}
    assert check_change("experiment", exp(status=before, **bound), exp(status=after, **bound)) == []


@pytest.mark.parametrize("after", ["evaluating", "awaiting-credits"])
def test_cannot_leave_uploaded_without_binding(after):
    problems = check_change("experiment", exp(status="uploaded"), exp(status=after))
    assert any("candidate_binding" in p and "uploaded" in p for p in problems)


def test_lever_frozen_after_designed():
    problems = check_change("experiment", exp(status="built"), exp(status="built", lever="other"))
    assert any("lever" in p and "frozen" in p for p in problems)


def test_binding_only_in_uploaded():
    binding = {"version_id": "V-1", "platform_policy_version_id": "pv_1"}
    problems = check_change(
        "experiment", exp(status="built"), exp(status="built", candidate_binding=binding)
    )
    assert any("candidate_binding" in p and "uploaded" in p for p in problems)


def test_xp_requests_need_binding_and_fields():
    problems = check_change(
        "experiment", exp(status="uploaded"), exp(status="uploaded", xp_requests=[{"id": "xp_1"}])
    )
    assert any("candidate_binding" in p for p in problems)
    assert any("batch" in p for p in problems)


def test_decision_set_once():
    decided = {"outcome": "hold", "reason": "flat"}
    before = exp(status="reviewed", decision=decided)
    after = exp(status="reviewed", decision={"outcome": "submit", "reason": "changed my mind"})
    assert any("decision" in p and "once" in p for p in check_change("experiment", before, after))


def test_launch_attempts_cannot_decrease():
    before = exp(lifecycle={"launch_attempts": 2})
    problems = check_change("experiment", before, exp(lifecycle={"launch_attempts": 1}))
    assert any("launch_attempts" in p for p in problems)


def hyp(**overrides):
    return doc({**HYP, "id": HID, **overrides})


def test_hypothesis_transition_needs_reason_ref_and_history():
    problems = check_change("hypothesis", hyp(), hyp(state="under-test"))
    assert any("state_reason" in p for p in problems)
    assert any("state_ref" in p for p in problems)
    assert any("state_history" in p for p in problems)


def test_hypothesis_transition_ok_with_history():
    after = hyp(
        state="under-test",
        state_reason="E-1 registered",
        state_ref="E-1",
        state_history=[
            {"from": "proposed", "to": "under-test", "reason": "E-1 registered", "ref": "E-1"}
        ],
    )
    assert check_change("hypothesis", hyp(), after) == []


def test_hypothesis_illegal_transition():
    after = hyp(
        state="supported",
        state_reason="r",
        state_ref="R-1",
        state_history=[{"from": "proposed", "to": "supported", "reason": "r", "ref": "R-1"}],
    )
    assert any("proposed -> supported" in p for p in check_change("hypothesis", hyp(), after))


def test_hypothesis_experiments_append_only():
    problems = check_change("hypothesis", hyp(experiments=["E-1"]), hyp(experiments=["E-2"]))
    assert any("append-only" in p for p in problems)


def claim(**overrides):
    return doc({**CLAIM, "id": CID, **overrides}, "Damage is 10.")


def test_claim_body_edit_allowed():
    assert check_change("claim", claim(), doc(claim()[0], "Damage is 12.")) == []


def test_claim_status_change_needs_reason():
    problems = check_change("claim", claim(), claim(status="verified"))
    assert any("status_reason" in p for p in problems)


def test_claim_evidence_append_only():
    ev = {"ref": "EV-1", "tier": "B", "direct": True, "stance": "supports"}
    problems = check_change("claim", claim(evidence=[ev]), claim(evidence=[]))
    assert any("append-only" in p for p in problems)


def test_resolution_must_target_existing_evidence():
    ev = {"ref": "R-9", "tier": "B", "direct": True, "stance": "resolves", "target": "EV-404"}
    assert any("EV-404" in p for p in check_change("claim", None, claim(evidence=[ev])))


def test_superseded_by_set_once():
    problems = check_change("claim", claim(superseded_by="C-1"), claim(superseded_by="C-2"))
    assert any("superseded_by" in p for p in problems)


def test_journal_immutable_once_outcome_set():
    before = doc({**JOURNAL, "id": JID, "outcome": "done"}, JOURNAL_BODY)
    after = doc({**JOURNAL, "id": JID, "outcome": "done"}, JOURNAL_BODY + "\nmore")
    assert any("immutable" in p for p in check_change("journal", before, after))


def test_journal_editable_before_outcome():
    before = doc({**JOURNAL, "id": JID}, JOURNAL_BODY)
    after = doc({**JOURNAL, "id": JID, "outcome": "done"}, JOURNAL_BODY + "\nmore")
    assert check_change("journal", before, after) == []


def test_id_change_rejected():
    problems = check_change(
        "topic", doc({**TOPIC, "id": TID}), doc({**TOPIC, "id": TID[:-1] + "1"})
    )
    assert any("id" in p for p in problems)


def test_hypothesis_transition_cannot_reuse_reason_or_ref():
    before = hyp(state="under-test", state_reason="E-1 registered", state_ref="E-1")
    after = hyp(
        state="supported",
        state_reason="E-1 registered",
        state_ref="E-1",
        state_history=[
            {"from": "under-test", "to": "supported", "reason": "E-1 registered", "ref": "E-1"}
        ],
    )
    problems = check_change("hypothesis", before, after)
    assert any("new state_reason" in p for p in problems)
    assert any("new state_ref" in p for p in problems)


def test_claim_status_change_cannot_reuse_reason():
    before = claim(status_reason="first look")
    after = claim(
        status="verified",
        status_reason="first look",
        status_history=[{"from": "belief", "to": "verified", "reason": "first look", "ref": None}],
    )
    assert any("new status_reason" in p for p in check_change("claim", before, after))


def test_store_status_change_needs_fresh_reason_and_ref(store):
    hid = store.create("hypothesis", HYP)
    store.set_status(hid, "under-test", reason="E-1 registered", ref="E-1")
    with pytest.raises(ValueError, match="new state_reason"):
        store.update(hid, {"state": "supported"})
    with pytest.raises(ValueError, match="new state_ref"):
        store.set_status(hid, "supported", reason="R-1 submit", ref="E-1")
    store.set_status(hid, "supported", reason="R-1 submit", ref="R-1")
    meta = store.load(hid)[0]
    assert meta["state"] == "supported"
    assert [h["ref"] for h in meta["state_history"]] == ["E-1", "R-1"]


def test_validate_change_rejects_reused_reason(store, layout):
    from ide_research_core.records.registry import path_for

    hid = store.create("hypothesis", HYP)
    store.set_status(hid, "under-test", reason="E-1 registered", ref="E-1")
    before = store.load(hid)
    after_meta = {
        **before[0],
        "state": "supported",
        "state_history": [
            *before[0]["state_history"],
            {"from": "under-test", "to": "supported", "reason": "E-1 registered", "ref": "E-1"},
        ],
    }
    rel = str(path_for(layout, hid).relative_to(layout.root))
    problems = store.validate_change(rel, before, (after_meta, before[1]))
    assert any("new state_reason" in p for p in problems)
    assert any("new state_ref" in p for p in problems)
