"""The coworlds Play supports. GotA only: its program is one game-hosted BASIC file.

Container coworlds are not supported yet (the hosted agent cannot build an image).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Game:
    name: str
    league_id: str  # the same league as researcher/games/gota/GAME.md (a test checks it)
    starter_player: str  # the manifest ``player[].id`` softmax.com names as the starter
    suffix: str  # the program file's extension


GOTA = Game(
    name="gota",
    league_id="league_3c60897b-25cf-4b37-9d1a-8554c1198f28",
    starter_player="baseline",
    suffix=".bas",
)
