---
name: writing-claims
description: Use before writing, superseding or refuting a claim.
---

# Writing claims

A claim is one finding with typed evidence. Everything you learn that someone will act on later (a measured effect, a cause seen in replays, a refuted lever, a user's preference, the state of the League) is a claim; a plan, a hope or a todo is not. Freshness, contested state and counts are computed from the evidence on every read, so you record only what you decided: text, tags, evidence and stance.

## Search first

`claim.search {query, tags?, include_retired: true}` before every write. A match means one of:

- the same finding: `claim.add_evidence {claim, type, ref, stance, note}`; a claim's text, tags and links do not change once written, so a changed finding is a new claim that supersedes it;
- a finding this one replaces: write the new claim, then `claim.retire {claim: C-old, status: superseded, superseded_by: C-new}`;
- a finding this one contradicts: write yours with the contradicting evidence; the pair shows as contested until one is refuted.

## Write

`claim.write {summary, body, tags, evidence, see_also?}`:

- `summary`: at most 40 words, one sentence, the finding with its number and condition ("V-7 wins 58% against the champion over 40 paired seeds (CI 52–64), at the cost of 9% more timeouts").
- `body`: at most 500 words. Detail, numbers with their n and comparison, the conditions, what you did not observe, and a closing paragraph "what would change our mind": the evidence that reopens or retires this finding. Negative and inconclusive results get the same care; they stop the next person repeating the work.
- `tags`: words from the shared vocabulary and the records it belongs to: the Goal (`G-n`), the lever, the League or policy version id, the game version, and a kind such as `mechanism`, `opponent`, `standings`, `noise`, `preference`, `lesson`. The result carries `new_tags`: for each tag no claim uses yet, the nearest existing tags. Reuse the existing one unless yours means something different; single-use tags are listed as housekeeping.
- `evidence`: at least one link `{type, ref, stance, note}`; `stance` is `supports`, `contradicts` or `qualifies`. Every reference is validated, and a record it names must exist.

## Evidence types and reference formats

| type | ref |
|---|---|
| `code` | `<path>:<a>-<b>@<sha>`: lines in the code repository at a commit; the inspector says whether they changed since |
| `doc` | `<repo-or-url>#<section>`, or `<url> (retrieved <date>): <quote>` |
| `transcript` | `session:<id>/<message id>#<sha256 of the quote>` |
| `measurement` | `<command> @<sha> -> <stored output path or pin id>`, the output kept under `documents/` |
| `experiment`, `version`, `claim` | the record id: `X-5`, `V-7`, `C-12` |
| `replay` | `episode:<ereq id>[/seat <n>][/phase <p>] xp:<request id>` (`xp:none` outside an XP request); `platform.replay` pins the file |
| `annotation` | `annotation:<id>`, a person's note on a replay |

A mixin may register game types; its skills say how to cite them. `claim.inspect_evidence {claim}` shows what each link says now; use it before you lean on an old claim.

## Retire, never delete

`claim.retire {claim, status: refuted, type, ref, note}` with the killing evidence, or `status: superseded` with `superseded_by`. Retired claims stay and stay findable (search ranks them last), so the history of a lever is readable. Nothing removes a claim.

## Preferences and lessons

A user's preference about how we work becomes a claim tagged `preference` (the Orchestrator also records the setting). A lesson from a failed approach is a claim tagged `lesson` whose body says what to do instead. Imported seed material carries the tag `imported` and `doc` evidence.
