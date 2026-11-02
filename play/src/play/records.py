"""The cogent's own records, read and written as the Player through the public records API.

``GET``/``PUT {server}/observatory/v2/cogents/<player>/c/<collection>/<key>`` with the player
credential as a Bearer token: the same calls the hosted bridge makes for ``c/native_research``
and ``c/state/league``. A player credential writes as the cogent. Play reads ``state/league``
(the league the coach chose) and writes ``tasks`` (the task cards the Mind on /play shows).

``POST {server}/observatory/v2/cogents/<player>/page`` writes the cogent's one page action (point,
open, press or go on a ref of the page outline), which the /play page runs once per ``id``.
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable

from ide_research_core.softmax_tool.platform_cli import SoftmaxCliError

TIMEOUT_S = 30
USER_AGENT = "softmax-play"

# (method, url, headers, body) -> (HTTP status, response body)
Request = Callable[[str, str, dict[str, str], bytes | None], tuple[int, bytes]]


def urllib_request(
    method: str, url: str, headers: dict[str, str], body: bytes | None
) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


class CogentRecords:
    def __init__(self, server: str, player: str, token: str, request: Request = urllib_request):
        self._cogent = f"{server}/observatory/v2/cogents/{urllib.parse.quote(player, safe='')}"
        self._root = f"{self._cogent}/c"
        self._token = token
        self._request = request

    def _call(
        self, method: str, collection: str, key: str, payload: dict | None = None
    ) -> tuple[int, bytes]:
        url = f"{self._root}/{collection}/{urllib.parse.quote(key, safe='')}"
        return self._send(method, url, payload)

    def _send(self, method: str, url: str, payload: dict | None = None) -> tuple[int, bytes]:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        }
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload).encode()
        return self._request(method, url, headers, body)

    def get(self, collection: str, key: str) -> dict | None:
        """The record (``{key, rev, at, body, ...}``), or None when there is none."""
        status, body = self._call("GET", collection, key)
        if status == 404:
            return None
        if status != 200:
            raise SoftmaxCliError(
                f"GET c/{collection}/{key} answered HTTP {status}: {body[:300]!r}"
            )
        return json.loads(body)

    def put(
        self, collection: str, key: str, body: dict, base_rev: int | None, idempotency_key: str
    ) -> None:
        payload = {"body": body, "base_rev": base_rev, "idempotency_key": idempotency_key}
        status, response = self._call("PUT", collection, key, payload)
        if status not in (200, 201):
            raise SoftmaxCliError(
                f"PUT c/{collection}/{key} answered HTTP {status}: {response[:500]!r}"
            )

    def page(self, action: dict) -> None:
        """Write the cogent's page action (``{id, kind, ref, note?}``)."""
        status, response = self._send("POST", f"{self._cogent}/page", action)
        if status not in (200, 201, 202, 204):
            raise SoftmaxCliError(f"POST page answered HTTP {status}: {response[:500]!r}")
