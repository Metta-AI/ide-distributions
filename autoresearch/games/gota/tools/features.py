# Source: Metta-AI/personal_labs @ 07dc6f94760fae3cf2de36877e0b6186cfd1dc63; path: gods_of_the_arena_lab/tools/features.py
"""Gods of the Arena feature adapter for the `coworld-hypothesis-miner` engine.

Consumes the rows `miner_rows.py` writes (one per episode and own seat) and exposes the
`adapter` and `METAS` names the engine loads. Features come from end-of-game results and
the policy's own `LH` telemetry snapshots (240-tick resolution); nothing here needs a
replay re-simulation. When miner_rows receives a verified replay report, its combat
counts override LH estimates, including for seats without telemetry. Policy activation
and timing features still require logs; missing features remain unavailable.

Score components are excluded per `score_kind` (set by `miner_rows.py --score`): XP is
25 per last hit, 150 per hero kill, 100 per building kill, and level is a function of XP,
so those cannot be allowed to "explain" an XP score.
"""

from __future__ import annotations

from variance_miner import Episode, FeatureMeta

# Features that are arithmetic parts of a score, by score kind.
SCORE_COMPONENTS = {
    "xp": {"last_hits", "hero_kills", "tower_kills", "level", "farmed_at_all"},
    "xp_rate": {"last_hits", "hero_kills", "tower_kills", "level", "farmed_at_all"},
    "last_hits": {"last_hits", "farmed_at_all"},
    "win": set(),
}


def _meta(name: str, kind: str, blurb: str, hint: str) -> FeatureMeta:
    return FeatureMeta(name=name, kind=kind, blurb=blurb, change_hint=hint)


METAS: dict[str, FeatureMeta] = {
    "last_hits": _meta("last_hits", "count", "footman last hits taken by this seat",
                       "change the last-hit gate (`lhPlan`) so more ordered targets convert"),
    "hero_kills": _meta("hero_kills", "count", "enemy heroes killed by this seat",
                        "open on heroes with the full rotation when the burst can finish them"),
    "tower_kills": _meta("tower_kills", "count", "towers or barracks killed by this seat",
                         "push with a footman in front once the lane is cleared"),
    "deaths": _meta("deaths", "count", "times this seat died",
                    "retreat earlier on hero or tower threat (`svPlan`)"),
    "level": _meta("level", "count", "final hero level",
                   "raise income earlier; level gates HP and damage"),
    "orders": _meta("orders", "count", "attack orders the last-hit module issued",
                    "widen or narrow the scan window that produces orders"),
    "secures": _meta("secures", "count", "ordered targets that paid out",
                     "tune the hit-timing threshold so ordered targets are actually killed"),
    "secure_rate": _meta("secure_rate", "count", "secures per order (conversion of last-hit orders)",
                         "order later or with a stricter HP window so more orders convert"),
    "poisons": _meta("poisons", "count", "poison uses",
                     "use poison on footmen just above basic-hit damage"),
    "lost": _meta("lost", "count", "ordered targets that vanished unpaid",
                  "stop ordering targets that allied footmen or towers will take first"),
    "early": _meta("early", "count", "basic hits that landed while the target survived",
                   "hold the basic attack until the target is inside kill range"),
    "missed": _meta("missed", "count", "footmen that died within chase distance while not ordered",
                    "order more of the reachable low-HP footmen"),
    "gold_unspent": _meta("gold_unspent", "count", "gold left at the end of the game",
                          "spend gold as soon as an item is affordable (`shBuy`)"),
    "first_last_hit_tick": _meta("first_last_hit_tick", "timing", "tick of the first last hit (never = game length + 1)",
                                 "reach the lane and start farming sooner"),
    "first_hero_kill_tick": _meta("first_hero_kill_tick", "timing", "tick of the first hero kill (never = game length + 1)",
                                  "look for an early kill while the ultimate is a third of an enemy's HP"),
    "first_death_tick": _meta("first_death_tick", "timing", "tick of the first death (never = game length + 1)",
                              "survive the opening: avoid engine combat until the threat check passes"),
    "farmed_at_all": _meta("farmed_at_all", "presence", "took at least one last hit",
                           "make sure every class reaches a lane with footmen"),
    "died_at_all": _meta("died_at_all", "presence", "died at least once",
                         "retreat on the first hero or tower threat"),
    "game_ticks": _meta("game_ticks", "count", "length of the game in ticks (shared by all seats of the episode)",
                        "a short game means one god fell fast; check whether it was ours"),
    "team_won": _meta("team_won", "presence", "this seat's team won (a team outcome, not a seat behavior)",
                      "not a behavior; use it to see whether the score tracks team results"),
}


def adapter(row: dict) -> Episode | None:
    score_kind = row.get("score_kind", "xp")
    score = row.get("score")
    ticks = row.get("ticks")
    if score is None or not ticks:
        return None
    never = float(ticks) + 1.0
    features: dict[str, float] = {
        "game_ticks": float(ticks),
        "team_won": float(bool(row.get("won"))),
    }
    if row.get("level") is not None:
        features["level"] = float(row["level"])
    telemetry = row.get("telemetry")
    if telemetry:
        for key in ("last_hits", "hero_kills", "tower_kills", "deaths", "orders",
                    "secures", "poisons", "lost"):
            features[key] = float(telemetry[key])
        for key in ("early", "missed"):
            if telemetry.get(key) is not None:
                features[key] = float(telemetry[key])
        features["gold_unspent"] = float(telemetry["gold"])
        if telemetry["orders"] > 0:
            features["secure_rate"] = telemetry["secures"] / telemetry["orders"]
        for key in ("first_last_hit_tick", "first_hero_kill_tick", "first_death_tick"):
            value = telemetry.get(key)
            features[key] = never if value is None else float(value)
        features["farmed_at_all"] = float(telemetry["last_hits"] > 0)
        features["died_at_all"] = float(telemetry["deaths"] > 0)
    combat = row.get("combat")
    if combat is not None:
        for key in ("last_hits", "hero_kills", "tower_kills", "deaths"):
            features[key] = float(combat[key])
        features["farmed_at_all"] = float(combat["last_hits"] > 0)
        features["died_at_all"] = float(combat["deaths"] > 0)
    for name in SCORE_COMPONENTS.get(score_kind, set()):
        features.pop(name, None)
    notes = {"class": str(row.get("hero_class")), "role": str(row.get("role")),
             "team": str(row.get("team")), "score_kind": score_kind}
    return Episode(episode_id=f"{row['episode_id']}:{row['position']}", score=float(score),
                   features=features, notes=notes)
