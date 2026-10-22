"""Conversation-scoped platform reads. These tools never change the research team's defaults."""

from collections.abc import Mapping
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from pydantic import BaseModel, Field, JsonValue, TypeAdapter

from ide_research_core.games.loader import load_game
from ide_research_core.softmax_tool.client import SoftmaxClient
from ide_research_core.softmax_tool.platform_cli import PlatformCli


class Game(BaseModel):
    id: str
    name: str
    coworld_name: str | None = None
    coworld_id: str | None = None
    canonical_coworld_id: str | None = None


class League(BaseModel):
    id: str
    name: str
    game: Game
    description: str | None = None
    participation_url: str
    wiki_markdown_url: str | None = None
    disabled_at: str | None = None


class Page(BaseModel):
    query: str = ""
    offset: Annotated[int, Field(ge=0)] = 0
    limit: Annotated[int, Field(ge=1, le=50)] = 20

    def result(self, key: str, rows: list[dict]) -> dict:
        end = self.offset + self.limit
        return {
            key: rows[self.offset : end],
            "total": len(rows),
            "next_offset": end if end < len(rows) else None,
        }


class Coworld(BaseModel):
    id: str
    name: str
    version: str
    manifest: dict[str, JsonValue]


def matches(query: str, *values: str) -> bool:
    """Names such as WebDiplomacy and web-diplomacy share a searchable spelling."""
    normalized = "".join(
        character for character in query.casefold() if character.isalnum()
    )
    return any(
        normalized
        in "".join(character for character in value.casefold() if character.isalnum())
        for value in values
    )


class Discovery:
    def __init__(self, env: Mapping[str, str], root: Path):
        self.env = env
        self.root = root
        self.cli = PlatformCli(env)
        self.client = SoftmaxClient(env)

    def discover_leagues(self, query="", offset=0, limit=20):
        page = Page(query=query, offset=offset, limit=limit)
        catalog = TypeAdapter(list[League]).validate_python(
            self.cli.api_get_json("/observatory/v2/leagues")
        )
        found = [
            league
            for league in catalog
            if league.disabled_at is None
            and matches(
                page.query,
                league.id,
                league.name,
                league.game.id,
                league.game.name,
                league.game.coworld_name or "",
                league.game.coworld_id or "",
                league.game.canonical_coworld_id or "",
            )
        ]
        found.sort(
            key=lambda league: (
                league.game.name.casefold(),
                league.name.casefold(),
                league.id,
            )
        )
        return page.result("leagues", [league.model_dump() for league in found])

    def league_players(self, league_id, query="", offset=0, limit=20):
        page = Page(query=query, offset=offset, limit=limit)
        rows = self.client.standings(league_id)
        found = [
            row
            for row in rows
            if matches(page.query, row["player_id"], row["player_name"] or "")
        ]
        return {
            "league_id": league_id,
            **page.result("players", found),
            "scope": "ranked players; absence does not establish that a player or policy does not exist",
        }

    def game_support(self, league_id):
        league = League.model_validate(
            self.cli.api_get_json(
                f"/observatory/v2/leagues/{quote(league_id, safe='')}"
            )
        )
        coworld_id = league.game.coworld_id or league.game.canonical_coworld_id
        support = []
        # Inspect installed contracts by their declared league; never turn a model-supplied
        # name into a path or execute downloaded documentation.
        for path in sorted((self.root / "games").glob("*/GAME.md")):
            game = load_game(self.root, path.parent.name)
            if (
                game.league_id == league.id
                or game.name.casefold() == (league.game.coworld_name or "").casefold()
            ):
                support.append(
                    {
                        "name": game.name,
                        "league_id": game.league_id,
                        "guide": game.guide,
                        "bindings": game.bindings,
                    }
                )
        return {
            "league": league.model_dump(),
            "coworld": Coworld.model_validate(
                self.cli.run_json("coworld", ["show", coworld_id])
            ).model_dump()
            if coworld_id
            else None,
            "participation_guide": self.cli.api_get_text(
                f"/observatory/v2/leagues/{quote(league.id, safe='')}.md"
            ),
            "installed_research_support": support,
            "research_bindings_available": bool(support),
            "workspace_game_hint": self.env.get("RESEARCH_GAME"),
            "credential_player_id": self.client.whoami().player_id,
        }

    def leaderboard(self, league_id, player_id):
        return {
            "league_id": league_id,
            "player_id": player_id,
            "current_champion": self.client.leaderboard_champion(player_id, league_id),
        }

    def latest_league_result(self, league_id, player_id):
        result = self.client.latest_league_result(player_id, league_id)
        return {
            "league_id": league_id,
            "player_id": player_id,
            "available": result is not None,
            "result": result,
        }
