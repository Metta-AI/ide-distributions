---
confidence: 1
evidence:
- direct: true
  ref: examples/pudge_wars/fileplayers.nim:144-152@cd8111a5ee4f2fdc38c027615b33421f6c7d2536
  source_group: Metta-AI/polyworld-pudge-wars@cd8111a5ee4f2fdc38c027615b33421f6c7d2536
  stance: supports
  tier: S
evidence_grade: high
id: C-01M5X3RR2TGGCBGV8FR6GRH3D7
kind: mechanism
last_checked_version: 0.1.8
revise_if: The cited source changes at a new game source commit (kb renew reports
  it).
status: verified
topics: []
---

A policy that fails to load (size, package or compile error) writes a player failure naming the failed policy index instead of results; the episode does not run.
