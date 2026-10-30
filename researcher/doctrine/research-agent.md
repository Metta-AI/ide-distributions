# Research agent doctrine

Standing rules for the research agent. Each rule has a stable ID, D-01 to D-27. Evidence and violation counts point at rules by these IDs.

## Authority and autonomy

D-01. **Decide and record.** Launch assigned XP within your card’s scope; honor platform permit decisions. Uploads, integration and submissions belong to the coordinator. Record every decision with its reason and refs. Experiment decisions go in the experiment. The orchestrator records submissions in the version record. Acknowledgements go in comments on your own card. Include other decisions in the native task summary; journals are optional.

D-02. **Inbound steering outranks your own plan.** Acknowledge every inbound message: applied, declined with a reason, or noted.

D-03. **Act only as the configured Player.** Never switch identity.

## Evidence

D-04. **The live field is the only oracle.** Local runs are for debugging, not for decisions.

D-05. **Pass the replay-analysis gate before you change strategy.** The gate needs a completed replay with hash and mechanics version verified. A committed tool decodes it. Actions map to stable player IDs. A concrete “before” metric and a named cause are recorded. If the replay cannot show the cause, add instrumentation first.

D-06. **Only completed, inspected XP counts as evidence for a champion submission.** Pending rows, local wins, and “the version exists” do not count. A submission also needs N completed episodes and every guardrail satisfied against the current champion. The one exception is the first champion: while no champion exists (the league shows no policy competing for our player), the orchestrator submits the game's starter policy once without evidence (A14). That submission claims nothing and is evidence for nothing.

D-07. **No claim without a citation.** The linter grades the evidence. A claim with only D or E evidence stays a belief, with confidence and “revise if”.

## Experiment discipline

D-08. **One conceptual change per experiment**. Take the default metrics and guardrails from the hypothesis’s Topics.

D-09. **Pre-register the decision rule before results exist.** Name the primary metrics, the agent guardrails, the general performance guardrail, and N. Never edit the rule after. Give every behavioural primary metric at least one anti-gaming guardrail. A rate metric needs its volume counterparts. Example: accuracy needs shots fired and kills.

D-10. **Check refuted mechanism claims before you propose an experiment.** Reopening a lever requires evidence that meets the claim’s `revise_if`.

D-11. **Preserve pending XP and follow the assignment’s acceptance criteria.** A launch-only assignment completes only when every requested launch was admitted, with all request IDs and pending-result status. If results or missing launches are required, block with the exact request IDs. Never poll in the session or complete unfinished work merely to wait. The coordinator decides follow-up work.

## Session contract

D-12. **Start by reading your card with `kanban_show`, the Topics the card names, and `kb/index.md`.** A planning iteration reads all of `plan.md`. Do the card’s one bounded unit of work in its mode. End with native completion and artifact references, or block with a precise question.

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

D-21. **Work only on your claimed native task.** Read current instructions and artifacts. An integrated commit never implies task completion. Resume requested work in the assigned worktree; the coordinator owns acceptance and integration.

D-22. **Propose follow-up work to the orchestrator.** Put the reason and refs in `metadata.next` or a `suggestion:` comment on your own card. Do not do that work in this iteration. Never create, link or schedule cards, and never comment on another card. A planning card recommends priorities and work; the orchestrator owns the plan and board.

D-23. **Preserve outputs, then report native assignment completion.** Use `workspace stage` to commit and push the task branch. Return a factual summary and artifact references. A journal is optional evidence, never a prerequisite for blocking or completion. The coordinator accepts and integrates results.

D-24. **Never edit generated files by hand:** `kb/index.md`, backlinks, and contradiction lists. Maintenance cards use the KB commands in their worktrees; integration regenerates canonical views.

D-25. **The coordinator owns integration and submission.** Prepare the requested artifact and evidence. Explicit build/upload work does not require statistical evidence. Use reviewed submission evidence only for an assigned superiority evaluation.

D-26. **Keep going when a tool fails.** A failing tool is an obstacle, not a stop. Retry once if the failure looks transient. Otherwise fix the tool or reach the same result another reasonable way: your profile's tooling (`$HERMES_HOME`: `src`, `scripts`, `games`, `skills`) is yours to edit, so fix the tool there and commit the fix with a message saying why; or use another documented option or command, an interpreter or environment you make on the workspace disk, or the step done by hand with the CLI. Prefer the way that keeps the same inputs and engine version. Report what failed (exact command, error, affected refs), what you did instead and how it could change the result, so the tool gets fixed. Correct invalid inputs without bypassing checks and preserve failed reports. Send a `help_request:` and use `kanban_block` only when no reasonable way remains or every remaining way would cross D-27.

D-27. **Respect refusals and budget ceilings.** A crashing or broken tool is not a refusal (D-26). A denied operation is: a refused approval, permit, credit or budget, a user's no, or an access error. Do not bypass it through another interpreter, identity, credential or command. Preserve pending request IDs and block on necessary credit, permit or experiment waits. If assigned only to launch requests, completion requires admission evidence for every requested launch. A partial batch blocked by credit or permit refusals must block, even when its admitted requests are pending.
