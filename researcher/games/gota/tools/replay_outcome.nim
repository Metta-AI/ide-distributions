## Replay result boundary for Polyworld 5287c437; shared by expansion and regression tests.
import std/json
import ../sim

proc replayOutcome*(world: World): string =
  world.outcome()

proc validateOutcome*(world: World, expected: JsonNode) =
  if expected{"outcome"}.getStr != replayOutcome(world):
    raise newException(ValueError, "results.json outcome mismatch")
