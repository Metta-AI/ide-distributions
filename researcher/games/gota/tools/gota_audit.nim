# Source: Metta-AI/co-gas @ 6742feb08f8b119591d09261cadc581065fa698b; path: players/users/relh/co-gas/polyworld-basic/tools/gota_audit.nim
import std/[os,json,strutils,sets]
import examples/gods_of_the_arena/[replays,sim,maps,bots,content]
import polyworld/cli
if paramStr(1) == "--smoke":
  let seed = if paramCount() >= 3: parseInt(paramStr(3)) else: 2026
  let smokeTicks = if paramCount() >= 4: parseInt(paramStr(4)) else: 1000
  let game = if paramCount() >= 6:
    let replay = loadReplay(paramStr(6))
    newGame(generateMap(replay.config.seed, replay.config.mapPreset),
      240, 10, false, ReplayData())
  else:
    newGame(generateMap(int32(seed)), 240, 10, false, ReplayData())
  if paramCount() >= 5:
    game.loadBots([
      BotGroup(count: 5, path: paramStr(2)),
      BotGroup(count: 5, path: paramStr(5))
    ])
  else:
    game.loadBots([BotGroup(count: 10, path: paramStr(2))])
  if paramCount() >= 7 and paramStr(7) in ["close-contact", "ranged-contact"]:
    let defender = game.world.heroes[1]
    let offset =
      if paramStr(7) == "ranged-contact": 480_000'i32
      else: 240_000'i32
    game.world.heroes[5].place(WorldPoint(
      x: defender.position.x - offset,
      y: defender.position.y,
      z: defender.position.z
    ))
  if paramCount() >= 7 and paramStr(7) == "injured-druid":
    game.world.heroes[8].hp = 100
  if paramCount() >= 7 and paramStr(7) == "injured-druid-contact":
    let druid = game.world.heroes[8]
    druid.hp = 100
    game.world.heroes[0].place(WorldPoint(
      x: druid.position.x + 120_000,
      y: druid.position.y,
      z: druid.position.z
    ))
  if paramCount() >= 7 and paramStr(7) == "vanguard-threat":
    let enemy = game.world.heroes[4]
    game.world.heroes[5].place(WorldPoint(
      x: enemy.position.x + WorldScale,
      y: enemy.position.y,
      z: enemy.position.z + WorldScale
    ))
  if paramCount() >= 7 and paramStr(7) == "lich-tower-contact":
    let tower = game.world.buildings[3]
    game.world.heroes[2].class = Lich
    game.world.heroes[2].place(WorldPoint(
      x: tower.position.x + 3 * WorldScale,
      y: tower.position.y,
      z: tower.position.z - 5 * WorldScale
    ))
    game.world.heroes[7].place(WorldPoint(
      x: tower.position.x - WorldScale,
      y: tower.position.y,
      z: tower.position.z - WorldScale
    ))
  if paramCount() >= 7 and paramStr(7) == "demon-tower-contact":
    let tower = game.world.buildingById(16)
    game.world.heroes[9].place(WorldPoint(
      x: tower.position.x - 4 * WorldScale,
      y: tower.position.y,
      z: tower.position.z + 6 * WorldScale
    ))
    game.world.heroes[2].place(WorldPoint(
      x: tower.position.x - 5 * WorldScale,
      y: tower.position.y,
      z: tower.position.z + 6 * WorldScale
    ))
  if paramCount() >= 7 and paramStr(7) == "magic-poison-contact":
    let mage = game.world.heroes[7]
    game.world.heroes[4].place(WorldPoint(
      x: mage.position.x + 2 * WorldScale,
      y: mage.position.y,
      z: mage.position.z
    ))
    game.world.heroes[8].place(WorldPoint(
      x: mage.position.x,
      y: mage.position.y,
      z: mage.position.z + WorldScale
    ))
  if paramCount() >= 7 and paramStr(7) == "critical-home-anchor":
    let home = game.world.forts[1]
    game.world.forts[1].hp = 108
    game.world.heroes[7].place(WorldPoint(
      x: home.center.x + 4 * WorldScale,
      y: home.center.y,
      z: home.center.z - 7 * WorldScale
    ))
  if paramCount() >= 7 and paramStr(7) == "home-wave-intercept":
    game.tickWorld(proc() = discard)
    let home = game.world.forts[1]
    game.world.heroes[5].place(WorldPoint(
      x: home.center.x + 10 * WorldScale,
      y: home.center.y,
      z: home.center.z - 10 * WorldScale
    ))
    var moved = 0
    for footman in game.world.footmen.mitems:
      if footman.team == RedTeam and moved < 3:
        footman.place(WorldPoint(
          x: home.center.x + (16'i32 + moved.int32) * WorldScale,
          y: home.center.y,
          z: home.center.z - 16 * WorldScale
        ))
        inc moved
    doAssert moved == 3
  if paramCount() >= 7 and paramStr(7) == "home-hero-pair-intercept":
    let home = game.world.forts[1]
    game.world.heroes[5].place(WorldPoint(
      x: home.center.x + 30 * WorldScale,
      y: home.center.y,
      z: home.center.z
    ))
    game.world.heroes[0].place(WorldPoint(
      x: home.center.x + 10 * WorldScale,
      y: home.center.y,
      z: home.center.z
    ))
    game.world.heroes[1].place(WorldPoint(
      x: home.center.x + 12 * WorldScale,
      y: home.center.y,
      z: home.center.z + 2 * WorldScale
    ))
  if paramCount() >= 7 and paramStr(7) in
      ["critical-home-threat", "critical-home-clear"]:
    let home = game.world.forts[1]
    game.world.forts[1].hp = 108
    game.world.heroes[7].place(WorldPoint(
      x: home.center.x + 4 * WorldScale,
      y: home.center.y,
      z: home.center.z - 7 * WorldScale
    ))
    if paramStr(7) == "critical-home-threat":
      game.world.heroes[1].place(WorldPoint(
        x: home.center.x + 14 * WorldScale,
        y: home.center.y,
        z: home.center.z - 16 * WorldScale
      ))
  if paramCount() >= 7 and paramStr(7) in ["demon-tower-wave-behind", "demon-tower-wave-leading"]:
    game.tickWorld(proc() = discard)
    let tower = game.world.buildingById(16)
    game.world.heroes[9].place(WorldPoint(
      x: tower.position.x - 4 * WorldScale,
      y: tower.position.y,
      z: tower.position.z + 6 * WorldScale
    ))
    for footmanIndex in 0 ..< game.world.footmen.len:
      if game.world.footmen[footmanIndex].team == BlueTeam:
        let offset = if paramStr(7) == "demon-tower-wave-behind": 7'i32 else: 5'i32
        game.world.footmen[footmanIndex].place(WorldPoint(
          x: tower.position.x - 4 * WorldScale,
          y: tower.position.y,
          z: tower.position.z + offset * WorldScale
        ))
        break
  if paramCount() >= 7 and paramStr(7) == "isolated-lich":
    let tower = game.world.buildings[3]
    game.world.heroes[2].class = Lich
    game.world.heroes[2].place(WorldPoint(
      x: tower.position.x + 20 * WorldScale,
      y: tower.position.y,
      z: tower.position.z - 20 * WorldScale
    ))
  if paramCount() >= 7 and paramStr(7) == "home-threat-lich":
    let home = game.world.forts[0]
    let gate = game.world.buildings[8]
    game.world.heroes[2].class = Lich
    game.world.heroes[2].place(WorldPoint(
      x: home.center.x + 5 * WorldScale,
      y: home.center.y,
      z: home.center.z - 5 * WorldScale
    ))
    game.world.heroes[7].place(WorldPoint(
      x: gate.position.x,
      y: gate.position.y,
      z: gate.position.z
    ))
  if paramCount() >= 7 and paramStr(7) == "barracks-contact-lich":
    let barracks = game.world.buildingById(50)
    for i in 0 ..< game.world.buildings.len:
      if game.world.buildings[i].kind == TowerBuilding and
          game.world.buildings[i].team == barracks.team and
          game.world.buildings[i].lane == barracks.lane:
        game.world.buildings[i].hp = 0
    game.world.heroes[2].class = Lich
    game.world.heroes[2].place(WorldPoint(
      x: barracks.position.x + 4 * WorldScale,
      y: barracks.position.y,
      z: barracks.position.z - 4 * WorldScale
    ))
  if paramCount() >= 7 and paramStr(7) in ["gate-finish-lich", "gate-finish-lich-weakened", "gate-finish-lich-wave"]:
    if paramStr(7) == "gate-finish-lich-wave":
      game.tickWorld(proc() = discard)
      doAssert game.world.footmen.len > 0
    game.world.buildings[3].hp = 0
    game.world.buildings[4].hp = 0
    if paramStr(7) == "gate-finish-lich-weakened":
      game.world.buildings[5].hp = 300
    let gate = game.world.buildings[5]
    game.world.heroes[2].class = Lich
    game.world.heroes[2].place(WorldPoint(
      x: gate.position.x + 6 * WorldScale,
      y: gate.position.y,
      z: gate.position.z - 6 * WorldScale
    ))
    game.world.heroes[7].place(WorldPoint(
      x: gate.position.x + 5 * WorldScale,
      y: gate.position.y,
      z: gate.position.z - 6 * WorldScale
    ))
    if paramStr(7) == "gate-finish-lich-wave":
      for footmanIndex in 0 ..< game.world.footmen.len:
        if game.world.footmen[footmanIndex].team == RedTeam:
          game.world.footmen[footmanIndex].place(WorldPoint(
            x: gate.position.x + 3 * WorldScale,
            y: gate.position.y,
            z: gate.position.z - 3 * WorldScale
          ))
          break
  if paramCount() >= 7 and paramStr(7) in
      ["crossbow-gate-wave", "crossbow-gate-no-wave", "crossbow-gate-low-hp"]:
    game.tickWorld(proc() = discard)
    game.world.buildings[3].hp = 0
    game.world.buildings[4].hp = 0
    let gate = game.world.buildings[5]
    game.world.heroes[1].class = Crossbowman
    game.world.heroes[1].place(WorldPoint(
      x: gate.position.x + 6 * WorldScale,
      y: gate.position.y,
      z: gate.position.z - 6 * WorldScale
    ))
    if paramStr(7) == "crossbow-gate-low-hp":
      game.world.heroes[1].hp = game.world.heroes[1].maxHp div 3
    game.world.heroes[7].place(WorldPoint(
      x: gate.position.x + 5 * WorldScale,
      y: gate.position.y,
      z: gate.position.z - 6 * WorldScale
    ))
    if paramStr(7) != "crossbow-gate-no-wave":
      for footmanIndex in 0 ..< game.world.footmen.len:
        if game.world.footmen[footmanIndex].team == RedTeam:
          game.world.footmen[footmanIndex].place(WorldPoint(
            x: gate.position.x + 3 * WorldScale,
            y: gate.position.y,
            z: gate.position.z - 3 * WorldScale
          ))
          break
  if paramCount() >= 7 and paramStr(7) == "death-knight-wave-gap":
    game.tickWorld(proc() = discard)
    let deathKnight = game.world.heroes[0]
    deathKnight.place(WorldPoint(
      x: deathKnight.position.x +
        (17 - mapCoordinate(deathKnight.position.x)) * WorldScale,
      y: deathKnight.position.y,
      z: deathKnight.position.z +
        (14 - mapCoordinate(deathKnight.position.z)) * WorldScale
    ))
    let ranger = game.world.heroes[6]
    ranger.place(WorldPoint(
      x: ranger.position.x +
        (19 - mapCoordinate(ranger.position.x)) * WorldScale,
      y: ranger.position.y,
      z: ranger.position.z +
        (14 - mapCoordinate(ranger.position.z)) * WorldScale
    ))
    for footmanIndex in 0 ..< game.world.footmen.len:
      if game.world.footmen[footmanIndex].team == RedTeam:
        game.world.footmen[footmanIndex].place(WorldPoint(
          x: game.world.footmen[footmanIndex].position.x +
            (51 - mapCoordinate(game.world.footmen[footmanIndex].position.x)) * WorldScale,
          y: game.world.footmen[footmanIndex].position.y,
          z: game.world.footmen[footmanIndex].position.z +
            (14 - mapCoordinate(game.world.footmen[footmanIndex].position.z)) * WorldScale
        ))
        break
  if paramCount() >= 7 and paramStr(7) == "formation-outnumbered":
    game.tickWorld(proc() = discard)
    let testHero = game.world.heroes[8]
    testHero.class = DruidWarden
    for allyIndex in [5, 6, 7, 9]:
      game.world.heroes[allyIndex].hp = 0
    var friendlyPlaced = false
    for footman in game.world.footmen.mitems:
      if footman.team == testHero.team and not friendlyPlaced:
        footman.place(WorldPoint(
          x: testHero.position.x,
          y: testHero.position.y,
          z: testHero.position.z + 10 * WorldScale
        ))
        friendlyPlaced = true
    for enemyIndex in 0 .. 2:
      let enemy = game.world.heroes[enemyIndex]
      enemy.place(WorldPoint(
        x: testHero.position.x + (12 + 2 * enemyIndex.int32) * WorldScale,
        y: testHero.position.y,
        z: testHero.position.z
      ))
    doAssert friendlyPlaced
  if paramCount() >= 7 and paramStr(7) == "fort-finish-low-hp":
    let testHero = game.world.heroes[4]
    var enemyFortIndex = 0
    if game.world.forts[enemyFortIndex].team == testHero.team:
      enemyFortIndex = 1
    game.world.forts[enemyFortIndex].hp = 100
    for building in game.world.buildings.mitems:
      if building.guardsGod and
          building.team == game.world.forts[enemyFortIndex].team:
        building.hp = 0
    testHero.hp = testHero.maxHp div 3
    testHero.place(WorldPoint(
      x: game.world.forts[enemyFortIndex].center.x + 6 * WorldScale,
      y: game.world.forts[enemyFortIndex].center.y,
      z: game.world.forts[enemyFortIndex].center.z
    ))
    let nearbyEnemy = game.world.heroes[5]
    nearbyEnemy.place(WorldPoint(
      x: testHero.position.x + 2 * WorldScale,
      y: testHero.position.y,
      z: testHero.position.z
    ))
    doAssert game.world.fortExposed(game.world.forts[enemyFortIndex].team)
  if paramCount() >= 7 and paramStr(7) == "reserved-heal-slot":
    let testHero = game.world.heroes[8]
    testHero.inventory = [
      RangerBoots, CrimsonDagger, SunsteelLongsword,
      KnightArmor, BattleAxe, NoItem
    ]
    testHero.itemCounts = [1'i32, 1, 1, 1, 1, 0]
    testHero.gold = 500
    testHero.refreshHeroStats()
    testHero.hp = testHero.maxHp
  game.recorder = initReplayRecorder(Setup())
  ## initReplayRecorder(Setup()) has a zero battle budget. Without a budget,
  ## the loop silently stops after drafting and reports an all-zero smoke.
  game.recorder.data.config.maxTicks = int32(smokeTicks)
  for tick in 1..smokeTicks:
    if paramCount() >= 7 and paramStr(7) == "reserved-heal-slot" and tick == 3:
      game.world.heroes[8].hp = game.world.heroes[8].maxHp div 3
    game.tickWorld(proc() = game.runBotDecisions())
    for vm in game.heroVms:
      if vm != nil:
        doAssert not vm.failed, "Policy VM failed during diagnostic smoke"
  var heroes = newJArray()
  var commands: array[2, array[5, int]]
  var commandsByClass: array[HeroClassCount, array[5, int]]
  var barracksAttacksByClass: array[HeroClassCount, int]
  var purchasesByClass: array[HeroClassCount, array[21, int]]
  var usesByClass: array[HeroClassCount, int]
  var castsByClass: array[HeroClassCount, array[4, int]]
  var crossbowWalkTrace = newJArray()
  var lichWalkTrace = newJArray()
  var earlyWarlockBerserkerWalkTrace = newJArray()
  var earlyVanguardWalkTrace = newJArray()
  var crossbowWalkPhases = newJArray()
  var homeWaveActionTrace = newJArray()
  var deathKnightActionTrace = newJArray()
  var crossbowGateActionTrace = newJArray()
  var formationActionTrace = newJArray()
  for action in game.recorder.data.actions:
    if action.heroId == 108 and action.tick <= 20:
      formationActionTrace.add %*{
        "tick":action.tick,"kind":action.kind,
        "first":action.first,"second":action.second
      }
    if action.heroId == 101 and action.tick <= 20:
      crossbowGateActionTrace.add %*{
        "tick":action.tick,"kind":action.kind,
        "first":action.first,"second":action.second
      }
    if action.heroId == 100 and action.tick <= 20:
      deathKnightActionTrace.add %*{
        "tick":action.tick,"kind":action.kind,
        "first":action.first,"second":action.second
      }
    if action.heroId == 105 and action.tick <= 20:
      homeWaveActionTrace.add %*{
        "tick":action.tick,"kind":action.kind,
        "first":action.first,"second":action.second
      }
    var team = -1
    var heroClass = -1
    for hero in game.world.heroes:
      if hero.id == action.heroId:
        team = hero.team.ord
        heroClass = hero.class.ord
        break
    if team < 0:
      continue
    if action.kind == ActionBuyItem and action.first >= 0 and action.first < 21:
      inc purchasesByClass[heroClass][action.first]
    if action.kind == ActionUseItem:
      inc usesByClass[heroClass]
    if action.kind in [ActionCastTarget, ActionCastPoint] and action.slot in 0 .. 3:
      inc castsByClass[heroClass][int(action.slot)]
    if action.kind == ActionWalkTo:
      if heroClass == 0 and action.tick <= 20 and
          earlyVanguardWalkTrace.len < 20:
        earlyVanguardWalkTrace.add %*{
          "tick":action.tick,"heroId":action.heroId,
          "team":team,"destination":[action.first,action.second]
        }
      if heroClass in [8, 9] and action.tick <= 300 and
          earlyWarlockBerserkerWalkTrace.len < 40:
        earlyWarlockBerserkerWalkTrace.add %*{
          "tick":action.tick,"heroId":action.heroId,
          "team":team,"class":heroClass,
          "destination":[action.first,action.second]
        }
      if heroClass == 7 and action.tick <= 20 and lichWalkTrace.len < 20:
        lichWalkTrace.add %*{
          "tick":action.tick,"destination":[action.first,action.second]
        }
      if heroClass == 6 and crossbowWalkTrace.len < 40:
        crossbowWalkTrace.add %*{
          "tick": action.tick,
          "destination": [action.first, action.second]
        }
      if heroClass == 6 and (action.tick == 1 or action.tick mod 100 == 0) and
          crossbowWalkPhases.len < 60:
        crossbowWalkPhases.add %*{
          "tick": action.tick,
          "destination": [action.first, action.second]
        }
      inc commands[team][0]
      inc commandsByClass[heroClass][0]
    elif action.kind == ActionAttackTarget:
      if action.first >= 40 and action.first < 52:
        inc barracksAttacksByClass[heroClass]
      if action.first >= 1000:
        inc commands[team][1]
        inc commandsByClass[heroClass][1]
      elif action.first >= 100:
        inc commands[team][2]
        inc commandsByClass[heroClass][2]
      elif action.first >= 10:
        inc commands[team][3]
        inc commandsByClass[heroClass][3]
      elif action.first > 0:
        inc commands[team][4]
        inc commandsByClass[heroClass][4]
  for hero in game.world.heroes:
    let enemy = game.world.forts[if hero.team == RedTeam: 1 else: 0]
    let
      x = mapCoordinate(hero.position.x)
      y = mapCoordinate(hero.position.z)
      enemyX = mapCoordinate(enemy.center.x)
      enemyY = mapCoordinate(enemy.center.z)
    heroes.add %*{
      "id":hero.id,"team":hero.team.ord,"x":x,"y":y,
      "enemyFortDistanceSquared":(x - enemyX) * (x - enemyX) +
        (y - enemyY) * (y - enemyY),
      "hp":hero.hp,"maxHp":hero.maxHp,
      "xp":hero.totalXp,"gold":hero.gold,"inventory":hero.inventory,
      "mana":hero.mana,"maxMana":hero.maxMana,
      "attacksLanded":hero.attacksLanded,
      "moveX":hero.moveTileX,"moveY":hero.moveTileY
    }
  var lichVisibleObjects = newJArray()
  var fixtureLichVisibleObjects = newJArray()
  var homeWaveVisibleFootmen = newJArray()
  var deathKnightVisibleObjects = newJArray()
  var visible: WorldObject
  for objectIndex in 0 ..< game.world.worldObjectCount(100):
    if game.world.worldObjectAt(100, objectIndex, visible):
      deathKnightVisibleObjects.add %*{
        "id":visible.id,"kind":visible.kind,"team":visible.team.ord,
        "x":mapCoordinate(visible.position.x),
        "y":mapCoordinate(visible.position.z)
      }
  for objectIndex in 0 ..< game.world.worldObjectCount(105):
    if game.world.worldObjectAt(105, objectIndex, visible) and
        visible.alive and visible.team == RedTeam and visible.kind == 3:
      homeWaveVisibleFootmen.add %*{
        "id":visible.id,
        "x":mapCoordinate(visible.position.x),
        "y":mapCoordinate(visible.position.z)
      }
  let lich = game.world.heroes[7]
  for objectIndex in 0 ..< game.world.worldObjectCount(lich.id):
    if game.world.worldObjectAt(lich.id, objectIndex, visible) and
        visible.alive and visible.team != lich.team:
      lichVisibleObjects.add %*{
        "id":visible.id,"kind":visible.kind,
        "x":mapCoordinate(visible.position.x),
        "y":mapCoordinate(visible.position.z)
      }
  let fixtureLich = game.world.heroes[2]
  for objectIndex in 0 ..< game.world.worldObjectCount(fixtureLich.id):
    if game.world.worldObjectAt(fixtureLich.id, objectIndex, visible) and
        visible.team != fixtureLich.team and visible.kind == 5:
      fixtureLichVisibleObjects.add %*{
        "id":visible.id,"kind":visible.kind,"alive":visible.alive,
        "x":mapCoordinate(visible.position.x),
        "y":mapCoordinate(visible.position.z)
      }
  let smokePhase = $game.world.phase
  let smokeBattleTicks = game.world.battleTick()
  echo $(%*{
    "smokeTicks":smokeTicks,"seed":seed,"heroes":heroes,
    "phase":smokePhase,"draftTurn":game.world.draftTurn,
    "draftTicks":game.world.draftTicks,"battleTick":smokeBattleTicks,
    "commandKinds":["walk","footman","hero","tower","fort"],
    "commandsByTeam":commands,"commandsByClass":commandsByClass,
    "barracksAttacksByClass":barracksAttacksByClass,
    "purchasesByClass":purchasesByClass,"usesByClass":usesByClass,
    "castsByClass":castsByClass,
    "crossbowWalkTrace":crossbowWalkTrace,
    "crossbowWalkPhases":crossbowWalkPhases,
    "homeWaveActionTrace":homeWaveActionTrace,
    "deathKnightActionTrace":deathKnightActionTrace,
    "crossbowGateActionTrace":crossbowGateActionTrace,
    "formationActionTrace":formationActionTrace,
    "lichWalkTrace":lichWalkTrace,
    "earlyWarlockBerserkerWalkTrace":earlyWarlockBerserkerWalkTrace,
    "earlyVanguardWalkTrace":earlyVanguardWalkTrace,
    "homeWaveVisibleFootmen":homeWaveVisibleFootmen,
    "deathKnightVisibleObjects":deathKnightVisibleObjects,
    "lichVisibleEnemies":lichVisibleObjects
    ,"fixtureLichVisibleBarracks":fixtureLichVisibleObjects
  })
  quit(0)
if paramStr(1) == "--match":
  let seed = if paramCount() >= 4: parseInt(paramStr(4)) else: 2026
  let game = newGame(generateMap(int32(seed)), 240, 10, false, ReplayData())
  game.replayData.config.maxTicks = 28_800
  game.loadBots([
    BotGroup(count: 5, path: paramStr(2)),
    BotGroup(count: 5, path: paramStr(3))
  ])
  while game.world.tick < 28_800 and not game.world.gameOver:
    game.tickWorld(proc() = game.runBotDecisions())
  var classes, levels, xp, gold, deaths, hits = newJArray()
  for hero in game.world.heroes:
    classes.add %hero.class.ord
    levels.add %hero.level
    xp.add %hero.totalXp
    gold.add %hero.gold
    deaths.add %hero.deaths
    hits.add %hero.attacksLanded
  var forts = newJArray()
  for fort in game.world.forts:
    forts.add %max(fort.hp, 0)
  var towers = newJArray()
  for tower in game.world.buildings:
    towers.add %max(tower.hp, 0)
  echo $(%*{
    "seed": seed,
    "ticks": game.world.tick,
    "gameOver": game.world.gameOver,
    "winner": (if game.world.gameOver: game.world.winner.ord else: -1),
    "fortHp": forts,
    "towerHp": towers,
    "classIds": classes,
    "levels": levels,
    "totalXp": xp,
    "gold": gold,
    "deaths": deaths,
    "attacksLanded": hits,
    "draftTicks": game.world.draftTicks,
    "battleTick": game.world.battleTick()
  })
  quit(0)
let data = loadReplay(paramStr(1))
if paramCount() >= 2 and paramStr(2) == "--header":
  # Metadata only: use episode participant policy IDs to resolve identities.
  # This does not replay the simulation or validate its state hashes.
  echo $(%*{"players": data.config.players, "seed": data.config.seed,
    "mapPreset": data.config.mapPreset,
    "setup": data.header.setup.heroes, "hashesVerified": false})
  quit(0)
let tracePlayerName = if paramCount() >= 2: paramStr(2) else: "Andrew Brower"
if paramCount() >= 3 and paramStr(2) == "--walks":
  let targetName = paramStr(3)
  var targetHeroIds = initHashSet[int32]()
  for i, player in data.config.players:
    if player.name == targetName:
      targetHeroIds.incl(data.header.setup.heroes[i].id)
  var coordinates = newJObject()
  for action in data.actions:
    if action.heroId in targetHeroIds and
        action.kind in [ActionWalkTo, ActionAttackMove]:
      let key = $action.first & "," & $action.second
      coordinates[key] = %(if coordinates.hasKey(key):
          coordinates[key].getInt + 1 else: 1)
  echo $(%*{"player": targetName, "walkCoordinates": coordinates})
  quit(0)
if paramCount() >= 3 and paramStr(2) == "--walk-phases":
  let targetName = paramStr(3)
  var targetHeroIds = initHashSet[int32]()
  for i, player in data.config.players:
    if player.name == targetName:
      targetHeroIds.incl(data.header.setup.heroes[i].id)
  var coordinates = newJObject()
  for action in data.actions:
    if action.heroId in targetHeroIds and
        action.kind in [ActionWalkTo, ActionAttackMove]:
      var team, heroClass = 0
      for hero in data.header.setup.heroes:
        if hero.id == action.heroId:
          team = int(hero.team)
          heroClass = int(hero.class)
          break
      let key = $team & ":" & $heroClass & ":" & $(action.tick div 500) & ":" &
        $action.first & "," & $action.second
      coordinates[key] = %(if coordinates.hasKey(key):
          coordinates[key].getInt + 1 else: 1)
  echo $(%*{
    "player": targetName,
    "phaseWidthTicks": 500,
    "walkCoordinatesByTeamClassAndPhase": coordinates
  })
  quit(0)
if paramCount() >= 3 and paramStr(2) == "--actions":
  let targetName = paramStr(3)
  var targetHeroIds = initHashSet[int32]()
  for i, player in data.config.players:
    if player.name == targetName:
      targetHeroIds.incl(data.header.setup.heroes[i].id)
  var kinds: array[15, int]
  var buys: array[21, int]
  var towerTargets: array[18, int]
  var fortTargets, heroTargets, footmanTargets, otherTargets: int
  for action in data.actions:
    if action.heroId notin targetHeroIds:
      continue
    if int(action.kind) < kinds.len:
      inc kinds[int(action.kind)]
    if action.kind == ActionBuyItem and action.first >= 0 and action.first < buys.len:
      inc buys[int(action.first)]
    if action.kind == ActionAttackTarget:
      if action.first >= 10 and action.first < 28:
        inc towerTargets[int(action.first - 10)]
      elif action.first >= 1000:
        inc footmanTargets
      elif action.first >= 100:
        inc heroTargets
      elif action.first > 0:
        inc fortTargets
      else:
        inc otherTargets
  var heroIds = newJArray()
  for heroId in targetHeroIds:
    heroIds.add %heroId
  echo $(%*{
    "player": targetName,
    "heroIds": heroIds,
    "actionKinds": kinds,
    "buys": buys,
    "towerTargetsById10": towerTargets,
    "attackTargets": {
      "fort": fortTargets,
      "hero": heroTargets,
      "footman": footmanTargets,
      "other": otherTargets
    }
  })
  quit(0)
let game = newGame(
  generateMap(data.config.seed, data.config.mapPreset),
  int32(data.header.setup.spawnIntervalTicks),
  data.header.setup.heroes.len,
  true,
  data
)
var index,mismatches = 0
if paramCount() >= 2 and paramStr(2) == "--action-summary":
  # Setup is validated by newGame; this fast inventory does not replay hashes.
  # Consumers must map hero IDs through hosted participants, not player names.
  var heroes = newJObject()
  for hero in data.header.setup.heroes:
    heroes[$hero.id] = %*{"class": hero.class, "team": hero.team,
      "actionKinds": {}, "attackTargetsById": {}}
  for action in data.actions:
    let heroKey = $action.heroId
    if not heroes.hasKey(heroKey):
      continue
    let actionKey = $action.kind
    let counts = heroes[heroKey]["actionKinds"]
    counts[actionKey] = %(if counts.hasKey(actionKey): counts[actionKey].getInt + 1 else: 1)
    if action.kind == ActionAttackTarget:
      let targets = heroes[heroKey]["attackTargetsById"]
      let targetKey = $action.first
      targets[targetKey] = %(if targets.hasKey(targetKey): targets[targetKey].getInt + 1 else: 1)
  var buildings = newJArray()
  for building in game.world.buildings:
    buildings.add %*{"id": building.id, "team": building.team.ord,
      "kind": building.kind.ord, "guardsGod": building.guardsGod}
  echo $(%*{"hashesVerified": false, "setupVerified": true,
    "ticks": data.hashes.len, "setup": data.header.setup.heroes,
    "heroes": heroes, "buildings": buildings})
  quit(0)
var purchases = newSeq[array[21,int]](game.world.heroes.len)
var purchaseContexts = newSeq[JsonNode](game.world.heroes.len)
var useContexts = newSeq[JsonNode](game.world.heroes.len)
for i in 0 ..< game.world.heroes.len:
  purchaseContexts[i] = newJArray()
  useContexts[i] = newJArray()
var actionCounts = newSeq[array[4,int]](game.world.heroes.len)
var casts = newSeq[array[4,int]](game.world.heroes.len)
var acceptedCasts = newSeq[array[4,int]](game.world.heroes.len)
var walkTicks = initHashSet[(int,uint32)]()
var attackTicks = initHashSet[(int,uint32)]()
var walkBeforeAttack = newSeq[int](game.world.heroes.len)
var attackBeforeWalk = newSeq[int](game.world.heroes.len)
const DistanceLimits = [1'i64, 2, 3, 4, 5, 6, 8, 10, 12, 15, 20, 30, 64]
const DistanceBinCount = DistanceLimits.len + 1
var attackTargetDistance = newSeq[array[DistanceBinCount,int]](game.world.heroes.len)
var attackFortDistance = newSeq[array[DistanceBinCount,int]](game.world.heroes.len)
var attackNearestEnemyDistance = newSeq[array[DistanceBinCount,int]](game.world.heroes.len)
var walkNearestEnemyDistance = newSeq[array[DistanceBinCount,int]](game.world.heroes.len)
var walkNearestFriendlyFootmanDistance = newSeq[array[DistanceBinCount,int]](game.world.heroes.len)
var walkDestinationNearestFriendlyFootmanDistance = newSeq[array[DistanceBinCount,int]](game.world.heroes.len)
var visibleTowerWalks = newSeq[int](game.world.heroes.len)
var visibleTowerWalkExamples = newJArray()
var walkDestinationMatches = newSeq[array[5,int]](game.world.heroes.len)
var walkHypothesisMatches = newSeq[array[12,int]](game.world.heroes.len)
var attackTargetKinds = newSeq[array[6,int]](game.world.heroes.len)
var attackWithNearbyExposedTowerKinds = newSeq[array[6,int]](game.world.heroes.len)
var contestedTowerActionKinds = newSeq[array[7,int]](game.world.heroes.len)
var inRangeHeroFinishChoices = newSeq[array[3,int]](game.world.heroes.len)
var exposedGodGuardOpportunityTargetKinds =
  newSeq[array[6,int]](game.world.heroes.len)
var exposedGodGuardOpportunityByRangeHealthWave =
  newSeq[array[2, array[2, array[2, array[6,int]]]]](game.world.heroes.len)
var godGuardAttackRequests = newSeq[int](game.world.heroes.len)
var closeGodGuardDecisionTrace = newJArray()
var closeGodGuardTraceCount = newSeq[int](game.world.heroes.len)
var homeDefenseLongHeroAttackRequests = newSeq[int](game.world.heroes.len)
var homeHeroPairOpportunities = newSeq[int](game.world.heroes.len)
var homeHeroPairMatchedActions = newSeq[int](game.world.heroes.len)
var homeHeroPairTrace = newJArray()
var gateOpportunityTargetKinds = newSeq[array[6,int]](game.world.heroes.len)
var gateOpportunityTargetsByHealthAndWave =
  newSeq[array[2, array[2, array[6,int]]]](game.world.heroes.len)
var exactGateTargetsByHealthWaveAndGateHp =
  newSeq[array[2, array[2, array[2,int]]]](game.world.heroes.len)
var gateOpportunityWalks = newSeq[int](game.world.heroes.len)
var gateOpportunityExamples = newJArray()
var barracksOpportunities = newSeq[array[7,int]](game.world.heroes.len)
var barracksOpportunityExamples = newJArray()
var earlyDemonHunterTargetTrace = newJArray()
var towerFocusExamples = newJArray()
var attackTargetKindDistance =
  newSeq[array[6, array[DistanceBinCount,int]]](game.world.heroes.len)
var attackStructureExposure = newSeq[array[4,int]](game.world.heroes.len)
var exposedFortOpportunityHealth = newSeq[array[2,int]](game.world.heroes.len)
var exposedFortOpportunityTargetKindsByHealth =
  newSeq[array[2, array[6,int]]](game.world.heroes.len)
var exposedFortOpportunityTargetsByHealthAndFortHp =
  newSeq[array[2, array[2, array[6,int]]]](game.world.heroes.len)
var exposedFortOpportunityExamples = newJArray()
var exposedFortOpportunityDistance = newSeq[array[DistanceBinCount,int]](game.world.heroes.len)
var exposedTowerOpportunityDistance = newSeq[array[DistanceBinCount,int]](game.world.heroes.len)
var attackNearestEnemyKinds = newSeq[array[6,int]](game.world.heroes.len)
var walkNearestEnemyKinds = newSeq[array[6,int]](game.world.heroes.len)
var nearestBarracksActions = newSeq[array[3,int]](game.world.heroes.len)
var nearestBarracksExamples = newJArray()
var nearestBarracksExampleCount = newSeq[int](game.world.heroes.len)
var unsupportedForwardMoves = newSeq[int](game.world.heroes.len)
var unsupportedForwardMovesByHealth = newSeq[array[2,int]](game.world.heroes.len)
var unsupportedHeroPressureMoves = newSeq[int](game.world.heroes.len)
var waveBackedTowerActions = newSeq[array[7,int]](game.world.heroes.len)
var formationExamples = newJArray()
var hypothesisTrace = newJArray()
var homeDefenseVisibilityTrace = newJArray()
var tracedWalkDestination = newSeq[array[2,int32]](game.world.heroes.len)
var tracedWalkSet = newSeq[bool](game.world.heroes.len)

proc scriptDistanceSquared(first, second: WorldPoint): int64 =
  let
    dx = int64(mapCoordinate(first.x)) - int64(mapCoordinate(second.x))
    dz = int64(mapCoordinate(first.z)) - int64(mapCoordinate(second.z))
  dx * dx + dz * dz

proc distanceBin(squared: int64): int =
  for i, limit in DistanceLimits:
    if squared <= limit * limit:
      return i
  DistanceLimits.len

proc nearestVisibleSquared(
    world: World, hero: Hero, enemy, friendlyFootman: bool, kind: var int
): int64 =
  result = int64.high
  kind = 0
  var value: WorldObject
  for objectIndex in 0 ..< world.worldObjectCount(hero.id):
    if not world.worldObjectAt(hero.id, objectIndex, value) or not value.alive:
      continue
    if enemy and value.team == hero.team:
      continue
    if not enemy and value.team != hero.team:
      continue
    if friendlyFootman and value.kind != 3:
      continue
    if value.id == hero.id:
      continue
    let squared = scriptDistanceSquared(hero.position, value.position)
    if squared < result:
      result = squared
      kind = int(value.kind)

proc recordActionContext(world: World, action: ReplayAction) =
  let heroIndex = world.heroIndex(action.heroId)
  if heroIndex < 0 or action.kind notin [ActionWalkTo, ActionAttackMove, ActionAttackTarget]:
    return
  let hero = world.heroes[heroIndex]
  let home = world.forts[if hero.team == RedTeam: 0 else: 1]
  if home.hp > 0 and scriptDistanceSquared(hero.position, home.center) <= 2025:
    var
      visibleHomeHeroes = 0
      exposedEnemyFort = false
      nearestHomeHeroDistance = int64.high
      nearestHomeHero: WorldObject
      observed: WorldObject
    for objectIndex in 0 ..< world.worldObjectCount(hero.id):
      if world.worldObjectAt(hero.id, objectIndex, observed) and observed.alive and
          observed.team != hero.team:
        if observed.kind == 1:
          exposedEnemyFort = true
        if observed.kind == 2 and
            scriptDistanceSquared(observed.position, home.center) <= 400:
          inc visibleHomeHeroes
          let distance = scriptDistanceSquared(hero.position, observed.position)
          if distance < nearestHomeHeroDistance:
            nearestHomeHeroDistance = distance
            nearestHomeHero = observed
    if visibleHomeHeroes >= 2 and not exposedEnemyFort:
      inc homeHeroPairOpportunities[heroIndex]
      let matched =
        (action.kind == ActionAttackTarget and action.first == nearestHomeHero.id) or
        (action.kind == ActionWalkTo and
          action.first == mapCoordinate(nearestHomeHero.position.x) and
          action.second == mapCoordinate(nearestHomeHero.position.z))
      if matched:
        inc homeHeroPairMatchedActions[heroIndex]
      if homeHeroPairTrace.len < 100:
        homeHeroPairTrace.add %*{
          "tick": action.tick, "heroId": hero.id, "heroClass": hero.class.ord,
          "homeHp": home.hp, "visibleEnemyHeroesWithin20Home": visibleHomeHeroes,
          "nearestHomeHeroId": nearestHomeHero.id,
          "nearestHomeHeroDistanceSquared": nearestHomeHeroDistance,
          "actionKind": action.kind, "actionFirst": action.first,
          "actionSecond": action.second, "matched": matched
        }
  if action.kind == ActionWalkTo and action.tick mod 100 == 0:
    if scriptDistanceSquared(hero.position, home.center) <= 3025:
      var visibleWave, actualWave: int
      var exposedEnemyFort = false
      var observed: WorldObject
      for objectIndex in 0 ..< world.worldObjectCount(hero.id):
        if world.worldObjectAt(hero.id, objectIndex, observed) and
            observed.alive and observed.team != hero.team:
          if observed.kind == 1:
            exposedEnemyFort = true
          if observed.kind == 3 and
              scriptDistanceSquared(observed.position, home.center) <= 1600:
            inc visibleWave
      for footman in world.footmen:
        if footman.hp > 0 and footman.team != hero.team and
            scriptDistanceSquared(footman.position, home.center) <= 1600:
          inc actualWave
      if actualWave > 0:
        homeDefenseVisibilityTrace.add %*{
          "tick": action.tick, "heroId": hero.id, "heroClass": hero.class.ord,
          "heroHp": hero.hp, "homeHp": home.hp,
          "heroX": mapCoordinate(hero.position.x),
          "heroY": mapCoordinate(hero.position.z),
          "destination": [action.first, action.second],
          "visibleEnemyWaveWithin40Home": visibleWave,
          "actualEnemyWaveWithin40Home": actualWave,
          "exposedEnemyFortVisible": exposedEnemyFort
        }
  var nearestEnemyKind, nearestFriendlyKind: int
  let nearestEnemy = world.nearestVisibleSquared(
    hero, true, false, nearestEnemyKind
  )
  if nearestEnemyKind == 5 and nearestEnemy <= 64:
    let actionIndex = if action.kind == ActionWalkTo: 0
      elif action.kind == ActionAttackMove: 1 else: 2
    inc nearestBarracksActions[heroIndex][actionIndex]
    if action.kind == ActionWalkTo and nearestBarracksExampleCount[heroIndex] < 3:
      inc nearestBarracksExampleCount[heroIndex]
      nearestBarracksExamples.add %*{
        "tick": action.tick, "heroId": hero.id, "heroClass": hero.class.ord,
        "distanceSquared": nearestEnemy, "hp": hero.hp, "maxHp": hero.maxHp,
        "destination": [action.first, action.second]
      }
  var exposedFortDistance = int64.high
  var exposedFortId = 0'i32
  var exposedFortHp = 0'i32
  var exposedTowerDistance = int64.high
  var nearestVisibleHeroDistance = int64.high
  var weakestInRangeHeroHp = int32.high
  var weakestInRangeHeroDistance = int64.high
  var weakestInRangeHeroId = 0'i32
  var exposedTowerId = 0'i32
  var exposedTowerPosition: WorldPoint
  var exposedGodGuardDistance = int64.high
  var exposedGodGuardId = 0'i32
  var exposedGodGuardHp = 0'i32
  var exposedGodGuardPosition: WorldPoint
  var exposedGateDistance = int64.high
  var exposedGateId = 0'i32
  var exposedGatePosition: WorldPoint
  var exposedBarracksId = 0'i32
  var exposedBarracksDistance = int64.high
  var localFriendlyFootmen, localFriendlyHeroes: int
  var localEnemyFootmen, localEnemyHeroes: int
  var opportunity: WorldObject
  for objectIndex in 0 ..< world.worldObjectCount(hero.id):
    if not world.worldObjectAt(hero.id, objectIndex, opportunity) or
        not opportunity.alive:
      continue
    let opportunityDistance = scriptDistanceSquared(hero.position, opportunity.position)
    if opportunity.id != hero.id and opportunityDistance <= 400:
      if opportunity.team == hero.team:
        if opportunity.kind == 2:
          inc localFriendlyHeroes
        elif opportunity.kind == 3:
          inc localFriendlyFootmen
      else:
        if opportunity.kind == 2:
          inc localEnemyHeroes
        elif opportunity.kind == 3:
          inc localEnemyFootmen
    if opportunity.team == hero.team:
      continue
    if opportunity.kind == 2:
      nearestVisibleHeroDistance = min(nearestVisibleHeroDistance, opportunityDistance)
      let scaledRange = hero.class.heroAttackRange() div 1000
      if opportunityDistance * 3600 <= int64(scaledRange) * scaledRange and
          (opportunity.hp < weakestInRangeHeroHp or
            (opportunity.hp == weakestInRangeHeroHp and opportunityDistance < weakestInRangeHeroDistance)):
        weakestInRangeHeroHp = opportunity.hp
        weakestInRangeHeroDistance = opportunityDistance
        weakestInRangeHeroId = opportunity.id
    if opportunity.kind == 1 and world.fortExposed(opportunity.team):
      let fortDistance = scriptDistanceSquared(hero.position, opportunity.position)
      if fortDistance < exposedFortDistance:
        exposedFortDistance = fortDistance
        exposedFortId = opportunity.id
        exposedFortHp = opportunity.hp
    elif opportunity.kind == 4:
      let tower = world.buildingById(opportunity.id)
      if world.buildingExposed(tower):
        let towerDistance = scriptDistanceSquared(hero.position, opportunity.position)
        if tower.lane < 0 and towerDistance < exposedGodGuardDistance:
          exposedGodGuardDistance = towerDistance
          exposedGodGuardId = opportunity.id
          exposedGodGuardHp = opportunity.hp
          exposedGodGuardPosition = opportunity.position
        if towerDistance < exposedTowerDistance:
          exposedTowerDistance = towerDistance
          exposedTowerId = opportunity.id
          exposedTowerPosition = opportunity.position
        if tower.tier.ord == 2 and towerDistance < exposedGateDistance:
          exposedGateDistance = towerDistance
          exposedGateId = opportunity.id
          exposedGatePosition = opportunity.position
    elif opportunity.kind == 5:
      let building = world.buildingById(opportunity.id)
      if world.buildingExposed(building):
        let barracksDistance = scriptDistanceSquared(hero.position, opportunity.position)
        if barracksDistance < exposedBarracksDistance:
          exposedBarracksDistance = barracksDistance
          exposedBarracksId = opportunity.id
  # Record orders, not damage: walk, attack-move, then target kinds 1..5.
  let heroCombatDistance = if hero.class == DruidWarden: 25'i64 else: 64'i64
  if exposedTowerDistance <= 100 and nearestVisibleHeroDistance <= heroCombatDistance:
    var category = 0
    if action.kind == ActionAttackMove:
      category = 1
    elif action.kind == ActionAttackTarget:
      var target: WorldObject
      for objectIndex in 0 ..< world.worldObjectCount(hero.id):
        if world.worldObjectAt(hero.id, objectIndex, target) and target.id == action.first:
          category = 1 + target.kind.int
          break
    inc contestedTowerActionKinds[heroIndex][category]
  if exposedBarracksDistance <= 64:
    inc barracksOpportunities[heroIndex][0]
    if action.kind == ActionWalkTo:
      inc barracksOpportunities[heroIndex][1]
    elif action.kind == ActionAttackMove:
      inc barracksOpportunities[heroIndex][2]
    elif action.kind == ActionAttackTarget:
      inc barracksOpportunities[heroIndex][3]
      if action.first == exposedBarracksId:
        inc barracksOpportunities[heroIndex][4]
      elif barracksOpportunityExamples.len < 15:
        barracksOpportunityExamples.add %*{
          "tick":action.tick,"heroId":hero.id,"heroClass":hero.class.ord,
          "barracksId":exposedBarracksId,
          "barracksDistanceSquared":exposedBarracksDistance,
          "targetId":action.first
        }
  if exposedFortDistance != int64.high:
    inc exposedFortOpportunityDistance[heroIndex][distanceBin(exposedFortDistance)]
    if hero.hp * 2 < hero.maxHp:
      inc exposedFortOpportunityHealth[heroIndex][0]
    else:
      inc exposedFortOpportunityHealth[heroIndex][1]
  if exposedTowerDistance != int64.high:
    inc exposedTowerOpportunityDistance[heroIndex][distanceBin(exposedTowerDistance)]
  var friendlyFootmanAtTower = false
  if exposedTowerDistance <= 900:
    var friendly: WorldObject
    for objectIndex in 0 ..< world.worldObjectCount(hero.id):
      if world.worldObjectAt(hero.id, objectIndex, friendly) and friendly.alive and
          friendly.team == hero.team and friendly.kind == 3 and
          scriptDistanceSquared(friendly.position, exposedTowerPosition) <= 64:
        friendlyFootmanAtTower = true
        break
  if friendlyFootmanAtTower:
    if action.kind == ActionWalkTo:
      inc waveBackedTowerActions[heroIndex][0]
    elif action.kind == ActionAttackMove:
      inc waveBackedTowerActions[heroIndex][1]
    elif action.kind == ActionAttackTarget:
      var target: WorldObject
      for objectIndex in 0 ..< world.worldObjectCount(hero.id):
        if world.worldObjectAt(hero.id, objectIndex, target) and
            target.id == action.first:
          inc waveBackedTowerActions[heroIndex][int(target.kind) + 1]
          break
  if action.kind in [ActionWalkTo, ActionAttackMove] and
      localEnemyHeroes + localEnemyFootmen >= 3 and
      localEnemyHeroes + localEnemyFootmen >
        localFriendlyHeroes + localFriendlyFootmen + 1:
    var enemyFortPosition: WorldPoint
    for fort in world.forts:
      if fort.team != hero.team:
        enemyFortPosition = fort.center
        break
    let
      destinationDx = int64(action.first) - int64(mapCoordinate(enemyFortPosition.x))
      destinationDy = int64(action.second) - int64(mapCoordinate(enemyFortPosition.z))
      currentDx = int64(mapCoordinate(hero.position.x)) -
        int64(mapCoordinate(enemyFortPosition.x))
      currentDy = int64(mapCoordinate(hero.position.z)) -
        int64(mapCoordinate(enemyFortPosition.z))
    if destinationDx * destinationDx + destinationDy * destinationDy <
        currentDx * currentDx + currentDy * currentDy:
      inc unsupportedForwardMoves[heroIndex]
      inc unsupportedForwardMovesByHealth[heroIndex][
        if hero.hp * 2 < hero.maxHp: 0 else: 1
      ]
      if localEnemyHeroes >= 2 and localFriendlyHeroes == 0 and
          localFriendlyFootmen == 0:
        inc unsupportedHeroPressureMoves[heroIndex]
      if formationExamples.len < 30:
        formationExamples.add %*{
          "tick": action.tick, "heroId": hero.id, "heroClass": hero.class.ord,
          "heroTeam": hero.team.ord, "hp": hero.hp, "maxHp": hero.maxHp,
          "self": [mapCoordinate(hero.position.x), mapCoordinate(hero.position.z)],
          "destination": [action.first, action.second],
          "friendlyFootmenWithin20": localFriendlyFootmen,
          "friendlyHeroesWithin20": localFriendlyHeroes,
          "enemyFootmenWithin20": localEnemyFootmen,
          "enemyHeroesWithin20": localEnemyHeroes
        }
  if exposedGateDistance <= 900 and action.kind in [ActionWalkTo, ActionAttackMove]:
    inc gateOpportunityWalks[heroIndex]
  if action.kind == ActionAttackTarget:
    if nearestEnemy != int64.high:
      inc attackNearestEnemyDistance[heroIndex][distanceBin(nearestEnemy)]
      inc attackNearestEnemyKinds[heroIndex][nearestEnemyKind]
    var target: WorldObject
    for objectIndex in 0 ..< world.worldObjectCount(hero.id):
      if world.worldObjectAt(hero.id, objectIndex, target) and target.id == action.first:
        if target.kind == 2 and target.alive and weakestInRangeHeroId != 0:
          inc inRangeHeroFinishChoices[heroIndex][0]
          if target.id == weakestInRangeHeroId:
            inc inRangeHeroFinishChoices[heroIndex][1]
          elif target.hp > weakestInRangeHeroHp:
            inc inRangeHeroFinishChoices[heroIndex][2]
        if hero.class.ord == 4 and action.tick >= 600 and action.tick <= 1200 and
            action.tick mod 25 == 0:
          earlyDemonHunterTargetTrace.add %*{
            "tick":action.tick,"heroId":hero.id,
            "heroTeam":hero.team.ord,
            "heroHp":hero.hp,"heroX":mapCoordinate(hero.position.x),
            "heroY":mapCoordinate(hero.position.z),
            "targetId":target.id,"targetKind":target.kind,
            "targetHp":target.hp,
            "targetDistanceSquared":scriptDistanceSquared(hero.position,target.position)
          }
        inc attackTargetKinds[heroIndex][int(target.kind)]
        if exposedFortDistance != int64.high:
          let healthBin = if hero.hp * 2 >= hero.maxHp: 1 else: 0
          let fortHpBin = if exposedFortHp <= 120: 1 else: 0
          inc exposedFortOpportunityTargetKindsByHealth[heroIndex][healthBin][int(target.kind)]
          inc exposedFortOpportunityTargetsByHealthAndFortHp[heroIndex][healthBin][fortHpBin][int(target.kind)]
          if healthBin == 0 and target.id != exposedFortId and
              exposedFortOpportunityExamples.len < 30:
            exposedFortOpportunityExamples.add %*{
              "tick":action.tick,"heroId":hero.id,
              "heroClass":hero.class.ord,"heroHp":hero.hp,
              "heroMaxHp":hero.maxHp,"fortId":exposedFortId,
              "fortHp":exposedFortHp,
              "fortDistanceSquared":exposedFortDistance,
              "targetId":target.id,"targetKind":target.kind
            }
        if exposedGodGuardDistance <= 900:
          inc exposedGodGuardOpportunityTargetKinds[heroIndex][int(target.kind)]
          let rangeBin = if exposedGodGuardDistance <= 64: 1 else: 0
          let healthBin = if hero.hp * 2 >= hero.maxHp: 1 else: 0
          var waveBin = 0
          for footman in world.footmen:
            if footman.hp > 0 and footman.team == hero.team and
                scriptDistanceSquared(footman.position, exposedGodGuardPosition) <= 64:
              waveBin = 1
              break
          inc exposedGodGuardOpportunityByRangeHealthWave[heroIndex][rangeBin][healthBin][waveBin][int(target.kind)]
          if rangeBin == 1 and closeGodGuardTraceCount[heroIndex] < 300:
            inc closeGodGuardTraceCount[heroIndex]
            closeGodGuardDecisionTrace.add %*{
              "tick": action.tick, "heroId": hero.id,
              "heroClass": hero.class.ord, "heroTeam": hero.team.ord,
              "heroHp": hero.hp, "heroMaxHp": hero.maxHp,
              "guardId": exposedGodGuardId, "guardHp": exposedGodGuardHp,
              "guardDistanceSquared": exposedGodGuardDistance,
              "targetId": target.id, "targetKind": target.kind,
              "targetHp": target.hp,
              "targetDistanceSquared": scriptDistanceSquared(hero.position, target.position),
              "friendlyWaveWithin8Guard": waveBin == 1,
              "friendlyHeroesWithin20": localFriendlyHeroes,
              "enemyHeroesWithin20": localEnemyHeroes,
              "friendlyFootmenWithin20": localFriendlyFootmen,
              "enemyFootmenWithin20": localEnemyFootmen
            }
        if exposedGateDistance <= 900:
          inc gateOpportunityTargetKinds[heroIndex][int(target.kind)]
          let healthBin = if hero.hp * 2 >= hero.maxHp: 1 else: 0
          var waveBin = 0
          for footman in world.footmen:
            if footman.hp > 0 and footman.team == hero.team and
                scriptDistanceSquared(footman.position, exposedGatePosition) <= 64:
              waveBin = 1
              break
          inc gateOpportunityTargetsByHealthAndWave[heroIndex][healthBin][waveBin][int(target.kind)]
          if target.id == exposedGateId:
            let gateHpBin = if world.buildingById(exposedGateId).hp <= 450: 1 else: 0
            inc exactGateTargetsByHealthWaveAndGateHp[heroIndex][healthBin][waveBin][gateHpBin]
          if target.id != exposedGateId and gateOpportunityExamples.len < 15:
            gateOpportunityExamples.add %*{
              "tick":action.tick,"heroId":hero.id,"heroClass":hero.class.ord,
              "heroX":mapCoordinate(hero.position.x),
              "heroY":mapCoordinate(hero.position.z),
              "gateId":exposedGateId,"gateDistanceSquared":exposedGateDistance,
              "heroHp":hero.hp,"heroMaxHp":hero.maxHp,
              "friendlyFootmanWithin8TilesOfGate":waveBin,
              "targetId":target.id,"targetKind":target.kind
            }
        if target.kind == 2:
          let ownFort = world.forts[if hero.team == RedTeam: 0 else: 1]
          if scriptDistanceSquared(hero.position, ownFort.center) <= 400 and
              scriptDistanceSquared(target.position, ownFort.center) <= 900 and
              scriptDistanceSquared(hero.position, target.position) > 64:
            inc homeDefenseLongHeroAttackRequests[heroIndex]
        if exposedTowerDistance <= 64:
          inc attackWithNearbyExposedTowerKinds[heroIndex][int(target.kind)]
          if hero.class.ord == 7 and target.kind != 4 and
              towerFocusExamples.len < 12:
            towerFocusExamples.add %*{
              "tick":action.tick,"heroId":hero.id,
              "heroX":mapCoordinate(hero.position.x),
              "heroY":mapCoordinate(hero.position.z),
              "towerId":exposedTowerId,
              "towerDistanceSquared":exposedTowerDistance,
              "targetId":target.id,"targetKind":target.kind,
              "targetX":mapCoordinate(target.position.x),
              "targetY":mapCoordinate(target.position.z)
            }
        if target.kind == 4:
          let tower = world.buildingById(target.id)
          if tower.lane < 0:
            inc godGuardAttackRequests[heroIndex]
          if world.buildingExposed(tower):
            inc attackStructureExposure[heroIndex][0]
          else:
            inc attackStructureExposure[heroIndex][1]
        elif target.kind == 1:
          if world.fortExposed(target.team):
            inc attackStructureExposure[heroIndex][2]
          else:
            inc attackStructureExposure[heroIndex][3]
          inc attackFortDistance[heroIndex][distanceBin(
            scriptDistanceSquared(hero.position, target.position)
          )]
        let targetDistanceBin = distanceBin(
          scriptDistanceSquared(hero.position, target.position)
        )
        inc attackTargetDistance[heroIndex][targetDistanceBin]
        inc attackTargetKindDistance[heroIndex][int(target.kind)][targetDistanceBin]
        break
  else:
    if nearestEnemy != int64.high:
      inc walkNearestEnemyDistance[heroIndex][distanceBin(nearestEnemy)]
      inc walkNearestEnemyKinds[heroIndex][nearestEnemyKind]
    let nearestFriendly = world.nearestVisibleSquared(
      hero, false, true, nearestFriendlyKind
    )
    if nearestFriendly != int64.high:
      inc walkNearestFriendlyFootmanDistance[heroIndex][distanceBin(nearestFriendly)]
    var value: WorldObject
    var matched = false
    var destinationNearestFriendly = int64.high
    var
      homeX = mapCoordinate(hero.spawnPosition.x)
      homeY = mapCoordinate(hero.spawnPosition.z)
      nearestDistance = int64.high
      nearestX, nearestY: int32
      frontProgress = -1'i64
      frontX, frontY: int32
      edgeDistance = int64.high
      edgeProgress = -1'i64
      edgeX, edgeY: int32
      centerDistance = int64.high
      centerX, centerY: int32
      firstFootmanFound = false
      firstFootmanX, firstFootmanY: int32
      lastFootmanX, lastFootmanY: int32
      friendlyObjectDistance = int64.high
      friendlyObjectX, friendlyObjectY: int32
      destinationObjectDistance = int64.high
      destinationObjectId, destinationObjectKind, destinationObjectClass: int32
      destinationObjectX, destinationObjectY: int32
      friendlyHeroes = newJArray()
      friendlyFootmen = newJArray()
    for objectIndex in 0 ..< world.worldObjectCount(hero.id):
      if world.worldObjectAt(hero.id, objectIndex, value) and value.alive and
          value.team == hero.team and value.kind == 1:
        homeX = mapCoordinate(value.position.x)
        homeY = mapCoordinate(value.position.z)
        break
    for objectIndex in 0 ..< world.worldObjectCount(hero.id):
      if not world.worldObjectAt(hero.id, objectIndex, value):
        continue
      let
        valueX = mapCoordinate(value.position.x)
        valueY = mapCoordinate(value.position.z)
        heroDx = int64(valueX) - int64(mapCoordinate(hero.position.x))
        heroDy = int64(valueY) - int64(mapCoordinate(hero.position.z))
        heroSquared = heroDx * heroDx + heroDy * heroDy
        destinationDx = int64(valueX) - int64(action.first)
        destinationDy = int64(valueY) - int64(action.second)
        destinationSquared = destinationDx * destinationDx + destinationDy * destinationDy
      if value.alive and destinationSquared < destinationObjectDistance:
        destinationObjectDistance = destinationSquared
        destinationObjectId = value.id
        destinationObjectKind = value.kind
        destinationObjectClass = value.class
        destinationObjectX = valueX
        destinationObjectY = valueY
      if value.alive and value.team == hero.team and value.kind == 2:
        friendlyHeroes.add %*{
          "id": value.id,
          "class": value.class,
          "position": [valueX, valueY]
        }
      if value.alive and value.team == hero.team and value.id != hero.id and
          heroSquared < friendlyObjectDistance:
        friendlyObjectDistance = heroSquared
        friendlyObjectX = valueX
        friendlyObjectY = valueY
      if value.alive and value.team == hero.team and value.kind == 3:
        friendlyFootmen.add %*{
          "id": value.id,
          "position": [valueX, valueY],
          "selfDistanceSquared": heroSquared,
          "destinationDistanceSquared": destinationSquared
        }
        let
          dx = int64(valueX) - int64(action.first)
          dz = int64(valueY) - int64(action.second)
          homeDx = int64(valueX) - int64(homeX)
          homeDy = int64(valueY) - int64(homeY)
          progress = homeDx * homeDx + homeDy * homeDy
        destinationNearestFriendly = min(
          destinationNearestFriendly, dx * dx + dz * dz
        )
        if not firstFootmanFound:
          firstFootmanFound = true
          firstFootmanX = valueX
          firstFootmanY = valueY
        lastFootmanX = valueX
        lastFootmanY = valueY
        if heroSquared < nearestDistance:
          nearestDistance = heroSquared
          nearestX = valueX
          nearestY = valueY
        if progress > frontProgress:
          frontProgress = progress
          frontX = valueX
          frontY = valueY
        let
          centerDx = int64(valueX) - 58
          centerDy = int64(valueY) - 58
          centerSquared = centerDx * centerDx + centerDy * centerDy
        if centerSquared < centerDistance:
          centerDistance = centerSquared
          centerX = valueX
          centerY = valueY
        let horizontalEdgeDistance = homeDy * homeDy
        if horizontalEdgeDistance < edgeDistance or
            (horizontalEdgeDistance == edgeDistance and
              progress > edgeProgress):
          edgeDistance = horizontalEdgeDistance
          edgeProgress = progress
          edgeX = valueX
          edgeY = valueY
      if not matched and value.alive and
          mapCoordinate(value.position.x) == action.first and
          mapCoordinate(value.position.z) == action.second:
        matched = true
        if value.team != hero.team:
          inc walkDestinationMatches[heroIndex][3]
          if value.kind == 4:
            inc visibleTowerWalks[heroIndex]
            if visibleTowerWalks[heroIndex] <= 3:
              visibleTowerWalkExamples.add %*{"tick": action.tick,
                "heroId": hero.id, "towerId": value.id,
                "destination": [action.first, action.second]}
        elif value.kind == 3:
          inc walkDestinationMatches[heroIndex][0]
        elif value.kind == 2:
          inc walkDestinationMatches[heroIndex][1]
        else:
          inc walkDestinationMatches[heroIndex][2]
    if not matched:
      inc walkDestinationMatches[heroIndex][4]
    if destinationNearestFriendly != int64.high:
      inc walkDestinationNearestFriendlyFootmanDistance[heroIndex][distanceBin(
        destinationNearestFriendly
      )]
    template matches(x, y: int32): bool =
      action.first == x and action.second == y
    if nearestDistance != int64.high and matches(nearestX, nearestY):
      inc walkHypothesisMatches[heroIndex][0]
    if frontProgress >= 0 and matches(frontX, frontY):
      inc walkHypothesisMatches[heroIndex][1]
    if edgeProgress >= 0 and matches(edgeX, edgeY):
      inc walkHypothesisMatches[heroIndex][2]
    if friendlyObjectDistance != int64.high and
        matches(friendlyObjectX, friendlyObjectY):
      inc walkHypothesisMatches[heroIndex][3]
    if matches(mapCoordinate(hero.spawnPosition.x),
        mapCoordinate(hero.spawnPosition.z)):
      inc walkHypothesisMatches[heroIndex][4]
    if matches(homeX, homeY):
      inc walkHypothesisMatches[heroIndex][5]
    if matches(64, 64):
      inc walkHypothesisMatches[heroIndex][6]
    if matches(mapCoordinate(hero.position.x), mapCoordinate(hero.position.z)):
      inc walkHypothesisMatches[heroIndex][7]
    if action.kind == ActionAttackMove:
      inc walkHypothesisMatches[heroIndex][8]
    if firstFootmanFound and matches(firstFootmanX, firstFootmanY):
      inc walkHypothesisMatches[heroIndex][9]
    if firstFootmanFound and matches(lastFootmanX, lastFootmanY):
      inc walkHypothesisMatches[heroIndex][10]
    if centerDistance != int64.high and matches(centerX, centerY):
      inc walkHypothesisMatches[heroIndex][11]
    if data.config.players[heroIndex].name == tracePlayerName and
        hypothesisTrace.len < 500 and
        (not tracedWalkSet[heroIndex] or
          tracedWalkDestination[heroIndex] != [action.first, action.second]):
      tracedWalkSet[heroIndex] = true
      tracedWalkDestination[heroIndex] = [action.first, action.second]
      hypothesisTrace.add %*{
        "tick": action.tick,
        "heroId": hero.id,
        "class": hero.class.ord,
        "hp": hero.hp,
        "maxHp": hero.maxHp,
        "mana": hero.mana,
        "maxMana": hero.maxMana,
        "gold": hero.gold,
        "level": hero.level,
        "attackObjectId": hero.attackObjectId,
        "self": [mapCoordinate(hero.position.x), mapCoordinate(hero.position.z)],
        "destination": [action.first, action.second],
        "nearestFootman": [nearestX, nearestY],
        "frontFootman": [frontX, frontY],
        "edgeFrontFootman": [edgeX, edgeY],
        "centerFootman": [centerX, centerY],
        "firstFootman": [firstFootmanX, firstFootmanY],
        "lastFootman": [lastFootmanX, lastFootmanY],
        "spawn": [mapCoordinate(hero.spawnPosition.x),
          mapCoordinate(hero.spawnPosition.z)],
        "fort": [homeX, homeY],
        "destinationClosestObject": {
          "id": destinationObjectId,
          "kind": destinationObjectKind,
          "class": destinationObjectClass,
          "position": [destinationObjectX, destinationObjectY],
          "distanceSquared": destinationObjectDistance
        },
        "friendlyHeroes": friendlyHeroes,
        "friendlyFootmen": friendlyFootmen,
        "nearestEnemySquared": nearestEnemy,
        "nearestEnemyKind": nearestEnemyKind
      }
for action in data.actions:
  var hero = -1
  for i, setup in data.header.setup.heroes:
    if setup.id == action.heroId:
      hero = i
      break
  if hero < 0:
    continue
  case action.kind
  of ActionWalkTo, ActionAttackMove:
    inc actionCounts[hero][0]
    if (hero, action.tick) in attackTicks:
      inc attackBeforeWalk[hero]
    walkTicks.incl((hero, action.tick))
  of ActionAttackTarget:
    inc actionCounts[hero][1]
    if (hero, action.tick) in walkTicks:
      inc walkBeforeAttack[hero]
    attackTicks.incl((hero, action.tick))
  of ActionBuyItem: inc actionCounts[hero][2]
  of ActionUseItem: inc actionCounts[hero][3]
  of ActionCastTarget, ActionCastPoint:
    if action.slot in 0 .. 3: inc casts[hero][int(action.slot)]
  else: discard
var sameTickWalkAttack = newSeq[int](game.world.heroes.len)
var walkOnlyTicks = newSeq[int](game.world.heroes.len)
var attackOnlyTicks = newSeq[int](game.world.heroes.len)
for key in walkTicks:
  if key in attackTicks:
    inc sameTickWalkAttack[key[0]]
  else:
    inc walkOnlyTicks[key[0]]
for key in attackTicks:
  if key notin walkTicks:
    inc attackOnlyTicks[key[0]]
var lowHealthFull = newSeq[int](game.world.heroes.len)
var emittedSpells = newSeq[array[4, int]](game.world.heroes.len)
var seenSpells = initHashSet[(int32, int32, int)]()
var samples = newJArray()
var openingMoves = newJArray()
var primaryManaConflicts = newJArray()
var manaShortfallHeroTargets = newSeq[array[4, int]](game.world.heroes.len)
var manaShortfallExamples = newJArray()
var manaExampleCounts = newSeq[int](game.world.heroes.len)
proc decide() =
  while index < data.actions.len and data.actions[index].tick == uint32(game.world.tick):
    let a = data.actions[index]
    game.world.recordActionContext(a)
    if a.kind in [ActionWalkTo, ActionAttackMove] and a.tick < 400:
      openingMoves.add %*a
    case a.kind
    of ActionWalkTo: discard game.world.applyWalkTo(a.heroId,a.first,a.second,a.offset)
    of ActionAttackMove: discard game.world.applyAttackMove(a.heroId,a.first,a.second,a.offset)
    of ActionAttackTarget: discard game.world.applyAttackTarget(a.heroId,a.first)
    of ActionBuyItem:
      let heroIndex = game.world.heroIndex(a.heroId)
      if heroIndex >= 0:
        let
          hero = game.world.heroes[heroIndex]
          preGold = hero.gold
          preHp = hero.hp
          preMaxHp = hero.maxHp
          preMana = hero.mana
          preMaxMana = hero.maxMana
          accepted = game.world.applyBuyItem(a.heroId,a.first)
        if accepted:
          inc purchases[heroIndex][a.first]
        purchaseContexts[heroIndex].add %*{
          "tick": a.tick,
          "item": a.first,
          "accepted": accepted,
          "gold": preGold,
          "hp": preHp,
          "maxHp": preMaxHp,
          "mana": preMana,
          "maxMana": preMaxMana,
          "inventory": hero.inventory,
          "itemCounts": hero.itemCounts
        }
    of ActionUseItem:
      let heroIndex = game.world.heroIndex(a.heroId)
      if heroIndex >= 0:
        let
          hero = game.world.heroes[heroIndex]
          slot = int(a.first)
          item = if slot >= 0 and slot < hero.inventory.len:
              hero.inventory[slot].ord
            else: -1
          itemCount = if slot >= 0 and slot < hero.itemCounts.len:
              hero.itemCounts[slot]
            else: -1
          preHp = hero.hp
          preMaxHp = hero.maxHp
          preMana = hero.mana
          preMaxMana = hero.maxMana
          accepted = game.world.applyUseItem(a.heroId,a.first)
        useContexts[heroIndex].add %*{
          "tick": a.tick,
          "slot": a.first,
          "item": item,
          "itemCount": itemCount,
          "accepted": accepted,
          "hp": preHp,
          "maxHp": preMaxHp,
          "healthPermille": (if preMaxHp > 0: preHp * 1000 div preMaxHp else: -1),
          "mana": preMana,
          "maxMana": preMaxMana,
          "manaPermille": (if preMaxMana > 0: preMana * 1000 div preMaxMana else: -1),
          "postHp": hero.hp,
          "postMana": hero.mana
        }
    of ActionCastTarget:
      let heroIndex = game.world.heroIndex(a.heroId)
      let preMana = if heroIndex >= 0: game.world.heroes[heroIndex].mana else: 0'i32
      if heroIndex >= 0 and a.slot in 0 .. 3:
        let hero = game.world.heroes[heroIndex]
        let slot = HeroAbilitySlot(a.slot)
        let spec = heroAbility(hero.class, slot).abilitySpec
        var target: WorldObject
        if hero.hp > 0 and hero.state != Dying and hero.spellsReady and
            game.world.casts.len < 512 and spec.kind == Strike and
            spec.casting != SelfCast and hero.charges[slot] > 0 and
            hero.cooldowns[slot] == 0 and preMana < spec.manaCost and game.world.spellTarget(a.first, target) and
            game.world.heroIndex(a.first) >= 0 and target.alive and target.team != hero.team and
            game.world.visible(hero.team, target.position):
          let dx = int64(hero.position.x) - target.position.x
          let dy = int64(hero.position.z) - target.position.z
          if dx * dx + dy * dy <= int64(spec.range) * spec.range:
            inc manaShortfallHeroTargets[heroIndex][slot.ord]
            if manaExampleCounts[heroIndex] < 12:
              inc manaExampleCounts[heroIndex]
              manaShortfallExamples.add %*{"tick": a.tick, "heroId": hero.id,
                "class": hero.class.ord, "slot": slot.ord, "targetId": a.first,
                "targetHp": target.hp, "mana": preMana, "cost": spec.manaCost,
                "gold": hero.gold, "hp": hero.hp, "maxHp": hero.maxHp,
                "inventory": hero.inventory}
      if game.world.applyCastTarget(
          a.heroId, a.slot, a.first
        ) and heroIndex >= 0:
        inc acceptedCasts[heroIndex][int(a.slot)]
        if a.slot == PrimaryAbility.ord:
          let hero = game.world.heroes[heroIndex]
          let ultimate = heroAbility(hero.class, UltimateAbility).abilitySpec
          if hero.charges[UltimateAbility] > 0 and hero.cooldowns[UltimateAbility] == 0 and
              preMana >= ultimate.manaCost and hero.mana < ultimate.manaCost:
            var requestedUltimate = false
            var next = index + 1
            while next < data.actions.len and data.actions[next].tick == a.tick:
              if data.actions[next].heroId == a.heroId and
                  data.actions[next].kind in [ActionCastTarget, ActionCastPoint] and
                  data.actions[next].slot == UltimateAbility.ord:
                requestedUltimate = true
              inc next
            primaryManaConflicts.add %*{"tick": a.tick, "heroId": a.heroId,
              "heroClass": hero.class.ord, "targetId": a.first,
              "preMana": preMana, "postMana": hero.mana,
              "ultimateCost": ultimate.manaCost,
              "ultimateRequestedSameTick": requestedUltimate}
    of ActionCastPoint:
      let heroIndex = game.world.heroIndex(a.heroId)
      if game.world.applyCastPoint(
          a.heroId, a.slot, a.first, a.second, a.offset
        ) and heroIndex >= 0:
        inc acceptedCasts[heroIndex][int(a.slot)]
    of ActionUseItemAt:
      discard game.world.applyUseItemAt(a.heroId, a.slot, a.first, a.second, a.offset)
    of ActionLevelAbility:
      discard game.world.applyLevelAbility(a.heroId, a.slot)
    of ActionBuyback:
      discard game.world.applyBuyback(a.heroId)
    of ActionDraft:
      discard game.world.applyDraft(a.heroId, a.first)
    else: discard
    inc index
  game.world.heroTurnStart = (game.world.heroTurnStart + 1) mod game.world.heroes.len
for tick in 1..data.hashes.len:
  game.tickWorld(decide)
  if game.stateHash() != data.hashes[tick-1]: inc mismatches
  for spell in game.world.casts:
    let key = (spell.heroId, spell.started, spell.ability.ord)
    if key notin seenSpells:
      seenSpells.incl key
      let casterIndex = game.world.heroIndex(spell.heroId)
      if casterIndex >= 0:
        for slot in HeroAbilitySlot:
          if heroAbility(game.world.heroes[casterIndex].class, slot) == spell.ability:
            inc emittedSpells[casterIndex][slot.ord]
  for i,h in game.world.heroes:
    if h.hp > 0 and h.hp * 2 < h.maxHp:
      var empty,heals = 0
      for item in h.inventory:
        if ord(item)==0: inc empty
        if ord(item) in [1,2]: inc heals
      if empty == 0 and heals == 0: inc lowHealthFull[i]
  if tick mod 250 == 0 or
      (tick >= 600 and tick <= 1100 and tick mod 25 == 0) or
      game.world.gameOver or tick == data.hashes.len:
    var heroes = newJArray()
    for h in game.world.heroes:
      heroes.add %*{
        "id":h.id,"team":h.team.ord,"x":mapCoordinate(h.position.x),
        "y":mapCoordinate(h.position.z),"hp":h.hp,"maxHp":h.maxHp,
        "mana":h.mana,"maxMana":h.maxMana,"spellsReady":h.spellsReady,
        "xp":h.totalXp,"gold":h.gold,"level":h.level,
        "attackObjectId":h.attackObjectId,"attacksLanded":h.attacksLanded,
        "hasMoveTarget":h.hasMoveTarget,"moveX":h.moveTileX,
        "moveY":h.moveTileY,"attackMoving":h.attackMoving,
        "inventory":h.inventory
      }
    var forts = newJArray()
    for fort in game.world.forts:
      forts.add %*{
        "id":fort.id,"team":fort.team.ord,
        "x":mapCoordinate(fort.center.x),
        "y":mapCoordinate(fort.center.z),"hp":fort.hp
      }
    var towers = newJArray()
    for tower in game.world.buildings:
      towers.add %*{
        "id":tower.id,"team":tower.team.ord,"lane":tower.lane,
        "guardsGod":tower.lane < 0,
        "kind":tower.kind.ord,
        "tier":tower.tier.ord,"x":mapCoordinate(tower.position.x),
        "y":mapCoordinate(tower.position.z),"hp":tower.hp,
        "exposed":game.world.buildingExposed(tower)
      }
    var footmen = newJArray()
    for footman in game.world.footmen:
      if footman.hp > 0:
        footmen.add %*{
          "id":footman.id,"team":footman.team.ord,"lane":footman.lane,
          "x":mapCoordinate(footman.position.x),
          "y":mapCoordinate(footman.position.z),"hp":footman.hp,
          "waypointIndex":footman.waypointIndex
        }
    samples.add %*{
      "tick":tick,"heroes":heroes,"forts":forts,"towers":towers,
      "footmen":footmen
    }
var finalTotalXp, finalGold, finalLevel = newJArray()
for hero in game.world.heroes:
  finalTotalXp.add %hero.totalXp
  finalGold.add %hero.gold
  finalLevel.add %hero.level
let report = %*{"players":data.config.players,"setup":data.header.setup.heroes,"hashMismatches":mismatches,"actionCountKinds":["walk","attack","buy","use"],"actionCounts":actionCounts,"walkAttackCadence":{"sameTick":sameTickWalkAttack,"walkOnly":walkOnlyTicks,"walkBeforeAttack":walkBeforeAttack,"attackBeforeWalk":attackBeforeWalk},"distanceBinUpperTiles":DistanceLimits,"actionContext":{"kindIndex":"0 none, 1 fort, 2 hero, 3 footman, 4 tower, 5 barracks","structureExposureIndex":"0 tower exposed, 1 tower blocked, 2 fort exposed, 3 fort blocked","fortOpportunityHealthIndex":"0 below half, 1 at least half","exposedFortOpportunityHealth":exposedFortOpportunityHealth,"exposedFortOpportunityDistance":exposedFortOpportunityDistance,"exposedTowerOpportunityDistance":exposedTowerOpportunityDistance,"attackWithNearbyExposedTowerKinds":attackWithNearbyExposedTowerKinds,"towerFocusExamples":towerFocusExamples,"attackFortDistance":attackFortDistance,"attackStructureExposure":attackStructureExposure,"attackTargetDistance":attackTargetDistance,"attackTargetKindDistance":attackTargetKindDistance,"attackTargetKinds":attackTargetKinds,"attackNearestEnemyDistance":attackNearestEnemyDistance,"attackNearestEnemyKinds":attackNearestEnemyKinds,"walkNearestEnemyDistance":walkNearestEnemyDistance,"walkNearestEnemyKinds":walkNearestEnemyKinds,"walkNearestFriendlyFootmanDistance":walkNearestFriendlyFootmanDistance,"walkDestinationNearestFriendlyFootmanDistance":walkDestinationNearestFriendlyFootmanDistance,"unsupportedForwardMoves":unsupportedForwardMoves,"unsupportedForwardMoveHealthIndex":"0 below half, 1 at least half","unsupportedForwardMovesByHealth":unsupportedForwardMovesByHealth,"unsupportedHeroPressureMoves":unsupportedHeroPressureMoves,"waveBackedTowerActionIndex":"0 walk, 1 attack-move, 2 fort, 3 hero, 4 footman, 5 tower, 6 barracks","waveBackedTowerActions":waveBackedTowerActions,"formationExamples":formationExamples,"walkDestinationMatches":{"friendlyFootmanFriendlyHeroFriendlyOtherEnemyNone":walkDestinationMatches},"walkHypothesisMatches":{"nearestFootmanFrontFootmanEdgeFrontFootmanNearestFriendlyObjectSpawnFortCenterSelfAttackMoveFirstFootmanLastFootmanCenterFootman":walkHypothesisMatches},"hypothesisTrace":hypothesisTrace},"casts":casts,"acceptedCasts":acceptedCasts,"purchases":purchases,"purchaseContexts":purchaseContexts,"useContexts":useContexts,"lowHealthFullNoHealTicks":lowHealthFull,"final":{"gameOver":game.world.gameOver,"winner":(if game.world.gameOver: game.world.winner.ord else: -1),"totalXp":finalTotalXp,"gold":finalGold,"level":finalLevel},"samples":samples,"openingMoves":openingMoves}
report["actionContext"]["exposedFortOpportunityTargetKindsByHealth"] =
  %exposedFortOpportunityTargetKindsByHealth
report["actionContext"]["exposedFortOpportunityTargetsByHealthAndFortHp"] =
  %exposedFortOpportunityTargetsByHealthAndFortHp
report["actionContext"]["exposedFortOpportunityExamples"] =
  exposedFortOpportunityExamples
report["actionContext"]["homeDefenseLongHeroAttackRequests"] = %homeDefenseLongHeroAttackRequests
report["actionContext"]["visibleTowerWalks"] = %visibleTowerWalks
report["actionContext"]["visibleTowerWalkExamples"] = visibleTowerWalkExamples
report["actionContext"]["contestedTowerActionKinds"] = %contestedTowerActionKinds
report["actionContext"]["inRangeHeroFinishChoices"] = %inRangeHeroFinishChoices
report["actionContext"]["inRangeHeroFinishChoicesIndex"] = %"hero attacks with an in-range hero, weakest selected, healthier selected; command-time state after earlier spells, not damage"
report["actionContext"]["contestedTowerActionKindsIndex"] = %"walk, attack-move, fort, hero, footman, tower, barracks; exposed tower within 10 tiles and visible hero within existing combat range; command counts, not damage"
report["actionContext"]["homeHeroPairOpportunities"] = %homeHeroPairOpportunities
report["actionContext"]["homeHeroPairMatchedActions"] = %homeHeroPairMatchedActions
report["actionContext"]["homeHeroPairTrace"] = homeHeroPairTrace
report["emittedSpellsBySlot"] = %emittedSpells
report["primaryManaConflicts"] = primaryManaConflicts
report["manaShortfallHeroTargets"] = %manaShortfallHeroTargets
report["manaShortfallExamples"] = manaShortfallExamples
report["manaShortfallScope"] = %"Requested non-self Strike casts at a living visible enemy hero within spell range, living ready caster, cast capacity available, charge available and cooldown zero but insufficient mana. Counts are repeated action attempts, not independent missed spells."
report["emittedSpellsScope"] = %"Unique simulator spell events (manual plus automatic), not damage or healing landed; compare acceptedCasts for successful manual requests."
report["actionContext"]["godGuardAttackRequests"] = %godGuardAttackRequests
report["actionContext"]["exposedGodGuardOpportunityTargetKinds"] =
  %exposedGodGuardOpportunityTargetKinds
report["actionContext"]["closeGodGuardDecisionTrace"] = closeGodGuardDecisionTrace
report["actionContext"]["exposedGodGuardOpportunityByRangeHealthWave"] =
  %exposedGodGuardOpportunityByRangeHealthWave
report["actionContext"]["gateOpportunityTargetKinds"] = %gateOpportunityTargetKinds
report["actionContext"]["gateOpportunityTargetsByHealthAndWave"] =
  %gateOpportunityTargetsByHealthAndWave
report["actionContext"]["exactGateTargetsByHealthWaveAndGateHp"] =
  %exactGateTargetsByHealthWaveAndGateHp
report["actionContext"]["gateOpportunityWalks"] = %gateOpportunityWalks
report["actionContext"]["gateOpportunityExamples"] = gateOpportunityExamples
report["actionContext"]["barracksOpportunities"] = %barracksOpportunities
report["actionContext"]["nearestBarracksActionIndex"] = %"0 walk, 1 attack-move, 2 attack-target; nearest exposed enemy is barracks within 8 tiles"
report["actionContext"]["nearestBarracksActions"] = %nearestBarracksActions
report["actionContext"]["nearestBarracksExamples"] = %nearestBarracksExamples
report["actionContext"]["barracksOpportunityExamples"] = barracksOpportunityExamples
report["actionContext"]["earlyDemonHunterTargetTrace"] = earlyDemonHunterTargetTrace
report["actionContext"]["homeDefenseVisibilityTrace"] = homeDefenseVisibilityTrace
echo $report
