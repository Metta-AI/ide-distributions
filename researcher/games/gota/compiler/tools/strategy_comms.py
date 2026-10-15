# Vendored from personal_labs_paintbot_pw@6b0bffa5085ffc4bf94ecaa8baee463e55599c84: paintbot_pw_lab/tools/strategy_comms.py
"""Comms-v1 wire arithmetic and lossless communication telemetry.

The BASIC runtime implements this exact protocol; this module is the independent
reader used by tests and replay audits. Scrambling is not authentication.
"""
from __future__ import annotations

from dataclasses import dataclass

MODULUS = 1_000_000_000
DEFAULT_KEYS = (7919, 17431, 23117, 1907, 31, 73)
PRIORITY = (2, 8, 7, 1, 4, 0, 3, 5, 6)
MAX_DECODE = 8
HP_BASE = 16


@dataclass(frozen=True)
class Message:
    kind: int
    sender: int
    fields_a: int
    fields_b: int
    cell: int


def validate(message: Message) -> None:
    """Validate the catalogue, including reserved values, before arithmetic."""
    m = message
    if not 0 <= m.sender < 16 or not 0 <= m.cell < 65536:
        raise ValueError("invalid sender or cell")
    if not 0 <= m.kind <= 8 or m.fields_a < 0 or m.fields_b < 0:
        raise ValueError("invalid message type or fields")
    limits = ((2303, 255), (255, 0), (24, 0), (143, 3), (720, 0),
              (255, 720), (4095, 0), (15, 255), (15, 0))
    a, b = limits[m.kind]
    if m.fields_a > a or m.fields_b > b:
        raise ValueError("payload exceeds catalogue bounds")
    if m.kind in (3, 8) and m.cell != 0:
        raise ValueError("message uses the speaker position")


def mask(tick: int, block: int, keys=DEFAULT_KEYS) -> int:
    h1 = ((tick % 30011) * keys[0] + keys[1] + block * keys[2]) % 30011
    h2 = (h1 * h1 + keys[3]) % 32749
    return h1 * 32768 + h2


def payloads(message: Message, tick: int, keys=DEFAULT_KEYS) -> tuple[int, int]:
    validate(message)
    if tick < 0:
        raise ValueError("negative send tick")
    core = message.kind + 16 * (message.sender + 16 * message.fields_a)
    b = message.cell + 65536 * message.fields_b
    check = (core % 97 + 7 * (b % 97) + ((tick % 9973) * keys[4]) % 97 + keys[5]) % 97
    return check + 100 * core, b


def unpack(a: int, b: int, tick: int, keys=DEFAULT_KEYS) -> Message:
    if not 0 <= a < MODULUS or not 0 <= b < MODULUS or tick < 0:
        raise ValueError("invalid payload or tick")
    core = a // 100
    m = Message(core % 16, (core // 16) % 16, core // 256, b // 65536, b % 65536)
    if payloads(m, tick, keys) != (a, b):
        raise ValueError("message check failed")
    return m


def encode(message: Message, tick: int, keys=DEFAULT_KEYS) -> str:
    a, b = payloads(message, tick, keys)
    return "".join(str(MODULUS + (p + mask(tick, i, keys)) % MODULUS)
                   for i, p in enumerate((a, b)))


def decode(text: str, tick: int, keys=DEFAULT_KEYS) -> Message:
    if len(text) != 20 or not text.isascii() or not text.isdigit() or text[0] != "1" or text[10] != "1":
        raise ValueError("expected two ten-digit blocks starting with 1")
    values = [(int(text[i * 10:(i + 1) * 10]) - MODULUS - mask(tick, i, keys)) % MODULUS
              for i in range(2)]
    return unpack(*values, tick, keys)


def batch_record(message: Message, decision_tick: int, direction: int, keys=DEFAULT_KEYS) -> str:
    """Two fixed-width decimal blocks; first leading digit is 1 receive / 2 send."""
    if direction not in (1, 2):
        raise ValueError("invalid direction")
    a, b = payloads(message, decision_tick - (direction == 2), keys)
    return f"{(2 if direction == 1 else 1) * MODULUS + a:010d}{MODULUS + b:010d}"


def decode_batch(text: str, tick: int, keys=DEFAULT_KEYS) -> list[tuple[int, Message]]:
    if not text or len(text) % 20 or len(text) > (MAX_DECODE + 1) * 20:
        raise ValueError("invalid batch length")
    if not text.isascii() or not text.isdigit():
        raise ValueError("nondecimal batch")
    events = []
    sent = False
    receives = 0
    for offset in range(0, len(text), 20):
        record = text[offset:offset + 20]
        if record[0] not in "12" or record[10] != "1":
            raise ValueError("invalid batch marker")
        direction = 1 if record[0] == "2" else 2
        if sent:
            raise ValueError("send must be the last batch record")
        sent = direction == 1
        receives += direction == 2
        if receives > MAX_DECODE:
            raise ValueError("too many receives")
        a = int(record[:10]) - int(record[0]) * MODULUS
        b = int(record[10:]) - MODULUS
        events.append((direction, unpack(a, b, tick - (direction == 2), keys)))
    return events



def replay_shape(row: dict) -> bool | None:
    """The engine's byte-based attempt filter, or unknown after a lossy text export."""
    raw = str(row["text"]).encode("utf-8", "surrogateescape")
    length = int(row["bytes"]) if row.get("bytes") is not None else len(raw)
    if len(raw) != length:
        return None
    return length == 20 and raw[0] == 49 and raw[10] == 49


def audit_transport(shouts: list[dict], events: list[dict], seat: int,
                    living_ticks: set[int], keys=DEFAULT_KEYS, max_decode=MAX_DECODE) -> dict:
    """Reconstruct the bounded inbox and compare logs with hash-verified replay speech.

    Replay shouts at t were sent at decision t-1 and arrive for decision t.
    This checks transport and logged acceptance, not the truth of payload claims.
    """
    from collections import Counter, defaultdict
    delivered, sent, recorded = defaultdict(list), defaultdict(list), defaultdict(list)
    for row in shouts:
        if row["seat"] == seat:
            sent[row["t"] - 1].append(row["text"])
        if seat in row["heard_by"] and row["t"] in living_ticks:
            delivered[row["t"]].append(row)
    for event in events:
        if event["kind"] == "PWC":
            recorded[event["t"]].append(event)
    counts = Counter()
    failures, issues = [], []
    ticks = sorted(set(delivered) | set(sent) | set(recorded))
    for tick in ticks:
        expected_receives = []
        attempts = 0
        # A lossy message can consume an attempt even when it cannot decode. Preserve
        # sends and other ticks, without inventing this tick's receive denominator.
        inbox = sorted(delivered[tick], key=lambda row: row["seat"])
        receive_known = all(replay_shape(row) is not None for row in inbox)
        if not receive_known:
            issues.append({"t": tick, "reason": "unknown_message_shape"})
            counts["unknown_shape_ticks"] += 1
        # Engine delivery visits speaker seats in order, preserving each speaker's queue.
        for row in inbox if receive_known else []:
            text = row["text"]
            own = row["seat"] % 2 == seat % 2
            shape = replay_shape(row)
            if shape and attempts >= max_decode:
                counts["capacity_excluded"] += 1
                counts["teammate_capacity_excluded"] += own
                continue
            counts["eligible_teammate_deliveries"] += own
            if not shape:
                counts["shape_rejected"] += 1
                continue
            attempts += 1
            try:
                message = decode(text, tick - 1, keys)
            except ValueError:
                counts["decode_rejected"] += 1
                continue
            if message.sender % 2 != seat % 2 or message.sender == seat:
                counts["sender_rejected"] += 1
                continue
            a, b = payloads(message, tick - 1, keys)
            expected_receives.append((message.sender, a, b))
            counts["accepted"] += 1
            counts["decoded_teammate_deliveries"] += own and message.sender == row["seat"]
            if message.sender != row["seat"]:
                counts["accepted_sender_claim_mismatch"] += 1
        receives, sends = [], []
        for event in recorded[tick]:
            if event["s"] == 2:
                receives.append((event["w"], *event["d"]))
            else:
                message = unpack(*event["d"], tick, keys)
                sends.append(encode(message, tick, keys))
                if message.sender != seat:
                    failures.append({"t": tick, "reason": "wrong_logged_sender"})
        if receive_known and receives != expected_receives:
            failures.append({"t": tick, "reason": "receive_mismatch",
                             "expected": expected_receives, "actual": receives})
        if Counter(sends) != Counter(sent[tick]):
            failures.append({"t": tick, "reason": "send_mismatch",
                             "expected": sent[tick], "actual": sends})
        if len(sent[tick]) > 1:
            failures.append({"t": tick, "reason": "multiple_sends"})
    eligible = counts["eligible_teammate_deliveries"]
    return {"status": "fail" if failures else ("unmeasurable" if issues else "pass"), "counts": dict(counts),
            "eligible_decode_rate": counts["decoded_teammate_deliveries"] / eligible if eligible and not issues else None,
            "issues": issues,
            "failures": failures}
