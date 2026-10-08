"""Live GotA scalar surface in structured policies, verified at Polyworld 801749e5 / Bassy a02e6dd5.

Historical Paintbot helpers remain available; real GotA driver paths select this backend.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from games.gota.gates.limits import HARD, POLYWORLD_COMMIT

LIMITS = HARD
BASELINE = ROOT / "tests/fixtures/gota/v13-live.bas"
BASELINE_SHA256 = "1637c5604a522342de710afbffb16cdaa84befe99d3df17fa28e146414f86f9c"
HOST_DATA = set(
    """selfId selfTeam selfClass selfX selfY selfHp selfMaxHp selfMana
selfMaxMana selfGold selfLevel worldTick selfLayer selfMoveSpeed selfAttackRange
selfAttackDamage selfTarget selfAttackCooldown selfAttacksLanded selfPortalCooldown
selfChannelTicks selfStunTicks selfRootTicks selfSilenceTicks selfDeaths selfRespawnTicks
drafting draftTurnId draftMode mapWidth mapHeight mapLayers worldScale tickRate
GroundLayer RedFortLayer BlueFortLayer WaterLayer
NoActionError ActionNotAlive ActionInvalidSlot ActionUnknownItem ActionInsufficientGold
ActionAlreadyEquipped ActionStackFull ActionInventoryFull ActionEmptySlot ActionNotConsumable
ActionFullHealth ActionFullMana ActionTargetUnavailable ActionOutOfRange ActionNoRoute
ActionInvalidPoint ActionCooldown ActionNoCharges ActionInsufficientMana ActionSpellLimit
ActionChanneling ActionStunned ActionRooted ActionOutsideKeep ActionAbilityLocked
ActionNoAbilityPoints ActionAbilityMaxLevel ActionHeroLevelRequired ActionNotDead
ActionMatchEnded ActionDrafting ActionNotDrafting ActionNotDraftTurn ActionUnknownHero
ActionHeroTaken ActionSilenced VanguardKnight Ranger Arcanist DruidWarden DemonHunter
DeathKnight Crossbowman Lich Warlock Berserker TerrainNone TerrainGrass TerrainRoad
TerrainRock TerrainTrees TerrainMarsh TerrainWall TerrainWater""".lower().split()
)
KEYWORDS = {
    "and",
    "call",
    "case",
    "dim",
    "do",
    "else",
    "elseif",
    "end",
    "eqv",
    "exit",
    "false",
    "for",
    "gosub",
    "goto",
    "if",
    "imp",
    "is",
    "let",
    "loop",
    "mod",
    "next",
    "not",
    "on",
    "or",
    "print",
    "rem",
    "return",
    "select",
    "step",
    "stop",
    "sub",
    "then",
    "to",
    "true",
    "until",
    "wend",
    "while",
    "xor",
    "type",
    "as",
}


def cached_probe(commit=POLYWORLD_COMMIT) -> Path:
    """Never fetch/build implicitly; Phase C is explicitly offline."""
    path = ROOT / "games/gota/tools/bin" / f"gate_probe-{commit[:8]}"
    if not path.is_file():
        raise FileNotFoundError(f"cached native gate probe required: {path}")
    from games.gota.tools.native_build import validate
    validate(path, 'gate_probe', commit, tools=ROOT / 'games/gota/tools')
    return path
