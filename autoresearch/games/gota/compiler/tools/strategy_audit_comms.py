# Vendored from personal_labs_paintbot_pw@6b0bffa5085ffc4bf94ecaa8baee463e55599c84: paintbot_pw_lab/tools/strategy_audit_comms.py
"""Comms-v1 COM checks for strategy_audit: packet truth, payload claims and decode delivery.

Only exact, reviewed check texts are evaluated, and only for the standard component of each
wire type in a build that declares comms version 1. Everything else stays unmeasurable.
A packet's checksum is not authentication: these checks compare logged packets with the
hash-verified replay, they do not trust the claimed sender.
"""
from __future__ import annotations

import json

import strategy_comms as cm
from pw_intent import observed_view

WIRES = {0: "COM.enemy_sighting", 1: "COM.focus_call", 2: "COM.grenade_warning", 3: "COM.under_fire",
         4: "COM.glory_seen", 5: "COM.pickup_taken", 6: "COM.pickup_ready", 7: "COM.disguise_alert",
         8: "COM.disguise_friend"}
ACTED = ("Acted properly", "each sent packet decodes and its fields match the sender's replay state at the send tick.")
DECODED = ("Result", "teammates alive within 12.8 m of the sender decode it on the next tick, within decode capacity.")
EXTRA = {
    ("COM.enemy_sighting", "True", "the reported label was a visible body at the reported cell and an enemy in the "
     "replay. A corroborated misclassification fails this by design."): "enemy_true",
    ("COM.disguise_alert", "True", "the reported body was a disguised enemy in the replay."): "alert_true",
    ("COM.grenade_warning", "True", "a grenade from the sender lands within 150 cm of the reported cell."): "grenade_true",
    ("COM.pickup_taken", "True", "the reported station was taken by the sender within event_window ticks before the "
     "send in the replay."): "taken",
    ("COM.pickup_ready", "True", "the reported station was ready at the send tick in the replay."): "unmeasurable",
    ("COM.disguise_friend", "Result", "a friend record never protects a body that is an enemy in the replay."):
        "unmeasurable",
    ("COM.focus_call", "Result", "called targets die sooner than uncalled targets."): "unmeasurable",
}
UNMEASURABLE = {  # why a check cannot be computed from the logs and the trace
    ("COM.glory_seen", ACTED[1]): "glory_heart_state_not_traced",
    ("COM.under_fire", ACTED[1]): "sound_cues_not_traced",
    ("COM.pickup_ready", ACTED[1]): "pickup_readiness_and_sight_not_traced",
    ("COM.disguise_alert", ACTED[1]): "evidence_source_not_logged",
    ("COM.pickup_ready", "the reported station was ready at the send tick in the replay."):
        "pickup_readiness_not_traced",
    ("COM.disguise_friend", "a friend record never protects a body that is an enemy in the replay."):
        "receiver_friend_records_not_logged",
    ("COM.focus_call", "called targets die sooner than uncalled targets."): "causal_outcome_not_identified",
}
MOTOR_SHA256 = '0dea990b7e612224bdeb11fcc26794e6edbf0781b5f32621a155f22514c86f18'
GRENADE_HIT_CM = 150     # the source check's landing tolerance
_CACHE: dict = {}


# ---------------------------------------------------------------- binding and geometry

def wire_of(mapping: dict, component: str) -> int | None:
    """The wire type when this component is the standard codec component of the build."""
    comms = mapping.get("comms") or {}
    wire = next((w for w, cid in WIRES.items() if cid == component), None)
    if wire is None or comms.get("version") != 1 or comms.get("messages", {}).get(str(wire)) != component:
        return None
    return wire


def evaluator(component: str, level: str, text: str) -> str | None:
    if (level, text) == ACTED:
        return "acted"
    if (level, text) == DECODED:
        return "decoded"
    return EXTRA.get((component, level, text))


def cell_of(x: int, y: int, bounds) -> int:
    """comms-v1 cell: 256 x 256 grid over the map bounds (the source formula)."""
    min_x, min_y, max_x, max_y = bounds
    gx = min(255, max(0, (x - min_x) * 256 // (max_x - min_x + 1)))
    gy = min(255, max(0, (y - min_y) * 256 // (max_y - min_y + 1)))
    return gx + 256 * gy


def cell_center(cell: int, bounds) -> tuple[int, int]:
    min_x, min_y, max_x, max_y = bounds
    w, h = max_x - min_x + 1, max_y - min_y + 1
    return min_x + ((cell % 256) * w + w // 2) // 256, min_y + ((cell // 256) * h + h // 2) // 256


def tdiv(a: int, b: int) -> int:
    """BASIC integer division: truncates toward zero."""
    q = abs(a) // abs(b)
    return q if (a < 0) == (b < 0) else -q


def physical_shout(ctx: dict, msg, t: int, seat: int, keys) -> dict | None:
    """The hash-verified replay shout carrying exactly this packet (recorded at t + 1)."""
    text = cm.encode(msg, t, keys)
    return next((r for r in ctx["shouts"].get(t + 1, []) if int(r["seat"]) == seat and r["text"] == text), None)


def motor_isqrt(n: int) -> int:
    """SK.motor's integer square root (Newton from 23170, at most 24 steps)."""
    if n <= 0:
        return 0
    root = 23170
    guess = tdiv(root + tdiv(n, root), 2)
    steps = 0
    while guess < root and steps < 24:
        root = guess
        guess = tdiv(root + tdiv(n, root), 2)
        steps += 1
    return root


def _context(ep) -> dict:
    # One episode at a time. Holding the object (not its id) prevents a reused id from serving stale tables.
    if _CACHE.get("ep") is not ep:
        _CACHE.clear()
        bounds = ep.meta.get("bounds")
        visibility = {(int(r.t), int(r.seat)): {int(b) for b in r.sees} for r in ep["visibility"].itertuples()}
        shouts: dict[int, list[dict]] = {}
        for row in ep["shouts"].to_dict("records"):
            shouts.setdefault(int(row["t"]), []).append(row)
        for rows in shouts.values():
            rows.sort(key=lambda r: int(r["seat"]))  # stable: each speaker keeps its own order
        events = ep["events"].to_dict("records")
        _CACHE["ep"] = ep
        _CACHE["context"] = {"bounds": list(bounds) if bounds and len(bounds) == 4 else None,
                       "visibility": visibility, "shouts": shouts,
                       "pickups": ep["pickups"].to_dict("records"),
                       "throws": [r for r in events if r["kind"] == "grenade_throw"],
                       "blasts": [r for r in events if r["kind"] == "grenade_blast"]}  # self_destruct is separate
    return _CACHE["context"]


def view_of(ctx: dict, states: dict, seat: int, t: int, rules: int):
    """The sender's observed identities at t (pw_intent.observed_view), or None on a replay gap."""
    bodies = ctx["visibility"].get((t, seat))
    if bodies is None or any((t, b) not in states for b in bodies | {seat}):
        return None, None
    positions = {b: (int(states[t, b]["x"]), int(states[t, b]["z"])) for b in bodies | {seat}}
    disguised = {b: bool(states[t, b]["disguised"]) for b in bodies | {seat}}
    return observed_view(seat, bodies, disguised, positions, 16, rules >= 27), positions


# ---------------------------------------------------------------- payload evaluators: (status, reason, detail)

def _located(ctx, states, rules, seat, t, label, cell, hp=None):
    """Shared truth of a label, hit points and cell in the sender's view at t."""
    if t < 0:
        return "fail", "negative_sighting_tick", {}, None
    view, positions = view_of(ctx, states, seat, t, rules)
    if view is None:
        return "unmeasurable", "replay_visibility_gap", {}, None
    body = view.get(label)
    if body is None:
        return "fail", "label_not_visible", {"label": label}, None
    detail = {"label": label, "body": body}
    if hp is not None and min(15, max(0, int(states[t, body]["hp"]))) != hp:
        return "fail", "hp_mismatch", {**detail, "hp": hp, "replay_hp": int(states[t, body]["hp"])}, body
    if ctx["bounds"] is None:
        return "unmeasurable", "map_bounds_missing", detail, body
    actual = cell_of(*positions[body], ctx["bounds"])
    if actual != cell:
        return "fail", "cell_mismatch", {**detail, "cell": cell, "replay_cell": actual}, body
    return "pass", None, detail, body


def acted(wire, msg, t, seat, ctx, states, rules, comp):
    if wire == 0:
        heading = msg.fields_a // 256
        status, reason, detail, _ = _located(ctx, states, rules, seat, t - msg.fields_b, msg.fields_a % 16, msg.cell,
                                             (msg.fields_a // 16) % 16)
        if status == "pass" and heading != 8:
            return "unmeasurable", "heading_not_verified", {**detail, "heading": heading}
        return status, reason, detail
    if wire == 1:
        status, reason, detail, _ = _located(ctx, states, rules, seat, t, msg.fields_a % 16, msg.cell,
                                             msg.fields_a // 16)
        return status, reason, detail
    if wire == 8:
        label = seat ^ 1
        if msg.fields_a != label:
            return "fail", "label_mismatch", {"label": msg.fields_a, "expected": label}
        if (t, seat) not in states:
            return "unmeasurable", "replay_state_gap", {}
        if not bool(states[t, seat]["disguised"]):
            return "fail", "sender_not_disguised", {"label": label}
        return "pass", None, {"label": label}
    if wire == 2:
        return _grenade_acted(msg, t, seat, ctx, states)
    if wire == 5:
        return _taken(msg, t, seat, ctx, comp)
    return "unmeasurable", "no_evaluator_for_wire", {}


def _grenade_acted(msg, t, seat, ctx, states):
    now, after = states.get((t, seat)), states.get((t + 1, seat))
    if now is None or after is None or any(after.get(k) is None for k in ("cmd_charge", "cmd_aim_x", "cmd_aim_z")):
        return "unmeasurable", "command_not_traced", {}
    if int(after["cmd_charge"]) != 1:
        return "fail", "no_charge_command", {}
    if ctx["bounds"] is None:
        return "unmeasurable", "map_bounds_missing", {}
    tx, ty = int(after["cmd_aim_x"]), int(after["cmd_aim_z"])
    actual = cell_of(tx, ty, ctx["bounds"])
    if actual != msg.cell:
        return "fail", "cell_mismatch", {"cell": msg.cell, "replay_cell": actual}
    d2 = (tx - int(now["x"])) ** 2 + (ty - int(now["z"])) ** 2
    need = max(1, tdiv((motor_isqrt(d2) - 150) * 24, 1130) + 1)
    release = min(24, max(0, need - int(now["charge"])))
    if release != msg.fields_a:
        return "fail", "release_mismatch", {"release_in": msg.fields_a, "replay_release_in": release}
    return "pass", None, {"release_in": release}


def _taken(msg, t, seat, ctx, comp):
    window = comp.param("event_window").value if comp is not None and comp.param("event_window") else None
    if window is None:
        return "unmeasurable", "event_window_param_missing", {}
    station = [r for r in ctx["pickups"] if int(r["pickup"]) == msg.fields_a and t - window <= int(r["t"]) <= t]
    mine = [r for r in station if r["seat"] is not None and int(r["seat"]) == seat]
    if not mine:
        return ("unmeasurable", "ambiguous_or_unattributed_take", {}) if station else ("fail", "no_take_event", {})
    row = max(mine, key=lambda r: int(r["t"]))
    if bool(row["ambiguous"]):
        return "unmeasurable", "ambiguous_take", {"take_tick": int(row["t"])}
    delay = 120 if row["pickup_kind"] == "grenade" else 720  # mechanics.nim pickupEquipment
    left = min(720, max(0, delay - (t - int(row["t"]))))
    if left != msg.fields_b:
        return "fail", "ready_in_mismatch", {"ready_in": msg.fields_b, "replay_ready_in": left}
    if ctx["bounds"] is None:
        return "unmeasurable", "map_bounds_missing", {}
    actual = cell_of(int(row["x"]), int(row["z"]), ctx["bounds"])
    if actual != msg.cell:
        return "fail", "cell_mismatch", {"cell": msg.cell, "replay_cell": actual}
    return "pass", None, {"take_tick": int(row["t"])}


def enemy_true(msg, t, seat, ctx, states, rules):
    status, reason, detail, body = _located(ctx, states, rules, seat, t - msg.fields_b, msg.fields_a % 16, msg.cell)
    if status != "pass":
        return status, reason, detail
    if body % 2 == seat % 2:
        return "fail", "friendly_body", detail
    return "pass", None, detail


def alert_true(msg, t, seat, ctx, states, rules):
    view, positions = view_of(ctx, states, seat, t, rules)
    if view is None:
        return "unmeasurable", "replay_visibility_gap", {}
    body = view.get(msg.fields_a)
    if body is None or ctx["bounds"] is None or cell_of(*positions[body], ctx["bounds"]) != msg.cell:
        return "unmeasurable", "reported_point_is_not_a_visible_body", {"label": msg.fields_a}
    detail = {"label": msg.fields_a, "body": body}
    if body % 2 == seat % 2 or not bool(states[t, body]["disguised"]):
        return "fail", "not_a_disguised_enemy", detail
    return "pass", None, detail


def _value(v):
    """A traced number, or None when missing (None or NaN)."""
    return None if v is None or v != v else int(v)


def grenade_true(msg, t, seat, ctx, states, sends, end_tick):
    """Follow the warned charge to its end, with no tick deadline (mechanics.nim at 0.3.115).

    A charge grows only on steps with the charge order. The first step without it throws at the current
    charge (state charge 0 and the grenade_throw event share that post-step tick). A mister or radar pickup,
    or death, zeroes it without a throw. The motor can hold longer than fieldsA predicts, because need is
    recomputed from the moving target, so the throw is associated by lifecycle, not by deadline. Before the
    first positive charge the order itself is followed: a disarmed cog holds it without charging, and the
    lifecycle fails only when the order stops (or on death or a lost grenade) before any charge."""
    if ctx["bounds"] is None:
        return "unmeasurable", "map_bounds_missing", {}
    first = states.get((t, seat))
    if first is None:
        return "unmeasurable", "replay_state_gap", {"tick": t}
    # A re-warning mid-charge starts already charged: its lifecycle can end (with a throw) on the next step.
    end, started, outcome = None, int(first["charge"]) > 0, None
    for k in range(t + 1, end_tick + 1):  # the trace keeps the final state; a throw on the last step counts
        row = states.get((k, seat))
        if row is None:
            return "unmeasurable", "replay_state_gap", {"tick": k}
        if int(row["hp"]) <= 0:
            end = k
            break
        if not started:
            # Before the first positive charge: a disarmed cog (mister or radar) holds the order without
            # charging (mechanics.nim: `if cmd.chargeGrenade and not w.disarmed(i)`).
            if int(row["charge"]) > 0:
                started = True
                continue
            grenade, command = _value(row.get("grenade")), _value(row.get("cmd_charge"))
            if grenade == 0:
                end, outcome = k, ("fail", "grenade_lost_before_start", {"end_tick": k})
                break
            if command is None:
                return "unmeasurable", "command_not_traced", {"tick": k}
            if command == 0:
                end, outcome = k, ("fail", "charge_never_started", {"end_tick": k})
                break
            continue
        if int(row["charge"]) == 0:
            end = k
            break
    if end is None:
        return "unmeasurable", "after_episode_end", {}
    if any(t < other < end for other in sends):
        return "unmeasurable", "superseded_by_newer_warning", {}
    if outcome is not None:
        return outcome
    throw = next((r for r in ctx["throws"] if r["seat"] is not None and int(r["seat"]) == seat
                  and t < int(r["t"]) <= end), None)
    if throw is None:
        dead = int(states[end, seat]["hp"]) <= 0
        return "fail", "cancelled_by_death" if dead else "charge_dropped_without_throw", {"end_tick": end}
    data = _data(throw)
    to, lands_at = data.get("to"), data.get("lands_at")
    # The release decision is the tick before the throw's post-step tick; fieldsA predicted t + fieldsA.
    detail = {"throw_tick": int(throw["t"]), "lands_at": lands_at,
              "release_estimate_error": (int(throw["t"]) - 1) - (t + msg.fields_a)}
    if to is None or lands_at is None:
        return "unmeasurable", "landing_not_traced", detail
    # Only an observed landing counts: once the match is decided, stepEquipment returns before airborne
    # grenades explode (mechanics.nim), so a projected landing after the last state may never happen.
    if int(lands_at) > end_tick:
        return "unmeasurable", "landing_after_episode_end", detail
    candidates = [b for b in ctx["blasts"] if b["seat"] is not None and int(b["seat"]) == seat
                  and int(b["t"]) == int(lands_at)]
    matches = [b for b in candidates if list(_data(b).get("pos") or []) == list(to)]
    if not candidates:
        return "unmeasurable", "landing_not_observed", detail
    if len(matches) != 1:  # never guess between same-owner blasts, or accept a blast away from the target
        return "unmeasurable", "ambiguous_landing", {**detail, "blasts": len(candidates)}
    px, py = _data(matches[0])["pos"]
    cx, cy = cell_center(msg.cell, ctx["bounds"])
    distance2 = (int(px) - cx) ** 2 + (int(py) - cy) ** 2
    detail["distance_cm"] = round(distance2 ** 0.5)
    return ("pass", None, detail) if distance2 <= GRENADE_HIT_CM ** 2 else ("fail", "landed_elsewhere", detail)


def _data(event: dict) -> dict:
    return json.loads(event["data"]) if isinstance(event["data"], str) else (event["data"] or {})


def decoded(msg, t, seat, ctx, states, seat_logs, keys, capacity, end_tick):
    """Every eligible same-build teammate receiver logged exactly this packet on tick t + 1."""
    delivery = t + 1
    if delivery >= end_tick:
        return "not_exercised", "delivery_after_episode_end", {}
    rows = ctx["shouts"].get(delivery, [])
    row = physical_shout(ctx, msg, t, seat, keys)
    if row is None:
        return "fail", "shout_missing", {}
    a, b = cm.payloads(msg, t, keys)
    counts = {"eligible": 0, "decoded": 0, "missed": 0, "dead": 0, "capacity_excluded": 0, "unknown": 0}
    for receiver in sorted(int(r) for r in row["heard_by"]):
        if receiver % 2 != seat % 2 or receiver == seat or receiver not in seat_logs:
            continue
        events, issues = seat_logs[receiver]
        if issues:
            counts["unknown"] += 1
            continue
        state = states.get((delivery, receiver))
        if state is None or int(state["hp"]) <= 0:
            counts["dead"] += 1
            continue
        attempts, uncertain = 0, 0
        for other in (r for r in rows if receiver in r["heard_by"]):
            if other is row:
                break
            counted = cm.replay_shape(other)
            uncertain += counted is None
            attempts += bool(counted)
        if attempts < capacity <= attempts + uncertain:  # the unknown texts decide capacity
            counts["unknown"] += 1
            continue
        if attempts >= capacity:
            counts["capacity_excluded"] += 1
            continue
        counts["eligible"] += 1
        found = any(e["kind"] == "PWC" and e["s"] == 2 and e["t"] == delivery and e["w"] == seat
                    and list(e["d"]) == [a, b] for e in events)
        counts["decoded" if found else "missed"] += 1
    if counts["unknown"]:
        return "unmeasurable", "receiver_log_invalid", counts
    if not counts["eligible"]:
        return "not_exercised", "no_eligible_receiver", counts
    return ("pass", None, counts) if not counts["missed"] else ("fail", "receiver_missed_packet", counts)


# ---------------------------------------------------------------- entry point

def audit_seat(ep, seat, states, events, issues, strategy, mapping, evidence, seat_logs):
    """Add evidence rows for every codec COM check, from this seat's sends."""
    keys = tuple((mapping.get("comms") or {}).get("keys") or cm.DEFAULT_KEYS)
    capacity = ((mapping.get("comms") or {}).get("batch") or {}).get("receives", cm.MAX_DECODE)
    rules = int(ep.meta.get("rules", 0))
    end_tick = int(ep.summary["ticks"])
    ctx = _context(ep)
    for key, check in evidence.checks.items():
        component = check["component"]
        if component not in WIRES.values():
            continue

        def add(t, status, reason=None, **detail):
            evidence.add(key, ep.episode_id, seat, t, status, reason, **detail)

        wire = wire_of(mapping, component)
        kind = evaluator(component, check["level"], check["text"])
        if wire is None or kind is None:
            add(None, "unmeasurable", "no_evaluator_for_check_text")
            continue
        if issues:
            add(None, "unmeasurable", issues[0]["reason"], issues=issues)
            continue
        code = mapping["codes"]["message"][component]
        sends, bad = [], []
        for event in events:
            if event["kind"] != "PWC" or event["s"] != 1 or event["m"] != code:
                continue
            try:
                message = cm.unpack(*event["d"], event["t"], keys)
            except ValueError:
                bad.append(event["t"])
                continue
            sends.append((event["t"], message, event["w"]))
        for t in bad:
            add(t, "fail", "packet_does_not_decode")
        if not sends and not bad:
            add(None, "not_exercised", "no_sends")
            continue
        comp = strategy.components.get(component) if strategy is not None else None
        reason_text = UNMEASURABLE.get((component, check["text"]))
        for t, message, logged in sends:
            if message.kind != wire or message.sender != seat or logged != seat:
                add(t, "fail", "wrong_wire_or_sender", kind=message.kind, sender=message.sender, logged=logged)
                continue
            if kind != "decoded" and physical_shout(ctx, message, t, seat, keys) is None:
                status, reason, detail = (("unmeasurable", "send_after_episode_end", {}) if t + 1 > end_tick
                                          else ("fail", "shout_not_in_replay", {}))
            elif reason_text:
                status, reason, detail = "unmeasurable", reason_text, {}
            elif kind == "acted" and wire == 2 and mapping["components"].get("SK.motor", {}).get("unit_sha256") != MOTOR_SHA256:
                status, reason, detail = "unmeasurable", "grenade_motor_model_mismatch", {}
            elif kind == "acted":
                status, reason, detail = acted(wire, message, t, seat, ctx, states, rules, comp)
            elif kind == "decoded":
                status, reason, detail = decoded(message, t, seat, ctx, states, seat_logs, keys, capacity, end_tick)
            elif kind == "enemy_true":
                status, reason, detail = enemy_true(message, t, seat, ctx, states, rules)
            elif kind == "alert_true":
                status, reason, detail = alert_true(message, t, seat, ctx, states, rules)
            elif kind == "grenade_true":
                times = [s for s, _, _ in sends]
                status, reason, detail = grenade_true(message, t, seat, ctx, states, times, end_tick)
            elif kind == "taken":
                status, reason, detail = _taken(message, t, seat, ctx, comp)
            else:
                status, reason, detail = "unmeasurable", "no_evaluator_for_check_text", {}
            add(t, status, reason, **detail)
