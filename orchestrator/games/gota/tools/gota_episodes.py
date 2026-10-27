# Source: Metta-AI/personal_labs @ 07dc6f94760fae3cf2de36877e0b6186cfd1dc63; path: gods_of_the_arena_lab/tools/gota_episodes.py
"""Read downloaded Gods of the Arena episodes into per-episode and per-seat records.

Shared by the lab's `coworld-ab` adapter (`compare.py`) and the
`coworld-hypothesis-miner` row builder (`miner_rows.py`). It reads what the shared
downloader (`coworld-episode-artifacts`) writes for one experience request:

    <root>/<episode dir>/episode.json          participants, status, scores
    <root>/<episode dir>/results.json          outcome, ticks, per-seat total_xp
    <root>/<episode dir>/game_logs.log         headless summary: levels, towers, gods
    <root>/<episode dir>/logs/policy_agent_N.log   our own seats' PRINT logs (LH telemetry)

Nothing here re-simulates a replay. Everything is end-of-game state plus the
policy's own `LH` telemetry snapshots (see policy/README.md, "Telemetry"), so
inferred combat counts exist only for seats whose policy prints them. Passing
replay_stats to load_batch joins verified expander counts for all seats by
(episode_id, position), checking outcome, duration, policy name and XP.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

if __package__:
    from .evaluate import metrics
else:
    from evaluate import metrics

# HeroClass enum order: Polyworld 5287c437 content.nim:26-36.
CLASS_NAMES = ["VanguardKnight", "Ranger", "Arcanist", "DruidWarden", "DemonHunter",
               "DeathKnight", "Crossbowman", "Lich", "Warlock", "Berserker"]


def replay_class_name(row: dict) -> str:
    """Use a verified replay's enum ordinal; missing class evidence stays unknown."""
    value = row.get("class")
    if value is None:
        return "unknown"
    if type(value) is not int or not 0 <= value < len(CLASS_NAMES):
        raise ValueError("invalid replay hero class; regenerate verified replay stats")
    return CLASS_NAMES[value]


# Coarser grouping: the farm-priority tiers of docs/roles.md (duelists first, then burst
# killers who need levels, then siege fronts, then the Warlock, then the healer).
ROLE_OF_CLASS = {
    "Berserker": "duelist", "DemonHunter": "duelist", "Ranger": "duelist",
    "Lich": "burst", "Arcanist": "burst", "Crossbowman": "burst",
    "DeathKnight": "siege", "VanguardKnight": "siege",
    "Warlock": "warlock", "DruidWarden": "healer",
}
ROLE_NAMES = ["duelist", "burst", "siege", "warlock", "healer"]

# The `LH` telemetry line the James Botts policy prints every 240 ticks. Older
# builds omit the last two fields.
LH_FIELDS = ("tick", "last_hits", "hero_kills", "tower_kills", "level", "orders",
             "secures", "poisons", "lost", "deaths", "gold", "early", "missed")


def team_of_seat(position: int) -> str:
    return "red" if position < 5 else "blue"


@dataclass
class Telemetry:
    """The policy's final `LH` snapshot plus first-occurrence ticks scanned from all
    snapshots. Snapshot resolution is 240 ticks, so a first-occurrence tick is the
    first snapshot at which the count was positive. `None` means "never happened"."""

    tick: int
    last_hits: int
    hero_kills: int
    tower_kills: int
    level: int
    orders: int
    secures: int
    poisons: int
    lost: int
    deaths: int
    gold: int
    early: int | None
    missed: int | None
    first_last_hit_tick: int | None
    first_hero_kill_tick: int | None
    first_death_tick: int | None
    snapshots: int


@dataclass
class CombatCounts:
    last_hits: int
    hero_kills: int
    tower_kills: int  # Includes barracks, matching the LH and XP accounting contract.
    deaths: int
    nearby_death_share: float | None = None
    nearby_kill_share: float | None = None


@dataclass
class Seat:
    episode_id: str
    position: int
    hero_class: str
    team: str
    policy_name: str | None
    version: int | None
    policy_version_id: str | None
    player_name: str | None
    won: bool
    total_xp: int | None
    level: int | None            # from the game log's `heroes:` line; all seats
    has_log: bool
    vm_error: bool | None        # `BASIC error:` in the seat's own log; None without a log
    telemetry: Telemetry | None
    combat: CombatCounts | None = None


@dataclass
class EpisodeRecord:
    episode_id: str
    directory: Path
    status: str | None
    ops_fail: bool
    known: bool                  # False when neither a failure nor a full result is on disk
    outcome: str | None          # RedTeam | BlueTeam | draw | time_limit
    ticks: int | None
    seed: int | None
    coworld_version: str | None
    towers: dict[str, int] = field(default_factory=dict)
    god_hp: dict[str, int] = field(default_factory=dict)
    scripts_active: int | None = None
    seats: list[Seat] = field(default_factory=list)

    @property
    def draw(self) -> bool:
        """Historical draw_rate includes native draws and timeouts (no winning team)."""
        return self.outcome in {"draw", "time_limit"}


def parse_spec(spec: str) -> tuple[str, int | None]:
    """'james-botts-gota:v1' -> ('james-botts-gota', 1)."""
    if ":v" in spec:
        name, version = spec.rsplit(":v", 1)
        return name, int(version)
    return spec, None


def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object")  # noqa: TRY004 - caller catches input errors
    return data


def _game_summary(path: Path) -> dict:
    """Pull the headless summary out of the container log dump.

    The downloader stores each container's stdout as a Python bytes repr, so line
    breaks arrive as the two characters backslash-n. Normalize before matching.
    """
    if not path.exists():
        return {}
    text = path.read_text(errors="replace").replace("\\n", "\n")
    out: dict = {}
    match = re.search(r"^heroes: red((?: L\d+){5}), blue((?: L\d+){5})", text, re.MULTILINE)
    if match:
        out["levels"] = [int(v) for v in re.findall(r"L(\d+)", match.group(1) + match.group(2))]
    match = re.search(r"^towers: red (\d+), blue (\d+)", text, re.MULTILINE)
    if match:
        out["towers"] = {"red": int(match.group(1)), "blue": int(match.group(2))}
    match = re.search(r"^gods: red (\d+) hp, blue (\d+) hp", text, re.MULTILINE)
    if match:
        out["god_hp"] = {"red": int(match.group(1)), "blue": int(match.group(2))}
    match = re.search(r"^scripts: (\d+)/(\d+) active", text, re.MULTILINE)
    if match:
        out["scripts_active"] = int(match.group(1))
    return out


def _parse_telemetry(path: Path) -> tuple[bool, Telemetry | None]:
    """Return (vm_error, telemetry) from one seat's private log."""
    vm_error = False
    last: dict | None = None
    firsts: dict[str, int | None] = {"last_hits": None, "hero_kills": None, "deaths": None}
    snapshots = 0
    with path.open(errors="replace") as log:
        for line in log:
            if "BASIC error:" in line:
                vm_error = True
            parts = line.split()
            if parts[:1] != ["LH"]:
                continue
            try:
                values = [int(v) for v in parts[1:1 + len(LH_FIELDS)]]
            except ValueError:
                last = None
                continue
            if len(values) < 11:
                last = None
                continue
            snapshots += 1
            row = dict(zip(LH_FIELDS, values))
            for key, first in firsts.items():
                if first is None and row[key] > 0:
                    firsts[key] = row["tick"]
            last = row
    if last is None:
        return vm_error, None
    return vm_error, Telemetry(
        tick=last["tick"], last_hits=last["last_hits"], hero_kills=last["hero_kills"],
        tower_kills=last["tower_kills"], level=last["level"], orders=last["orders"],
        secures=last["secures"], poisons=last["poisons"], lost=last["lost"],
        deaths=last["deaths"], gold=last["gold"], early=last.get("early"),
        missed=last.get("missed"), first_last_hit_tick=firsts["last_hits"],
        first_hero_kill_tick=firsts["hero_kills"], first_death_tick=firsts["deaths"],
        snapshots=snapshots,
    )


def load_episode(directory: Path) -> EpisodeRecord | None:
    """Read one downloaded episode directory; None when it has no episode.json."""
    episode = _read_json(directory / "episode.json")
    if episode is None:
        return None
    episode_id = episode.get("id")
    if not episode_id:
        raise ValueError(f"{directory}: episode.json has no id")
    results = _read_json(directory / "results.json") or {}
    scores = results.get("scores") or []
    total_xp = results.get("total_xp") or []
    status = episode.get("status")
    failed = bool(status in {"failed", "cancelled"} or episode.get("error_type")
                  or episode.get("failed_policy_index") is not None
                  or episode.get("failed_agent_index") is not None)
    complete = status == "completed" and len(scores) == 10 and len(total_xp) == 10
    if complete:
        metrics(results, 0)  # Validate the same native result contract as GAME.md.
    summary = _game_summary(directory / "game_logs.log")
    levels = summary.get("levels")

    record = EpisodeRecord(
        episode_id=episode_id, directory=directory, status=status, ops_fail=failed,
        known=failed or complete, outcome=results.get("outcome"),
        ticks=results.get("ticks"), seed=results.get("seed"),
        coworld_version=episode.get("coworld_version"),
        towers=summary.get("towers", {}), god_hp=summary.get("god_hp", {}),
        scripts_active=summary.get("scripts_active"),
    )
    for participant in episode.get("participants") or []:
        position = participant.get("position")
        if position is None or not 0 <= position < 10:
            continue
        log_path = directory / "logs" / f"policy_agent_{position}.log"
        has_log = log_path.exists()
        vm_error, telemetry = _parse_telemetry(log_path) if has_log else (None, None)
        record.seats.append(Seat(
            episode_id=episode_id, position=position, hero_class="unknown",
            team=team_of_seat(position), policy_name=participant.get("policy_name"),
            version=participant.get("version"),
            policy_version_id=participant.get("policy_version_id"),
            player_name=participant.get("player_name"),
            won=bool(complete and results["outcome"] ==
                     ("RedTeam" if position < 5 else "BlueTeam")),
            total_xp=total_xp[position] if complete else None,
            level=levels[position] if levels else None,
            has_log=has_log, vm_error=vm_error, telemetry=telemetry,
        ))
    return record


def load_batch(root: Path, replay_stats: Path | None = None) -> tuple[list[EpisodeRecord], Counter]:
    """Load every episode directory under `root` (the downloader's output directory,
    or a parent holding several). Raises on a duplicate episode id."""
    records: list[EpisodeRecord] = []
    excluded: Counter = Counter()
    seen: set[str] = set()
    paths = [root] if (root / "episode.json").exists() else sorted(
        p.parent for p in root.rglob("episode.json"))
    for directory in paths:
        record = load_episode(directory)
        if record is None:
            excluded["missing_episode_metadata"] += 1
            continue
        if record.episode_id in seen:
            raise ValueError(f"duplicate episode id {record.episode_id} under {root}")
        seen.add(record.episode_id)
        records.append(record)
    if replay_stats is not None:
        report = json.loads(replay_stats.read_text())
        by_id = {record.episode_id: record for record in records}
        joined: set[str] = set()
        for episode in report["episodes"]:
            episode_id = episode["episode_id"]
            if episode_id not in by_id:
                continue
            if episode_id in joined:
                raise ValueError(f"duplicate replay episode {episode_id}")
            joined.add(episode_id)
            record = by_id[episode_id]
            rows = episode["seats"]
            if (episode.get("verified") is not True or len(rows) != 10
                    or {row["slot"] for row in rows} != set(range(10))
                    or episode["ticks"] != record.ticks or episode["outcome"] != record.outcome):
                raise ValueError(f"invalid replay coverage or outcome for {episode_id}")
            by_slot = {row["slot"]: row for row in rows}
            for seat in record.seats:
                row = by_slot[seat.position]
                if (row["xp"] != seat.total_xp or bool(row["win"]) != seat.won
                        or row["policy_name"] != seat.policy_name):
                    raise ValueError(f"replay identity/result mismatch: {episode_id}:{seat.position}")
                seat.hero_class = replay_class_name(row)
                seat.combat = CombatCounts(row["last_hits"], row["hero_kills"],
                                           row["building_kills"], row["deaths"],
                                           row.get("nearby_death_share"), row.get("nearby_kill_share"))
        excluded["missing_exact_replay"] += sum(not r.ops_fail and r.episode_id not in joined for r in records)
    return records, excluded


def own_seats(record: EpisodeRecord, policy: str, version: int | None) -> list[Seat]:
    """Seats in this episode held by `policy` (name) at `version` (None = any)."""
    return [seat for seat in record.seats
            if seat.policy_name == policy and (version is None or seat.version == version)]
