' Host-provided layouts for policies opting in with @gota-structures.
TYPE Position
  x AS FIXED
  y AS FIXED
END TYPE

TYPE ControlState
  stunTicks AS INTEGER
  rootTicks AS INTEGER
  silenceTicks AS INTEGER
END TYPE

TYPE HeroState
  id AS INTEGER
  team AS INTEGER
  class AS INTEGER
  role AS INTEGER
  hp AS INTEGER
  maxHp AS INTEGER
  mana AS INTEGER
  maxMana AS INTEGER
  gold AS INTEGER
  level AS INTEGER
  xp AS INTEGER
  nextLevelXp AS INTEGER
  totalXp AS INTEGER
  kills AS INTEGER
  deaths AS INTEGER
  assists AS INTEGER
  layer AS INTEGER
  alive AS INTEGER
  hasMoveTarget AS INTEGER
  attackDamage AS INTEGER
  targetId AS INTEGER
  attackCooldownTicks AS INTEGER
  attacksLanded AS INTEGER
  respawnTicks AS INTEGER
  abilityPoints AS INTEGER
  portalCooldownTicks AS INTEGER
  channelTicks AS INTEGER
  canShop AS INTEGER
  inOwnSpawn AS INTEGER
  buybackPrice AS INTEGER
  position AS Position
  velocity AS Position
  moveSpeed AS FIXED
  attackRange AS FIXED
  score AS INTEGER
  controls AS ControlState
END TYPE

TYPE ObjectState
  id AS INTEGER
  kind AS INTEGER
  team AS INTEGER
  class AS INTEGER
  hp AS INTEGER
  maxHp AS INTEGER
  alive AS INTEGER
  level AS INTEGER
  mana AS INTEGER
  targetId AS INTEGER
  campId AS INTEGER
  leader AS INTEGER
  returning AS INTEGER
  position AS Position
  facing AS Position
  velocity AS Position
  controls AS ControlState
END TYPE

TYPE AbilityState
  level AS INTEGER
  maxLevel AS INTEGER
  requiredLevel AS INTEGER
  canLevel AS INTEGER
  damage AS INTEGER
  heal AS INTEGER
  restore AS INTEGER
  manaCost AS INTEGER
  charges AS INTEGER
  cooldownTicks AS INTEGER
  rechargeTicks AS INTEGER
  casting AS INTEGER
  effectKind AS INTEGER
  range AS FIXED
  radius AS FIXED
END TYPE

TYPE ItemState
  id AS INTEGER
  count AS INTEGER
  cooldownTicks AS INTEGER
END TYPE

TYPE ObservedItemState
  id AS INTEGER
  count AS INTEGER
END TYPE

TYPE SpellState
  abilityId AS INTEGER
  casterId AS INTEGER
  impactTick AS INTEGER
  hostile AS INTEGER
  support AS INTEGER
  position AS Position
END TYPE

TYPE CampState
  tier AS INTEGER
  position AS Position
END TYPE

TYPE MatchState
  tick AS INTEGER
  battleTick AS INTEGER
  maxTicks AS INTEGER
  tickRate AS INTEGER
  seed AS INTEGER
  waveTicks AS INTEGER
  waveIntervalTicks AS INTEGER
  gameOver AS INTEGER
  objectCount AS INTEGER
  spellCount AS INTEGER
  campCount AS INTEGER
  allyTowers AS INTEGER
  allyTowersTotal AS INTEGER
  allyBarracks AS INTEGER
  allyBarracksTotal AS INTEGER
  enemyTowers AS INTEGER
  enemyTowersTotal AS INTEGER
  enemyBarracks AS INTEGER
  enemyBarracksTotal AS INTEGER
END TYPE

TYPE MapState
  width AS INTEGER
  height AS INTEGER
  layers AS INTEGER
  origin AS INTEGER
  enemyGod AS Position
END TYPE

TYPE DraftState
  active AS INTEGER
  mode AS INTEGER
  turnId AS INTEGER
  playerCount AS INTEGER
END TYPE

TYPE PlayerState
  id AS INTEGER
  team AS INTEGER
  class AS INTEGER
END TYPE

TYPE HeroChoice
  role AS INTEGER
  available AS INTEGER
END TYPE

TYPE ActionResult
  accepted AS INTEGER
  error AS INTEGER
END TYPE

TYPE TileState
  x AS INTEGER
  y AS INTEGER
  layer AS INTEGER
  kind AS INTEGER
  walkable AS INTEGER
  height AS INTEGER
  waterDepth AS INTEGER
END TYPE

DIM self AS HeroState
DIM match AS MatchState
DIM map AS MapState
DIM draft AS DraftState
DIM lastAction AS ActionResult
DIM tile AS TileState
DIM objects(4095) AS ObjectState
DIM abilities(3) AS AbilityState
DIM items(5) AS ItemState
DIM objectItems(24575) AS ObservedItemState
DIM spells(2047) AS SpellState
DIM camps(63) AS CampState
DIM players(9) AS PlayerState
DIM heroChoices(9) AS HeroChoice
