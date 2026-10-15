import json
import subprocess
import sys

import pytest

from ide_research_core.cli import main

from .fixtures import CLAIM, EXP_DESIGNED, HYP, RESULT


def run(capsys, *argv):
    code = main(["records", *argv])
    out = capsys.readouterr().out
    return code, json.loads(out)


@pytest.fixture
def root(tmp_path):
    return str(tmp_path)


def new(capsys, root, entity, meta, *extra):
    code, env = run(capsys, "new", entity, "--root", root, "--meta", json.dumps(meta), *extra)
    assert code == 0, env
    return env["data"]["id"]


def test_new_and_show(capsys, root):
    cid = new(capsys, root, "claim", CLAIM, "--body", "Damage is 10.", "--slug", "damage")
    code, env = run(capsys, "show", cid, "--root", root)
    assert code == 0 and env["ok"] is True
    assert env["data"]["meta"]["kind"] == "mechanism"
    assert env["data"]["body"].strip() == "Damage is 10."
    assert env["data"]["path"].endswith(f"kb/claims/{cid}--damage.md")


def test_new_invalid_is_business_failure(capsys, root):
    code, env = run(capsys, "new", "topic", "--root", root, "--meta", "{}")
    assert code == 1 and env["ok"] is False and env["error"]["problems"]


def test_bad_json_is_bad_arguments(capsys, root):
    code, env = run(capsys, "new", "topic", "--root", root, "--meta", "{not json")
    assert code == 2 and env["ok"] is False


def test_unknown_entity_is_bad_arguments(capsys, root):
    with pytest.raises(SystemExit) as exc:
        main(["records", "new", "lever", "--root", root, "--meta", "{}"])
    assert exc.value.code == 2


def test_show_missing_is_business_failure(capsys, root):
    code, env = run(capsys, "show", "C-01K6Z3V8QX0000000000000000", "--root", root)
    assert code == 1 and "not found" in env["error"]["message"]


def test_malformed_id_is_bad_arguments(capsys, root):
    code, env = run(capsys, "show", "nonsense", "--root", root)
    assert code == 2 and env["ok"] is False


def test_update_immutable_result_fails(capsys, root):
    rid = new(capsys, root, "result", RESULT, "--body", "analysis A")
    code, env = run(capsys, "update", rid, "--root", root, "--body", "analysis B")
    assert code == 1 and "immutable" in env["error"]["message"]


def test_update_patch(capsys, root):
    eid = new(capsys, root, "experiment", EXP_DESIGNED)
    code, _ = run(capsys, "update", eid, "--root", root, "--patch", json.dumps({"lever": "x"}))
    assert code == 0
    assert run(capsys, "show", eid, "--root", root)[1]["data"]["meta"]["lever"] == "x"


def test_status_applies_transition(capsys, root):
    hid = new(capsys, root, "hypothesis", HYP)
    code, env = run(
        capsys,
        "status",
        hid,
        "under-test",
        "--reason",
        "E-1 registered",
        "--ref",
        "E-1",
        "--root",
        root,
    )
    assert code == 0, env
    meta = run(capsys, "show", hid, "--root", root)[1]["data"]["meta"]
    assert meta["state"] == "under-test" and meta["state_ref"] == "E-1"
    assert meta["state_history"][-1]["reason"] == "E-1 registered"


def test_status_without_ref_fails_for_hypothesis(capsys, root):
    hid = new(capsys, root, "hypothesis", HYP)
    code, env = run(capsys, "status", hid, "under-test", "--reason", "r", "--root", root)
    assert code == 1 and "state_ref" in env["error"]["message"]


def test_status_illegal_transition_fails(capsys, root):
    eid = new(capsys, root, "experiment", EXP_DESIGNED)
    code, env = run(capsys, "status", eid, "reviewed", "--root", root)
    assert code == 1 and "designed -> reviewed" in env["error"]["message"]


def test_link_appends(capsys, root):
    eid = new(capsys, root, "experiment", EXP_DESIGNED)
    assert run(capsys, "link", eid, "results", "R-1", "--root", root)[0] == 0
    assert run(capsys, "link", eid, "results", "R-2", "--root", root)[0] == 0
    meta = run(capsys, "show", eid, "--root", root)[1]["data"]["meta"]
    assert meta["results"] == ["R-1", "R-2"]


def test_link_json_object(capsys, root):
    cid = new(capsys, root, "claim", CLAIM, "--body", "x")
    ev = {"ref": "EV-1", "tier": "B", "direct": True, "stance": "supports"}
    assert run(capsys, "link", cid, "evidence", json.dumps(ev), "--root", root)[0] == 0
    assert run(capsys, "show", cid, "--root", root)[1]["data"]["meta"]["evidence"] == [ev]


def test_find(capsys, root):
    hid = new(capsys, root, "hypothesis", HYP)
    new(capsys, root, "hypothesis", {**HYP, "lever": "other"})
    code, env = run(capsys, "find", "hypothesis", "--where", "lever=SK.last_hit", "--root", root)
    assert code == 0 and env["data"]["ids"] == [hid]


def test_validate_one_and_all(capsys, root, tmp_path):
    hid = new(capsys, root, "hypothesis", HYP)
    new(capsys, root, "claim", CLAIM, "--body", "x")
    code, env = run(capsys, "validate", hid, "--root", root)
    assert code == 0 and env["data"]["checked"] == 1
    code, env = run(capsys, "validate", "--all", "--root", root)
    assert code == 0 and env["data"]["checked"] == 2
    (tmp_path / "kb" / "claims" / "stray.md").write_text("---\na: 1\n---\nx\n")
    code, env = run(capsys, "validate", "--all", "--root", root)
    assert code == 1 and "kb/claims/stray.md" in env["error"]["problems"]


def test_validate_requires_target(capsys, root):
    code, _ = run(capsys, "validate", "--root", root)
    assert code == 2


def test_root_from_run_context(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("RESEARCH_WORKSPACE", str(tmp_path / "canonical"))
    monkeypatch.setenv("HERMES_KANBAN_WORKSPACE", str(tmp_path / "worker"))
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_1")
    code, env = run(capsys, "new", "hypothesis", "--meta", json.dumps(HYP))
    assert code == 0
    assert env["data"]["path"].startswith(str(tmp_path / "worker"))


def test_python_m_entry_point(tmp_path):
    proc = subprocess.run(
        [sys.executable, "-m", "ide_research_core", "records", "find", "claim", "--root", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == {"ok": True, "data": {"ids": []}}


@pytest.mark.parametrize("flag", ["--meta-file", "--body-file"])
def test_missing_input_file_is_bad_arguments(capsys, root, tmp_path, flag):
    argv = ["new", "topic", "--root", root, flag, str(tmp_path / "missing.json")]
    code, env = run(capsys, *argv)
    assert code == 2 and env["ok"] is False and "missing.json" in env["error"]["message"]


def test_missing_patch_file_is_bad_arguments(capsys, root, tmp_path):
    hid = new(capsys, root, "hypothesis", HYP)
    code, env = run(capsys, "update", hid, "--root", root, "--patch-file", str(tmp_path / "nope"))
    assert code == 2 and env["ok"] is False
