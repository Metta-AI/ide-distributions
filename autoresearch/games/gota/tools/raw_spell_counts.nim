## Independent action-tape count; does not re-simulate or import the expander.
## Replay API: Polyworld 5287c437 examples/gods_of_the_arena/replays.nim:24-25,119-130.
import std/[os, json, strutils, tables, sequtils]
import ../replays
import polyworld/metrics

let args = commandLineParams()
if args.len < 1:
  quit("usage: raw_spell_counts REPLAY [PREFIX_TICKS OUT_REPLAY]", 2)
var data = loadReplay(args[0])
var counts = initCountTable[int32]()
for action in data.actions:
  if action.kind in [ActionCastTarget, ActionCastPoint]:
    counts.inc action.heroId
var rows = newJArray()
for hero, count in counts:
  rows.add %*{"hero_id": hero, "submitted_casts": count}
echo $(%*{"heroes": rows, "actions": data.actions.len})
if args.len == 3:
  let ticks = parseInt(args[1])
  data.actions = data.actions.filterIt(int(it.tick) <= ticks)
  data.hashes = data.hashes[0 ..< ticks]
  data.metrics = ReplayMetrics()
  saveReplay(args[2], data)
