# Source: Metta-AI/personal_labs @ 07dc6f94760fae3cf2de36877e0b6186cfd1dc63; path: gods_of_the_arena_lab/tools/expand_replay.nim
## Version-matched, hash-checked replay expansion. Build only via build_nim.sh.
## No simulator modifications. Ambiguous identities are null; reward totals stay exact.
import std/[json, os, strutils, sequtils]
import zippy
import polyworld/[metrics, tapes]
import ../[content, maps, replays, sim, observations, scores]

# Event entity kinds: sim.nim:528-533 @ Polyworld 5287c437.
const
  FootmanObjectKind = 3
  TowerObjectKind = 4
  BarracksObjectKind = 5

proc fail(message: string) {.noreturn.} =
  raise newException(ValueError, message)

proc position(point: WorldPoint): JsonNode =
  %*{"x": mapCoordinate(point.x), "y": mapCoordinate(point.z),
    "world_x": point.x, "world_y": point.y, "world_z": point.z}

proc seatRow(world: World, slot: int): JsonNode =
  let h = world.heroes[slot]
  %*{"slot": slot, "hero_id": h.id, "team": $h.team,
    "class": h.class.ord, "hero": h.class.heroSpec.name, "lane": h.lane}

proc emit(output: File, row: JsonNode, tick: int, kind: string,
          rowType = "event") =
  row["type"] = %rowType
  row["tick"] = %tick
  row["kind"] = %kind
  output.writeLine($row)

proc heroRow(world: World, slot: int): JsonNode =
  let h = world.heroes[slot]
  let v = world.stats.values[slot]
  result = world.seatRow(slot)
  result["pos"] = position(h.position)
  for key, value in position(h.position): result[key] = value
  result["hp"] = %h.hp
  result["max_hp"] = %h.maxHp
  result["mana"] = %h.mana
  result["max_mana"] = %h.maxMana
  result["level"] = %h.level
  result["xp"] = %h.totalXp
  result["banked_gold"] = %h.gold
  result["gold"] = %h.gold
  result["gold_earned"] = %v[GoldMetric]
  result["hero_kills"] = %v[KillsMetric]
  result["deaths"] = %v[LossesMetric]
  result["assists"] = %v[AssistsMetric]
  result["state"] = %($h.state)
  result["alive"] = %(h.hp > 0 and h.state != Dying)
  result["target_footman_id"] = %h.targetFootmanId
  result["target_hero_id"] = %h.targetHeroId
  result["target_building_id"] = %h.targetBuildingId
  result["attack_object_id"] = %h.attackObjectId
  result["attacking_fort"] = %h.attackingFort
  result["inventory"] = %h.inventory.mapIt(it.ord)
  result["item_counts"] = %h.itemCounts
  result["cooldowns"] = %h.cooldowns
  result["charges"] = %h.charges
  result["recharges"] = %h.recharges
  result["move_path_length"] = %h.movePath.len
  result["move_path_index"] = %h.movePathIndex
  result["stuck_ticks"] = %h.stuckTicks
  result["has_move_target"] = %h.hasMoveTarget

proc buildingRow(b: Building): JsonNode =
  %*{"object_id": b.id, "team": $b.team, "lane": b.lane,
    "building_kind": (if b.kind == TowerBuilding: "tower" else: "barracks"),
    "tier": (if b.kind == TowerBuilding: %($b.tier) else: newJNull()),
    "guards_god": b.guardsGod, "hp": b.hp, "max_hp": b.maxHp,
    "pos": position(b.position)}

proc sample(output: File, world: World) =
  var heroes = newJArray()
  var buildings = newJArray()
  var forts = newJArray()
  var counts: array[2, array[3, int]]
  for slot in 0 ..< world.heroes.len: heroes.add world.heroRow(slot)
  for b in world.buildings: buildings.add buildingRow(b)
  for f in world.forts:
    forts.add %*{"object_id": f.id, "team": $f.team, "hp": f.hp,
      "exposed": world.fortExposed(f.team), "pos": position(f.center)}
  for f in world.footmen:
    if f.hp > 0 and f.state != Dying: inc counts[f.team.ord][f.lane]
  output.emit(%*{"heroes": heroes, "buildings": buildings, "forts": forts,
    "footmen_by_team_lane": counts}, world.tick, "objective_state", "state")
  for slot, h in world.heroes:
    var objects = newJArray()
    var enemies, structures: int
    for i in 0 ..< world.worldObjectCount(h.id):
      var obj: WorldObject
      doAssert world.worldObjectAt(h.id, i, obj)
      objects.add %*{"object_id": obj.id, "object_kind": obj.kind,
        "team": $obj.team, "hp": obj.hp, "alive": obj.alive,
        "pos": position(obj.position)}
      if obj.team != h.team:
        if world.heroIndex(obj.id) >= 0 and obj.alive: inc enemies
        if obj.alive and (world.buildingIndex(obj.id) >= 0 or
            world.forts.anyIt(it.id == obj.id)):
          inc structures
    var row = world.seatRow(slot)
    row["enemy_heroes"] = %enemies
    row["enemy_structures_exposed"] = %structures
    row["visible_spells"] = %world.visibleSpellCount(h.id)
    row["objects"] = objects
    row["team_visible"] = %world.teamVisible[h.team.ord]
    row["sample_phase"] = %"end_of_tick"
    output.emit(row, world.tick, "visibility", "state")

proc commands(game: Game): seq[int64] =
  for slot in 0 ..< game.world.heroes.len:
    result.add game.metrics.read(slot, game.world.tick).commands

proc actionAcceptance(game: Game, before: World, actions: seq[ReplayAction],
                      totals: seq[int64]): seq[bool] =
  ## tickWorld increments commands iff applyReplayAction returns true, and nothing
  ## else in a playback tick increments it. For mixed outcomes within a seat,
  ## replay successive tape prefixes from the same checkpoint. Adjacent prefix
  ## command differences identify each boolean exactly, even for repeated actions.
  ## Probes use the unmodified tickWorld, including spawn/cooldown/vision ordering.
  result.setLen(actions.len)
  var requests = newSeq[int](totals.len)
  for a in actions:
    let slot = before.heroIndex(a.heroId)
    if slot >= 0: inc requests[slot]
  var ambiguous = false
  for slot, count in requests:
    if totals[slot] < 0 or totals[slot] > count: fail("invalid command delta")
    if totals[slot] > 0 and totals[slot] < count: ambiguous = true
  if not ambiguous:
    for i, a in actions:
      let slot = before.heroIndex(a.heroId)
      result[i] = slot >= 0 and totals[slot] > 0
    return
  let after = game.world.clone()
  var previous = newSeq[int64](totals.len)
  for i, a in actions:
    # Diagnostic prefixes have no recorded hashes. The full-prefix state is
    # compared to the authoritative tick below; that tick checks the real tape.
    let tape = ReplayData(actions: actions[0 .. i])
    let probe = Game(map: game.map, world: before.clone(), metrics: newMetrics(totals.len, TickRate),
      replayData: tape, replayPlayer: ReplayPlayer(data: tape), historyPlayback: true)
    probe.world.restore(before) # also restores the engine's navigationWorld pointer
    probe.tickWorld(nil)
    let current = probe.commands()
    let slot = before.heroIndex(a.heroId)
    for other in 0 ..< current.len:
      let delta = current[other] - previous[other]
      if delta < 0 or delta > 1 or (other != slot and delta != 0):
        fail("prefix replay changed an earlier action's acceptance")
    if slot >= 0: result[i] = current[slot] > previous[slot]
    previous = current
    if i == actions.high and stateHash(probe) != stateHash(game):
      fail("full prefix probe diverged from authoritative tick")
  if previous != totals: fail("prefix acceptance disagrees with full playback")
  game.world.restore(after)

proc actionEvents(output: File, world: World, actions: seq[ReplayAction],
                  accepted: seq[bool], firstIndex: int): seq[tuple[hero: int32, ability: Ability]] =
  for i, a in actions:
    let slot = world.heroIndex(a.heroId)
    var row = %*{"slot": slot, "hero_id": a.heroId, "kind": a.kind,
      "args": [a.first, a.second], "ability_slot": a.slot, "accepted": accepted[i],
      "action_index": firstIndex + i, "type": "action", "tick": a.tick}
    output.writeLine($row)
    if not accepted[i] or slot < 0: continue
    if a.kind in [ActionCastTarget, ActionCastPoint]:
      # Live replay API stores the ability index separately from the action kind.
      result.add (a.heroId, heroAbility(world.heroes[slot].class, HeroAbilitySlot(a.slot)))
  # Read authoritative inventory events instead of inferring item effects from commands.
  # The live engine consumes teleport scrolls when the channel starts.
  for event in world.events:
    if event.kind notin [ItemPurchased, ItemConsumed, GoldSpent]: continue
    let slot = int(event.target.player)
    if slot < 0: continue
    var row = world.seatRow(slot)
    row["item"] = %event.detail
    row["amount"] = %event.amount
    row["cause"] = %($event.cause)
    let kind = case event.kind
      of ItemPurchased: "item_bought"
      of ItemConsumed: "item_used"
      else: "gold_spent"
    output.emit(row, world.tick, kind)

type Totals = object
  lastHits, buildings, towerMin, towerMax, barracksMin, barracksMax: int
  xp, gold: int
  lastKiller: int

proc events(output: File, before, world: World, totals: var seq[Totals],
            explicit: seq[tuple[hero: int32, ability: Ability]],
            actionsEnabled: bool, attributionComplete: var bool) =
  let tick = world.tick
  var killers, deaths, creditedBuildings: seq[int]
  var destroyed: seq[int]
  for i, b in world.buildings:
    if before.buildings[i].hp > 0 and b.hp <= 0:
      destroyed.add i
      output.emit(buildingRow(b), tick, "building_destroyed")
  for slot, h in world.heroes:
    let v = world.stats.values[slot]
    let old = before.stats.values[slot]
    let kills = int(v[KillsMetric] - old[KillsMetric])
    for k in 0 ..< kills: killers.add slot
    for d in 0 ..< int(v[LossesMetric] - old[LossesMetric]): deaths.add slot
    var xp, gold: int
    for event in world.events:
      if event.target.player != slot: continue
      if event.kind == XpGained:
        xp += int(event.amount)
      elif event.kind == GoldGained:
        gold += int(event.amount)
        if event.amount > 0 and event.actor.kind == FootmanObjectKind:
          inc totals[slot].lastHits
          output.emit(world.seatRow(slot), tick, "last_hit")
        elif event.amount > 0 and event.actor.kind in [TowerObjectKind, BarracksObjectKind]:
          inc totals[slot].buildings
          creditedBuildings.add slot
      if event.kind in [XpGained, GoldGained]:
        var row = world.seatRow(slot)
        row["amount"] = %event.amount
        row["source_id"] = %event.actor.id
        row["source_kind"] = %event.actor.kind
        row["cause"] = %($event.cause)
        output.emit(row, tick, if event.kind == XpGained: "xp_gained" else: "gold_gained")
    if xp != h.totalXp - before.heroes[slot].totalXp or
        gold != int(v[GoldMetric] - old[GoldMetric]):
      fail("engine reward events disagree with state at tick " & $tick & " seat " & $slot)
    totals[slot].xp += xp
    totals[slot].gold += gold
    for n in 0 ..< int(v[AssistsMetric] - old[AssistsMetric]):
      output.emit(world.seatRow(slot), tick, "assist")
    if before.heroes[slot].hp <= 0 and h.hp > 0:
      var row = world.seatRow(slot)
      row["pos"] = position(h.position)
      row["killer_slot"] = (if totals[slot].lastKiller == -2: newJNull()
                            else: %totals[slot].lastKiller)
      output.emit(row, tick, "hero_respawn")
    if h.level > before.heroes[slot].level:
      var row = world.seatRow(slot)
      row["previous_level"] = %before.heroes[slot].level
      row["level"] = %h.level
      output.emit(row, tick, "level_up")
  for victim in deaths:
    let possible = killers.filterIt(world.heroes[it].team != world.heroes[victim].team)
    let victims = deaths.filterIt(world.heroes[it].team == world.heroes[victim].team)
    let unique = possible.len == 0 or
      (possible.deduplicate().len == 1 and possible.len == victims.len)
    let killer = if not unique: -2 elif possible.len > 0: possible[0] else: -1
    totals[victim].lastKiller = killer
    var row = world.seatRow(victim)
    row["pos"] = position(world.heroes[victim].position)
    row["killer_slot"] = (if unique: %killer else: newJNull())
    # Event-time positions avoid estimating fight proximity from sparse snapshots.
    var heroesBefore = newJArray()
    for slot, hero in before.heroes:
      heroesBefore.add %*{"slot": slot, "hp": hero.hp, "pos": position(hero.position)}
    row["heroes_before"] = heroesBefore
    if not unique:
      attributionComplete = false
      var candidates = possible.deduplicate()
      if possible.len < victims.len: candidates.add -1
      row["candidate_killer_slots"] = %candidates
      output.emit(%*{"subject": "hero_death", "victim_slot": victim,
        "candidate_killer_slots": candidates}, tick, "ambiguous_attribution")
    output.emit(row, tick, "hero_death")
  for killer in killers:
    let victims = deaths.filterIt(world.heroes[it].team != world.heroes[killer].team)
    var row = world.seatRow(killer)
    row["victim_slot"] = (if victims.len == 1: %victims[0] else: newJNull())
    row["victim_hero_id"] = (if victims.len == 1: %world.heroes[victims[0]].id
                              else: newJNull())
    if victims.len != 1:
      attributionComplete = false
      row["candidate_victim_slots"] = %victims
      output.emit(%*{"subject": "hero_kill", "slot": killer,
        "hero_id": world.heroes[killer].id, "candidate_victim_slots": victims},
        tick, "ambiguous_attribution")
    output.emit(row, tick, "hero_kill")
  for team in Team:
    let credits = creditedBuildings.filterIt(world.heroes[it].team != team)
    if credits.len == 0: continue
    let targets = destroyed.filterIt(world.buildings[it].team == team)
    let towerCount = targets.countIt(world.buildings[it].kind == TowerBuilding)
    let barracksCount = targets.len - towerCount
    let unique = credits.deduplicate().len == 1 and credits.len == targets.len
    var candidates = newJArray()
    for index in targets: candidates.add buildingRow(world.buildings[index])
    for slot in credits.deduplicate():
      let count = credits.countIt(it == slot)
      let towerMin = max(0, count - barracksCount)
      let towerMax = min(count, towerCount)
      totals[slot].towerMin += towerMin
      totals[slot].towerMax += towerMax
      totals[slot].barracksMin += count - towerMax
      totals[slot].barracksMax += count - towerMin
      if not unique:
        attributionComplete = false
        output.emit(%*{"subject": "building_kill", "slot": slot,
          "hero_id": world.heroes[slot].id, "count": count,
          "candidate_victims": candidates, "tower_kills_min": towerMin,
          "tower_kills_max": towerMax, "barracks_kills_min": count - towerMax,
          "barracks_kills_max": count - towerMin}, tick, "ambiguous_attribution")
      for n in 0 ..< count:
        var row: JsonNode
        if unique:
          row = buildingRow(world.buildings[targets[n]])
        else:
          row = %*{"object_id": newJNull(), "team": $team,
            "lane": newJNull(), "tier": newJNull(),
            "building_kind": (if towerMin == count: %"tower"
              elif towerMax == 0: %"barracks" else: newJNull()),
            "candidate_victims": candidates}
        row["slot"] = %slot
        row["hero_id"] = %world.heroes[slot].id
        output.emit(row, tick, "building_kill")
  for i, fort in world.forts:
    if fort.hp < before.forts[i].hp:
      let row = %*{"object_id": fort.id, "team": $fort.team,
        "hp": fort.hp, "previous_hp": before.forts[i].hp,
        "amount": before.forts[i].hp - fort.hp, "pos": position(fort.center)}
      output.emit(row.copy(), tick, "fort_damaged")
      if fort.hp <= 0: output.emit(row, tick, "fort_destroyed")
  var matched = newSeq[bool](explicit.len)
  for spell in world.casts:
    if spell.started != tick: continue
    let slot = world.heroIndex(spell.heroId)
    var isExplicit = false
    for i, entry in explicit:
      if not matched[i] and entry.hero == spell.heroId and entry.ability == spell.ability:
        matched[i] = true
        isExplicit = true
        break
    var abilitySlot = -1
    for i in HeroAbilitySlot:
      if heroAbility(world.heroes[slot].class, i) == spell.ability: abilitySlot = i.ord
    var row = world.seatRow(slot)
    row["ability"] = %spell.ability.ord
    row["ability_name"] = %($spell.ability)
    row["slot_index"] = %abilitySlot
    row["target_id"] = %spell.targetId
    row["point"] = position(spell.position)
    row["origin"] = position(spell.origin)
    row["auto"] = (if actionsEnabled: %(not isExplicit) else: newJNull())
    row["explicit"] = (if actionsEnabled: %isExplicit else: newJNull())
    output.emit(row, tick, "cast")
  if matched.anyIt(not it): fail("accepted explicit cast missing from world.casts")

proc main() =
  let args = commandLineParams()
  if args.len < 2:
    fail("usage: expand_replay REPLAY OUT_JSONL [--snapshot-every N] [--episode episode.json] [--no-actions]")
  var snapshotEvery = 0
  var actionsEnabled = true
  var attributionComplete = true
  var episode = newJNull()
  var index = 2
  while index < args.len:
    if args[index] in ["--actions", "--no-actions"]:
      actionsEnabled = args[index] == "--actions"
      inc index
      continue
    if index + 1 >= args.len: fail("missing option value")
    case args[index]
    of "--snapshot-every": snapshotEvery = parseInt(args[index+1])
    of "--episode": episode = parseFile(args[index+1])
    else: fail("unknown option: " & args[index])
    index += 2
  if snapshotEvery < 0: fail("snapshot interval must be nonnegative")
  let raw = readFile(args[0])
  let bytes = if raw.startsWith("\x1f\x8b"): uncompress(raw) else: raw
  if not bytes.startsWith("POLYWORLDREPLAY"): fail("not a POLYWORLDREPLAY file")
  let data = decodeReplay(bytes)
  if data.hashes.len == 0: fail("replay contains no recorded ticks")
  let game = newGame(generateMap(data.config.seed, data.config.mapPreset),
    data.config.spawnIntervalTicks, 0, true, data)
  game.replayPlayer = initReplayPlayer(data)
  game.historyPlayback = true
  let output = open(args[1], fmWrite)
  defer: output.close()
  var roster = newJArray()
  for slot in 0 ..< game.world.heroes.len: roster.add game.world.seatRow(slot)
  output.emit(%*{"replay": args[0], "game_version": data.header.gameVersion,
    "seed": data.config.seed, "ticks_recorded": data.hashes.len,
    "grid_tiles": data.header.setup.gridTiles, "tick_rate": TickRate,
    "world_scale": WorldScale, "heroes": roster,
    "reward_source": "Polyworld replayEvents XpGained/GoldGained",
    "actions_enabled": actionsEnabled, "schema_version": 2,
    "action_acceptance": (if actionsEnabled: "tickWorld command deltas with checkpoint prefix replay" else: "disabled")},
    0, "replay", "meta")
  var totals = newSeq[Totals](game.world.heroes.len)
  for total in totals.mitems: total.lastKiller = -1
  while game.world.tick < data.hashes.len:
    let before = game.world.clone()
    let first = game.replayPlayer.actionIndex
    let commandsBefore = game.commands()
    game.tickWorld(nil)
    if game.world.tick <= before.tick: fail("replay stopped before recorded end")
    if game.hashCheck.mismatches != 0:
      output.emit(%*{"verified": false, "hash_failed": true,
        "hash_error": game.hashCheck.error}, game.world.tick, "replay", "summary")
      fail("hash mismatch: " & game.hashCheck.error)
    let last = game.replayPlayer.actionIndex
    var explicit: seq[tuple[hero: int32, ability: Ability]]
    if actionsEnabled and last > first:
      let actions = data.actions[first ..< last]
      let commandsAfter = game.commands()
      var deltas = commandsAfter
      for slot in 0 ..< deltas.len: deltas[slot] -= commandsBefore[slot]
      let recordedEvents = game.world.events
      let accepted = game.actionAcceptance(before, actions, deltas)
      # Diagnostic checkpoint restore clears events; retain the authoritative tick.
      game.world.events = recordedEvents
      explicit = output.actionEvents(game.world, actions, accepted, first)
    output.events(before, game.world, totals, explicit, actionsEnabled, attributionComplete)
    if snapshotEvery > 0 and game.world.tick mod snapshotEvery == 0:
      output.sample(game.world)
  if not game.replayPlayer.finished: fail("unconsumed replay actions")
  var rows = newJArray()
  let wins = game.world.scores()
  let glory = scores(game.world.totalXp(), int(game.world.tick), wins)
  for slot, h in game.world.heroes:
    var row = game.world.heroRow(slot)
    row["tick"] = %game.world.tick
    row["win"] = %wins[slot]
    row["glory"] = %glory[slot]
    row["last_hits"] = %totals[slot].lastHits
    row["tower_kills"] = (if totals[slot].towerMin == totals[slot].towerMax:
      %totals[slot].towerMin else: newJNull())
    row["barracks_kills"] = (if totals[slot].barracksMin == totals[slot].barracksMax:
      %totals[slot].barracksMin else: newJNull())
    row["tower_kills_min"] = %totals[slot].towerMin
    row["tower_kills_max"] = %totals[slot].towerMax
    row["barracks_kills_min"] = %totals[slot].barracksMin
    row["barracks_kills_max"] = %totals[slot].barracksMax
    row["building_kills"] = %totals[slot].buildings
    if totals[slot].xp != h.totalXp or
        totals[slot].gold != int(game.world.stats.values[slot][GoldMetric]):
      fail("summary engine reward totals disagree with state")
    row["reward_xp_verified"] = %true
    row["reward_gold_verified"] = %true
    row["player_name"] = %data.config.players[slot].name
    row["policy_name"] = newJNull()
    if episode.kind == JObject:
      for p in episode["participants"]:
        if p["position"].getInt == slot:
          row["player_name"] = p{"player_name"}
          row["policy_name"] = p{"policy_name"}
    rows.add row
  let resultsPath = parentDir(args[0]) / "results.json"
  var resultsVerified = false
  if fileExists(resultsPath):
    let expected = parseFile(resultsPath)
    if expected["ticks"].getInt != game.world.tick:
      fail("results.json tick mismatch")
    if expected["scores"].len != rows.len or expected["total_xp"].len != rows.len:
      fail("results.json seat count mismatch")
    for slot, row in rows.elems:
      if expected["scores"][slot].getFloat != row["glory"].getFloat or
          expected["total_xp"][slot].getInt != row["xp"].getInt:
        fail("results.json score/XP mismatch for seat " & $slot)
    resultsVerified = true
  output.emit(%*{"verified": true, "attribution_complete": attributionComplete,
    "actions_enabled": actionsEnabled, "results_verified": resultsVerified, "hash_failed": false, "ticks": game.world.tick,
    "actions_consumed": data.actions.len,
    "outcome": (if game.world.gameOver: $game.world.winner else: "time_limit"),
    "heroes": rows}, game.world.tick, "replay", "summary")

try: main()
except CatchableError as error:
  stderr.writeLine("expand_replay: " & error.msg)
  quit(1)
