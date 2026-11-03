"""Associate client context with the native message row, without rewriting history."""


def record_native_message(
    *, session_id: str, conversation_history: list, user_message: object = None, **_
) -> None:
    """Bind application metadata to the native row ID supplied by the pinned observer hook."""
    from research_core import layout
    from research_core.plugin.executor import profile_env
    from research_core.store.actors import Actor
    from research_core.store.db import Store

    current = next(
        message for message in reversed(conversation_history) if message["role"] == "user"
    )
    native_id = current["_row_id"]
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
        with store.tx(Actor.harness()) as tx:
            tx.execute(
                "UPDATE client_admissions SET native_message_id=? WHERE op_id=?"
                " AND native_message_id IS NULL",
                (native_id, admission["op_id"]),
            )
    finally:
        store.close()
