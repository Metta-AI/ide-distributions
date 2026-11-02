"""Supported BASIC games and the selected program's published execution contract."""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


@dataclass(frozen=True)
class Game:
    name: str
    reference_league_id: str  # identifies adapter capability; never selects the user's league
    starter_player: str  # the manifest ``player[].id`` softmax.com names as the starter
    suffix: str  # the program file's extension
    seat_count: int  # the whole roster: the platform rejects a roster of any other length


GOTA = Game(
    name="gota",
    reference_league_id="league_3c60897b-25cf-4b37-9d1a-8554c1198f28",
    starter_player="baseline",
    suffix=".bas",
    seat_count=10,  # GotA's ten hero seats, red 0-4 and blue 5-9
)


class CommissionerConfig(BaseModel):
    default_variant_id: str | None = None


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    game: Literal["gota", "generals"]
    league_id: str
    coworld_id: str
    starter: str
    variant_id: str
    seat_count: int = Field(ge=2, le=10)
