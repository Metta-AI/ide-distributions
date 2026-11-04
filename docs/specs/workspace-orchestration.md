# Workspace transition ownership

One coordinator owns execution admission, outcome advancement, assignments, continuations and
retries. The Orchestrator chooses research work and reviews results. Workers perform assigned
work and report results through the supported tools.

## This layer

- Record immutable native input, key, authentication scope and replay horizon before transport.
  A missing acceptance reply retains the same operation identity.
- Route client inputs, worker starts and model wakes through one dispatcher. Normal completion
  and reconciliation call the same outcome transition.
- Keep settled attempts until their domain handoffs are acknowledged. Stable event, predecessor
  and source-message identities make handoff replay idempotent.
- Keep reservations while execution is uncertain. Terminal receipts or trusted replacement of
  the private runtime namespace prove exclusion; lease age does not.
- Preserve Stop, idle sleep and wake. The existing image supervisor replaces runtimes carrying
  untracked execution obligations; the next boot reconciles those obligations.
- Separate slow workflow and recovery observations from HTTP intake. A watcher fault does not
  stop chat intake. Pause closes the shared admission gate.

## Stack sequence

1. Immutable admission ledger and schema.
2. Single coordinator and runtime integration, replacing independent watcher schedulers.
3. Strict execution fencing, distinct retry identities and bounded recovery verification.
4. Durable worker/coordinator command inbox, operation receipts and commit wakeups.
5. Packaged acceptance proof and the complete transition contract.

Tests belong to the layer introducing their behavior. Every intermediate branch must pass its
applicable suite. The complete tip preserves the original runtime and configuration; this split
changes how the work is reviewed.

Merging an entry triggers the normal hosted image release. Stack position is not merge or
deployment approval. Native descendant containment, lossless child-result delivery and arbitrary
remote-effect guarantees remain outside this execution-admission contract.
