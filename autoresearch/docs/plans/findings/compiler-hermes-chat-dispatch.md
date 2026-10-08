# M1: Hermes CLI dispatch correction

The first C3-authorized flash compile on `afc8ab0` failed before generation.
Immutable failed build `afc8ab06-1` records three built-in attempts, each rejected
by argparse: top-level `-z/--oneshot` requires a prompt argument. No units were
produced, no gates ran, and M1 remains unqualified.

Installed Hermes is the sanctioned editable v0.21.5, source
`f97608f178d1ffeca59860195ab7da295f7c8e5f`, matching the existing Hermes facts.
Its `hermes_cli/_parser.py` defines `--query-file`, boolean `--oneshot` and
`--max-turns` on `chat`; `--usage-file` is top-level. The compiler now uses
`hermes --usage-file PATH chat --oneshot --query-file PATH -t file --max-turns 40
--model MODEL --provider openrouter`. Model selection, scratch isolation, input
identity, outputs, write guards and usage validation remain enforced.

The contained fake regression now requires this exact parser boundary. It first
failed with the previous dispatch, then passes with the corrected form. Real
`hermes chat --help` confirms those flags without a model call. The backend guide
is the owned operational reference; the old Plan 02 dispatch example is stale.

Evidence is `/tmp/ide-mvp-c3-real-m1-20261008-101446/` and
`/tmp/ide-mvp-c3-hermes-chat-help.txt`. Dedicated-key numerical usage before and
after the failed compile was identically `$0.002887121`, remaining
`$99.997112879`; the conservative delta from the parent's earlier shared-key
baseline is `$0.000781132`. No key was printed or stored by this task. No upload,
hosted XP, sibling edits or Task 2.5 action occurred.
