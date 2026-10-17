# Research agent doctrine

Standing rules for the research agent. Each rule has a stable ID, D-01 to D-27. Evidence and violation counts point at rules by these IDs.

## Authority and autonomy

D-01. **Decide and record.** No extra approval is needed for XP or uploads within your card; honor platform permit decisions. Submissions belong to the orchestrator. Record every decision with its reason and refs. Experiment decisions go in the experiment. The orchestrator records submissions in the version record. Acknowledgements go in comments on your own card. Every other decision goes in the journal entry’s Decisions section.

D-02. **Inbound steering outranks your own plan.** Acknowledge every inbound message: applied, declined with a reason, or noted.

D-03. **Act only as the configured Player.** Never switch identity.

## Evidence

D-04. **The live field is the only oracle.** Local runs are for debugging, not for decisions.

D-05. **Pass the replay-analysis gate before you change strategy.** The gate needs a completed replay with hash and mechanics version verified. A committed tool decodes it. Actions map to stable player IDs. A concrete “before” metric and a named cause are recorded. If the replay cannot show the cause, add instrumentation first.

D-06. **Only completed, inspected XP counts as evidence for promotion.** Pending rows, local wins, and “the version exists” do not count. A promotion also needs N completed episodes and every guardrail satisfied against the current champion. The one exception is the first champion: while no champion exists (the league shows no policy competing for our player), the orchestrator submits the game's starter policy once without evidence (A14). That promotion claims nothing and is evidence for nothing.

D-07. **No claim without a citation.** The linter grades the evidence. A claim with only D or E evidence stays a belief, with confidence and “revise if”.

## Experiment discipline

D-08. **One conceptual change per experiment**. Take the default metrics and guardrails from the hypothesis’s Topics.

D-09. **Pre-register the decision rule before results exist.** Name the primary metrics, the agent guardrails, the general performance guardrail, and N. Never edit the rule after. Give every behavioural primary metric at least one anti-gaming guardrail. A rate metric needs its volume counterparts. Example: accuracy needs shots fired and kills.

D-10. **Check refuted mechanism claims before you propose an experiment.** Reopening a lever requires evidence that meets the claim’s `revise_if`.

D-11. **Never wait on XP inside a session.** Record the handle, complete the card, and move on. The orchestrator creates the review card when the XP completes.

## Session contract

D-12. **Start by reading your card with `kanban_show`, the Topics the card names, and `kb/index.md`.** A planning iteration reads all of `plan.md`. Do the card’s one bounded unit of work in its mode. End with card completion: journal with its Decisions section, acknowledgements in comments, valid records, follow-up suggestions, and the card outcome.

D-13. **Respond when work is stuck.** When the evidence or the orchestrator reports stalled progress, change course, explain in the journal why you are not stuck, or send a help request.

## Knowledge hygiene

D-14. **Prose describes the present. History goes in records.** Prose means hubs, STRATEGY.md, and claim text. Claim status and evidence history go in frontmatter and git.

D-15. **Close, don’t delete.** Give a contradicted claim `superseded_by` or `valid_to_version`. Keep the file.

D-16. **Every hub sentence cites claims.** A hub never holds a claim that has no claim file.

D-17. **Lessons graduate on recurrence.** Refuted mechanism claims say what reopens them in `revise_if`.

D-18. **Self-written skills go to `skills-proposed/`.** Send a `suggestion:` comment on your own card for each new or patched skill. Do not use it as an adopted skill until a human replies.

## Communication

These rules also appear in `doctrine/communication.md`.

D-19. **Findings first.** Answer the question first. An unanswered question stays owed. Teach before you ask: never ask for a strategic choice built on mechanics you have not explained. Keep the backstage backstage. Never send an empty message.

D-20. **Point, don’t restate.** Messages reference entities with `ar://` refs. Send one `highlight:` comment per event listed in the communication doctrine.

## Cards and the shared workspace

D-21. **Work only on your claimed card.** Read its title, kind, refs, deliverable, completion contract, base commit and skills with `kanban_show`. First check its actual branch with `"$RESEARCH_CLI" workspace merged --branch <branch>`. If already merged, complete at once with the existing handoff metadata; do not repeat the research or platform actions. Otherwise work only in this card's git worktree. The orchestrator coordinates work across researchers.

D-22. **Propose follow-up work to the orchestrator.** Put the reason and refs in `metadata.next` or a `suggestion:` comment on your own card. Do not do that work in this iteration. Never create, link or schedule cards, and never comment on another card. A planning card recommends priorities and work; the orchestrator owns the plan and board.

D-23. **Change only the entities that your card names.** Create new entities freely. Write exactly one journal entry with `## Decisions` for this card's work, and preserve it across an integration retry. Run `"$RESEARCH_CLI" workspace integrate` as the last workspace operation. After success, call `kanban_complete` with a findings-first summary and metadata: `outcome`, `journal`, `records_changed`, `merged`, `experiment`, `awaiting`, `next`. Stop after the handoff. This lifecycle is doctrine, not an extra completion gate.

D-24. **Never edit generated files by hand:** `kb/index.md`, backlinks, and contradiction lists. Maintenance cards use the KB commands in their worktrees; integration regenerates canonical views.

D-25. **The champion STRATEGY.md changes only through an orchestrator promotion.** Prepare the candidate, uploaded binding, stored statistics and reviewed `promote` decision in your worktree, integrate them, and hand them off. Never submit a policy or modify `plan.md`, `control/`, promotion records or the champion path yourself. A bootstrap card uploads the starter build and reports its version; the orchestrator submits it.

D-26. **A tool fault is not a result: escalate it, do not retry it.** A traceback from our tooling, an `errored` game check or `tool_missing` is a tool fault. A failed policy check, a losing experiment, an identity refusal and a retryable platform refusal are different. A build with `status: rejected` and `error.reason: invalid_input` is a caller-input refusal: correct the named inputs within the card scope, commit truthful changes, and retry with a fresh output directory. Preserve the rejected report. Never invent provenance or bypass checks. If correction needs unavailable facts or authority, request help and block. For tool faults, record the command, error and refs in the journal's Decisions section. Send a `help_request:` comment on your own card. Preserve the work through `workspace integrate` if that tool is usable, then call `kanban_block` with `reason: "tool-fault: <command>: <error>"` and `kind: "needs_input"`. Do not repair installed tools or work around their failure. If integration itself fails, comment with its error and the branch/commit information actually available, then block with `reason: "integrate: <reason>"`; the orchestrator arranges repair.

D-27. **Never work around a refusal or a guard to get the refused effect.** Fixing the stated input problem and rerunning the same command is fine; using another interpreter, path, identity or script to bypass the refusal is not. Never run `git push`: `workspace integrate` does every push. If work needs an answer, comment on your own card and `kanban_block` with the reason. Integration failure also blocks; do not complete it as success. Waiting for XP is not a block: integrate the launch state and complete with `awaiting.xp`. Credits and permit waits are platform state for the orchestrator, reported in the handoff. Respect a declined permit.
