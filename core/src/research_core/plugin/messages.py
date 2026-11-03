"""Adapt standard OpenAI content at the supported Hermes model-request boundary.

Registration stays stdlib-only. Canonical session history is never rewritten.
"""


def adapt_request(*, request: dict, **_) -> dict | None:
    from research_core import layout
    from research_core.messages import UserMessage
    from research_core.plugin.executor import profile_env

    field = "messages" if "messages" in request else "input"
    messages = request.get(field)
    if not isinstance(messages, list):
        return None
    documents = layout.from_env(profile_env()).root / "documents"
    adapted = []
    changed = False
    for message in messages:
        if (
            not isinstance(message, dict)
            or message.get("role") != "user"
            or not isinstance(message.get("content"), list)
        ):
            adapted.append(message)
            continue
        content = []
        for part in message["content"]:
            if isinstance(part, dict) and part.get("type") in (
                "input_text",
                "input_image",
                "input_file",
            ):
                typed = UserMessage.model_validate(
                    {
                        "role": "user",
                        "content": [{k: v for k, v in part.items() if k != "cache_control"}],
                    }
                )
                native = typed.model_input(documents)[0]["content"]
                if field == "input":
                    native = [
                        {"type": "input_text", "text": item["text"]}
                        if item["type"] == "text"
                        else {
                            "type": "input_image",
                            "image_url": item["image_url"]["url"],
                            "detail": item["image_url"]["detail"],
                        }
                        for item in native
                    ]
                if "cache_control" in part:
                    native[-1]["cache_control"] = part["cache_control"]
                content.extend(native)
                changed = True
            else:
                content.append(part)
        adapted.append({**message, "content": content})
    return (
        {"request": {**request, field: adapted}, "source": "research-core.openai-content"}
        if changed
        else None
    )


def record_native_message(*, session_id: str, conversation_history: list, **_) -> None:
    """Bind application metadata to the native row ID supplied by the pinned observer hook."""
    from research_core import layout
    from research_core.plugin.executor import profile_env
    from research_core.store.actors import Actor
    from research_core.store.db import Store

    workspace = layout.from_env(profile_env())
    store = Store.open(workspace.store_path, kind="workspace")
    try:
        admission = store.one(
            "SELECT op_id FROM client_admissions WHERE session_id=? AND kind='message'"
            " AND dispatch_started=1 AND status IN ('queued','running') ORDER BY rowid LIMIT 1",
            (session_id,),
        )
        if admission is None:
            return  # Background turns have no client admission.
        current = next(
            message for message in reversed(conversation_history) if message["role"] == "user"
        )
        native_id = current["_row_id"]
        with store.tx(Actor.harness()) as tx:
            tx.execute(
                "UPDATE client_admissions SET native_message_id=? WHERE op_id=?"
                " AND native_message_id IS NOT ?",
                (native_id, admission["op_id"], native_id),
            )
    finally:
        store.close()
