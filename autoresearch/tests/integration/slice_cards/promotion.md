PROTOCOL SLICE TEST CARD @CARD@ (promotion). Do exactly the numbered steps below, in order, and nothing else. Run every command with the terminal tool in your workspace (your current directory), copied exactly as written, one numbered step per terminal call. Do not read other files and do not use any other skill.
@WAIT@
1. Check the promotion state:
```sh
"$AUTORESEARCH_CLI" promotion status
```

2. Submit the promoted candidate of experiment @EXPERIMENT@:
```sh
"$AUTORESEARCH_CLI" softmax submit @EXPERIMENT@
```
It prints one JSON object. Note its `ok` value and, when `ok` is false, its `error.reason`. If `ok` is true, set OUTCOME to done. If `ok` is false and `error.reason` is `identity`, set OUTCOME to blocked. For any other refusal, set OUTCOME to failed. Do not run step 2 again, and never edit strategy/STRATEGY.md yourself.

3. Stage one message. If OUTCOME is done, run:
```sh
"$AUTORESEARCH_CLI" comms stage highlight --title "Slice card @CARD@ promoted @EXPERIMENT@" --body "Slice card @CARD@ submitted the candidate of @EXPERIMENT@." --ref ar://experiment/@EXPERIMENT@ --ref ar://version/@VERSION@
```
Otherwise run this, with REASON replaced by the `error.reason` of step 2:
```sh
"$AUTORESEARCH_CLI" comms stage help_request --title "Slice card @CARD@ submit refused: REASON" --body "Slice card @CARD@ could not submit @EXPERIMENT@: the softmax tool refused with reason REASON." --ref ar://experiment/@EXPERIMENT@ --expects-response
```

4. Write the journal of this card, with OUTCOME replaced by the value from step 2, with RUN replaced by the number on the `- Run:` line at the end of your briefing and TASK replaced by the task id on its `- Task:` line:
```sh
mkdir -p .scratch && printf '{"iteration": RUN, "mode": "maintenance", "card": "TASK", "doctrine_version": @DOCTRINE@, "unit": "Protocol slice promotion card @CARD@.", "refs": ["ar://experiment/@EXPERIMENT@", "ar://version/@VERSION@"], "outcome": "OUTCOME"}\n' > .scratch/journal.json && printf 'Ran softmax submit for @EXPERIMENT@ and recorded the result.\n\n## Decisions\n\n- Follow the slice card exactly. Reason: it is a protocol test. Refs: ar://experiment/@EXPERIMENT@.\n' > .scratch/journal.md && "$AUTORESEARCH_CLI" records new journal --meta-file .scratch/journal.json --body-file .scratch/journal.md
```

5. Finish the card, with OUTCOME replaced as in step 4:
```sh
"$AUTORESEARCH_CLI" card finish --outcome OUTCOME
```
It prints one JSON object. On exit 0 its `data` is the handoff record.

6. Call the `kanban_complete` tool with the summary "Slice card @CARD@: OUTCOME." and with the `data` object printed by step 5 as `metadata`. Then stop.
