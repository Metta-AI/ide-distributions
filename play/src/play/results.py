"""A practice's games in words a beginner reads: who won, glory, XP, game length, and the change
against the previous version's practice.

GotA writes one ``results.json`` per game (polyworld ``examples/gods_of_the_arena``; recorded
samples in ``researcher/tests/fixtures``): ``scores`` (glory per seat), ``ticks``, ``seed``,
``outcome`` (``RedTeam``, ``BlueTeam``, ``draw`` or ``time_limit``), ``banked_gold``, ``returned``
and ``total_xp`` (lifetime XP per seat). Seats 0-4 are Red and 5-9 are Blue. Glory is the winning
heroes' XP per simulated minute; losers, draws and time-limit games give 0. Nothing else is in the
file: no kills, deaths, positions or time near towers.
"""

from pydantic import BaseModel, ConfigDict

TICKS_PER_SECOND = 24
TEAMS = {"RedTeam": "Red", "BlueTeam": "Blue"}
MEASURED = "who won, each hero's glory and XP, and how long the game ran"
NOT_MEASURED = (
    "The game's results record only who won, each hero's glory and XP, and the game length. "
    "They do not record where heroes went or what they did (for example time near enemy towers, "
    "kills or deaths); watch the games to see that."
)
GLORY = "Glory is the winners' XP per minute; the losing side, a draw or a game that runs out of time gives 0."


class GameResults(BaseModel):
    """The fields of GotA's results.json this summary reads; the rest are not used."""

    model_config = ConfigDict(extra="ignore")

    scores: list[float]
    ticks: int
    outcome: str
    total_xp: list[float]


def team(seat: int) -> str:
    return "Red" if seat < 5 else "Blue"


def game_summary(number: int, seats: list[int], results: GameResults, watch_url: str | None) -> dict:
    ours = {team(seat) for seat in seats}
    minutes = round(results.ticks / TICKS_PER_SECOND / 60, 1)
    winner = TEAMS.get(results.outcome)
    if winner is not None and len(ours) == 1:
        result = "won" if winner in ours else "lost"
        why = f"{winner}'s side destroyed the other fort after {minutes} minutes"
    elif winner is not None:
        result = "played both sides"
        why = f"{winner}'s side destroyed the other fort after {minutes} minutes"
    elif results.outcome == "time_limit":
        result = "no winner"
        why = f"time ran out after {minutes} minutes before either fort fell, so every hero's glory is 0"
    elif results.outcome == "draw":
        result = "draw"
        why = f"the game ended in a draw after {minutes} minutes"
    else:
        result = "unknown"
        why = f"the game reported the outcome {results.outcome!r}, which Play does not know"
    others = [xp for seat, xp in enumerate(results.total_xp) if seat not in seats]
    return {
        "game": number,
        "result": result,
        "why": why,
        "minutes": minutes,
        "your_glory": round(sum(results.scores[seat] for seat in seats) / len(seats), 1),
        "your_xp": round(sum(results.total_xp[seat] for seat in seats) / len(seats)),
        "other_heroes_xp": round(sum(others) / len(others)) if others else None,
        "watch_url": watch_url,
    }


def failed_game(number: int, why: str, watch_url: str | None) -> dict:
    return {"game": number, "result": "failed", "why": why[:300], "watch_url": watch_url}


def summarize(display: str, games: list[dict], previous: tuple[str, dict] | None) -> dict:
    """The practice's totals and a sentence the cogent can say as it is."""
    played = [g for g in games if g["result"] != "failed"]
    count = {key: sum(g["result"] == key for g in games) for key in ("won", "lost", "no winner", "draw", "failed")}
    summary = {
        "program": display,
        "games": len(games),
        **{key.replace(" ", "_"): value for key, value in count.items()},
        "average_glory": _average(g["your_glory"] for g in played),
        "average_xp": _average(g["your_xp"] for g in played),
        "average_minutes": _average(g["minutes"] for g in played),
    }
    parts = [f"{display} played {len(games)} practice game{'s' * (len(games) != 1)}"]
    tally = [f"{label} {count[key]}" for key, label in (
        ("won", "won"), ("lost", "lost"), ("no winner", "ran out of time"), ("draw", "drew"), ("failed", "failed to run")
    ) if count[key]]
    parts[0] += ": " + ", ".join(tally) + "." if tally else "."
    if played:
        parts.append(f"Your hero averaged {summary['average_xp']:,} XP and {summary['average_glory']:g} glory.")
    if previous is not None:
        before, numbers = previous
        summary["previous"] = {"program": before, **{k: numbers[k] for k in ("games", "won", "average_xp", "average_glory")}}
        parts.append(
            f"Last practice of {before}: won {numbers['won']} of {numbers['games']}, "
            f"{numbers['average_xp']:,} XP and {numbers['average_glory']:g} glory on average."
        )
    if count["no winner"] or count["lost"]:
        parts.append(GLORY)
    summary["plain"] = " ".join(parts)
    summary["measured"] = MEASURED
    summary["not_measured"] = NOT_MEASURED
    return summary


def _average(values) -> float | int:
    values = list(values)
    if not values:
        return 0
    mean = sum(values) / len(values)
    return round(mean) if mean >= 100 else round(mean, 1)
