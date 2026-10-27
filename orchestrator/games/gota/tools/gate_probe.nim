## Local gate instrumentation against Polyworld 801749e5; no compiler reimplementation.
import std/[os, json, strutils]
include examples/gods_of_the_arena/bots

const
  PolyworldCommit {.strdefine.} = ""
  BassyCommit {.strdefine.} = ""

let output = paramStr(3)
var report = %*{"ok": false}
var compiling = true
proc toolError(error: ref CatchableError): JsonNode =
  %*{"ok": false, "outcome": "errored", "error": {
    "type": $error.name, "message": error.msg, "traceback": getStackTrace(error)}}
try:
  let source = readFile(paramStr(2))
  let structured = usesStructures(source)
  let compiledSource = if structured: StructureSource & "\n" & source else: source
  let limits = if structured: structureLimits(neuralLimits()) else: neuralLimits()
  let program = compile(compiledSource, initHeroHost(0), limits)
  compiling = false
  var cells = 0
  for i in 0 ..< program.arrays:
    cells += int(program.arrayExtent(int32(i))[1])
  report = %*{"ok": true, "outcome": "passed", "globals": program.globals,
    "arrays": program.arrays, "array_cells": cells,
    "routines": program.routines, "static_instructions": program.instructions,
    "source_bytes": source.len, "mode": (if structured: "structured" else: "legacy")}
  if paramStr(1) == "simulate":
    let seed = parseInt(paramStr(4))
    let maxTicks = parseInt(paramStr(5))
    let opponent = if paramCount() >= 6: paramStr(6) else: paramStr(2)
    let candidateBlue = paramCount() >= 7 and paramStr(7) == "blue"
    let game = newGame(generateMap(int32(seed)), 240, 10, false, ReplayData(), draftMode = OpenDraft)
    game.loadBots([
      BotGroup(count: 5, path: if candidateBlue: opponent else: paramStr(2)),
      BotGroup(count: 5, path: if candidateBlue: paramStr(2) else: opponent)])
    game.recorder = initReplayRecorder(Setup())
    game.recorder.data.config.maxTicks = int32(maxTicks)
    var events, bytes: array[10, int]
    var maxEvents, maxBytes: int
    var maxInstructions, maxWork: int64
    var decisions = 0
    proc printer(index: int): PrintProc =
      result = proc(event: PrintEvent) =
        inc events[index]
        case event.kind
        of TextPrint: bytes[index] += event.text.len
        of ValuePrint: bytes[index] += ($event.value).len
        of FixedPrint: bytes[index] += event.text.len
        of NewlinePrint: inc bytes[index]
    for slot in 0 ..< 10:
      game.heroVms[slot].output = printer(slot)
    while not game.world.gameOver and int(game.world.tick) < maxTicks:
      game.tickWorld(proc() =
        events = default(array[10, int])
        bytes = default(array[10, int])
        game.runBotDecisions()
        for slot, vm in game.heroVms:
          maxInstructions = max(maxInstructions, vm.lastInstructions)
          maxWork = max(maxWork, vm.lastWork)
          maxEvents = max(maxEvents, events[slot])
          maxBytes = max(maxBytes, bytes[slot])
      )
    var errors = newJArray()
    var seatDecisions = newJArray()
    var xp = newJArray()
    var scores = newJArray()
    for slot, vm in game.heroVms:
      decisions += vm.decisions
      seatDecisions.add %vm.decisions
      if vm.failed: errors.add %*{"slot": slot, "error": vm.lastError}
      xp.add %game.world.heroes[slot].totalXp
      scores.add %(if game.world.gameOver and not game.world.draw and
        game.world.heroes[slot].team == game.world.winner: 1 else: 0)
    report["runtime"] = %*{"instructions": maxInstructions, "work": maxWork,
      "print_events": maxEvents, "print_bytes": maxBytes,
      "decisions": decisions, "seat_decisions": seatDecisions,
      "errors": errors, "ticks": game.world.tick,
      "total_xp": xp, "scores": scores, "seed": seed,
      "candidate_blue": candidateBlue}
    report["ok"] = %(errors.len == 0 and decisions > 0)
    report["outcome"] = %(if report["ok"].getBool(): "passed" else: "failed")
except BasicError as error:
  if compiling:
    report = %*{"ok": false, "outcome": "failed", "error": {
      "type": $error.name, "message": error.msg}}
  else:
    report = toolError(error)
except CatchableError as error:
  report = toolError(error)
report["engine"] = %PolyworldCommit
report["bassy"] = %BassyCommit
writeFile(output, $report)
