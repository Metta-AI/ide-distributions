"""Workspace episode reservations shared by all researchers and retries.

A reservation survives an ambiguous platform response. Only an authoritative refusal
releases it; successful or unknown requests continue counting toward the ceiling.
"""

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from ide_research_core.softmax_tool.protocol import Refused


@dataclass(frozen=True)
class EpisodeBudget:
    path: Path
    ceiling: int

    def connect(self):
        if self.ceiling < 0:
            raise ValueError("episode ceiling must be nonnegative")
        conn = sqlite3.connect(self.path, timeout=60)
        conn.executescript(
            "CREATE TABLE IF NOT EXISTS limits (id INTEGER PRIMARY KEY CHECK(id=1), episodes INTEGER NOT NULL, configured INTEGER NOT NULL);"
            "CREATE TABLE IF NOT EXISTS reservations (key TEXT PRIMARY KEY, episodes INTEGER NOT NULL, spec TEXT NOT NULL);"
        )
        conn.execute(
            "INSERT OR IGNORE INTO limits VALUES (1, ?, ?)",
            (self.ceiling, self.ceiling),
        )
        conn.commit()
        return conn

    def configure(self) -> None:
        """Operator startup applies an explicitly changed deployment allowance, retaining spend."""
        with closing(self.connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            effective, configured = conn.execute(
                "SELECT episodes, configured FROM limits WHERE id=1"
            ).fetchone()
            if self.ceiling != configured:
                effective = (
                    self.ceiling
                    if self.ceiling > configured
                    else min(effective, self.ceiling)
                )
                conn.execute(
                    "UPDATE limits SET episodes=?, configured=? WHERE id=1",
                    (effective, self.ceiling),
                )

    def reserve(self, key: str, spec: dict) -> None:
        episodes = spec["num_episodes"]
        if not isinstance(episodes, int) or isinstance(episodes, bool) or episodes < 1:
            raise ValueError("num_episodes must be a positive integer")
        payload = json.dumps(spec, sort_keys=True)
        with closing(self.connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT spec FROM reservations WHERE key=?", (key,)
            ).fetchone()
            if existing:
                if existing[0] != payload:
                    raise ValueError(
                        "evaluation operation reused with different inputs"
                    )
                return
            limit = min(
                self.ceiling,
                conn.execute("SELECT episodes FROM limits WHERE id=1").fetchone()[0],
            )
            used = conn.execute(
                "SELECT COALESCE(SUM(episodes),0) FROM reservations"
            ).fetchone()[0]
            if used + episodes > limit:
                raise Refused(
                    "episode_budget_exhausted",
                    "Workspace episode budget exhausted",
                    {"reserved": used, "requested": episodes, "limit": limit},
                )
            conn.execute(
                "INSERT INTO reservations VALUES (?, ?, ?)", (key, episodes, payload)
            )

    def release_refused(self, key: str) -> None:
        with closing(self.connect()) as conn, conn:
            conn.execute("DELETE FROM reservations WHERE key=?", (key,))

    def snapshot(self) -> dict:
        with closing(self.connect()) as conn, conn:
            conn.execute("BEGIN")
            limit = min(
                self.ceiling,
                conn.execute("SELECT episodes FROM limits WHERE id=1").fetchone()[0],
            )
            used = conn.execute(
                "SELECT COALESCE(SUM(episodes),0) FROM reservations"
            ).fetchone()[0]
            return {"limit": limit, "reserved": used, "remaining": max(0, limit - used)}

    def lower(self, episodes: int) -> dict:
        with closing(self.connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            limit = min(
                self.ceiling,
                conn.execute("SELECT episodes FROM limits WHERE id=1").fetchone()[0],
            )
            if not 0 <= episodes <= limit:
                raise ValueError(
                    "the coordinator may lower the episode limit, never raise it"
                )
            conn.execute("UPDATE limits SET episodes=? WHERE id=1", (episodes,))
            used = conn.execute(
                "SELECT COALESCE(SUM(episodes),0) FROM reservations"
            ).fetchone()[0]
            return {
                "limit": episodes,
                "reserved": used,
                "remaining": max(0, episodes - used),
            }
