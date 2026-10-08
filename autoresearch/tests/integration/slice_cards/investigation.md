PROTOCOL SLICE TEST CARD @CARD@. Do exactly the numbered steps below, in order, and nothing else. Run every command with the terminal tool in your workspace (your current directory), copied exactly as written, one numbered step per terminal call. If a command exits non-zero, read its error, fix only that problem, and run the step again. Do not read other files and do not use any other skill.

1. Change line 2 of the body of hypothesis @HYPOTHESIS@:
```sh
mkdir -p .scratch && printf 'Line 1: fixture hypothesis for the protocol slice.\nLine 2: edited by slice card @CARD@.\nLine 3: end of the fixture.\n' > .scratch/hypothesis.md && "$AUTORESEARCH_CLI" records update @HYPOTHESIS@ --body-file .scratch/hypothesis.md
```

2. Create one claim:
```sh
printf '{"kind": "behaviour", "status": "hypothesis", "confidence": 0.3, "evidence": [], "revise_if": "A later slice run shows otherwise.", "topics": []}\n' > .scratch/claim.json && printf 'Slice card @CARD@ ran its steps in order.\n' > .scratch/claim.md && "$AUTORESEARCH_CLI" records new claim --meta-file .scratch/claim.json --body-file .scratch/claim.md
```

3. Stage one highlight:
```sh
"$AUTORESEARCH_CLI" comms stage highlight --title "Slice highlight from card @CARD@" --body "Slice card @CARD@ edited @HYPOTHESIS@ and added a claim." --ref ar://hypothesis/@HYPOTHESIS@
```

4. Request one follow-up card:
```sh
"$AUTORESEARCH_CLI" queue request create --title "Slice follow-up of card @CARD@" --mode investigation --priority P3 --why "Protocol slice follow-up of card @CARD@. Stage one note titled Follow-up of card @CARD@, then complete the card with the complete-card skill, outcome done. Do nothing else." --ref ar://hypothesis/@HYPOTHESIS@
```

5. Write the journal of this card (it already carries the outcome done), with RUN replaced by the number on the `- Run:` line at the end of your briefing and TASK replaced by the task id on its `- Task:` line:
```sh
printf '{"iteration": RUN, "mode": "investigation", "card": "TASK", "doctrine_version": @DOCTRINE@, "unit": "Protocol slice card @CARD@.", "refs": ["ar://hypothesis/@HYPOTHESIS@"], "outcome": "done"}\n' > .scratch/journal.json && printf 'Edited @HYPOTHESIS@, added a claim, staged a highlight and requested a follow-up card.\n\n## Decisions\n\n- Follow the slice card exactly. Reason: it is a protocol test. Refs: ar://hypothesis/@HYPOTHESIS@.\n' > .scratch/journal.md && "$AUTORESEARCH_CLI" records new journal --meta-file .scratch/journal.json --body-file .scratch/journal.md
```

6. Finish the card:
```sh
"$AUTORESEARCH_CLI" card finish --outcome done
```
It prints one JSON object. On exit 0 its `data` is the handoff record.

7. Call the `kanban_complete` tool with the summary "Slice card @CARD@ done." and with the `data` object printed by step 6 as `metadata`. Then stop.
