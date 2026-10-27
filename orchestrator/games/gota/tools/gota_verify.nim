# Source: Metta-AI/co-gas @ 6742feb08f8b119591d09261cadc581065fa698b; path: players/users/relh/co-gas/polyworld-basic/tools/gota_verify.nim
## Native replay integrity check. Deliberately omits behavioral diagnostics.
import std/[os, json]
import examples/gods_of_the_arena/[replays, sim, maps]

let data = loadReplay(paramStr(1))
let game = newGame(generateMap(data.config.seed, data.config.mapPreset),
  int32(data.header.setup.spawnIntervalTicks), data.header.setup.heroes.len,
  true, data)
game.replayPlayer = initReplayPlayer(data)
game.historyPlayback = true
for expectedTick in 1 .. data.hashes.len:
  game.tickWorld(proc() = discard)
  if game.world.tick != expectedTick:
    break
let complete = data.hashes.len > 0 and game.world.tick == data.hashes.len
let verified = complete and game.hashCheck.mismatches == 0
echo $(%*{
  "scope": "Native integrity and final-state check only; not a behavioral audit",
  "hashesVerified": verified,
  "hashMismatches": game.hashCheck.mismatches,
  "firstMismatchTick": game.hashCheck.firstTick,
  "gameVersion": data.header.gameVersion,
  "seed": data.config.seed,
  "ticks": game.world.tick,
  "recordedTicks": data.hashes.len,
  "players": data.config.players,
  "setup": data.header.setup.heroes,
  "final": {
    "gameOver": game.world.gameOver,
    "winner": (if game.world.gameOver: game.world.winner.ord else: -1),
    "totalXp": game.world.totalXp(),
    "scores": game.world.scores()
  }
})
if not verified:
  quit(1)
