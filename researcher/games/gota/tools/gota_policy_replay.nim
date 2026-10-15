# Source: Metta-AI/co-gas @ 6742feb08f8b119591d09261cadc581065fa698b; path: players/users/relh/co-gas/polyworld-basic/tools/gota_policy_replay.nim
## Execute one source-backed policy from tick zero with persistent VM memory.
## Other heroes consume recorded commands in the simulator's decision order.
## By default stop at the first state divergence. With gotaCounterfactual,
## continue a bounded local fork against recorded opponents for diagnosis only.
import std/[os, strutils, json, importutils]
include examples/gods_of_the_arena/bots

privateAccess(Runtime)
privateAccess(Program)

const GotaSourceCommit {.strdefine.} = "unverified"
const GotaCounterfactual = defined(gotaCounterfactual)

doAssert paramCount() in 4 .. 8,
  "replay heroId policy.bas|- output.json [maxTicks] [globals] [trace.jsonl] [phase-only|phase-only-24|skip-repeat-walk|skip-active-repeat-walk | --fork ticks]"
let recordedOnly = paramStr(3) == "-"
# Crop recorded observation output without skipping replay validation or commands.
# Source-parity trajectories remain complete. This bounds bulky diagnostic files.
let traceFirstTick = parseInt(getEnv("GOTA_TRACE_FIRST_TICK", "0"))
let traceLastTick = parseInt(getEnv("GOTA_TRACE_LAST_TICK", $int.high))
let traceStride = parseInt(getEnv("GOTA_TRACE_STRIDE", "1"))
doAssert traceFirstTick >= 0 and traceLastTick >= traceFirstTick
doAssert traceStride > 0
let boundedFork = not recordedOnly and paramCount() == 8 and paramStr(7) == "--fork"
let forkTicks = if boundedFork: parseInt(paramStr(8)) else: 0
doAssert forkTicks >= 0 and forkTicks <= 240, "fork must be bounded to 240 ticks"
doAssert (if recordedOnly: paramCount() in 7 .. 8 else: paramCount() in 4 .. 7 or boundedFork),
  "recorded-only mode requires a capped trace path; source mode accepts an optional trajectory path"
let phaseOnly = recordedOnly and paramCount() == 8 and
  paramStr(8) in ["phase-only", "phase-only-24"]
let phaseStride = if recordedOnly and paramCount() == 8 and
    paramStr(8) == "phase-only-24": 24 else: 1
let skipRepeatWalk = recordedOnly and paramCount() == 8 and
  paramStr(8) in ["skip-repeat-walk", "skip-active-repeat-walk"]
let skipOnlyActive = skipRepeatWalk and paramStr(8) == "skip-active-repeat-walk"
let sourceTrajectory = not recordedOnly and paramCount() == 7
if recordedOnly and paramCount() == 8:
  doAssert phaseOnly or skipRepeatWalk,
    "unknown recorded trace option"
if recordedOnly:
  doAssert parseInt(paramStr(5)) > 0
  doAssert paramStr(6).len == 0, "recorded mode has no policy globals"
  doAssert not fileExists(paramStr(7)), "refusing to overwrite an existing trace"
if sourceTrajectory:
  doAssert not fileExists(paramStr(7)), "refusing to overwrite an existing trajectory"
let data = loadReplay(paramStr(1))
let focus = int32(parseInt(paramStr(2)))
let limit = if paramCount() >= 5:
  min(parseInt(paramStr(5)), data.hashes.len)
else:
  data.hashes.len
doAssert limit > 0
let game = newGame(generateMap(data.config.seed, data.config.mapPreset),
  data.config.spawnIntervalTicks, 0, true, data)
let hi = game.world.heroIndex(focus)
doAssert hi >= 0, "focus hero is absent from replay setup"
if recordedOnly:
  let limits = heroVmLimits()
  let host = initHeroHost(focus)
  let program = compile("end\n", host, limits)
  bindHeroData(program)
  game.heroVms = newSeq[HeroVm](game.world.heroes.len)
  game.heroVms[hi] = HeroVm(runtime: initRuntime(program, host, limits),
    # The empty program does not reference scalar host data, but the observer
    # reads it. Opt in to the same population path as a scalar BASIC policy.
    limits: limits, ready: true, legacyHeroData: true)
else:
  game.loadBots([BotGroup(count: game.world.heroes.len, path: paramStr(3))])
for i in 0 ..< game.heroVms.len:
  if i != hi:
    game.heroVms[i] = nil
game.recorder = initReplayRecorder(
  game.currentSetup(uint32(data.config.maxTicks)), data.config.mapPreset)

proc applyRecorded(world: World, action: ReplayAction) =
  ## Same dispatch as exact release sim.nim's private applyReplayAction.
  case action.kind
  of ActionWalkTo:
    discard world.applyWalkTo(action.heroId, action.first, action.second, action.offset)
  of ActionAttackMove:
    discard world.applyAttackMove(action.heroId, action.first, action.second, action.offset)
  of ActionAttackTarget:
    discard world.applyAttackTarget(action.heroId, action.first)
  of ActionBuyItem:
    discard world.applyBuyItem(action.heroId, action.first)
  of ActionBuyback:
    discard world.applyBuyback(action.heroId)
  of ActionUseItem:
    discard world.applyUseItem(action.heroId, action.first)
  of ActionUseItemAt:
    discard world.applyUseItemAt(action.heroId, action.slot,
      action.first, action.second, action.offset)
  of ActionCastTarget:
    discard world.applyCastTarget(action.heroId, action.slot, action.first)
  of ActionCastPoint:
    discard world.applyCastPoint(action.heroId, action.slot,
      action.first, action.second, action.offset)
  of ActionLevelAbility:
    discard world.applyLevelAbility(action.heroId, action.slot)
  of ActionDraft:
    discard world.applyDraft(action.heroId, action.first)
  else:
    raise newException(ValueError, "unhandled recorded command " & $action.kind)

proc actionJson(action: ReplayAction): JsonNode =
  %*{"tick": action.tick, "heroId": action.heroId, "kind": action.kind,
    "slot": action.slot, "first": action.first, "second": action.second,
    "offset": [$action.offset.x, $action.offset.y]}

proc actionsJson(actions: openArray[ReplayAction]): JsonNode =
  result = newJArray()
  for action in actions:
    result.add(actionJson(action))

proc globalsJson(): JsonNode =
  result = newJObject()
  if not recordedOnly and paramCount() >= 6:
    for rawName in paramStr(6).split(','):
      let name = rawName.strip()
      if name.len > 0:
        result[name] = %game.heroVms[hi].runtime.getGlobal(name)

proc targetObservations(actions: openArray[ReplayAction]): JsonNode =
  ## Called inside the frozen decision frame, before movement/combat advance.
  result = newJArray()
  var seen: seq[int32]
  for action in actions:
    if action.kind notin [ActionAttackTarget, ActionCastTarget] or
        action.first in seen:
      continue
    seen.add(action.first)
    var obj: WorldObject
    if game.world.worldObjectById(focus, action.first, obj):
      result.add(%*{"id": obj.id, "kind": obj.kind,
        "team": obj.faction, "alive": obj.alive,
        "returning": obj.returning, "hp": obj.hp,
        "position": [mapCoordinate(obj.position.x), mapCoordinate(obj.position.z)]})
    else:
      result.add(%*{"id": action.first, "visible": false})

proc traceObservation(): JsonNode =
  ## The empty VM has just received the exact public BASIC host data.
  ## Query supplemental fields through the same visibility-filtered host code.
  let hero = game.world.heroes[hi]
  var selfData = newJObject()
  for name in HeroDataNames:
    selfData[name] = %game.heroVms[hi].runtime.getData(name)
  for name in ["mapWidth", "mapHeight", "mapLayers", "worldScale", "tickRate"]:
    selfData[name] = %game.heroVms[hi].runtime.getData(name)
  doAssert selfData["selfId"].getInt() == int(focus)
  doAssert selfData["selfHp"].getInt() == int(max(hero.hp, 0'i32))
  doAssert selfData["worldTick"].getInt() == int(game.world.tick)
  var abilities = newJArray()
  for slot in 0'i32 .. 3'i32:
    var ability = newJObject()
    for field in AbilityField:
      ability[$field] = %abilityProc(focus, field)([slot])
    ability["cooldown"] = %hero.cooldowns[HeroAbilitySlot(slot)]
    ability["charges"] = %hero.charges[HeroAbilitySlot(slot)]
    ability["recharge"] = %hero.recharges[HeroAbilitySlot(slot)]
    abilities.add(ability)
  var items = newJArray()
  for slot in 0 ..< InventorySlots:
    items.add(%*{"id": hero.inventory[slot].ord, "count": hero.itemCounts[slot],
      "cooldown": hero.itemCooldown(slot, game.world.tick)})
  var camps = newJArray()
  for index in 0 ..< game.world.camps.len:
    let campIndex = int32(index)
    camps.add(%*{"x": campProc(focus, CampX)([campIndex]),
      "y": campProc(focus, CampY)([campIndex]),
      "tier": campProc(focus, CampTier)([campIndex])})
  var objects = newJArray()
  for index in 0 ..< game.world.worldObjectCount(focus):
    var obj: WorldObject
    doAssert game.world.worldObjectAt(focus, index, obj)
    var row = %*{"objectId": obj.id, "objectKind": obj.kind,
      "objectClass": obj.class, "objectTeam": obj.faction,
      "objectX": mapCoordinate(obj.position.x, hero.team),
      "objectY": mapCoordinate(obj.position.z, hero.team),
      "objectHp": max(obj.hp, 0'i32), "objectAlive": int32(obj.alive)}
    for field in ObjectField:
      if field notin [ObjectItemId, ObjectItemCount]:
        row[$field] = %objectProc(focus, field)([int32(index)])
    var equipment = newJArray()
    for slot in 0'i32 ..< int32(InventorySlots):
      equipment.add(%*{"id": objectProc(focus, ObjectItemId)([int32(index), slot]),
        "count": objectProc(focus, ObjectItemCount)([int32(index), slot])})
    row["items"] = equipment
    objects.add(row)
  var picks, available = newJArray()
  for other in game.world.heroes:
    picks.add(%*{"id": other.id, "team": other.team.ord,
      "class": game.world.draftedClass(other.id)})
  for classId in 0'i32 .. 9'i32:
    available.add(%int32(game.world.heroAvailable(classId)))
  var spellOptions = newJArray()
  if game.world.phase != Drafting:
    ## Object targets are legal at this frozen frame. Point casting still
    ## requires a separate, actor-chosen visible point candidate contract.
    for slot in 0'i32 .. 3'i32:
      let
        ability = heroAbility(hero.class, HeroAbilitySlot(slot))
        spec = ability.abilitySpec(hero.abilityLevels[HeroAbilitySlot(slot)])
        charges = if hero.spellsReady:
          hero.charges[HeroAbilitySlot(slot)]
        else:
          spec.charges
      var ready = hero.hp > 0 and hero.state != Dying and
        hero.portalEnds == 0 and
        hero.controls[StunControl].ends <= game.world.tick and
        hero.controls[SilenceControl].ends <= game.world.tick and
        hero.abilityLevels[HeroAbilitySlot(slot)] > 0 and
        game.world.casts.len < 512 and
        hero.cooldowns[HeroAbilitySlot(slot)] == 0 and
        charges > 0 and hero.mana >= spec.manaCost
      if spec.casting == SelfCast:
        if (spec.kind == Heal and hero.hp >= hero.maxHp) or
            (spec.kind == Restore and hero.mana >= hero.maxMana):
          ready = false
      var targetIds = newJArray()
      if ready:
        if spec.casting == SelfCast:
          targetIds.add(%hero.id)
        else:
          for objectIndex in 0 ..< game.world.worldObjectCount(focus):
            var observed, target: WorldObject
            doAssert game.world.worldObjectAt(focus, objectIndex, observed)
            if game.world.spellTarget(observed.id, target) and target.alive and
                game.world.visible(hero.team, target.position) and
                ((spec.kind == Strike and target.faction != hero.team.ord.int32) or
                (spec.kind != Strike and target.faction == hero.team.ord.int32)) and
                within(hero.position, target.position, spec.range):
              targetIds.add(%target.id)
      spellOptions.add(%*{"slot": slot, "ability": $ability,
        "kind": $spec.kind, "casting": $spec.casting,
        "rangeRaw": spec.range, "resourceReady": ready,
        "targetIds": targetIds,
        "pointCastingSupported": spec.casting != SelfCast})
  %*{"self": selfData, "abilities": abilities, "items": items,
    "abilityPoints": hero.abilityPoints,
    "canShop": int32(game.world.phase != Drafting and hero.canShop),
    "inOwnSpawn": int32(hero.inOwnSpawn),
    "buybackPrice": game.world.buybackPrice(focus),
    "draftPicks": picks, "draftAvailable": available, "objects": objects,
    "camps": camps,
    "spellOptions": spellOptions}

proc resourceMasks(): JsonNode =
  ## Independent native validators for checking the neural adapter's masks.
  var draft, skill, shop, buyback = newJArray()
  for head in [draft, skill, shop, buyback]:
    head.add(%true)
  for classId in 0'i32 .. 9'i32:
    draft.add(%(game.world.phase == Drafting and
      game.world.draftHeroId() == focus and game.world.heroAvailable(classId)))
  for slot in 0'i32 .. 3'i32:
    skill.add(%(abilityProc(focus, AbilityCanLevel)([slot]) != 0))
  for item in 1'i32 .. 22'i32:
    shop.add(%(game.world.purchaseReason(focus, item).len == 0))
  buyback.add(%(game.world.buybackReason(focus).len == 0))
  %*{"draft": draft, "skill": skill, "shop": shop, "buyback": buyback}

proc resourceState(): JsonNode =
  ## Compact, exact state for checking updates between resource commands.
  let hero = game.world.heroes[hi]
  var levels, items = newJArray()
  for slot in HeroAbilitySlot:
    levels.add(%hero.abilityLevels[slot])
  for slot in 0 ..< InventorySlots:
    items.add(%*{"id": hero.inventory[slot].ord,
      "count": hero.itemCounts[slot]})
  %*{"gold": hero.gold, "hp": hero.hp, "level": hero.level,
    "abilityPoints": hero.abilityPoints, "abilityLevels": levels,
    "items": items, "canShop": int32(game.world.phase != Drafting and hero.canShop),
    "drafting": int32(game.world.phase == Drafting),
    "inOwnSpawn": int32(hero.inOwnSpawn),
    "buybackPrice": game.world.buybackPrice(focus),
    "draftedClass": game.world.draftedClass(focus)}

var trace: File
if recordedOnly or sourceTrajectory:
  trace = open(paramStr(7), fmWrite)

var
  cursor, decisions, expectedCount, actualCount, differentDecisions, traceRows,
    skippedWalks: int
  hasPreviousNav = false
  previousNav: ReplayAction
  firstActionDifference = newJNull()
  firstStateDifference = newJNull()
  comparisonObservations = newJNull()
  lastFocusActions = newJArray()
  protocolError = ""
  maxWork, maxInstructions: int64

proc decide() =
  activeGame = game
  let tick = uint32(game.world.tick)
  var tickActions, expected: seq[ReplayAction]
  while cursor < data.actions.len and data.actions[cursor].tick == tick:
    let action = data.actions[cursor]
    tickActions.add(action)
    if action.heroId == focus:
      expected.add(action)
    inc cursor
  let start = game.recorder.data.actions.len
  proc run(index: int) =
    let heroId = game.world.heroes[index].id
    if heroId == focus:
      game.runHeroScript(index)
      inc decisions
      maxWork = max(maxWork, game.heroVms[hi].lastWork)
      maxInstructions = max(maxInstructions, game.heroVms[hi].lastInstructions)
      if recordedOnly:
        doAssert game.recorder.data.actions.len == start,
          "observation-only VM unexpectedly emitted a command"
        let masks = resourceMasks()
        var emitTrace = not phaseOnly
        var resourceCommandSeen = false
        if phaseOnly:
          for family in ["draft", "skill", "shop", "buyback"]:
            for index in 1 ..< masks[family].len:
              if masks[family][index].getBool():
                emitTrace = true
          for action in expected:
            if action.kind in [ActionDraft, ActionLevelAbility, ActionBuyItem,
                ActionBuyback]:
              emitTrace = true
              resourceCommandSeen = true
          if not resourceCommandSeen and int(tick) mod phaseStride != 0:
            emitTrace = false
        emitTrace = emitTrace and int(tick) >= traceFirstTick and
          int(tick) <= traceLastTick and int(tick) mod traceStride == 0
        let observation = if emitTrace: traceObservation() else: newJNull()
        var labels = newJArray()
        for action in expected:
          var label = if emitTrace: actionJson(action) else: newJNull()
          let resourceAction = action.kind in [ActionDraft, ActionLevelAbility,
            ActionBuyItem, ActionBuyback]
          if emitTrace and resourceAction:
            label["resourceMasksBefore"] = resourceMasks()
            if phaseOnly:
              label["resourceStateBefore"] = resourceState()
          if emitTrace and action.kind in [ActionCastTarget, ActionCastPoint]:
            ## An ability can be leveled earlier in this same decision. The
            ## decision-start observation then has a stale spell mask.
            label["spellOptionsBefore"] = traceObservation()["spellOptions"]
          let hero = game.world.heroes[hi]
          let activeSamePath = hero.moveRevision == game.world.navigationRevision and
            hero.hasMoveTarget and hero.moveTileX == int(action.first) and
            hero.moveTileY == int(action.second) and hero.moveOffset == action.offset and
            hero.movePathIndex < hero.movePath.len and hero.attackObjectId == 0 and
            not hero.attackMoving and hero.targetFootmanId == 0 and
            hero.targetHeroId == 0 and hero.targetBuildingId == 0 and
            not hero.attackingFort and hero.lastActionError == NoActionError and
            hero.hp > 0 and hero.state != Dying and hero.portalEnds == 0 and
            hero.controls[StunControl].ends <= game.world.tick and
            hero.controls[RootControl].ends <= game.world.tick
          let repeatedWalk = skipRepeatWalk and hasPreviousNav and
            action.kind == ActionWalkTo and previousNav.kind == ActionWalkTo and
            action.first == previousNav.first and action.second == previousNav.second and
            action.offset == previousNav.offset and
            (not skipOnlyActive or activeSamePath)
          if repeatedWalk:
            inc skippedWalks
            label["skipped"] = %true
            label["error"] = newJNull()
            label["accepted"] = newJNull()
          else:
            game.world.applyRecorded(action)
            if emitTrace:
              label["error"] = %game.world.heroes[hi].lastActionError.ord
              label["accepted"] = %(game.world.heroes[hi].lastActionError == NoActionError)
            if action.kind in [ActionWalkTo, ActionAttackMove, ActionAttackTarget] and
                game.world.heroes[hi].lastActionError == NoActionError:
              previousNav = action
              hasPreviousNav = true
          if emitTrace:
            if phaseOnly and resourceAction:
              label["resourceMasksAfter"] = resourceMasks()
              label["resourceStateAfter"] = resourceState()
            labels.add(label)
        if emitTrace:
          if phaseOnly:
            trace.writeLine($(%*{"schema": 2, "tick": tick, "heroId": focus,
              "gameVersion": data.header.gameVersion, "observation": observation,
              "resourceMasks": masks, "resourceMasksAfterDecision": resourceMasks(),
              "resourceStateAfterDecision": resourceState(),
              "commands": labels, "wait": expected.len == 0}))
          else:
            trace.writeLine($(%*{"schema": 1, "tick": tick, "heroId": focus,
              "gameVersion": data.header.gameVersion, "observation": observation,
              "resourceMasks": masks, "commands": labels, "wait": expected.len == 0}))
          inc traceRows
    else:
      for action in tickActions:
        if action.heroId == heroId:
          game.world.applyRecorded(action)
  if game.world.phase == Drafting:
    run(game.world.heroIndex(game.world.draftHeroId()))
  else:
    for offset in 0 ..< game.world.heroes.len:
      run((game.world.heroTurnStart + offset) mod game.world.heroes.len)
    game.world.heroTurnStart =
      (game.world.heroTurnStart + 1) mod game.world.heroes.len
  let actual = if recordedOnly: expected
    else: game.recorder.data.actions[start ..< game.recorder.data.actions.len]
  if sourceTrajectory:
    lastFocusActions = actionsJson(actual)
  expectedCount += expected.len
  actualCount += actual.len
  if expected != actual:
    inc differentDecisions
    if firstActionDifference.kind == JNull:
      firstActionDifference = %*{"tick": tick,
        "expected": actionsJson(expected), "actual": actionsJson(actual),
        "globals": globalsJson(),
        "targetObservations": targetObservations(expected & actual)}
  if tick == uint32(limit):
    comparisonObservations = targetObservations(expected & actual)

var forkEnd = 0
var forkSamples, forkEvents = newJArray()
while game.world.tick < limit and (forkEnd == 0 or game.world.tick < forkEnd):
  let before = game.world.tick
  game.tickWorld(decide)
  if game.world.tick == before:
    protocolError = "simulation stopped before requested replay prefix completed"
    break
  if cursor < data.actions.len and data.actions[cursor].tick <= uint32(game.world.tick):
    protocolError = "recorded action did not occur at a native decision tick"
    break
  let actual = stateHash(game)
  let expected = data.hashes[game.world.tick - 1]
  if actual != expected:
    if firstStateDifference.kind == JNull:
      firstStateDifference = %*{"tick": game.world.tick,
        "expected": $expected, "actual": $actual, "globals": globalsJson()}
      if forkTicks > 0:
        forkEnd = game.world.tick + forkTicks
    if not GotaCounterfactual and forkTicks == 0:
      break
  if forkEnd > 0:
    when defined(replayEvents):
      for event in game.world.events:
        if event.actor.id == focus or event.target.id == focus:
          forkEvents.add %event
    let h = game.world.heroes[hi]
    forkSamples.add %*{"tick": game.world.tick, "hp": h.hp,
      "xp": h.totalXp, "deaths": h.deaths, "hits": h.attacksLanded,
      "position": [mapCoordinate(h.position.x), mapCoordinate(h.position.z)],
      "positionRaw": [h.position.x, h.position.z], "target": h.attackObjectId}
  if game.heroVms[hi].failed:
    break
  if sourceTrajectory:
    let hero = game.world.heroes[hi]
    var relevantEvents = newJArray()
    when defined(replayEvents):
      for event in game.world.events:
        if event.actor.id == focus or event.target.id == focus:
          if event.kind in {XpGained, Death, Damage, Healing, SpellReleased,
              ActionRejected, CampEngaged, ItemPurchased, ItemConsumed,
              GoldSpent, RecoveryStarted, RecoveryInterrupted, RecoveryCompleted}:
            relevantEvents.add(%event)
    trace.writeLine($(%*{"tick": game.world.tick, "heroId": focus,
      "hp": hero.hp, "maxHp": hero.maxHp, "mana": hero.mana,
      "level": hero.level, "totalXp": hero.totalXp,
      "gold": hero.gold, "deaths": hero.deaths,
      "positionRaw": [hero.position.x, hero.position.z],
      "attackObjectId": hero.attackObjectId,
      "targetHeroId": hero.targetHeroId,
      "targetFootmanId": hero.targetFootmanId,
      "targetBuildingId": hero.targetBuildingId,
      "globals": globalsJson(),
      "actions": lastFocusActions, "events": relevantEvents}))
    inc traceRows

let vm = game.heroVms[hi]
var failureDiagnostics = newJNull()
if vm.failed:
  let runtime = vm.runtime
  var nearbyCode = newJArray()
  for index in max(0, int(runtime.pc) - 8) ..
      min(runtime.program.code.high, int(runtime.pc) + 8):
    nearbyCode.add(%*{"pc": index, "instruction": $runtime.program.code[index]})
  var allGlobals = newJObject()
  for name in runtime.program.globalNames:
    try:
      allGlobals[name] = %runtime.getGlobal(name)
    except BasicError:
      allGlobals[name] = %"non-integer value"
  failureDiagnostics = %*{"pc": runtime.pc,
    "instructionsUsed": runtime.instructionsUsed,
    "workUsed": runtime.workUsed, "globals": allGlobals,
    "nearbyCode": nearbyCode,
    "globalNames": runtime.program.globalNames}
let focusHero = game.world.heroes[hi]
let stateMatched = firstStateDifference.kind == JNull and
  protocolError.len == 0 and not vm.failed and game.world.tick == limit
let actionMatched = differentDecisions == 0 and stateMatched
if recordedOnly or sourceTrajectory:
  trace.close()
writeFile(paramStr(4), $(%*{
  "scope": (if forkTicks > 0: "bounded counterfactual with recorded opponent commands; not hosted outcome evidence"
    elif phaseOnly: "compact masked resource decisions with after-command masks"
    elif recordedOnly: "decision-time recorded-command observations"
    elif GotaCounterfactual: "bounded local fork with recorded opponents; no hosted outcome claim"
    else: "persistent single-policy source parity against recorded opponents"),
  "sourceCommit": GotaSourceCommit, "gameVersion": data.header.gameVersion,
  "replay": paramStr(1), "source": paramStr(3), "heroId": focus,
  "participantPosition": hi,
  "replaySlot": data.header.setup.heroes[hi].slot,
  "team": data.header.setup.heroes[hi].team,
  "requestedTicks": limit, "reachedTick": game.world.tick,
  "counterfactual": GotaCounterfactual or forkTicks > 0,
  "counterfactualTicksRequested": forkTicks,
  "counterfactualSamples": forkSamples, "counterfactualEvents": forkEvents,
  "finalFocus": %*{"hp": focusHero.hp, "deaths": focusHero.deaths,
    "xpWithinLevel": focusHero.xp, "totalXp": focusHero.totalXp,
    "level": focusHero.level,
    "positionRaw": [focusHero.position.x, focusHero.position.z]},
  "stateMatched": stateMatched,
  "actionMatched": (if recordedOnly: newJNull() else: %actionMatched),
  "sourceVerified": not recordedOnly and actionMatched,
  "completeReplay": stateMatched and limit == data.hashes.len,
  "traceRows": traceRows,
  "recordedTraceWindow": [traceFirstTick, traceLastTick],
  "recordedTraceStride": traceStride,
  "phaseOnly": phaseOnly,
  "phaseStride": phaseStride,
  "skipRepeatWalk": skipRepeatWalk,
  "skipOnlyActive": skipOnlyActive,
  "skippedWalks": skippedWalks,
  "tracePath": (if recordedOnly or sourceTrajectory: paramStr(7) else: ""),
  "sourceTrajectory": sourceTrajectory,
  "traceOmissions": ["terrain", "spell warnings", "chat"],
  "expectedActions": expectedCount, "actualActions": actualCount,
  "decisions": decisions, "differentDecisions": differentDecisions,
  "firstActionDifference": firstActionDifference,
  "firstStateDifference": firstStateDifference,
  "protocolError": protocolError, "vmFailed": vm.failed,
  "vmError": vm.lastError, "failureDiagnostics": failureDiagnostics,
  "maxWork": maxWork,
  "maxInstructions": maxInstructions, "globals": globalsJson(),
  "comparisonTargetObservations": comparisonObservations
}) & "\n")
if GotaCounterfactual and not boundedFork:
  if protocolError.len > 0 or vm.failed or game.world.tick != limit:
    quit(1)
elif not actionMatched:
  quit(1)
