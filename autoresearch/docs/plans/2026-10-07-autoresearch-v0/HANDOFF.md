# Handoff prompt: implement autoresearch v0

Paste everything below the line into a new Claude Code session started in `~/coding/ide-mvp`.

---

You are implementing **autoresearch v0**: an autonomous research agent for the Softmax game Gods of the Arena (GotA), packaged as a Hermes Agent profile distribution. The design is finished and approved; the implementation plan is written and was reviewed with Codex over six rounds to a GO verdict. Your job is to **execute the plan**, not to redesign it.

## Read first, in this order
1. `autoresearch/docs/plans/2026-10-07-autoresearch-v0/00-overview.md` — binding cross-cutting contracts C1–C12, the dependency graph, and the decisions section. Every task inherits these.
2. `autoresearch/docs/plans/2026-10-07-autoresearch-v0/01-foundation-and-vertical-slice.md` — the critical path.
3. `02-gota-and-compiler.md` and `03-research-completion-and-hardening.md` in the same folder.
4. Specs, only when a task cites them: `autoresearch/docs/designs/autoresearch-system-design-2026-10-06.html` (approved; authority for research behaviour), `autoresearch/docs/designs/autoresearch-runtime-2026-10-06.html` (runtime), `autoresearch/docs/designs/autoresearch-open-questions-2026-10-07.html`. They are HTML; read the text with `python3 -c "import re,html,sys;print(html.unescape(re.sub(r'<[^>]+>',' ',open(sys.argv[1]).read())))" FILE`.
5. `autoresearch/docs/plans/findings/player-xp-probe.md` (Task 0.1 is already done: PASS).

## How to execute
- Use **superpowers:subagent-driven-development**: a fresh subagent implements each task with TDD, and a fresh reviewer checks it before the next task starts. Follow each task's steps and tests exactly; the tests in the plan are the acceptance criteria.
- Order: Plan 01 Milestone 0 (Task 0.2 Hermes facts) and Plan 02 Task 0.1 (globals budget) first and in parallel; then Plan 01 Milestones 1 → 5. Plan 02 Milestones 1–2 and Plan 03 Milestones 1–4 run in parallel tracks per the dependency graph in `00-overview.md`. Plan 03 Milestones 5–6 come last.
- **Any Task 0.2 FAIL changes a contract**: write the amendment into `00-overview.md` and get James's OK before Plan 01 Milestone 3. Do not work around a failed Hermes fact silently.
- When the plan and the code disagree with reality (Hermes behaviour, CLI output), stop and report with evidence; do not invent behaviour.

## Decisions already made (do not reopen)
- **Goal: end of day Wed Oct 7** for the Kanban protocol slice (Plan 01 Task 5.1) and the compiler M1 baseline. If the Kanban slice has not passed by then, build the serial fallback (Plan 01 Task 5.2). **There is no BASIC fallback for the compiler**; the semantic↔policy compiler with goal tracing is the only build path.
- **Models (open-weight only):** primary `moonshotai/kimi-k3`, fallback `z-ai/glm-5.3`, tests and protocol slice `z-ai/glm-5.3-flash`, all through OpenRouter.
- **GotA league:** `league_3c60897b-25cf-4b37-9d1a-8554c1198f28`. League score "Emmett's Glory" (winning heroes' lifetime XP per simulated minute; 0 on loss/draw/timeout). Performance guardrails: `glory` and `win_rate`. Live coworld version 2026.10.6.1.
- **Credentials:** one Player credential per install; a Player-only token can create/read/download XP (verified). Use player **Games Bond** (`ply_f39295d0-ac38-4a62-ac4f-e1d62d7dc9d1`) for any live test so James Botts's league entry is untouched. For live probes, isolate credentials in a temporary `HOME` (copy only the user token) so `~/.softmax/credentials.yaml` is never changed; delete the temp copy afterwards. Get service credentials (OpenRouter key etc.) from the metta token broker (`python3 ~/coding/metta/scripts/token_broker_client.py scopes`, then `exec --scope … --reason "…" -- <cmd>`), not from Vault or ambient AWS.
- **Versions:** Hermes v0.21.5 via `uv tool install git+https://github.com/NousResearch/hermes-agent@v2026.9.24` (PyPI lags). coworld 0.1.57 and softmax-cli 0.26.38 pinned in the project; the global uv-tool coworld is stale (0.1.38), so never rely on it.

## Repository and git state
- Repo `~/coding/ide-mvp` (GitHub `Metta-AI/ide-mvp`, private; **use `gh`, not Graphite** for this repo).
- Open PRs: #1 (System Design, approved doc) ← #2 (Runtime doc + open questions; base = #1's branch). The plan lives on local branch `jboggs/autoresearch-v0-plan` (stacked on #2's branch, **not pushed yet**). Start implementation on a new branch from `jboggs/autoresearch-v0-plan`.
- `autoresearch/` is the root of the Hermes profile distribution; never put files for it outside `autoresearch/`.
- Commit at each task's commit step. Do not push, open PRs, or merge unless James asks. Run the `audit-documentation` skill before commits that change behaviour or docs.

## Useful references on disk (read-only)
- Hermes source/docs clone (commit 9ab4a4f): `/private/tmp/claude-501/-Users-jamesboggs-coding-ide-mvp/442c7dde-de5e-4a1a-9515-260d86172672/scratchpad/hermes-agent` — if missing, re-clone `NousResearch/hermes-agent` and check out tag `v2026.9.24`.
- Prior work: optimizer-seed `~/coding/optim-compare/seeds/optimizer-seed`; co-gas (re-clone `Metta-AI/co-gas`; the local copy under optim-compare is stale); GotA lab `~/coding/personal_labs/personal_labs_gota` (branch gota-finish-timing); Paintbot PW compiler `~/coding/personal_labs/personal_labs_paintbot_pw/paintbot_pw_lab` (has uncommitted edits — ask James to commit them or pin the last clean commit before vendoring, Plan 02 Task 2.1).
- `~/coding/metta` is James's reference checkout: `git pull` it before reading; never modify it.

## Reporting
Report progress to James at each milestone boundary: what passed (with test output), what changed in the plan and why, and any decision needed. At the end of the session give the codebase-friction rating required by James's CLAUDE.md.
