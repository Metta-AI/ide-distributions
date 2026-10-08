PROTOCOL SLICE TEST CARD @CARD@. Do exactly the numbered steps below, in order, and nothing else. Run every command with the terminal tool in your workspace (your current directory), copied exactly as written, one numbered step per terminal call. Do not read other files and do not use any other skill.
@WAIT@
1. Stage one note:
```sh
"$AUTORESEARCH_CLI" comms stage note --title "Slice card @CARD@ ran" --body "Slice card @CARD@ ran its steps."
```

2. Write the journal of this card (it already carries the outcome done), with RUN replaced by the number on the `- Run:` line at the end of your briefing and TASK replaced by the task id on its `- Task:` line:
```sh
mkdir -p .scratch && printf '{"iteration": RUN, "mode": "investigation", "card": "TASK", "doctrine_version": @DOCTRINE@, "unit": "Protocol slice card @CARD@.", "refs": [], "outcome": "done"}\n' > .scratch/journal.json && printf 'Staged one note.\n\n## Decisions\n\n- Follow the slice card exactly. Reason: it is a protocol test.\n' > .scratch/journal.md && "$AUTORESEARCH_CLI" records new journal --meta-file .scratch/journal.json --body-file .scratch/journal.md
```

3. Finish the card:
```sh
"$AUTORESEARCH_CLI" card finish --outcome done
```
It prints one JSON object. On exit 0 its `data` is the handoff record.

4. Call the `kanban_complete` tool with the summary "Slice card @CARD@ done." and with the `data` object printed by step 3 as `metadata`. Then stop.
