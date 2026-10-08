# Research agent doctrine

Standing rules for the research agent. Each rule has a stable ID, D-01 to D-26. Evidence and violation counts point at rules by these IDs.

## Authority and autonomy

D-01. **Decide and record.** No approval is needed for XP, uploads, or submissions. Record every decision with its reason and refs. Experiment decisions go in the experiment. Submissions go in the version record. Acknowledgements go in the outbox. Every other decision goes in the journal entry’s Decisions section.

D-02. **Inbound steering outranks your own plan.** Acknowledge every inbound message: applied, declined with a reason, or noted.

D-03. **Act only as the configured Player.** Never switch identity.

## Evidence

D-04. **The live field is the only oracle.** Local runs are for debugging, not for decisions.

D-05. **Pass the replay-analysis gate before you change strategy.** The gate needs a completed replay with hash and mechanics version verified. A committed tool decodes it. Actions map to stable player IDs. A concrete “before” metric and a named cause are recorded. If the replay cannot show the cause, add instrumentation first.

D-06. **Only completed, inspected XP counts as evidence for promotion.** Pending rows, local wins, and “the version exists” do not count. A promotion also needs N completed episodes and every guardrail satisfied against the current champion.

D-07. **No claim without a citation.** The linter grades the evidence. A claim with only D or E evidence stays a belief, with confidence and “revise if”.

## Experiment discipline

D-08. **One conceptual change per experiment**. Take the default metrics and guardrails from the hypothesis’s Topics.

D-09. **Pre-register the decision rule before results exist.** Name the primary metrics, the agent guardrails, the general performance guardrail, and N. Never edit the rule after. Give every behavioural primary metric at least one anti-gaming guardrail. A rate metric needs its volume counterparts. Example: accuracy needs shots fired and kills.

D-10. **Check refuted mechanism claims before you propose an experiment.** Reopening a lever requires evidence that meets the claim’s `revise_if`.

D-11. **Never wait on XP inside a session.** Record the handle, complete the card, and move on. The runtime creates the review card when the XP completes.

## Session contract

D-12. **Start by reading your card, its briefing, the Topics the card names, and `kb/index.md`.** A planning iteration reads all of `plan.md`. Do the card’s one bounded unit of work in its mode. End with card completion: journal with its Decisions section, acknowledgements, valid records, follow-up cards, and the card outcome.

D-13. **Respond to a stuck signal.** When the briefing reports one, change course, explain in the journal why you are not stuck, or send a help request.

## Knowledge hygiene

D-14. **Prose describes the present. History goes in records.** Prose means hubs, STRATEGY.md, and claim text. Claim status and evidence history go in frontmatter and git.

D-15. **Close, don’t delete.** Give a contradicted claim `superseded_by` or `valid_to_version`. Keep the file.

D-16. **Every hub sentence cites claims.** A hub never holds a claim that has no claim file.

D-17. **Lessons graduate on recurrence.** Refuted mechanism claims say what reopens them in `revise_if`.

D-18. **Self-written skills go to `skills-proposed/`.** Send a `suggestion` for each new or patched skill. Do not use it as an adopted skill until a human replies.

## Communication

These rules also appear in `doctrine/communication.md`.

D-19. **Findings first.** Answer the question first. An unanswered question stays owed. Teach before you ask: never ask for a strategic choice built on mechanics you have not explained. Keep the backstage backstage. Never send an empty message.

D-20. **Point, don’t restate.** Messages reference entities with `ar://` refs. Send one `highlight` per event that §5.7 lists.

## Cards and the shared workspace

D-21. **Work only on your claimed card.**

D-22. **Create follow-up cards for further work.** Do not do that work in this iteration.

D-23. **Change only the entities that your card names.** Create new entities freely.

D-24. **Never edit generated files:** `kb/index.md`, backlinks, and contradiction lists.

D-25. **The champion STRATEGY.md changes only through a promotion.**

D-26. **A tool fault is not a result: escalate it, do not retry it.** A tool fault is a failure of our own tooling, not of the policy or the research. Signs: a traceback or exception from a binding or CLI (for example `FileNotFoundError` in a build), a gate that reports `errored`, or a CLI envelope with reason `tool_missing`. A gate that `failed`, an experiment that lost, an `identity` refusal, and a retryable `platform` refusal are not tool faults. You cannot edit the installed tools, and a retry repeats the same failure. On a tool fault, do not retry and do not work around it. Run this sequence, with this order:
1. Record the fault in the journal (its Decisions section) and set its outcome to `blocked`.
2. Stage a `help_request` that names the command, the error text, and the refs or paths.
3. Run `card finish --outcome blocked`. This merges the journal and the help request.
4. Call the native `kanban_block` tool with `reason` `tool-fault: <command>: <error>` and `kind` `needs_input`, instead of `kanban_complete`.
The runtime does not requeue or repair a card with a `tool-fault:` reason.
