## Source: personal_labs_gota@09d3ed28:gods_of_the_arena_lab/tools/gota_match.nim
## Pinned local instrumentation. Include the real host to share its private limits,
## schema, and binding code; never duplicate the policy API or simulation.
include ../bots
import std/[json, os, strutils, importutils, monotimes, times, macros, tables, algorithm, sha1]
import ../events
import zippy

privateAccess(Program)
privateAccess(Runtime)
privateAccess(Host)

const
  PolyworldCommit {.strdefine.} = ""
  BassyCommit {.strdefine.} = ""

const Manifest = staticRead("../../../coworld/gota/coworld_manifest_template.json")

proc limitsJson(limits: Limits): JsonNode =
  result = %*{
    "globals": limits.maxGlobals, "arrays": limits.maxArrays,
    "array_cells": limits.maxArrayElements, "routines": limits.maxRoutines,
    "code_instructions": limits.maxCodeInstructions,
    "registers": limits.maxRegisters, "source_bytes": limits.maxSourceBytes,
    "instructions": limits.maxInstructions, "work": limits.maxWorkUnits,
    "parameters": limits.maxParameters,
    "instructions_per_decision": limits.maxInstructions,
    "work_per_decision": limits.maxWorkUnits,
    "print_bytes": limits.maxPrintBytes, "print_events": limits.maxPrintEvents
  }
  result["raw"] = %limits

proc relaxedLimits(limits: Limits): Limits =
  result = limits
  result.maxGlobals = 1_000_000
  result.maxArrays = 100_000
  result.maxArrayElements = 10_000_000
  result.maxRoutines = 100_000
  result.maxParameters = 1024
  result.maxRegisters = 100_000
  result.maxCodeInstructions = 10_000_000
  result.maxSourceBytes = 16 * 1024 * 1024
  result.maxSyntaxDepth = 64
  result.maxMemoryBytes = 1024 * 1024 * 1024

proc usage(program: Program, sourceBytes: int): JsonNode =
  %*{"globals": program.globalNames.len, "arrays": program.arrays.len,
    "array_cells": program.arrayCells, "routines": program.routines.len,
    "code_instructions": program.instructions,
    "registers": program.maxRegisterCount, "source_bytes": sourceBytes}

proc structureUsage(program: Program): JsonNode =
  var globals, arrays, cells: int
  for name in program.globalNames:
    if '.' in name: inc globals
  for array in program.arrays:
    privateAccess(typeof(array))
    if '.' in array.name:
      inc arrays
      cells += array.length
  %*{"globals": globals, "arrays": arrays, "array_cells": cells}

proc statsSource(policySource: string): JsonNode =
  let structured = usesStructures(policySource)
  let source = if structured: StructureSource & "\n" & policySource else: policySource
  let limits = if structured: structureLimits(neuralLimits()) else: neuralLimits()
  let host = initHeroHost(0)
  var strictError = ""
  try:
    discard compile(source, host, limits)
  except BasicError as error:
    strictError = error.msg
  let program = compile(source, host, relaxedLimits(limits))
  var arrays = newJArray()
  var routines = newJArray()
  var routineMetrics = newJArray()
  for array in program.arrays:
    privateAccess(typeof(array))
    arrays.add %*{"name": array.name, "cells": array.length}
  for routine in program.routines:
    privateAccess(typeof(routine))
    routines.add %routine.name
    routineMetrics.add %*{"name": routine.name,
      "code_instructions": routine.codeLength, "registers": routine.registerCount}
  result = %*{"mode": (if structured: "structured" else: "legacy"),
    "limits": limitsJson(limits), "used": usage(program, source.len),
    "globals": program.globalNames, "arrays": arrays, "routines": routines,
    "routine_metrics": routineMetrics,
    "strict_ok": strictError.len == 0, "strict_error": strictError,
    "policy_source_bytes": policySource.len,
    "host_source_bytes": (if structured: StructureSource.len + 1 else: 0)}
  let empty = compile(StructureSource & "\nend\n", host,
    relaxedLimits(structureLimits(neuralLimits())))
  let referenced = compile(StructureSource &
    "\nprint self.hp, self.position.x, objects(0).hp, abilities(0).level\n", host,
    relaxedLimits(structureLimits(neuralLimits())))
  result["structured_storage"] = %*{
    "empty": structureUsage(empty), "referenced": structureUsage(referenced),
    "probe": "PRINT self.hp, self.position.x, objects(0).hp, abilities(0).level"}

macro literals(source: static[string]): untyped =
  ## Recover declared spelling only: the live registry remains the API authority.
  var values = newNimNode(nnkBracket)
  proc visit(node: NimNode) =
    if node.kind in {nnkStrLit, nnkTripleStrLit, nnkRStrLit}:
      values.add newLit(node.strVal)
    for child in node: visit(child)
  visit(parseStmt(source))
  result = values

proc sourceNames(): Table[string, string] =
  for name in literals(staticRead("../bots.nim")):
    result[name.toLowerAscii] = name
  for name in literals(staticRead("../../../src/polyworld/llms.nim")):
    result[name.toLowerAscii] = name
  for name in literals(staticRead("../../../src/polyworld/tensors.nim")):
    result[name.toLowerAscii] = name
  for name in HeroDataNames: result[name.toLowerAscii] = name
  for value in HeroClass: result[($value).toLowerAscii] = $value
  for value in ActionError: result[($value).toLowerAscii] = $value
  for value in TerrainKind: result[($value).toLowerAscii] = $value
  # The host registers terrain queries as name & "At".
  var terrainNames: seq[string]
  for key, name in result:
    if key.startsWith("terrain"): terrainNames.add name
  for name in terrainNames: result[(name & "At").toLowerAscii] = name & "At"
  # BASIC stores names case-insensitively; obtain field spelling from its schema.
  for line in StructureSource.splitLines:
    let words = line.strip.splitWhitespace
    if words.len >= 3 and words[1].toUpperAscii == "AS":
      result[words[0].toLowerAscii] = words[0]
    if words.len >= 4 and words[0].toUpperAscii == "DIM":
      let name = words[1].split('(')[0]
      result[name.toLowerAscii] = name

proc hostSurface(): JsonNode =
  let host = initHeroHost(0)
  let names = sourceNames()
  var data = newJArray()
  for name in host.dataNames: data.add %names.getOrDefault(name, name)
  var functions = newJArray()
  for fn in host.functions:
    privateAccess(typeof(fn))
    functions.add %*{"name": names.getOrDefault(fn.name, fn.name), "arguments": fn.parameters,
      "work": fn.workUnits}
  let program = compile(StructureSource & "\nend\n", host,
    relaxedLimits(structureLimits(neuralLimits())))
  var records = newJObject()
  proc addField(name: string, array: bool, cells: int) =
    var pieces: seq[string]
    for part in name.split('.'): pieces.add names.getOrDefault(part, part)
    let parts = pieces.join(".").split('.', 1)
    if parts.len != 2: return
    if not records.hasKey(parts[0]):
      records[parts[0]] = %*{"array": array, "cells": cells, "fields": []}
    records[parts[0]]["fields"].add %parts[1]
  for name in program.globalNames: addField(name, false, 1)
  for array in program.arrays:
    privateAccess(typeof(array))
    addField(array.name, true, int(array.length))
  %*{"host_data": data, "host_functions": functions, "records": records,
    "limits": limitsJson(structureLimits(neuralLimits())),
    "baseline": statsSource("' @gota-structures"),
    "engine": {"polyworld_commit": PolyworldCommit, "bassy_commit": BassyCommit}}

proc printer(file: File): PrintProc =
  result = proc(event: PrintEvent) =
    case event.kind
    of TextPrint: file.write(event.text)
    of ValuePrint: file.write($event.value)
    of FixedPrint: file.write($event.fixedValue)
    of NewlinePrint: file.write("\n")

var
  submitted: array[10, seq[JsonNode]]
  decisionSeats: seq[int]
  decisionRespawn: array[10, int32]
  decisionState: array[10, JsonNode]
  decisionPhase: string

proc decisionFrame(game: Game, policyDraft = true) =
  decisionSeats.setLen(0)
  decisionPhase = $game.world.phase
  if game.world.phase == Drafting:
    if policyDraft: decisionSeats.add game.world.heroIndex(game.world.draftHeroId())
  else:
    for offset in 0 ..< game.world.heroes.len:
      decisionSeats.add (game.world.heroTurnStart + offset) mod game.world.heroes.len
  for seat in decisionSeats:
    let hero = game.world.heroes[seat]
    decisionRespawn[seat] = hero.respawnTicks()
    decisionState[seat] = %*{"hp": hero.hp,
      "position": {"x": (worldToTiles(hero.position.x, WorldScale) + fixed(int32(mapTiles() div 2)) - FixedHalf).toFloat64,
        "y": (worldToTiles(hero.position.z, WorldScale) + fixed(int32(mapTiles() div 2)) - FixedHalf).toFloat64}}



proc captureActions(host: var Host, game: Game, seat: int) =
  ## The real callback records each action before applying it. Its return value
  ## is acceptance. Wrapping it preserves argument order and repeated commands.
  proc capture(first: int, accepted: bool) =
    for i in first ..< game.recorder.data.actions.len:
      let action = game.recorder.data.actions[i]
      submitted[seat].add %*{"kind": action.kind, "slot": action.slot,
        "first": action.first, "second": action.second,
        "offset_x": int32(action.offset.x), "offset_y": int32(action.offset.y),
        "accepted": accepted}
  proc integerWrapper(original: HostProc): HostProc =
    result = proc(args: openArray[int32]): int32 =
      let first = game.recorder.data.actions.len
      result = original(args)
      capture(first, result != 0)
  proc numericWrapper(original: NumericHostProc): NumericHostProc =
    result = proc(args: openArray[Value]): Value =
      let first = game.recorder.data.actions.len
      result = original(args)
      capture(first, result.asInt != 0)
  for fn in host.functions.mitems:
    privateAccess(typeof(fn))
    privateAccess(typeof(fn.callback))
    # Only commands append to the action tape. Queries may return fixed values,
    # so do not coerce their results to integers when no action was recorded.
    if fn.name in ["walkto", "attackmove", "attacktarget", "buyitem", "buyback",
        "useitem", "useitemat", "levelability", "casttarget", "castpoint", "drafthero"]:
      if fn.callback.integer != nil:
        fn.callback.integer = integerWrapper(fn.callback.integer)
      elif fn.callback.numeric != nil:
        fn.callback.numeric = numericWrapper(fn.callback.numeric)

var loadedSourceHashes: seq[string]

proc loadSeats(game: Game, paths: seq[string], logs: seq[File], tables: bool) =
  ## Same binding as loadBots, with compile failures isolated to their seat.
  activeGame = game
  loadedSourceHashes = newSeq[string](10)
  game.heroVms = newSeq[HeroVm](10)
  game.inboxes.setLen(10)
  for inbox in game.inboxes.mitems: inbox = newMailbox()
  let schema = initHeroHost(0)
  for seat, path in paths:
    try:
      let policy = loadPolicy(readPolicyBytes(path))
      loadedSourceHashes[seat] = $secureHash(policy.source)
      let structured = usesStructures(policy.source)
      let source = if structured: StructureSource & "\n" & policy.source else: policy.source
      let limits = if structured: structureLimits(neuralLimits()) else: neuralLimits()
      let program = compile(source, schema, limits)
      bindHeroData(program)
      let llm = newLlmClient(seat)
      var host = initHeroHost(game.world.heroes[seat].id, policy, llm, seat)
      if tables: captureActions(host, game, seat)
      let vm = HeroVm(structured: structured,
        legacyHeroData: not structured or program.usesHeroData(),
        runtime: initRuntime(program, host, limits), limits: limits,
        prepareDecision: llm.decisionCallback(), pollRequests: llm.requestPoller(),
        ready: true, output: printer(logs[seat]))
      game.heroVms[seat] = vm
      game.structuredBots = game.structuredBots or structured
      vm.bindStructures(program, game.world, game.world.heroes[seat].id)
      llm.bindRuntime(vm.runtime)
    except CatchableError as error:
      game.heroVms[seat] = HeroVm(failed: true, lastError: error.msg)
      logs[seat].write("\nBASIC error: " & error.msg & "\n")

proc failureLocation(vm: HeroVm): JsonNode =
  ## The failed runtime is never restarted; preserve Bassy's routine and bytecode PC.
  result = %*{"vm_routine": newJNull(), "vm_pc": newJNull()}
  if vm != nil and vm.failed and vm.runtime != nil:
    let routine = vm.runtime.program.routines[int(vm.runtime.routine)]
    privateAccess(typeof(routine))
    result["vm_routine"] = %routine.name
    result["vm_pc"] = %vm.runtime.offset

proc tableRow(game: Game): JsonNode =
  let world = game.world
  var heroes = newJArray()
  for slot, hero in world.heroes:
    let location = failureLocation(if slot < game.heroVms.len: game.heroVms[slot] else: nil)
    var accepted = newJArray()
    for command in submitted[slot]:
      if command["accepted"].getBool: accepted.add command
    heroes.add %*{"slot": slot, "hero_id": hero.id, "team": hero.team.ord,
      "class": hero.class.ord, "alive": hero.hp > 0 and hero.state != Dying,
      "respawn_ticks": hero.respawnTicks(),
      "vm_routine": location["vm_routine"], "vm_pc": location["vm_pc"],
      "decision_state": (if slot in decisionSeats: decisionState[slot] else: newJNull()),
      "decision_respawn_ticks": (if slot in decisionSeats: %decisionRespawn[slot] else: newJNull()),
      "position": {"x": (worldToTiles(hero.position.x, WorldScale) + fixed(int32(mapTiles() div 2)) - FixedHalf).toFloat64,
        "y": (worldToTiles(hero.position.z, WorldScale) + fixed(int32(mapTiles() div 2)) - FixedHalf).toFloat64},
      "hp": hero.hp, "mana": hero.mana, "gold": hero.gold, "level": hero.level,
      "xp": hero.totalXp, "target_id": hero.attackObjectId,
      "commands_submitted": submitted[slot], "commands_accepted": accepted}
  var deaths = newJArray()
  for event in world.events:
    if event.kind == Death:
      deaths.add %*{"kind": "death", "tick": event.tick,
        "actor": event.actor, "target": event.target,
        "cause": $event.cause, "detail": event.detail}
  %*{"tick": world.tick, "battle_tick": world.battleTick(),
    "phase": $world.phase, "sample_phase": "end_of_tick",
    "decision_seats": decisionSeats,
    "decision_phase": (if decisionSeats.len > 0: %decisionPhase else: newJNull()),
    "map_width": mapTiles(), "map_height": mapTiles(),
    "heroes": heroes, "events": deaths}

proc match(job: JsonNode): JsonNode =
  let started = getMonoTime()
  let originalStdout = stdout
  stdout = stderr
  defer: stdout = originalStdout
  var configNode: JsonNode
  for variant in parseJson(Manifest)["variants"]:
    if variant["id"].getStr == "competition": configNode = variant["game_config"]
  configNode["seed"] = job["seed"]
  configNode["max_ticks"] = job["ticks"]
  configNode["draft_mode"] = job["draft"]
  let config = parseConfig($configNode)
  let gameMap = generateMap(config.seed, config.mapPreset)
  let game = newGame(gameMap, config.spawnIntervalTicks, 10, false, ReplayData(),
    draftMode = config.draftMode)
  game.replayData = initReplayData(currentSetup(game, config.maxTicks.uint32), gameMap.preset)
  game.replayData.config = config
  let oracle = job{"oracle"}.getBool
  let tables = job{"tables"}.getBool or oracle
  if tables or job{"replay"}.getBool:
    game.recorder = initReplayRecorder(currentSetup(game, config.maxTicks.uint32), gameMap.preset)
    game.recorder.data.config = config
  let output = job["out"].getStr
  createDir(output)
  var logs: seq[File]
  var paths: seq[string]
  defer:
    for file in logs: file.close()
  for seat, path in job["seats"].getElems:
    paths.add path.getStr
    logs.add open(output / ("seat-" & $seat & ".log"), fmWrite)
  game.loadSeats(paths, logs, tables)
  var tableFile: File
  if tables: tableFile = open(output / (if oracle: "trace.jsonl" else: "tables.jsonl"), fmWrite)
  defer:
    if tables: tableFile.close()
  var peakInstructions, peakWork: array[10, int64]
  var reportedFailures: array[10, bool]
  for seat, vm in game.heroVms: reportedFailures[seat] = vm.failed
  while not game.finished():
    for commands in submitted.mitems: commands.setLen(0)
    decisionSeats.setLen(0)
    game.tickWorld(proc() =
      game.decisionFrame(not job.hasKey("roster"))
      if game.world.phase == Drafting and job.hasKey("roster"):
        let heroId = game.world.draftHeroId()
        let seat = game.world.heroIndex(heroId)
        let classId = job["roster"][seat].getInt.int32
        game.recorder.record ReplayAction(tick: game.world.tick.uint32,
          heroId: heroId, kind: ActionDraft, first: classId)
        let accepted = game.world.applyDraft(heroId, classId)
        if tables:
          submitted[seat].add %*{"kind": ActionDraft, "slot": 0,
            "first": classId, "second": 0, "offset_x": 0, "offset_y": 0,
            "accepted": accepted}
        if not accepted: raise newException(ValueError, "fixed roster draft rejected")
      else:
        game.runBotDecisions()
    )
    for seat, vm in game.heroVms:
      if vm.failed and not reportedFailures[seat]:
        logs[seat].write("\nBASIC error: " & vm.lastError & "\n")
        reportedFailures[seat] = true
      peakInstructions[seat] = max(peakInstructions[seat], vm.lastInstructions)
      peakWork[seat] = max(peakWork[seat], vm.lastWork)
    if tables:
      if oracle:
        var actions = newJArray()
        var order = newJArray()
        for seat in decisionSeats:
          order.add %game.world.heroes[seat].id
          for command in submitted[seat]:
            actions.add %*{"heroId": game.world.heroes[seat].id, "tick": game.world.tick,
              "kind": command["kind"], "slot": command["slot"],
              "first": command["first"], "second": command["second"],
              "offset": [command["offset_x"], command["offset_y"]]}
        tableFile.writeLine($(%*{"type": "tick", "tick": game.world.tick,
          "phase": (if decisionPhase == "Drafting": "draft" else: "battle"),
          "decision_order": order, "actions": actions}))
      else:
        tableFile.writeLine($tableRow(game))
  var heroes = newJArray()
  for seat, hero in game.world.heroes:
    let values = game.world.stats.values[seat]
    let vm = game.heroVms[seat]
    let location = failureLocation(vm)
    heroes.add %*{"slot": seat, "team": hero.team.ord, "class": hero.class.ord,
      "policy": paths[seat], "policy_source_sha1": loadedSourceHashes[seat].toLowerAscii, "xp": hero.totalXp,
      "glory": score(hero.totalXp, int(game.world.tick),
        game.world.gameOver and not game.world.draw and hero.team == game.world.winner),
      "level": hero.level, "kills": values[KillsMetric], "deaths": values[LossesMetric],
      "vm_failed": vm.failed, "vm_error": vm.lastError, "decisions": vm.decisions,
      "vm_routine": location["vm_routine"], "vm_pc": location["vm_pc"],
      "peak_instructions": peakInstructions[seat], "peak_work": peakWork[seat]}
  if game.recordingError.len > 0: raise newException(IOError, game.recordingError)
  if job{"replay"}.getBool: saveReplay(output / "replay.bin", game.recorder.data)
  result = %*{"outcome": game.world.outcome(), "ticks": game.world.tick,
    "battle_ticks": game.world.battleTick(), "draft_ticks": game.world.draftTicks, "state_hash": stateHash(game).toHex(16),
    "heroes": heroes, "config": configNode, "engine": PolyworldCommit, "bassy": BassyCommit,
    "seconds": (getMonoTime() - started).inMilliseconds.float / 1000}
  writeFile(output / "result.json", result.pretty & "\n")

proc commandCounts(game: Game): seq[int64] =
  for seat in 0 ..< game.world.heroes.len:
    result.add game.metrics.read(seat, game.world.tick).commands

proc replayAcceptance(game: Game, before: World, actions: seq[ReplayAction],
                      deltas: seq[int64]): seq[bool] =
  ## Adapted from lab expand_replay: prefix playback disambiguates repeated
  ## identical accepted/rejected actions. Never guess from rejection text.
  result.setLen(actions.len)
  var counts = newSeq[int](deltas.len)
  for action in actions: inc counts[before.heroIndex(action.heroId)]
  var ambiguous = false
  for seat, count in counts:
    if deltas[seat] < 0 or deltas[seat] > count:
      raise newException(ValueError, "invalid replay command delta")
    if deltas[seat] > 0 and deltas[seat] < count: ambiguous = true
  if not ambiguous:
    for i, action in actions: result[i] = deltas[before.heroIndex(action.heroId)] > 0
    return
  let after = game.world.clone()
  var previous = newSeq[int64](deltas.len)
  for i, action in actions:
    let tape = ReplayData(actions: actions[0 .. i], config: game.config)
    let probe = Game(map: game.map, world: before.clone(),
      metrics: newMetrics(deltas.len, TickRate), replayData: tape,
      replayPlayer: ReplayPlayer(data: tape), historyPlayback: true)
    probe.world.restore(before)
    probe.tickWorld(nil)
    let current = probe.commandCounts()
    let seat = before.heroIndex(action.heroId)
    for other in 0 ..< current.len:
      let delta = current[other] - previous[other]
      if delta < 0 or delta > 1 or (other != seat and delta != 0):
        raise newException(ValueError, "replay prefix changed an earlier action")
    result[i] = current[seat] > previous[seat]
    previous = current
    if i == actions.high and stateHash(probe) != stateHash(game):
      raise newException(ValueError, "replay prefix state diverged")
  if previous != deltas:
    raise newException(ValueError, "replay command acceptance did not reconcile")
  game.world.restore(after)

proc replay(job: JsonNode): JsonNode =
  let started = getMonoTime()
  let raw = readFile(job["replay"].getStr)
  let bytes = if raw.startsWith("\x1f\x8b"): uncompress(raw) else: raw
  let data = decodeReplay(bytes) # checks game identity and the pinned gameplay version
  if data.hashes.len == 0: raise newException(ValueError, "replay has no recorded ticks")
  let game = newGame(generateMap(data.config.seed, data.config.mapPreset),
    data.config.spawnIntervalTicks, 0, true, data)
  if game.world.heroes.len != 10:
    raise newException(ValueError, "replay must have ten hero seats")
  game.replayPlayer = initReplayPlayer(data)
  game.historyPlayback = true
  let output = job["out"].getStr
  createDir(output)
  let tables = open(output / "tables.jsonl.tmp", fmWrite)
  var complete = false
  defer:
    tables.close()
    if not complete: removeFile(output / "tables.jsonl.tmp")
  while game.world.tick < data.hashes.len:
    for commands in submitted.mitems: commands.setLen(0)
    decisionSeats.setLen(0)
    if game.world.heroTurnTicks <= 1:
      game.decisionFrame(not job{"fixed_roster"}.getBool)
    let before = game.world.clone()
    let first = game.replayPlayer.actionIndex
    let countsBefore = game.commandCounts()
    game.tickWorld(nil)
    if game.world.tick <= before.tick:
      raise newException(ValueError, "replay stopped before its recorded end")
    if game.hashCheck.mismatches != 0:
      raise newException(ValueError, "replay hash mismatch at pin " & PolyworldCommit &
        ": " & game.hashCheck.error)
    let last = game.replayPlayer.actionIndex
    if last > first:
      let actions = data.actions[first ..< last]
      var deltas = game.commandCounts()
      for seat in 0 ..< deltas.len: deltas[seat] -= countsBefore[seat]
      let accepted = game.replayAcceptance(before, actions, deltas)
      for i, action in actions:
        submitted[game.world.heroIndex(action.heroId)].add %*{
          "kind": action.kind, "slot": action.slot,
          "first": action.first, "second": action.second,
          "offset_x": int32(action.offset.x), "offset_y": int32(action.offset.y),
          "accepted": accepted[i]}
    tables.writeLine($tableRow(game))
  if not game.replayPlayer.finished:
    raise newException(ValueError, "replay has unconsumed actions")
  var heroes = newJArray()
  for seat, hero in game.world.heroes:
    let values = game.world.stats.values[seat]
    heroes.add %*{"slot": seat, "team": hero.team.ord, "class": hero.class.ord,
      "xp": hero.totalXp, "glory": score(hero.totalXp, int(game.world.tick),
        game.world.gameOver and not game.world.draw and hero.team == game.world.winner),
      "level": hero.level, "kills": values[KillsMetric], "deaths": values[LossesMetric],
      "vm_failed": newJNull(), "vm_error": newJNull(),
      "vm_routine": newJNull(), "vm_pc": newJNull(),
      "peak_instructions": newJNull(), "peak_work": newJNull()}
  result = %*{"outcome": game.world.outcome(), "ticks": game.world.tick,
    "battle_ticks": game.world.battleTick(), "draft_ticks": game.world.draftTicks,
    "state_hash": stateHash(game).toHex(16), "heroes": heroes,
    "verified": true, "verified_ticks": data.hashes.len,
    "game_version": data.header.gameVersion, "engine": PolyworldCommit,
    "seconds": (getMonoTime() - started).inMilliseconds.float / 1000}
  tables.flushFile()
  moveFile(output / "tables.jsonl.tmp", output / "tables.jsonl")
  writeFile(output / "result.json", result.pretty & "\n")
  complete = true

proc anatomy(job: JsonNode): JsonNode =
  ## Compact replay evidence: every recorded hash, five-second state samples,
  ## all deaths, and submitted action tape in the final three minutes.
  ## Action acceptance is deliberately not inferred by this mode.
  let started = getMonoTime()
  let raw = readFile(job["replay"].getStr)
  let data = decodeReplay(if raw.startsWith("\x1f\x8b"): uncompress(raw) else: raw)
  if data.hashes.len == 0: raise newException(ValueError, "replay has no recorded ticks")
  let game = newGame(generateMap(data.config.seed, data.config.mapPreset),
    data.config.spawnIntervalTicks, 0, true, data)
  game.replayPlayer = initReplayPlayer(data)
  game.historyPlayback = true
  var samples = newJArray()
  var deaths = newJArray()
  var actions = newJArray()
  while game.world.tick < data.hashes.len:
    let previousTick = game.world.tick
    let first = game.replayPlayer.actionIndex
    game.tickWorld(nil)
    if game.world.tick <= previousTick:
      raise newException(ValueError, "replay stopped before its recorded end")
    if game.hashCheck.mismatches != 0:
      raise newException(ValueError, "replay hash mismatch: " & game.hashCheck.error)
    for event in game.world.events:
      if event.kind == Death:
        deaths.add %*{"tick": event.tick, "actor": event.actor, "target": event.target,
          "cause": $event.cause, "detail": event.detail}
    if game.world.tick >= data.hashes.len - 3 * 60 * 24:
      for index in first ..< game.replayPlayer.actionIndex:
        let action = data.actions[index]
        actions.add %*{"tick": game.world.tick, "hero_id": action.heroId,
          "kind": action.kind, "slot": action.slot,
          "first": action.first, "second": action.second,
          "offset_x": int32(action.offset.x), "offset_y": int32(action.offset.y)}
    if game.world.tick mod 120 == 0 or game.world.tick == data.hashes.len:
      var sample = tableRow(game)
      var buildings = newJArray()
      for building in game.world.buildings:
        buildings.add %*{"id": building.id, "team": building.team.ord,
          "lane": building.lane, "kind": $building.kind, "tier": $building.tier,
          "guards_god": building.guardsGod, "hp": building.hp, "max_hp": building.maxHp}
      var gods = newJArray()
      for fort in game.world.forts:
        gods.add %*{"id": fort.id, "team": fort.team.ord, "hp": fort.hp,
          "exposed": game.world.fortExposed(fort.team)}
      for seat, hero in game.world.heroes:
        sample["heroes"][seat]["deaths"] = %game.world.stats.values[seat][LossesMetric]
        sample["heroes"][seat]["kills"] = %game.world.stats.values[seat][KillsMetric]
        sample["heroes"][seat]["state"] = %($hero.state)
      sample["buildings"] = buildings
      sample["gods"] = gods
      samples.add sample
  if not game.replayPlayer.finished:
    raise newException(ValueError, "replay has unconsumed actions")
  result = %*{"outcome": game.world.outcome(), "ticks": game.world.tick,
    "draft_ticks": game.world.draftTicks, "state_hash": stateHash(game).toHex(16),
    "verified": true, "verified_ticks": data.hashes.len,
    "game_version": data.header.gameVersion, "engine": PolyworldCommit,
    "samples": samples, "deaths": deaths, "last_three_minutes_submitted_actions": actions,
    "seconds": (getMonoTime() - started).inMilliseconds.float / 1000}

proc sortedJson(node: JsonNode): JsonNode =
  case node.kind
  of JObject:
    result = newJObject()
    var keys: seq[string]
    for key in node.keys: keys.add key
    keys.sort()
    for key in keys: result[key] = sortedJson(node[key])
  of JArray:
    result = newJArray()
    for value in node: result.add sortedJson(value)
  else: result = node

try:
  if paramCount() == 1 and paramStr(1) == "host": echo $sortedJson(hostSurface())
  elif paramCount() == 2 and paramStr(1) == "stats": echo $statsSource(readFile(paramStr(2)))
  elif paramCount() == 2 and paramStr(1) == "match": echo $match(parseFile(paramStr(2)))
  elif paramCount() == 2 and paramStr(1) == "replay": echo $replay(parseFile(paramStr(2)))
  elif paramCount() == 2 and paramStr(1) == "anatomy": echo $anatomy(parseFile(paramStr(2)))
  else:
    stderr.writeLine("usage: gota_match host | stats POLICY.bas | match JOB.json | replay JOB.json | anatomy JOB.json")
    quit(2)
except CatchableError as error:
  stderr.writeLine(error.msg)
  quit(1)
