"""Conversation-scoped platform reads. These tools never change the research team's defaults."""

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from pydantic import BaseModel, Field, JsonValue, TypeAdapter

from ide_research_core.games.loader import load_game
from ide_research_core.softmax_tool.client import SoftmaxClient
from ide_research_core.softmax_tool.discovery_schema import SupportRead
from ide_research_core.softmax_tool.platform_cli import PlatformCli
from ide_research_core.softmax_tool.protocol import Refused


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

    def game_support(self, league_id, document=None, offset=0, limit=4000, expected_sha256=None):
        request = SupportRead(
            league_id=league_id, document=document, offset=offset,
            limit=limit, expected_sha256=expected_sha256,
        )
        league_id, document, offset, expected_sha256 = (
            request.league_id, request.document, request.offset, request.expected_sha256,
        )
        if document is None and (offset or expected_sha256 is not None):
            raise Refused("bad_arguments", "Select a document before supplying an offset or expected_sha256")
        if offset and expected_sha256 is None:
            raise Refused("bad_arguments", "Pass the document's expected_sha256 when continuing a read")
        league = League.model_validate(
            self.cli.api_get_json(
                f"/observatory/v2/leagues/{quote(league_id, safe='')}"
            )
        )
        if league.id != league_id:
            raise Refused("scope_mismatch", "The platform returned a different league")
        coworld_id = league.game.coworld_id or league.game.canonical_coworld_id
        coworld = Coworld.model_validate(
            self.cli.api_get_json(f"/observatory/v2/coworlds/{quote(coworld_id, safe='')}")
        ) if coworld_id else None
        if coworld is not None and coworld.id != coworld_id:
            raise Refused("scope_mismatch", "The platform returned a different Coworld")
        documents = {
            "participation": self.cli.api_get_text(
                f"/observatory/v2/leagues/{quote(league.id, safe='')}.md"
            ),
        }
        if coworld is not None:
            documents["manifest"] = json.dumps(coworld.manifest, ensure_ascii=False, sort_keys=True)
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
                        "document": f"research:{game.name}",
                        "bindings": game.bindings,
                    }
                )
                documents[f"research:{game.name}"] = game.guide
        index = [
            {"id": key, "sha256": hashlib.sha256(text.encode()).hexdigest(), "characters": len(text)}
            for key, text in documents.items()
        ]
        page = None
        if document is not None:
            if document not in documents:
                raise Refused("bad_arguments", "Choose a document id returned by game_support")
            text = documents[document]
            metadata = next(item for item in index if item["id"] == document)
            if expected_sha256 is not None and metadata["sha256"] != expected_sha256:
                raise Refused("document_changed", "Document changed; reload game_support before continuing")
            if offset > len(text):
                raise Refused("bad_arguments", "Document offset exceeds its character count")
            end = min(offset + request.limit, len(text))
            page = {
                **metadata, "content": text[offset:end], "offset": offset,
                "next_offset": end if end < len(text) else None,
                "offset_unit": "unicode_characters",
            }
        return {
            "league": league.model_dump(),
            "coworld": coworld.model_dump(exclude={"manifest"}) if coworld is not None else None,
            "documents": index,
            "document": page,
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
