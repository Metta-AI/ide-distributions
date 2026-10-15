"""Card request files: written in a worktree, read back from a commit."""

import json
from dataclasses import asdict
from pathlib import Path
from typing import Literal

import jsonschema

from autoresearch.ids import new_id
from autoresearch.io import gitops
from autoresearch.io.docs import write_json
from autoresearch.paths import Layout
from autoresearch.queue.card import CardSpec, Priority

REQUESTS_DIR = "cards/requests"
_SCHEMA_DIR = Path(__file__).resolve().parents[3] / "schemas"


def load_schema(name: str) -> dict:
    return json.loads((_SCHEMA_DIR / name).read_text(encoding="utf-8"))


def write_request(
    layout: Layout,
    op: Literal["create", "reprioritize", "drop"],
    spec: CardSpec | None = None,
    target_request: str | None = None,
    priority: Priority | None = None,
    reason: str | None = None,
) -> str:
    """Validate and atomically write ``cards/requests/<Q-id>.json``; return the Q id."""
    request_id = new_id("card_request")
    request: dict = {"id": request_id, "op": op}
    if spec is not None:
        request["spec"] = asdict(spec) | {"request_id": request_id}
    if target_request is not None:
        request["target_request"] = target_request
    if priority is not None:
        request["priority"] = priority
    if reason is not None:
        request["reason"] = reason
    jsonschema.validate(request, load_schema("card_request.json"))
    write_json(layout.card_requests / f"{request_id}.json", request)
    return request_id


def read_requests(repo: Path, sha: str) -> list[dict]:
    """Requests present at commit ``sha``, sorted by id. Never reads the working directory."""
    listing = gitops.git(repo, "ls-tree", "--name-only", sha, f"{REQUESTS_DIR}/")
    requests = []
    for path in sorted(listing.splitlines()):
        if not path.endswith(".json"):
            continue
        content = gitops.show(repo, sha, path)
        if content is not None:
            requests.append(json.loads(content))
    return requests
