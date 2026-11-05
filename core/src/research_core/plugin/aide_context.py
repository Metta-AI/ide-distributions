"""Inject current durable AIDE context through Hermes's supported request middleware."""

import json


def inject_context(*, request: dict, session_id: str = "", **_) -> dict | None:
    if not session_id:
        return None
    from research_core import layout
    from research_core.plugin.executor import profile_env
    from research_core.store.db import Store

    workspace = layout.from_env(profile_env())
    with Store.open(workspace.store_path, kind="workspace") as store:
        rows = store.q(
            "SELECT request FROM client_admissions WHERE session_id=? AND kind='message'"
            " AND dispatch_started=1 AND status IN ('queued','running')"
            " AND delivery_mode!='steer'"
            " AND json_array_length(request,'$.canonical_request.messages')>1",
            (session_id,),
        )
    if not rows:
        return None
    assert len(rows) == 1, "AIDE context requires one serialized native admission"
    prefixes = json.loads(rows[0]["request"])["canonical_request"]["messages"][:-1]
    messages = request["messages"]
    current_user = max(index for index, message in enumerate(messages) if message["role"] == "user")
    if messages[max(0, current_user - len(prefixes)) : current_user] == prefixes:
        return None
    return {
        "request": {
            **request,
            "messages": [*messages[:current_user], *prefixes, *messages[current_user:]],
        }
    }
