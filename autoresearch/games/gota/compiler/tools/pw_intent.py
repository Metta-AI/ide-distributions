#!/usr/bin/env python3
# Vendored from personal_labs_paintbot_pw@6b0bffa5085ffc4bf94ecaa8baee463e55599c84: paintbot_pw_lab/tools/pw_intent.py
"""PWI intent telemetry: record local episodes with seat logs, parse, audit.

Our BASIC prints at most one `PWI` line per decision (reference/intent_telemetry.bas):

    PWI v=1 t=<worldTick> m=<mode> h=<heart> s=<target seat> r=<reason> e=<enemies seen> c=<changed>

pw_episodes.py already stores every `PWI ` line in the `policy_log` table (line_kind
`intent`, raw `key=value` strings in `fields`). This module owns the format: it types those
fields (`intents`), summarizes them per seat and policy (`intent_summary`), and joins them to
the hash-checked replay to list where the policy's intent and belief disagree with what
happened (`audit`).

    # Local episodes WITH seat logs (the -d:coworld engine through the hosted handoff; the
    # headless and native runners discard PRINT output). Default --out analysis/pw_intent/episodes/;
    # a complete recording with the same inputs is reused:
    uv run python paintbot_pw_lab/tools/pw_intent.py record A.bas B.bas --seeds 1-4 --json
    # Intent lines per seat, then the belief/intent audit (writes divergences.csv, checks.csv):
    uv run python paintbot_pw_lab/tools/pw_intent.py show paintbot_pw_lab/analysis/pw_intent/episodes --json
    uv run python paintbot_pw_lab/tools/pw_intent.py audit paintbot_pw_lab/analysis/pw_intent/episodes --json

Alignment: the line's `t` is `worldTick` when the policy decided = the table tick of the world
it decided from, so the audit joins it to `visibility`/`states` rows at the SAME t (the command
it issued is in the `states` row at t + 1). Belief: `s` and `e` are observed identities, and a
disguised body answers to its disguise (`observed_view`). Audit needs dense samples: `audit`
traces with state_every=1 and vis_every=1 by default (about 3 s per 3,000-tick episode) and
counts every line it could not check.

--json prints one envelope (pw_cli.py). Exit codes: 0 ok; 1 some episodes failed to record or
load (the rest are written, failures listed); 2 usage error; 3 pw_trace or the handoff engine
not built (run paintbot_pw_lab/tools/build_tools.sh). Contract: docs/tools/pw_intent.md.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pw_cli  # noqa: E402
import pw_episodes as pe  # noqa: E402
import pw_release  # noqa: E402

INTENT_VERSION = 1
# Every key of a v1 line, all integers. Keep in step with reference/intent_telemetry.bas.
FIELDS = {"v": "version", "t": "t", "m": "mode", "h": "heart", "s": "target", "r": "reason",
          "e": "seen", "c": "changed"}
MODE_NAMES = {0: "hold", 1: "heart", 2: "cover", 3: "supply", 4: "retreat", 5: "fight",
              6: "chase", 7: "defend"}
REASON_NAMES = {0: "none", 1: "squad_pick", 2: "avoided_unreachable", 3: "outnumbered",
                4: "low_hp", 5: "wanted_supply", 6: "heard_sound", 7: "teammate_in_line"}
HEART_MODES = (1, 2, 4, 7)       # modes whose `h` is a heart the seat means to walk to
# Checked 2026-09-30 against league walkers whose walk goal is a heart (no PWI lines in league episodes;
# evidence in docs/tools/pw_intent.md § Thresholds). All three kept.
HEART_APPROACH_TICKS = 72        # 3 s: an intended heart should be closer by then (a free walk closes up to 2,016)
HEART_APPROACH_MIN_GAIN = 100    # 72-tick goal gain is bimodal: 30% of windows < 100 (almost all 0), ~1% per 100 above
HEART_ARRIVED = 400              # share gaining < 100: 58% at 200-300, 38% at 300-400, 26% at 400-500, 13-18% beyond
LEAGUE_GLORY = {"behind_lives": 5, "behind_cogs": 10}
AUDIT_RULES = ("target_not_visible", "target_dead", "seen_mismatch",     # consistency: expect 0
               "target_is_ally", "seen_fooled",                           # deception by disguise
               "heart_not_approached")                                    # intent vs outcome
CHECK_STATUSES = ("checked", "unchecked", "skipped_superseded", "skipped_died", "skipped_arrived")
CHECK_COLUMNS = ["episode_id", "rule", *CHECK_STATUSES, "divergent"]
CONSISTENCY_RULES = AUDIT_RULES[:3]
DIVERGENCE_COLUMNS = ["episode_id", "seat", "t", "rule", "mode", "heart", "target", "detail"]
DISGUISE_RULES = 27               # rules version from which a uniform changes the observed seat (sim.nim observedSeat)
INTENT_COLUMNS = ["episode_id", "seat", "policy_key", "t", "version", "mode", "mode_name", "heart",
                  "target", "reason", "reason_name", "seen", "changed", "parse_error", "file", "line_no"]


class IntentError(ValueError):
    pass


# ---------------------------------------------------------------- parsing

def parse_fields(fields: dict[str, str]) -> dict:
    """Type one line's raw key=value fields. Raises IntentError on a malformed or foreign line."""
    missing = [key for key in FIELDS if key not in fields]
    if missing:
        raise IntentError(f"missing keys {missing}")
    out = {}
    for key, name in FIELDS.items():
        text = fields[key]
        if not text.lstrip("-").isdigit():
            raise IntentError(f"{key}={text!r} is not an integer")
        out[name] = int(text)
    if out["version"] != INTENT_VERSION:
        raise IntentError(f"version {out['version']} != {INTENT_VERSION}")
    if out["changed"] not in (0, 1):
        raise IntentError(f"c={out['changed']} is not 0/1")
    return out


def parse_line(line: str) -> dict:
    """Parse one raw `PWI ...` line (the same tokenizing pw_episodes.policy_log uses)."""
    if not line.startswith(pe.INTENT_PREFIX):
        raise IntentError("not a PWI line")
    fields = dict(item.split("=", 1) for item in line[len(pe.INTENT_PREFIX):].split() if "=" in item)
    return parse_fields(fields)



# Strategy telemetry v2. Parsing is independent of the v1 gameplay audit: G5 validates
# the wire contract and coverage; the five-level strategy audit is a later milestone.
V2_KEYS = {
    "PWD": ("t", "r", "c", "i", "h", "p", "f"),
    "PWP": ("t", "a", "r", "o", "n", "p"),
    "PWE": ("t", "c", "e", "k"),
    "PWB": ("t", "k", "d"),
    "PWC": ("t", "m", "s", "w", "d"),
}


def parse_v2_line(line: str, mapping: dict) -> dict:
    """Validate a v2 line against its build's code table, including vector lengths."""
    import re
    parts = line.strip().split()
    if not parts or parts[0] not in V2_KEYS:
        raise IntentError("not a telemetry v2 line")
    kind = parts[0]
    keys = ("v", *V2_KEYS[kind])
    if len(parts) != len(keys) + 1:
        raise IntentError(f"{kind}: wrong field count")
    values = {}
    for expected, token in zip(keys, parts[1:]):
        if "=" not in token:
            raise IntentError(f"{kind}: malformed field")
        key, text = token.split("=", 1)
        if key != expected:
            raise IntentError(f"{kind}: expected {expected}, got {key}")
        vector = (kind == "PWD" and key in ("i", "f")) or (kind in ("PWB", "PWC") and key == "d")
        entries = text.split(",") if vector else [text]
        if any(not re.fullmatch(r"-?[0-9]+", entry) for entry in entries):
            raise IntentError(f"{kind}.{key}: expected int32")
        numbers = [int(entry) for entry in entries]
        if any(not -(2**31) <= number < 2**31 for number in numbers):
            raise IntentError(f"{kind}.{key}: int32 overflow")
        values[key] = numbers if vector else numbers[0]
    if values["v"] != 2 or values["t"] < 0:
        raise IntentError("invalid version or tick")
    codes = mapping["codes"]

    def code(field, table, allow_zero=False):
        valid = set(codes.get(table, {}).values())
        if allow_zero:
            valid.add(0)
        if values[field] not in valid:
            raise IntentError(f"{kind}.{field}: unknown {table} code {values[field]}")

    if kind == "PWD":
        code("r", "rule", True)
        code("c", "capability", True)
        if len(values["i"]) != 3 or len(values["f"]) != mapping["flag_words"]:
            raise IntentError("PWD: wrong input or flag vector length")
        if values["h"] not in (0, 1) or values["p"] < 0:
            raise IntentError("PWD: invalid held flag or priority version")
        count = len(codes.get("situation", {}))
        for index, word in enumerate(values["f"]):
            bits = min(31, max(0, count - index * 31))
            if word < 0 or word >= 2**bits:
                raise IntentError("PWD: unknown situation flag bits")
        rule = next((r for r in mapping["rules"] if r["code"] == values["r"]), None)
        expected_cap = codes["capability"][rule["capability"]] if rule else 0
        if values["c"] != expected_cap:
            raise IntentError("PWD: capability does not match rule")
    elif kind == "PWP":
        code("a", "adaptation", True)
        code("r", "rule")
        if not 0 <= values["o"] <= 1000 or not 0 <= values["n"] <= 1000 or values["p"] < 0:
            raise IntentError("PWP: invalid priority or version")
        if values["a"] == 0:
            rule = next(rule for rule in mapping["rules"] if rule['code'] == values['r'])
            if values['p'] != 0 or values['o'] != rule['priority'] or values['n'] != rule['priority']:
                raise IntentError("PWP: invalid initial priority snapshot")
    elif kind == "PWE":
        code("c", "capability")
        code("e", "event")
        capability = next(key for key, value in codes["capability"].items() if value == values["c"])
        valid = set(codes.get("condition", {}).get(capability, {}).values())
        if values["k"] not in valid | {0}:
            raise IntentError("PWE: unknown capability condition")
    elif kind == "PWB":
        code("k", "knowledge")
        knowledge = next(key for key, value in codes["knowledge"].items() if value == values["k"])
        fields = mapping["components"][knowledge]["log_fields"]
        if len(values["d"]) != sum(field["cells"] for field in fields):
            raise IntentError("PWB: wrong belief vector length")
    elif kind == "PWC":
        code("m", "message")
        if values["s"] not in (1, 2) or not 0 <= values["w"] < 16:
            raise IntentError("PWC: invalid direction or speaker")
        message = next(key for key, value in codes["message"].items() if value == values["m"])
        fields = mapping["components"][message].get("log_fields", [])
        cells = sum(field["cells"] for field in fields)
        if len(values["d"]) != max(1, cells) or (not cells and values["d"] != [0]):
            raise IntentError("PWC: wrong message vector length")
    return {"kind": kind, **values}



def parse_telemetry_line(line: str, mapping: dict) -> list[dict]:
    """Expand a compact batch into ordinary PWC events; preserve the v2 contract."""
    if not line.startswith("PWC v=3 "):
        return [parse_v2_line(line, mapping)]
    import re
    import strategy_comms as comms
    match = re.fullmatch(r"PWC v=3 t=([0-9]+) b=([0-9]+)", line.strip())
    if match is None:
        raise IntentError("PWC: malformed compact batch")
    tick = int(match[1])
    if tick >= 2**31:
        raise IntentError("PWC: tick overflow")
    config = mapping.get("comms")
    if not config or config.get("version") != 1:
        raise IntentError("PWC: build does not declare comms-v1")
    try:
        keys = tuple(config["keys"])
        decoded = comms.decode_batch(match[2], tick, keys)
        events = []
        for direction, message in decoded:
            component = config["messages"][str(message.kind)]
            code = mapping["codes"]["message"][component]
            a, b = comms.payloads(message, tick - (direction == 2), keys)
            event = parse_v2_line(
                f"PWC v=2 t={tick} m={code} s={direction} w={message.sender} d={a},{b}", mapping)
            events.append(event)
        return events
    except (ValueError, KeyError, IndexError, TypeError) as error:
        raise IntentError(f"PWC: invalid compact batch: {error}") from error


def validate_v2_logs(root: Path, mapping: dict) -> dict:
    """G5: every candidate seat needs readable telemetry and declared check fields.

    Checks that cannot be observed are explicitly unmeasurable and fail coverage.
    This is wire/coverage validation, not a claim that gameplay checks passed.
    """
    failures, seats = [], []
    codec = bool(mapping.get("comms"))
    all_seen = set()
    message_counts = {key: {"sent": 0, "received": 0} for key in mapping["codes"].get("message", {})}
    required = {"PWD." + key for key in V2_KEYS["PWD"]}
    for component in mapping["components"].values():
        for check in component.get("checks", []):
            required.update(item.replace("`", "") for item in check.get("reads", []) if item != "replay")
    for key, component in mapping["components"].items():
        required.update(f'{key}.{field["name"]}' for field in component.get("log_fields", []))
    event_required = {field for field in required if codec and
                      (field.startswith("PWC.") or field.startswith("COM."))}
    required -= event_required
    metas = sorted(root.rglob("*.meta.json"))
    for meta_path in metas:
        meta = json.loads(meta_path.read_text())
        side = meta.get("a_side")
        if side not in (0, 1):
            failures.append({"file": str(meta_path), "message": "missing candidate side"})
            continue
        for seat in range(side, 16, 2):
            path = meta_path.parent / f"player-{seat}.log"
            seen, kinds, by_tick, count = set(), Counter(), Counter(), 0
            snapshots = set()
            if not path.is_file():
                failures.append({"file": str(path), "message": "unmeasurable: missing seat log"})
                continue
            last_tick = -1
            batch_tick = -1
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if "BASIC error:" in line or "BASIC VM disabled" in line:
                    failures.append({"file": str(path), "line": number, "message": line})
                if line.split(" ", 1)[0] not in V2_KEYS:
                    continue
                try:
                    events = parse_telemetry_line(line, mapping)
                    tick = events[0]["t"]
                    if tick < last_tick:
                        raise IntentError("telemetry ticks went backwards")
                    if tick == batch_tick:
                        raise IntentError("telemetry follows compact batch in the same tick")
                    if line.startswith("PWC v=3 "):
                        batch_tick = tick
                    last_tick = tick
                    by_tick[tick] += len(line.encode()) + 1
                    count += 1
                    for event in events:
                        if codec and event["kind"] == "PWC":
                            if (event["s"] == 1 and event["w"] != seat) or (
                                    event["s"] == 2 and (event["w"] % 2 != seat % 2 or event["w"] == seat)):
                                raise IntentError("PWC: speaker does not match direction and candidate team")
                        kinds[event["kind"]] += 1
                        if event["kind"] == "PWP" and event["a"] == 0 and event["p"] == 0:
                            snapshots.add(event["r"])
                        seen.update(event["kind"] + "." + key for key in V2_KEYS[event["kind"]])
                        if event["kind"] in ("PWB", "PWC"):
                            table, code_key = ("knowledge", "k") if event["kind"] == "PWB" else ("message", "m")
                            component = next(key for key, value in mapping["codes"][table].items() if value == event[code_key])
                            seen.update(component + "." + field["name"] for field in mapping["components"][component].get("log_fields", []))
                            if event["kind"] == "PWC":
                                direction = "sent" if event["s"] == 1 else "received"
                                message_counts[component][direction] += 1
                except (IntentError, KeyError, ValueError) as error:
                    failures.append({"file": str(path), "line": number, "message": str(error)})
            all_seen.update(seen)
            missing = sorted(required - seen)
            missing_snapshots = set(mapping['codes']['rule'].values()) - snapshots
            if missing_snapshots:
                failures.append({"file": str(path), "message": "missing initial priority snapshots",
                                 "rules": sorted(missing_snapshots)})
            if not count or missing:
                failures.append({"file": str(path), "message": "unmeasurable: missing telemetry fields", "fields": missing})
            peak = max(by_tick.values(), default=0)
            if peak > 512:
                failures.append({"file": str(path), "message": f"print bytes per tick {peak} > 512"})
            seats.append({"seat": seat, "file": str(path), "lines": count, "kinds": dict(kinds),
                          "missing_fields": missing, "peak_bytes": peak})
    if not metas:
        failures.append({"message": "unmeasurable: no complete recording metadata"})
    unexercised = [key for key, counts in message_counts.items() if not sum(counts.values())]
    if codec:
        for direction in ("sent", "received"):
            if not any(counts[direction] for counts in message_counts.values()):
                failures.append({"message": f"unmeasurable: communication {direction} path not exercised"})
    coverage = {"messages": message_counts,
                "unexercised_message_types": unexercised if codec else [],
                "unexercised_event_fields": sorted(event_required - all_seen)}
    return {"passed": not failures and bool(seats), "summary": {"seat_recordings": len(seats),
            "lines": sum(s["lines"] for s in seats), "failures": len(failures),
            "unexercised_message_types": coverage["unexercised_message_types"]},
            "seats": seats, "failures": failures, "coverage": coverage}



def intents(ep) -> pd.DataFrame:
    """One row per intent line in the episode's policy_log. Malformed lines are kept with
    `parse_error` set and null typed fields, never dropped."""
    log = ep["policy_log"]
    lines = log[log["line_kind"] == "intent"]
    keys = dict(zip(ep["seats"]["seat"], ep["seats"]["policy_key"]))
    rows = []
    for line in lines.itertuples(index=False):
        row = {"episode_id": line.episode_id, "seat": int(line.seat), "policy_key": keys.get(int(line.seat)),
               "file": line.file, "line_no": int(line.line_no), "parse_error": None}
        try:
            row.update(parse_fields(json.loads(line.fields)))
            row["mode_name"] = MODE_NAMES.get(row["mode"], f"mode_{row['mode']}")
            row["reason_name"] = REASON_NAMES.get(row["reason"], f"reason_{row['reason']}")
        except IntentError as error:
            row["parse_error"] = str(error)
        rows.append(row)
    frame = pd.DataFrame(rows, columns=INTENT_COLUMNS)
    for column in ("seat", "t", "version", "mode", "heart", "target", "reason", "seen", "changed", "line_no"):
        frame[column] = frame[column].astype("Int64")
    for column in ("episode_id", "policy_key", "mode_name", "reason_name", "parse_error", "file"):
        frame[column] = frame[column].astype("string")
    return frame.sort_values(["seat", "t"], kind="stable").reset_index(drop=True)


def intent_summary(ep, frame: pd.DataFrame | None = None) -> pd.DataFrame:
    """Per seat with a log: line counts, mode shares (share of lines, not of ticks) and how
    often the chosen heart switched. Seats without a log are absent (unknown, not zero)."""
    frame = intents(ep) if frame is None else frame
    log = ep["policy_log"]
    logged = sorted(int(s) for s in log.loc[log["line_kind"] == "log_present", "seat"].unique())
    errors = Counter(int(s) for s in log.loc[log["line_kind"] == "vm_error", "seat"])
    rows = []
    for seat in logged:
        mine = frame[(frame["seat"] == seat) & frame["parse_error"].isna()]
        row = {"episode_id": ep.episode_id, "seat": seat, "intent_lines": len(mine),
               "bad_lines": int(((frame["seat"] == seat) & frame["parse_error"].notna()).sum()),
               "vm_errors": errors.get(seat, 0),
               "last_t": None if mine.empty else int(mine["t"].max())}
        for code, name in MODE_NAMES.items():
            row[f"mode_share_{name}"] = None if mine.empty else float((mine["mode"] == code).mean())
        hearts = mine.loc[mine["heart"] >= 0, "heart"].to_numpy(dtype=int)
        row["heart_switches"] = int((hearts[1:] != hearts[:-1]).sum())
        rows.append(row)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- audit

def _heart_positions(ep) -> dict[int, tuple[int, int]]:
    return {int(h["idx"]): (int(h["pos"][0]), int(h["pos"][1])) for h in ep.meta["hearts"]}


def observed_view(observer: int, bodies, disguised: dict[int, bool], positions: dict[int, tuple[int, int]],
                  seats: int, disguise_rules: bool = True) -> dict[int, int]:
    """What BASIC's `visible(i)` / `playerX(i)` show `observer`: {identity: body}.

    `bodies` are the seats the engine's `visible()` says the observer sees (the `visibility`
    table, which is by body). A disguised body answers to its disguise, `body xor 1`, which is a
    seat of the OTHER team, and never to the observer's own seat (then `+2 mod seats`). When two
    visible bodies share an identity, BASIC reports the nearer one (ties: the lower body). Mirrors
    `sim.nim` `observedSeat` and `bots.nim` `bodyForSeat` (rules >= 27; unchanged at 0.3.79)."""
    view: dict[int, int] = {}
    ox, oz = positions[observer]

    def d2(body: int) -> int:
        x, z = positions[body]
        return (x - ox) ** 2 + (z - oz) ** 2

    for body in sorted(int(b) for b in bodies):
        if body == observer:
            continue
        identity = body
        if disguise_rules and disguised[body]:
            identity = body ^ 1
            if identity == observer:
                identity = (identity + 2) % seats
        current = view.get(identity)
        if current is None or d2(body) < d2(current):
            view[identity] = body
    return view


def audit(ep, frame: pd.DataFrame | None = None, horizon: int = HEART_APPROACH_TICKS) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Join intent lines to replay truth. Returns (divergences, checks).

    Alignment: a line's `t` is the `worldTick` of the world the policy decided from, and the
    `visibility`/`states` rows at table tick `t` describe that same world (both are stamped with
    `w.tick` after step t; `decide` then runs on it for step t + 1). So the line at `t` is joined
    to rows at `t`, not t + 1.

    Belief model: `s` and `e` are in the policy's OBSERVED identities (`observed_view`), so a
    disguised body counts under its disguise. Two kinds of rule follow:
      consistency (the policy or its telemetry is wrong about what it could see; expect 0):
        target_not_visible  s >= 0 but no body the seat sees answers to identity s at t
        target_dead         s >= 0, s is not in view and seat s has hp <= 0 at t
        seen_mismatch       e != the enemy-team identities in the seat's view at t
                            (only meaningful when `e` counts visible enemies)
      deception (the view itself was false because of a disguise; genuine belief != truth):
        target_is_ally      s is in view but the body behind it is a disguised teammate
        seen_fooled         the enemy-team identities in view != the enemy bodies in view
      intent vs outcome:
        heart_not_approached  m in HEART_MODES, h >= 0, no later line names another heart
                            before t+horizon, seat alive through t+1 .. t+horizon, farther than
                            HEART_ARRIVED from the heart at t+1, and the distance fell by less
                            than HEART_APPROACH_MIN_GAIN by t+horizon
    Each line is checked only when the replay samples it needs exist; the rest are counted
    as unchecked in `checks`, never dropped silently.
    """
    frame = intents(ep) if frame is None else frame
    lines = frame[frame["parse_error"].isna()]
    states = ep["states"].set_index(["t", "seat"])
    vis = ep["visibility"]
    sees = {(int(r.t), int(r.seat)): set(int(x) for x in r.sees) for r in vis.itertuples(index=False)} if len(vis) else {}
    teams = {int(s): int(t) for s, t in zip(ep["seats"]["seat"], ep["seats"]["team"])}
    seats = len(teams)
    disguise_rules = int(ep["episodes"]["rules"].iloc[0]) >= DISGUISE_RULES
    hearts = _heart_positions(ep)
    rows: list[dict] = []
    checks = Counter()

    def state(t: int, seat: int):
        key = (t, seat)
        return states.loc[key] if key in states.index else None

    def view_at(t: int, seat: int):
        """(bodies seen, observed view) at t, or None when a sample is missing."""
        bodies = sees.get((t, seat))
        if bodies is None:
            return None
        rows_at = {b: state(t, b) for b in bodies | {seat}}
        if any(r is None for r in rows_at.values()):
            return None
        disguised = {b: bool(r["disguised"]) for b, r in rows_at.items()}
        positions = {b: (int(r["x"]), int(r["z"])) for b, r in rows_at.items()}
        return bodies, observed_view(seat, bodies, disguised, positions, seats, disguise_rules)

    def describe(bodies, view) -> str:
        masked = {i: b for i, b in sorted(view.items()) if i != b}
        return f"bodies {sorted(bodies)}" + (f", disguised as {masked}" if masked else "")

    # The tick of each seat's next line naming a different heart, to skip superseded heart intents.
    lines = lines.copy()
    new_heart = lines["heart"].ne(lines.groupby("seat")["heart"].shift()).fillna(True).astype(bool)
    change_t = lines["t"].where(new_heart)
    lines["next_change"] = change_t.groupby(lines["seat"]).transform(lambda s: s.shift(-1).bfill())

    for line in lines.itertuples(index=False):
        t, seat, target = int(line.t), int(line.seat), int(line.target)
        base = {"episode_id": line.episode_id, "seat": seat, "t": t, "mode": line.mode_name,
                "heart": int(line.heart), "target": target}
        seen = view_at(t, seat)
        if seen is None:
            checks["seen_mismatch", "unchecked"] += 1
            checks["seen_fooled", "unchecked"] += 1
        else:
            bodies, view = seen
            observed_enemies = sum(1 for identity in view if teams[identity] != teams[seat])
            true_enemies = sum(1 for body in bodies if teams[body] != teams[seat])
            checks["seen_mismatch", "checked"] += 1
            if observed_enemies != int(line.seen):
                rows.append({**base, "rule": "seen_mismatch",
                             "detail": f"policy e={int(line.seen)}, view has {observed_enemies} enemy identities; "
                                       + describe(bodies, view)})
            checks["seen_fooled", "checked"] += 1
            if observed_enemies != true_enemies:
                rows.append({**base, "rule": "seen_fooled",
                             "detail": f"view has {observed_enemies} enemy identities, truth {true_enemies} enemy "
                                       f"bodies; " + describe(bodies, view)})
        if target >= 0:
            if seen is None:
                for rule in ("target_not_visible", "target_is_ally", "target_dead"):
                    checks[rule, "unchecked"] += 1
            else:
                bodies, view = seen
                checks["target_not_visible", "checked"] += 1
                checks["target_is_ally", "checked"] += 1
                if target not in view:
                    rows.append({**base, "rule": "target_not_visible",
                                 "detail": f"no body answers to {target}; " + describe(bodies, view)})
                elif teams[view[target]] == teams[seat]:
                    rows.append({**base, "rule": "target_is_ally",
                                 "detail": f"identity {target} is teammate {view[target]} in disguise"})
                target_state = None if target in view else state(t, target)
                if target in view:
                    checks["target_dead", "checked"] += 1        # a visible body is alive by definition
                elif target_state is None:
                    checks["target_dead", "unchecked"] += 1
                else:
                    checks["target_dead", "checked"] += 1
                    if int(target_state["hp"]) <= 0:
                        rows.append({**base, "rule": "target_dead", "detail": f"seat {target} hp {int(target_state['hp'])}"})
        if int(line.mode) in HEART_MODES and int(line.heart) >= 0:
            if pd.notna(line.next_change) and int(line.next_change) < t + horizon:
                checks["heart_not_approached", "skipped_superseded"] += 1
                continue
            start, end = state(t + 1, seat), state(t + horizon, seat)
            if start is None or end is None or int(line.heart) not in hearts:
                checks["heart_not_approached", "unchecked"] += 1
                continue
            window = ep["states"]
            window = window[(window["seat"] == seat) & window["t"].between(t + 1, t + horizon)]
            if not bool(window["alive"].all()):
                checks["heart_not_approached", "skipped_died"] += 1
                continue
            hx, hz = hearts[int(line.heart)]
            d0 = ((start["x"] - hx) ** 2 + (start["z"] - hz) ** 2) ** 0.5
            d1 = ((end["x"] - hx) ** 2 + (end["z"] - hz) ** 2) ** 0.5
            if d0 <= HEART_ARRIVED:
                checks["heart_not_approached", "skipped_arrived"] += 1
                continue
            checks["heart_not_approached", "checked"] += 1
            if d0 - d1 < HEART_APPROACH_MIN_GAIN:
                rows.append({**base, "rule": "heart_not_approached",
                             "detail": f"distance {d0:.0f} -> {d1:.0f} over {horizon} ticks"})
    divergences = pd.DataFrame(rows, columns=DIVERGENCE_COLUMNS)
    if not checks:
        return divergences, pd.DataFrame(columns=CHECK_COLUMNS)
    check_frame = pd.DataFrame([{"episode_id": ep.episode_id, "rule": rule,
                                 **{status: checks[rule, status] for status in CHECK_STATUSES},
                                 "divergent": int((divergences["rule"] == rule).sum())}
                                for rule in AUDIT_RULES if any(checks[rule, s] for s in CHECK_STATUSES)],
                               columns=CHECK_COLUMNS)
    return divergences, check_frame


# ---------------------------------------------------------------- local recording with seat logs

class RecordError(RuntimeError):
    """One episode could not be recorded (player failure, engine exit, timeout). `code` names why."""

    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


def _seeds(text: str) -> list[int]:
    seeds: list[int] = []
    try:
        for part in text.split(","):
            lo, _, hi = part.partition("-")
            seeds.extend(range(int(lo), int(hi or lo) + 1))
    except ValueError:
        raise pw_cli.UsageError(f"--seeds {text!r} is not a list of integers and ranges (e.g. 1-4,7)") from None
    if not seeds or len(set(seeds)) != len(seeds):
        raise pw_cli.UsageError(f"--seeds {text!r} is empty or repeats a seed")
    return seeds


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def episode_name(a: Path, b: Path, seed: int, a_side: int) -> str:
    """The recording's directory and tape stem. It carries both files' content hashes, so an
    edited policy never reuses an old recording and a re-run with the same inputs does."""
    return f"{a.stem}-{_sha256(a)[:8]}_vs_{b.stem}-{_sha256(b)[:8]}_s{seed}_a{a_side}"


def _handoff(tag: str) -> tuple[Path, Path]:
    """The -d:coworld engine and host.py that build_tools.sh leaves in the release worktree."""
    tree = pw_release.release_tree(tag)
    engine, host = tree / "tmp" / "paintbot-coworld", tree / "coworld" / "paintbot" / "runtime" / "host.py"
    if not engine.is_file() or not host.is_file():
        raise pw_cli.EnvironmentMissing(f"missing {engine} or {host}", pw_release.build_command("pw_trace", tag))
    return engine, host


def recorded_meta(out: Path) -> dict | None:
    """The meta of a COMPLETE recording in `out` (tape and meta both present), else None.
    The meta is written last, so a crash mid-record leaves no meta."""
    meta, replay = out / f"{out.name}.meta.json", out / f"{out.name}.replay"
    if not (meta.is_file() and replay.is_file()):
        return None
    return json.loads(meta.read_text())


def _settings(a: Path, b: Path, seed: int, a_side: int, tag: str, glory: dict, ticks: int) -> dict:
    return {"seed": seed, "a_side": a_side, "engine_release": tag, "glory_config": glory, "max_ticks": ticks,
            "sha256": [_sha256(a), _sha256(b)]}


def _meta_settings(meta: dict) -> dict:
    return {"seed": meta.get("seed"), "a_side": meta.get("a_side"), "engine_release": meta.get("engine_release"),
            "glory_config": meta.get("glory_config"), "max_ticks": meta.get("max_ticks"),
            "sha256": [meta["policies"]["A"]["sha256"], meta["policies"]["B"]["sha256"]]}


def record_episode(a: Path, b: Path, seed: int, a_side: int, out: Path, *, tag: str = pe.DEFAULT_TAG,
                   glory: dict = LEAGUE_GLORY, ticks: int = 14400, port: int = 8390) -> Path:
    """Play one 16-seat teams match through the release's hosted handoff (coworld host.py +
    the -d:coworld engine that tools/build_tools.sh leaves in the worktree), so each seat's
    PRINT output lands in player-N.log. Lays the result out as a local episode that
    pw_episodes reads: out/NAME.replay + NAME.meta.json + player-N.log in one directory
    (NAME = out.name). Returns the replay path. `out` must not exist. Raises RecordError for
    a failed episode and pw_cli.EnvironmentMissing when the handoff engine is not built."""
    engine, host = _handoff(tag)
    out.mkdir(parents=True)
    policies = [a if seat % 2 == a_side else b for seat in range(16)]
    seats = [{"slot": i, "file_uri": p.resolve().as_uri(), "content_hash": "sha256:" + _sha256(p),
              "size_bytes": p.stat().st_size, "log_uri": (out / f"player-{i}.log").as_uri(),
              "artifact_uri": (out / f"player-{i}.zip").as_uri()} for i, p in enumerate(policies)]
    (out / "seats.json").write_text(json.dumps({"schema": "coworld-player-seats/1", "seats": seats,
                                                "player_status_uri": (out / "status.json").as_uri()}))
    config = {"players": [{"name": f"Player {i}"} for i in range(16)], "tokens": [str(i) for i in range(16)],
              "seed": seed, "max_ticks": ticks, "mode": "teams", "glory": glory}
    (out / "config.json").write_text(json.dumps(config))
    env = dict(os.environ, COGAME_CONFIG_URI=(out / "config.json").as_uri(),
               COGAME_PLAYER_SEATS_URI=(out / "seats.json").as_uri(),
               COGAME_RESULTS_URI=(out / "results.json").as_uri(),
               COGAME_SAVE_REPLAY_URI=(out / "replay.bin").as_uri(),
               COGAME_PLAYER_FAILURE_URI=(out / "failure.json").as_uri(), COGAME_PORT=str(port))
    with (out / "game.log").open("w") as log:
        child = subprocess.Popen([sys.executable, str(host), "--engine", str(engine)], env=env,
                                 stdout=log, stderr=log, start_new_session=True)
        try:
            deadline = time.monotonic() + 600
            while not (out / "results.json").exists():
                if (out / "failure.json").exists():
                    # A seat's file failed to stage or compile; the hosted handoff then never
                    # writes results. The seat log names the error.
                    raise RecordError(f"player failure: {(out / 'failure.json').read_text().strip()} "
                                      f"(see {out}/player-*.log)", "player_failure")
                if child.poll() is not None:
                    raise RecordError(f"engine exited early; see {out / 'game.log'}", "engine_exit")
                if time.monotonic() > deadline:
                    raise RecordError("episode exceeded ten minutes", "timeout")
                time.sleep(0.2)
            # finishCoworld closes the seat logs and writes status.json before results.json.
        finally:
            if child.poll() is None:
                os.killpg(child.pid, 15)
            child.wait(timeout=15)
    name = out.name
    replay = out / f"{name}.replay"
    (out / "replay.bin").rename(replay)
    # Renamed so pw_episodes treats the directory as a local episode (a results.json makes it
    # "hosted", which has no per-seat policy names); kept for reference.
    (out / "results.json").rename(out / "coworld_results.json")
    meta = {"source": "local_coworld", "seed": seed, "glory_config": glory, "engine_release": tag, "a_side": a_side,
            "max_ticks": ticks,
            "policies": {"A": {"path": str(a), "sha256": _sha256(a)}, "B": {"path": str(b), "sha256": _sha256(b)}},
            "seats": [{"position": i, "policy_name": p.name, "team": i % 2} for i, p in enumerate(policies)]}
    # Written last: its presence marks the recording complete (recorded_meta).
    (out / f"{name}.meta.json").write_text(json.dumps(meta, indent=1))
    return replay


# ---------------------------------------------------------------- CLI

TOOL = "pw_intent"
CLI = "uv run python paintbot_pw_lab/tools/pw_intent.py"
DEFAULT_RECORD_OUT = pw_cli.default_out(TOOL, "episodes")      # <lab>/analysis/pw_intent/episodes/
DEFAULT_AUDIT_OUT = pw_cli.default_out(TOOL, "audit")          # <lab>/analysis/pw_intent/audit/<key>/
SHOWN_RECORD_OUT = DEFAULT_RECORD_OUT.relative_to(pw_release.REPO)   # as --help prints it (repo-relative)
SHOWN_AUDIT_OUT = DEFAULT_AUDIT_OUT.relative_to(pw_release.REPO)


def _roots(texts: list[str]) -> list[Path]:
    roots = [Path(t) for t in texts]
    missing = [str(r) for r in roots if not r.exists()]
    if missing:
        raise pw_cli.UsageError(f"no such episode root: {', '.join(missing)}")
    return roots


def _load(roots: list[Path], tag: str, dense: bool, refresh: bool, report: pw_cli.Report):
    pw_release.require_built("pw_trace", tag)                     # exit 3 naming build_tools.sh
    options = pe.TraceOptions(state_every=1, vis_every=1) if dense else pe.TraceOptions()
    try:
        batch = pe.load_batch(roots, tag=tag, options=options, refresh=refresh)
    except pe.EpisodeError as error:                              # e.g. no episodes under the roots
        raise pw_cli.UsageError(str(error)) from None
    report.add_batch(batch)
    for path, code, message in batch.failures:
        print(f"FAILED {code}: {path}: {message}", file=sys.stderr)
    return batch


def cmd_record(args, report: pw_cli.Report):
    a, b = Path(args.a).resolve(), Path(args.b).resolve()
    for flag, path in (("A", a), ("B", b)):
        if not path.is_file():
            raise pw_cli.UsageError(f"policy {flag} {path} is not a file")
    seeds = _seeds(args.seeds)
    try:
        sides = sorted({int(s) for s in args.sides.split(",")})
    except ValueError:
        sides = [-1]
    if not sides or any(s not in (0, 1) for s in sides):
        raise pw_cli.UsageError(f"--sides {args.sides!r}: each side must be 0 or 1", ["0", "1", "0,1"])
    try:
        glory = json.loads(args.glory)
    except json.JSONDecodeError as error:
        raise pw_cli.UsageError(f"--glory is not JSON: {error}") from None
    if not isinstance(glory, dict):
        raise pw_cli.UsageError("--glory must be a JSON object, e.g. '{\"behind_lives\":5,\"behind_cogs\":10}'")
    if args.ticks <= 0:
        raise pw_cli.UsageError("--ticks must be positive")
    _handoff(args.tag)                                             # exit 3 before any work
    out = Path(args.out) if args.out else DEFAULT_RECORD_OUT
    plan = []
    for seed in seeds:
        for a_side in sides:
            directory = out / episode_name(a, b, seed, a_side)
            meta = recorded_meta(directory)
            wanted = _settings(a, b, seed, a_side, args.tag, glory, args.ticks)
            if meta is not None and _meta_settings(meta) != wanted and not args.force:
                raise pw_cli.UsageError(f"{directory} holds a recording with other settings "
                                        f"({_meta_settings(meta)}); pass --force to re-record it or pick another --out")
            plan.append((seed, a_side, directory, meta is not None and not args.force))
    episodes = []
    report.counts.update(recorded=0, cached=0)
    for seed, a_side, directory, cached in plan:
        row = {"name": directory.name, "dir": str(directory), "seed": seed, "a_side": a_side}
        if not cached:
            shutil.rmtree(directory, ignore_errors=True)           # an incomplete or --force'd recording
            try:
                record_episode(a, b, seed, a_side, directory, tag=args.tag, glory=glory, ticks=args.ticks,
                               port=args.port)
            except RecordError as error:
                report.fail(directory.name, error.code, str(error))
                print(f"FAILED {error.code}: seed {seed} a_side {a_side}: {error}", file=sys.stderr)
                episodes.append({**row, "status": "failed"})
                continue
        status = json.loads((directory / "status.json").read_text())
        results = json.loads((directory / "coworld_results.json").read_text())
        row.update(status="cached" if cached else "recorded", replay=str(directory / f"{directory.name}.replay"),
                   ticks=results["ticks"], outcome=results["outcome"],
                   disabled_seats=[p["slot"] for p in status["players"] if p["exit_code"] != 0])
        report.counts["cached" if cached else "recorded"] += 1
        report.counts["processed"] += 1
        report.output(directory)
        episodes.append(row)
        print(f"{row['status']}: seed {seed} a_side {a_side}: ticks {row['ticks']} outcome {row['outcome']} "
              f"disabled seats {row['disabled_seats'] or 'none'} -> {row['replay']}", file=sys.stderr)
    report.counts["failed"] = len(report.failures)
    report.suggest(f"{CLI} show {out} --json")
    report.suggest(f"{CLI} audit {out} --json")
    return {"out": str(out), "episodes": episodes}


def cmd_show(args, report: pw_cli.Report):
    batch = _load(_roots(args.roots), args.tag, dense=False, refresh=args.refresh, report=report)
    pd.set_option("display.width", 200)
    episodes = []
    for ep in batch.episodes:
        summary = intent_summary(ep)
        print(f"== {ep.episode_id}", file=sys.stderr)
        if summary.empty:
            report.counts["excluded"] += 1
            report.counts["no_seat_logs"] = report.counts.get("no_seat_logs", 0) + 1
            print("   no seat logs (policy_log has no log_present rows)", file=sys.stderr)
            episodes.append({"episode_id": ep.episode_id, "seat_logs": False, "seats": []})
            continue
        columns = ["seat", "intent_lines", "bad_lines", "vm_errors", "last_t", "heart_switches"] + \
                  [c for c in summary.columns if c.startswith("mode_share_") and summary[c].fillna(0).any()]
        print(summary[columns].round(2).to_string(index=False), file=sys.stderr)
        episodes.append({"episode_id": ep.episode_id, "seat_logs": True, "seats": pw_cli.records(summary)})
    report.suggest(f"{CLI} audit {' '.join(args.roots)} --json")
    return {"episodes": episodes}


def audit_out_dir(roots: list[Path], horizon: int, sparse: bool) -> Path:
    """Default audit directory: a pure function of the inputs, so a re-run overwrites it."""
    resolved = sorted(str(r.resolve()) for r in roots)
    key = hashlib.sha256(json.dumps([resolved, horizon, sparse]).encode()).hexdigest()[:8]
    return DEFAULT_AUDIT_OUT / f"{Path(resolved[0]).name}-{key}"


def cmd_audit(args, report: pw_cli.Report):
    if args.horizon <= 0:
        raise pw_cli.UsageError("--horizon must be a positive number of ticks")
    roots = _roots(args.roots)
    batch = _load(roots, args.tag, dense=not args.sparse, refresh=args.refresh, report=report)
    all_divergences, all_checks = [], []
    for ep in batch.episodes:
        divergences, checks = audit(ep, horizon=args.horizon)
        print(f"== {ep.episode_id}", file=sys.stderr)
        if checks.empty:
            report.counts["excluded"] += 1
            report.counts["no_intent_lines"] = report.counts.get("no_intent_lines", 0) + 1
            print("   no intent lines", file=sys.stderr)
            continue
        all_divergences.append(divergences)
        all_checks.append(checks)
        print(checks.drop(columns="episode_id").to_string(index=False), file=sys.stderr)
        for rule, group in divergences.groupby("rule"):
            print(f"   {rule}: first {min(3, len(group))} of {len(group)}", file=sys.stderr)
            for row in group.head(3).itertuples(index=False):
                print(f"     seat {row.seat} t={row.t} mode={row.mode} h={row.heart} s={row.target}: {row.detail}",
                      file=sys.stderr)
    out = Path(args.out) if args.out else audit_out_dir(roots, args.horizon, args.sparse)
    out.mkdir(parents=True, exist_ok=True)
    divergences = pd.concat(all_divergences) if all_divergences else pd.DataFrame(columns=DIVERGENCE_COLUMNS)
    checks = pd.concat(all_checks) if all_checks else pd.DataFrame(columns=CHECK_COLUMNS)
    divergences.to_csv(report.output(out / "divergences.csv"), index=False)
    checks.to_csv(report.output(out / "checks.csv"), index=False)
    totals = {}
    for rule in AUDIT_RULES:
        mine = checks[checks["rule"] == rule]
        totals[rule] = {status: int(mine[status].sum()) for status in (*CHECK_STATUSES, "divergent")}
    consistency = sum(totals[rule]["divergent"] for rule in CONSISTENCY_RULES)
    print("totals: " + ", ".join(f"{r} {t['divergent']}/{t['checked']}" for r, t in totals.items()), file=sys.stderr)
    print(f"wrote {out}/divergences.csv and checks.csv", file=sys.stderr)
    return {"out_dir": str(out), "totals": totals, "consistency_divergences": consistency,
            "episodes_audited": len(all_checks)}


def build_parser() -> pw_cli.ArgumentParser:
    parser = pw_cli.ArgumentParser(
        TOOL, __doc__, json_flag=False,
        examples=[f"{CLI} record MINE.bas paintbot_pw_lab/reference/base.bas --seeds 1-4 --json",
                  f"{CLI} show {SHOWN_RECORD_OUT} --json",
                  f"{CLI} audit {SHOWN_RECORD_OUT} --json",
                  f"{CLI} record --help   (each subcommand documents its flags)"])
    sub = parser.add_subparsers(dest="command", required=True, metavar="{record,show,audit}")
    rec = sub.add_parser(
        "record", help="play local 16-seat episodes that keep seat logs (hosted handoff engine)",
        description="Play A against B locally through the release's hosted handoff, keeping each seat's "
                    "PRINT log. One directory per (seed, side); a complete recording with the same inputs is "
                    "reused, not replayed.",
        examples=[f"{CLI} record A.bas B.bas --seeds 1-4,9 --sides 0,1 --json",
                  f"{CLI} record A.bas B.bas --seeds 3 --sides 1 --out /tmp/pwi --ticks 3000",
                  f"{CLI} record A.bas B.bas --glory '{{\"behind_lives\":5,\"behind_cogs\":10}}' --tag coworld-v0.3.79 "
                  "--port 8391 --force"])
    rec.add_argument("a", help="policy A (.bas), e.g. MINE.bas")
    rec.add_argument("b", help="policy B (.bas), e.g. paintbot_pw_lab/reference/base.bas")
    rec.add_argument("--seeds", default="1", help="seeds: integers and ranges, e.g. 1-4,7 (default 1)")
    rec.add_argument("--sides", default="0,1", help="A's team(s): 0 = even seats, 1 = odd seats, e.g. 0 or 0,1 "
                                                    "(default 0,1: both)")
    rec.add_argument("--out", help=f"parent directory, one subdirectory per episode (default {SHOWN_RECORD_OUT}/), "
                                   "e.g. --out /tmp/pwi")
    rec.add_argument("--tag", default=pe.DEFAULT_TAG, help=f"engine release, e.g. --tag {pe.DEFAULT_TAG} (default)")
    rec.add_argument("--glory", default=json.dumps(LEAGUE_GLORY),
                     help=f"glory config JSON (default the league's {json.dumps(LEAGUE_GLORY)})")
    rec.add_argument("--ticks", type=int, default=14400, help="max ticks, e.g. --ticks 3000 (default 14400)")
    rec.add_argument("--port", type=int, default=8390, help="local handoff port, e.g. --port 8391 (default 8390)")
    rec.add_argument("--force", action="store_true", help="re-record episodes that already exist")
    rec.set_defaults(func=cmd_record)
    show = sub.add_parser(
        "show", help="intent lines per seat: counts, malformed lines, VM errors, mode shares",
        description="Summarize the PWI lines of every episode under the roots (sampled trace, cached).",
        examples=[f"{CLI} show {SHOWN_RECORD_OUT} --json",
                  f"{CLI} show EPISODE_DIR other.replay --tag {pe.DEFAULT_TAG} --refresh"])
    show.add_argument("roots", nargs="+", help="episode directories, .replay files, or parents of them")
    show.add_argument("--tag", default=pe.DEFAULT_TAG, help=f"pw_trace release (default {pe.DEFAULT_TAG})")
    show.add_argument("--refresh", action="store_true", help="rebuild the trace caches")
    show.set_defaults(func=cmd_show)
    aud = sub.add_parser(
        "audit", help="join intent lines to replay truth and list divergences",
        description="Belief/intent audit (dense trace: state_every=1, vis_every=1). Writes divergences.csv "
                    "and checks.csv. consistency_divergences in the result should be 0; anything else is a "
                    "policy or telemetry bug (docs/tools/pw_intent.md, Audit rules).",
        examples=[f"{CLI} audit {SHOWN_RECORD_OUT} --json",
                  f"{CLI} audit EPISODE_DIR --horizon 96 --out /tmp/pwi_audit",
                  f"{CLI} audit EPISODE_DIR --sparse --tag {pe.DEFAULT_TAG} --refresh"])
    aud.add_argument("roots", nargs="+", help="episode directories, .replay files, or parents of them")
    aud.add_argument("--horizon", type=int, default=HEART_APPROACH_TICKS,
                     help=f"heart_not_approached window in ticks, e.g. --horizon 96 (default {HEART_APPROACH_TICKS})")
    aud.add_argument("--out", help=f"output directory (default {SHOWN_AUDIT_OUT}/<first root name>-<input hash>/)")
    aud.add_argument("--sparse", action="store_true", help="use the default sampled trace (most lines unchecked)")
    aud.add_argument("--tag", default=pe.DEFAULT_TAG, help=f"pw_trace release (default {pe.DEFAULT_TAG})")
    aud.add_argument("--refresh", action="store_true", help="rebuild the trace caches")
    aud.set_defaults(func=cmd_audit)
    return parser


def main(argv: list[str] | None = None) -> int:
    return pw_cli.run(build_parser(), lambda args, report: args.func(args, report), argv)


if __name__ == "__main__":
    raise SystemExit(main())
