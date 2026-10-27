# Provenance: {{GAME_TITLE}}

Made by `research game new` on {{DATE}} from the researcher distribution's
`games/_template/` (adapted from optimizer-seed `cfa0cb335809e67490921d07b89be6d2afcd5833`,
`games/_template/` and `tools/add_game.sh`).

| | |
|---|---|
| **League** | `{{LEAGUE_ID}}` |
| **Game source** | {{SOURCE_ROW}} |
| **Mixin** | {{MIXIN_ROW}} |

A vendored mixin (`mixin/`) is a copy expected to drift from upstream; there is no
automatic sync. To see what changed upstream, fetch it again with `"$RESEARCH_CLI" source
fetch` and compare the snapshot with `mixin/`.
