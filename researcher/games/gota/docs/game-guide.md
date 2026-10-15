<!-- Source: Metta-AI/personal_labs @ 07dc6f94760fae3cf2de36877e0b6186cfd1dc63; path: gods_of_the_arena_lab/docs/wiki/game-guide.md -->

> Historical source reference. This file describes the pinned September source.
> See [toolkit provenance](../PROVENANCE.md) for verified live replay adaptations.

[Polyworld Buff](https://metta-ai.github.io/polyworld-buff/GOTA/) provides the illustrated game presentation. Gameplay rules are defined by the [game source](https://github.com/Metta-AI/polyworld/tree/main/examples/gods_of_the_arena).

For BASIC policies, see [policy and host surface](https://softmax.com/gods-of-the-arena/wiki/policy-and-host-surface).
The keyboard and mouse controls at the end describe human play.

![Gods of the Arena](https://softmax-public.s3.amazonaws.com/post-media/user/s25q6tn121cx1j3z7ql3ga5d/media_9473934f-e8a2-459d-a660-7a4ff2038a1b.png)

Two gods sit in opposite-corner forts. Three broad lanes, a shallow lake, connected camp clearings, and dense forests stand between them. Ten heroes, footmen, and towers fight until one god falls.

| Stat | Value |
| --- | --- |
| HEROES | 10 |
| TOWERS | 18 |
| LANES | 3 |
| MAX LEVEL | 20 |
| SHOP ITEMS | 20 |

THE MATCH

## How the arena works

Gods of the Arena is a 5v5 lane battler. Red fields Death Knight, Crossbowman, Lich, Warlock and Berserker. Blue fields Vanguard Knight, Ranger, Arcanist, Druid Warden and Demon Hunter. Each side starts with five heroes, six barracks, nine towers, and a fort that holds the god.

The default arena is 116 by 116 tiles with map seed 54. Map generation accepts even sizes from 64 through 256; use the match configuration and BASIC mapWidth/mapHeight for the actual dimensions. Blue starts in the southwest and Red in the northeast. Roads carve the forest and cross cliffs at short ramps. Each fort has three exits, two barracks per exit, and a separate walled spawn. Match seeds control gameplay randomness without changing this layout.

With the default 480-tick spawn interval, every surviving barracks spawns three footmen every 20 simulated seconds, so each lane sends six per team per wave; they march until they meet the enemy. Destroying a barracks stops its three. Heroes start at level 1 with 150 gold and their full four-ability kit already unlocked. Kills grant gold and XP. Heroes can grow to level 20 and spend gold in a six-slot inventory from level 1; purchases are not gated on reaching level 20 or standing near a shop.

Outer towers fall first, then inner, then the gate. A lane's barracks become attackable once all three of its towers are down. A god becomes attackable after one of its lanes is cleared. The first fort to reach zero health loses. Each winning-team seat scores 1 and each losing-team seat scores 0. A time-limit finish without a destroyed fort gives all ten seats 0; XP is separate from this win score.

| Reference | Details |
| --- | --- |
| Footmen | 60 HP, 12 damage, 15 gold |
| Towers | HP: outer 950, inner 1,300, gate 1,950. Damage: 18, 24, 30 |
| Barracks | 950 HP, no attack; two per lane per team; exposed after the lane's towers fall; 100 XP and 75 gold like a tower |
| Gods | 400 HP, exposed after a lane falls |
| Hero bounty | 150 XP and 100 gold |
| Level curve | 100 XP, then +75 each level |
| Clock | Default 28,800 ticks at 24 ticks/s: 20 simulated minutes; match configuration can differ |

THE ROSTER

## Heroes and ability kits

Each ability shows what it affects beside its artwork. Footprints describe the hit area; visual effects describe its appearance. Arc and cone effects share a sector footprint. Every area spell leaves a warning on the map until impact.

Every hero enters with Q, W, E, and R ready at **level 1** . There is no skill-point tree. Stats climb through level 20. Cooldowns and ranges below use the live HUD units: seconds and tiles.

![Vanguard Knight](https://softmax-public.s3.amazonaws.com/post-media/user/s25q6tn121cx1j3z7ql3ga5d/media_b72189f1-8aec-4f36-815b-710710564b65.png)

1-20

BLUE TEAM

### VANGUARD KNIGHT

MELEE · FRONTLINE PROTECTOR

| Stat | Value |
| --- | --- |
| HP +60/LVL | 330 → 1470 |
| MANA +8/LVL | 110 → 262 |
| BASIC DAMAGE +5/LVL | 25 → 120 |
| RANGE | 1.17 |
| MOVE | 2.32 → 2.78 |
| ATTACKS / S | 1.00 |

HEALTH  330 → 1470

MANA  110 → 262

Q

Affects **Self**

#### Lion Guard

Self cast

| Property | Value |
| --- | --- |
| Healing | +28 HP |
| Mana cost | 0 |
| Charges | 1 |
| Cast cooldown | 8s |
| Recharge | 8s / charge |
| Cast delay | Instant |

Restores your health.

W

Affects **Single target**

#### Firebrand Sword

Melee

| Property | Value |
| --- | --- |
| Damage | 40 |
| Mana cost | 20 |
| Charges | 3 |
| Cast cooldown | 2s |
| Recharge | 12s / charge |
| Cast delay | Instant |
| Cast range | 1.5 tiles |

Strike one enemy at close range. Aim at empty ground to swing into space.

E

Footprint **Circle**

#### Inferno Aegis

Area cast

| Property | Value |
| --- | --- |
| Healing | +50 HP |
| Mana cost | 35 |
| Charges | 1 |
| Cast cooldown | 10s |
| Recharge | 10s / charge |
| Cast delay | 0.5s |
| Cast range | 2.33 tiles |
| Radius | 2.33 tiles |
| Visual effect | Circle |

Centered on you. Heals all allied heroes in the circle at impact.

R

Footprint **Sector**

#### Blazing Blade

Area cast

| Property | Value |
| --- | --- |
| Damage | 90 |
| Mana cost | 70 |
| Charges | 1 |
| Cast cooldown | 20s |
| Recharge | 20s / charge |
| Cast delay | 0.25s |
| Cast range | 1.83 tiles |
| Radius | 1.83 tiles |
| Angle | 120° |
| Visual effect | Arc |

Starts at your position, facing your aim. Hits all enemies in the sector at impact.

![Ranger](https://softmax-public.s3.amazonaws.com/post-media/user/s25q6tn121cx1j3z7ql3ga5d/media_438ada95-1c29-4471-8193-7126e5c9db15.png)

1-20

BLUE TEAM

### RANGER

RANGER · MOBILE RANGED CARRY

| Stat | Value |
| --- | --- |
| HP +38/LVL | 200 → 922 |
| MANA +8/LVL | 110 → 262 |
| BASIC DAMAGE +6/LVL | 25 → 139 |
| RANGE | 5.50 |
| MOVE | 2.76 → 3.44 |
| ATTACKS / S | 1.33 |

HEALTH  200 → 922

MANA  110 → 262

Q

Affects **Single target**

#### Dragon Sight

Projectile

| Property | Value |
| --- | --- |
| Damage | 16 |
| Mana cost | 0 |
| Charges | 1 |
| Cast cooldown | 9s |
| Recharge | 9s / charge |
| Cast delay | Instant |
| Cast range | 7 tiles |

Follows the selected enemy. A ground shot hits the first enemy along its path.

W

Affects **Single target**

#### Verdant Arrow

Projectile

| Property | Value |
| --- | --- |
| Damage | 32 |
| Mana cost | 18 |
| Charges | 3 |
| Cast cooldown | 2s |
| Recharge | 12s / charge |
| Cast delay | Instant |
| Cast range | 6 tiles |

Follows the selected enemy. A ground shot hits the first enemy along its path.

E

Footprint **Circle**

#### Ricochet Disc

Area cast

| Property | Value |
| --- | --- |
| Damage | 48 |
| Mana cost | 32 |
| Charges | 1 |
| Cast cooldown | 8s |
| Recharge | 8s / charge |
| Cast delay | 1s |
| Cast range | 6.5 tiles |
| Radius | 2 tiles |
| Visual effect | Disc |

Locks at your aimed map position. Hits all enemies in the circle at impact.

R

Footprint **Line**

#### Storm Eagle

Area cast

| Property | Value |
| --- | --- |
| Damage | 95 |
| Mana cost | 80 |
| Charges | 1 |
| Cast cooldown | 24s |
| Recharge | 24s / charge |
| Cast delay | 1s |
| Cast range | 8 tiles |
| Width × length | 1.5 × 8 tiles |
| Visual effect | Line |

Starts at your position, facing your aim. Hits all enemies in the line at impact.

![Arcanist](https://softmax-public.s3.amazonaws.com/post-media/user/s25q6tn121cx1j3z7ql3ga5d/media_f7932a99-8f15-49b3-843a-18fab1909b5f.png)

1-20

BLUE TEAM

### ARCANIST

MAGE · BURST MAGE

| Stat | Value |
| --- | --- |
| HP +30/LVL | 190 → 760 |
| MANA +15/LVL | 180 → 465 |
| BASIC DAMAGE +8/LVL | 38 → 190 |
| RANGE | 5.00 |
| MOVE | 2.48 → 3.01 |
| ATTACKS / S | 0.80 |

HEALTH  190 → 760

MANA  180 → 465

Q

Affects **Self**

#### Mana Crystal

Self cast

| Property | Value |
| --- | --- |
| Restores | +28 mana |
| Mana cost | 0 |
| Charges | 1 |
| Cast cooldown | 6s |
| Recharge | 6s / charge |
| Cast delay | Instant |

Restores your mana.

W

Affects **Single target**

#### Frost Lance

Projectile

| Property | Value |
| --- | --- |
| Damage | 42 |
| Mana cost | 28 |
| Charges | 3 |
| Cast cooldown | 2s |
| Recharge | 12s / charge |
| Cast delay | Instant |
| Cast range | 5.5 tiles |

Follows the selected enemy. A ground shot hits the first enemy along its path.

E

Footprint **Circle**

#### Meteor Strike

Area cast

| Property | Value |
| --- | --- |
| Damage | 70 |
| Mana cost | 53 |
| Charges | 1 |
| Cast cooldown | 9s |
| Recharge | 9s / charge |
| Cast delay | 2s |
| Cast range | 6 tiles |
| Radius | 2 tiles |
| Visual effect | Circle |

Locks at your aimed map position. Hits all enemies in the circle at impact.

R

Footprint **Circle**

#### Arcane Meteor

Area cast

| Property | Value |
| --- | --- |
| Damage | 120 |
| Mana cost | 100 |
| Charges | 1 |
| Cast cooldown | 25s |
| Recharge | 25s / charge |
| Cast delay | 3s |
| Cast range | 7 tiles |
| Radius | 3 tiles |
| Visual effect | Circle |

Locks at your aimed map position. Hits all enemies in the circle at impact.

![Druid Warden](https://softmax-public.s3.amazonaws.com/post-media/user/s25q6tn121cx1j3z7ql3ga5d/media_a9c4042e-4cfe-4cce-8dbf-7be99f7330bc.png)

1-20

BLUE TEAM

### DRUID WARDEN

MAGE · DURABLE SUPPORT

| Stat | Value |
| --- | --- |
| HP +48/LVL | 250 → 1162 |
| MANA +14/LVL | 170 → 436 |
| BASIC DAMAGE +4/LVL | 22 → 98 |
| RANGE | 4.00 |
| MOVE | 2.56 → 3.09 |
| ATTACKS / S | 0.92 |

HEALTH  250 → 1162

MANA  170 → 436

Q

Affects **Self**

#### Nature Talisman

Self cast

| Property | Value |
| --- | --- |
| Healing | +22 HP |
| Mana cost | 0 |
| Charges | 1 |
| Cast cooldown | 8s |
| Recharge | 8s / charge |
| Cast delay | Instant |

Restores your health.

W

Footprint **Circle**

#### Healing Bloom

Area cast

| Property | Value |
| --- | --- |
| Healing | +55 HP |
| Mana cost | 30 |
| Charges | 1 |
| Cast cooldown | 7s |
| Recharge | 7s / charge |
| Cast delay | 0.5s |
| Cast range | 4 tiles |
| Radius | 2 tiles |
| Visual effect | Circle |

Locks at your aimed map position. Heals all allied heroes in the circle at impact.

E

Footprint **Circle**

#### Kindred Wisps

Area cast

| Property | Value |
| --- | --- |
| Healing | +80 HP |
| Mana cost | 45 |
| Charges | 1 |
| Cast cooldown | 12s |
| Recharge | 12s / charge |
| Cast delay | 0.5s |
| Cast range | 4 tiles |
| Radius | 2.5 tiles |
| Visual effect | Sphere |

Locks at your aimed map position. Heals all allied heroes in the circle at impact.

R

Footprint **Circle**

#### Golem Seed

Area cast

| Property | Value |
| --- | --- |
| Damage | 85 |
| Mana cost | 75 |
| Charges | 1 |
| Cast cooldown | 22s |
| Recharge | 22s / charge |
| Cast delay | 1s |
| Cast range | 3.33 tiles |
| Radius | 2 tiles |
| Visual effect | Cylinder |

Locks at your aimed map position. Hits all enemies in the circle at impact.

![Demon Hunter](https://softmax-public.s3.amazonaws.com/post-media/user/s25q6tn121cx1j3z7ql3ga5d/media_79dbb5e8-f052-47de-ba6a-1d5852cc88b5.png)

1-20

BLUE TEAM

### DEMON HUNTER

MELEE · MELEE ASSASSIN

| Stat | Value |
| --- | --- |
| HP +36/LVL | 220 → 904 |
| MANA +7/LVL | 90 → 223 |
| BASIC DAMAGE +7/LVL | 32 → 165 |
| RANGE | 1.25 |
| MOVE | 3.04 → 3.95 |
| ATTACKS / S | 1.50 |

HEALTH  220 → 904

MANA  90 → 223

Q

Affects **Self**

#### Shadow Cloak

Self cast

| Property | Value |
| --- | --- |
| Healing | +18 HP |
| Mana cost | 0 |
| Charges | 1 |
| Cast cooldown | 10s |
| Recharge | 10s / charge |
| Cast delay | Instant |

Restores your health.

W

Affects **Single target**

#### Void Blade

Melee

| Property | Value |
| --- | --- |
| Damage | 38 |
| Mana cost | 16 |
| Charges | 3 |
| Cast cooldown | 2s |
| Recharge | 12s / charge |
| Cast delay | Instant |
| Cast range | 1.5 tiles |

Strike one enemy at close range. Aim at empty ground to swing into space.

E

Footprint **Sector**

#### Gale Slash

Area cast

| Property | Value |
| --- | --- |
| Damage | 52 |
| Mana cost | 28 |
| Charges | 1 |
| Cast cooldown | 7s |
| Recharge | 7s / charge |
| Cast delay | 0.25s |
| Cast range | 2 tiles |
| Radius | 2 tiles |
| Angle | 120° |
| Visual effect | Cone |

Starts at your position, facing your aim. Hits all enemies in the sector at impact.

R

Affects **Single target**

#### Shadow Comet

Projectile

| Property | Value |
| --- | --- |
| Damage | 100 |
| Mana cost | 65 |
| Charges | 1 |
| Cast cooldown | 21s |
| Recharge | 21s / charge |
| Cast delay | Instant |
| Cast range | 5 tiles |

Follows the selected enemy. A ground shot hits the first enemy along its path.

![Death Knight](https://softmax-public.s3.amazonaws.com/post-media/user/s25q6tn121cx1j3z7ql3ga5d/media_40d70db0-bc20-4e94-adf0-7e16535af276.png)

1-20

RED TEAM

### DEATH KNIGHT

MELEE · SUSTAINING BRUISER

| Stat | Value |
| --- | --- |
| HP +62/LVL | 350 → 1528 |
| MANA +8/LVL | 90 → 242 |
| BASIC DAMAGE +6/LVL | 30 → 144 |
| RANGE | 1.27 |
| MOVE | 2.28 → 2.74 |
| ATTACKS / S | 0.86 |

HEALTH  350 → 1528

MANA  90 → 242

Q

Affects **Self**

#### Sanguine Chalice

Self cast

| Property | Value |
| --- | --- |
| Healing | +36 HP |
| Mana cost | 0 |
| Charges | 1 |
| Cast cooldown | 8s |
| Recharge | 8s / charge |
| Cast delay | Instant |

Restores your health.

W

Affects **Single target**

#### Afterlight Sickle

Melee

| Property | Value |
| --- | --- |
| Damage | 42 |
| Mana cost | 18 |
| Charges | 3 |
| Cast cooldown | 2s |
| Recharge | 12s / charge |
| Cast delay | Instant |
| Cast range | 1.5 tiles |

Strike one enemy at close range. Aim at empty ground to swing into space.

E

Footprint **Circle**

#### Withering Idol

Area cast

| Property | Value |
| --- | --- |
| Damage | 60 |
| Mana cost | 36 |
| Charges | 1 |
| Cast cooldown | 9s |
| Recharge | 9s / charge |
| Cast delay | 1s |
| Cast range | 2.67 tiles |
| Radius | 2 tiles |
| Visual effect | Circle |

Locks at your aimed map position. Hits all enemies in the circle at impact.

R

Footprint **Ring**

#### Dark Eclipse

Area cast

| Property | Value |
| --- | --- |
| Damage | 110 |
| Mana cost | 80 |
| Charges | 1 |
| Cast cooldown | 26s |
| Recharge | 26s / charge |
| Cast delay | 0.5s |
| Cast range | 2.33 tiles |
| Radius | 2.33 tiles |
| Inner radius | 0.67 tiles |
| Visual effect | Ring |

Centered on you. Hits all enemies in the ring at impact. The center is safe.

![Crossbowman](https://softmax-public.s3.amazonaws.com/post-media/user/s25q6tn121cx1j3z7ql3ga5d/media_e3badbf5-417b-4af6-b50b-041f07eaeeee.png)

1-20

RED TEAM

### CROSSBOWMAN

RANGER · HEAVY RANGED CARRY

| Stat | Value |
| --- | --- |
| HP +42/LVL | 230 → 1028 |
| MANA +6/LVL | 80 → 194 |
| BASIC DAMAGE +9/LVL | 46 → 217 |
| RANGE | 6.50 |
| MOVE | 2.40 → 2.86 |
| ATTACKS / S | 0.67 |

HEALTH  230 → 1028

MANA  80 → 194

Q

Affects **Single target**

#### Final Measure

Projectile

| Property | Value |
| --- | --- |
| Damage | 20 |
| Mana cost | 0 |
| Charges | 1 |
| Cast cooldown | 9s |
| Recharge | 9s / charge |
| Cast delay | Instant |
| Cast range | 7 tiles |

Follows the selected enemy. A ground shot hits the first enemy along its path.

W

Affects **Single target**

#### Siege Scarab

Projectile

| Property | Value |
| --- | --- |
| Damage | 50 |
| Mana cost | 22 |
| Charges | 3 |
| Cast cooldown | 2s |
| Recharge | 12s / charge |
| Cast delay | Instant |
| Cast range | 6.67 tiles |

Follows the selected enemy. A ground shot hits the first enemy along its path.

E

Footprint **Sector**

#### Lodestone Surge

Area cast

| Property | Value |
| --- | --- |
| Damage | 68 |
| Mana cost | 40 |
| Charges | 1 |
| Cast cooldown | 10s |
| Recharge | 10s / charge |
| Cast delay | 0.5s |
| Cast range | 6 tiles |
| Radius | 6 tiles |
| Angle | 90° |
| Visual effect | Cone |

Starts at your position, facing your aim. Hits all enemies in the sector at impact.

R

Footprint **Capsule**

#### Clockwork Charge

Area cast

| Property | Value |
| --- | --- |
| Damage | 115 |
| Mana cost | 70 |
| Charges | 1 |
| Cast cooldown | 23s |
| Recharge | 23s / charge |
| Cast delay | 1s |
| Cast range | 7.5 tiles |
| Width × length | 1.5 × 7.5 tiles |
| Visual effect | Capsule |

Starts at your position, facing your aim. Hits all enemies in the capsule at impact.

![Lich](https://softmax-public.s3.amazonaws.com/post-media/user/s25q6tn121cx1j3z7ql3ga5d/media_45de9716-e6be-4834-9bfc-c657952a02a6.png)

1-20

RED TEAM

### LICH

MAGE · CONTROL MAGE

| Stat | Value |
| --- | --- |
| HP +28/LVL | 185 → 717 |
| MANA +17/LVL | 210 → 533 |
| BASIC DAMAGE +8/LVL | 36 → 188 |
| RANGE | 5.50 |
| MOVE | 2.40 → 2.86 |
| ATTACKS / S | 0.75 |

HEALTH  185 → 717

MANA  210 → 533

Q

Affects **Single target**

#### Frost Sigil

Projectile

| Property | Value |
| --- | --- |
| Damage | 14 |
| Mana cost | 0 |
| Charges | 1 |
| Cast cooldown | 8s |
| Recharge | 8s / charge |
| Cast delay | Instant |
| Cast range | 6 tiles |

Follows the selected enemy. A ground shot hits the first enemy along its path.

W

Affects **Single target**

#### Ice Spear

Projectile

| Property | Value |
| --- | --- |
| Damage | 48 |
| Mana cost | 30 |
| Charges | 3 |
| Cast cooldown | 2s |
| Recharge | 12s / charge |
| Cast delay | Instant |
| Cast range | 6.67 tiles |

Follows the selected enemy. A ground shot hits the first enemy along its path.

E

Footprint **Circle**

#### Bone Marionette

Area cast

| Property | Value |
| --- | --- |
| Damage | 66 |
| Mana cost | 48 |
| Charges | 1 |
| Cast cooldown | 9s |
| Recharge | 9s / charge |
| Cast delay | 1s |
| Cast range | 5 tiles |
| Radius | 2 tiles |
| Visual effect | Dome |

Locks at your aimed map position. Hits all enemies in the circle at impact.

R

Footprint **Ring**

#### Bound Void

Area cast

| Property | Value |
| --- | --- |
| Damage | 125 |
| Mana cost | 110 |
| Charges | 1 |
| Cast cooldown | 27s |
| Recharge | 27s / charge |
| Cast delay | 1s |
| Cast range | 6.5 tiles |
| Radius | 2 tiles |
| Inner radius | 0.67 tiles |
| Visual effect | Torus |

Locks at your aimed map position. Hits all enemies in the ring at impact. The center is safe.

![Warlock](https://softmax-public.s3.amazonaws.com/post-media/user/s25q6tn121cx1j3z7ql3ga5d/media_32f588ce-7dd2-4c61-8e0e-0b7a11d36567.png)

1-20

RED TEAM

### WARLOCK

MAGE · UTILITY SUMMONER

| Stat | Value |
| --- | --- |
| HP +46/LVL | 240 → 1114 |
| MANA +16/LVL | 190 → 494 |
| BASIC DAMAGE +5/LVL | 26 → 121 |
| RANGE | 4.50 |
| MOVE | 2.48 → 3.01 |
| ATTACKS / S | 0.86 |

HEALTH  240 → 1114

MANA  190 → 494

Q

Affects **Self**

#### Aether Siphon

Self cast

| Property | Value |
| --- | --- |
| Restores | +30 mana |
| Mana cost | 0 |
| Charges | 1 |
| Cast cooldown | 7s |
| Recharge | 7s / charge |
| Cast delay | Instant |

Restores your mana.

W

Affects **Single target**

#### Moth Hex

Projectile

| Property | Value |
| --- | --- |
| Damage | 36 |
| Mana cost | 24 |
| Charges | 3 |
| Cast cooldown | 2s |
| Recharge | 12s / charge |
| Cast delay | Instant |
| Cast range | 4.67 tiles |

Follows the selected enemy. A ground shot hits the first enemy along its path.

E

Footprint **Line**

#### Dread Totem

Area cast

| Property | Value |
| --- | --- |
| Damage | 58 |
| Mana cost | 42 |
| Charges | 1 |
| Cast cooldown | 9s |
| Recharge | 9s / charge |
| Cast delay | 1s |
| Cast range | 4 tiles |
| Width × length | 1 × 4 tiles |
| Visual effect | Box |

Locks at your aimed map position. Hits all enemies in the line at impact.

R

Footprint **Ring**

#### Void Portal

Area cast

| Property | Value |
| --- | --- |
| Damage | 105 |
| Mana cost | 90 |
| Charges | 1 |
| Cast cooldown | 24s |
| Recharge | 24s / charge |
| Cast delay | 1s |
| Cast range | 5 tiles |
| Radius | 2 tiles |
| Inner radius | 0.67 tiles |
| Visual effect | Helix |

Locks at your aimed map position. Hits all enemies in the ring at impact. The center is safe.

![Berserker](https://softmax-public.s3.amazonaws.com/post-media/user/s25q6tn121cx1j3z7ql3ga5d/media_577c395a-719a-47b3-b9fd-142c997b78ba.png)

1-20

RED TEAM

### BERSERKER

MELEE · AGGRESSIVE MELEE CARRY

| Stat | Value |
| --- | --- |
| HP +55/LVL | 300 → 1345 |
| MANA +4/LVL | 40 → 116 |
| BASIC DAMAGE +8/LVL | 38 → 190 |
| RANGE | 1.33 |
| MOVE | 2.72 → 3.40 |
| ATTACKS / S | 1.20 |

HEALTH  300 → 1345

MANA  40 → 116

Q

Affects **Self**

#### Rage Crucible

Self cast

| Property | Value |
| --- | --- |
| Healing | +20 HP |
| Mana cost | 0 |
| Charges | 1 |
| Cast cooldown | 8s |
| Recharge | 8s / charge |
| Cast delay | Instant |

Restores your health.

W

Affects **Single target**

#### Molten Fist

Melee

| Property | Value |
| --- | --- |
| Damage | 45 |
| Mana cost | 0 |
| Charges | 3 |
| Cast cooldown | 2s |
| Recharge | 12s / charge |
| Cast delay | Instant |
| Cast range | 1.5 tiles |

Strike one enemy at close range. Aim at empty ground to swing into space.

E

Footprint **Sector**

#### Winged Boot

Area cast

| Property | Value |
| --- | --- |
| Damage | 40 |
| Mana cost | 12 |
| Charges | 1 |
| Cast cooldown | 8s |
| Recharge | 8s / charge |
| Cast delay | 0.5s |
| Cast range | 2.5 tiles |
| Radius | 2.5 tiles |
| Angle | 90° |
| Visual effect | Cone |

Starts at your position, facing your aim. Hits all enemies in the sector at impact.

R

Footprint **Circle**

#### Volcanic Eruption

Area cast

| Property | Value |
| --- | --- |
| Damage | 100 |
| Mana cost | 24 |
| Charges | 1 |
| Cast cooldown | 20s |
| Recharge | 20s / charge |
| Cast delay | 2s |
| Cast range | 2.17 tiles |
| Radius | 2 tiles |
| Visual effect | Circle |

Locks at your aimed map position. Hits all enemies in the circle at impact.

THE SHOP

## Items, costs, and bonuses

Six inventory slots. Consumables stack to 8. Heroes start with 150 gold. Footmen drop 15, towers and barracks 75, heroes 100.

### Consumables

![Ironroot Ration](https://softmax-public.s3.amazonaws.com/post-media/user/s25q6tn121cx1j3z7ql3ga5d/media_40305f3f-34ab-4c28-b7e0-1ef4b93710fd.png)

### Ironroot Ration

30

Use: restore 40 health.

![Vitality Elixir](https://softmax-public.s3.amazonaws.com/post-media/user/s25q6tn121cx1j3z7ql3ga5d/media_a80f0e08-4f30-4eb4-8ad5-2f903728f1f3.png)

### Vitality Elixir

50

Use: restore 90 health.

![Mana Potion](https://softmax-public.s3.amazonaws.com/post-media/user/s25q6tn121cx1j3z7ql3ga5d/media_c1552c0c-9652-4c19-b0d1-cf856c38af7d.png)

### Mana Potion

45

Use: restore 60 mana.

![Poison Potion](https://softmax-public.s3.amazonaws.com/post-media/user/s25q6tn121cx1j3z7ql3ga5d/media_d6406662-bf96-4460-985c-dea5f2d40b0f.png)

### Poison Potion

40

Use: deal 35 strike damage to a living, visible enemy within your hero's attack range. Tower order and god protection still apply.

### Equipment

![Steel Helmet](https://softmax-public.s3.amazonaws.com/post-media/user/s25q6tn121cx1j3z7ql3ga5d/media_79528ee7-2f3d-4c7c-83fc-f9ae92195441.png)

### Steel Helmet

80

+50 maximum health.

![Steel Buckler](https://softmax-public.s3.amazonaws.com/post-media/user/s25q6tn121cx1j3z7ql3ga5d/media_aeb97ac1-8287-4ac5-947c-605c6778be7c.png)

### Steel Buckler

90

+60 maximum health.

![Leather Gauntlets](https://softmax-public.s3.amazonaws.com/post-media/user/s25q6tn121cx1j3z7ql3ga5d/media_9f80db1c-c7ad-4ffd-9612-1f405760c73c.png)

### Leather Gauntlets

70

+4 damage.

![Ranger Boots](https://softmax-public.s3.amazonaws.com/post-media/user/s25q6tn121cx1j3z7ql3ga5d/media_091d737a-d642-4e16-b0b9-b85023aa9e0c.png)

### Ranger Boots

100

+0.32 move speed.

![Ruby Amulet](https://softmax-public.s3.amazonaws.com/post-media/user/s25q6tn121cx1j3z7ql3ga5d/media_8d412a28-f588-4be0-95f3-eaa85138e66a.png)

### Ruby Amulet

120

+70 maximum health.

![Sapphire Ring](https://softmax-public.s3.amazonaws.com/post-media/user/s25q6tn121cx1j3z7ql3ga5d/media_91ddf569-8cf3-4bd5-9de4-57894cabf7f4.png)

### Sapphire Ring

120

+40 maximum mana.

![Crimson Dagger](https://softmax-public.s3.amazonaws.com/post-media/user/s25q6tn121cx1j3z7ql3ga5d/media_aca24265-2cc6-4dd4-ac2d-a403a4d8d3ac.png)

### Crimson Dagger

110

+8 damage.

![Amethyst Wand](https://softmax-public.s3.amazonaws.com/post-media/user/s25q6tn121cx1j3z7ql3ga5d/media_5e801ee8-f595-4ce4-a039-4e6e442b428f.png)

### Amethyst Wand

140

+9 damage.

![Sunsteel Longsword](https://softmax-public.s3.amazonaws.com/post-media/user/s25q6tn121cx1j3z7ql3ga5d/media_7eb68cfd-b757-4a7c-b9fb-4a3a8194f3ff.png)

### Sunsteel Longsword

150

+10 damage.

![Ranger Bow](https://softmax-public.s3.amazonaws.com/post-media/user/s25q6tn121cx1j3z7ql3ga5d/media_f120f4bb-d39c-4826-ab28-ac6df22cd95b.png)

### Ranger Bow

150

+10 damage.

![Ironbark Pauldrons](https://softmax-public.s3.amazonaws.com/post-media/user/s25q6tn121cx1j3z7ql3ga5d/media_0349af9e-2950-439a-b58c-8f73ec7628d0.png)

### Ironbark Pauldrons

140

+80 maximum health.

![Knight Armor](https://softmax-public.s3.amazonaws.com/post-media/user/s25q6tn121cx1j3z7ql3ga5d/media_cf6055f1-424d-41bd-a5b2-72df6aa68792.png)

### Knight Armor

160

+120 maximum health.

![Thornwood Staff](https://softmax-public.s3.amazonaws.com/post-media/user/s25q6tn121cx1j3z7ql3ga5d/media_5d28246a-13a7-490a-beb1-15cd1279988a.png)

### Thornwood Staff

170

+40 maximum health, +6 damage.

![Battle Axe](https://softmax-public.s3.amazonaws.com/post-media/user/s25q6tn121cx1j3z7ql3ga5d/media_003d35c5-27c6-45b2-b976-aaffebadf366.png)

### Battle Axe

180

+14 damage.

![Rune Crossbow](https://softmax-public.s3.amazonaws.com/post-media/user/s25q6tn121cx1j3z7ql3ga5d/media_4874f493-cd2a-465e-8d99-9fe5d97594a6.png)

### Rune Crossbow

180

+14 damage.

![Arcane Spellbook](https://softmax-public.s3.amazonaws.com/post-media/user/s25q6tn121cx1j3z7ql3ga5d/media_7180c237-a9d5-475c-941c-c6d1ac712e5e.png)

### Arcane Spellbook

190

+30 maximum mana, +12 damage.

ACTIONS AND SPELLS

## Aim, cast, impact, recharge

**Basic attack + four abilities:**  Every hero has a separate single-target basic attack: a melee strike or a ranged shot, including magic bolts. Basic attacks cost no mana, use no spell charges, and repeat at the hero's attack speed. Their damage grows every level: base damage + (level − 1) × damage per level, plus equipment bonuses. Q/W/E/R are four additional abilities with their own cooldowns and charges.

### One target

Melee strikes need close range. Projectiles travel to one target. Neither becomes an area attack just because its effect looks wide. In player mode, aim at an object to target it, or aim at empty ground to swing or shoot into space.

### An area on the map

Circles, lines, sectors and other footprints affect every eligible occupant when the effect lands. The area locks when cast. Moving into it before impact can get you hit; moving out can avoid it. Enemy damage, allied healing and structure protection still apply.

### A visible warning

Cast delay is the time from releasing an area spell until it takes effect. The full footprint remains on the map during that delay, then flashes at impact. Every cast uses the same warnings and timing. Changing playback speed speeds up or slows down the battle and spell effects together.

**Mana**  is paid when a cast is accepted. Basic attacks cost zero mana, as does Molten Fist. Other spells have their own mana costs. Invalid casts do not spend mana or charges; a valid shot into empty space does.

### Charges

The number of casts held in reserve. You need at least one charge. Each hero starts with a full set; a respawn restores the set.

### Cast cooldown

The minimum time between uses of the same action, even if charges remain. It is separate from the effect's cast delay and from its recharge timer.

### Recharge cooldown

Charges refill one at a time. The timer starts with the first spent charge. Spending another does not restart it. When a charge returns, the next missing charge starts its own full recharge.

**Refill resources:**  Ironroot Ration costs 30 gold and heals 40 HP; Vitality Elixir costs 50 gold and heals 90 HP; Mana Potion costs 45 gold and restores 60 mana. Buy them in the [shop](https://metta-ai.github.io/polyworld-buff/GOTA/index.html#shop). Healing cannot revive a hero at zero HP.

PLAYER CONTROLS

## Control your hero

This section describes human-control mode. BASIC-controlled heroes can explicitly cast with `castTarget`/`castPoint`; bot automatic casting is also enabled.

Idle heroes automatically attack the closest visible enemy creep nearby. Melee heroes approach nearby creeps; ranged heroes acquire creeps within their attack range. Select an enemy or right-click it to make it take priority. Both melee and ranged heroes move toward that target until they reach their own basic-attack range, then stop and attack repeatedly. When the target dies or is lost in fog, they return to nearby creeps. A ground move order cancels the attack and takes priority over automatic acquisition. These rules also apply with zero mana or all four abilities on cooldown.

You control your four extra abilities; they only cast when you use them. Q/W/E/R cast immediately at your current valid target. Self actions are attempted on key press and affect you when accepted; resource/cooldown rules apply, and full-health healing or full-mana restoration is rejected. Melee actions also fire immediately: they strike a nearby target, or swing toward the pointer when there is none. For other abilities with no valid target, the key selects the ability and right-click casts at an object or the ground. Escape cancels the selection. With no ability selected, right-click moves or attacks. Click SHOP in the inventory, or press B, to open the full-screen item shop; click an item to buy it. Space pauses or resumes the battle, including while shopping. The HUD shows remaining charges and the wait until an empty action can be used again. A blue bar tracks progress toward the next charge.

| Input | Action |
| --- | --- |
| Q / W / E / R | Your four extra abilities. Self and melee actions fire immediately; other actions use your current target or select right-click aiming. |
| F / G | Use the first two inventory items, including health and mana potions. |
| O | Toggle character outlines behind scenery. Enabled by default; hidden portions are outlined without revealing enemies in fog of war. |

---

Sources: [content and ability definitions](https://github.com/Metta-AI/polyworld/blob/main/examples/gods_of_the_arena/content.nim),
[simulation](https://github.com/Metta-AI/polyworld/blob/main/examples/gods_of_the_arena/sim.nim),
[map configuration](https://github.com/Metta-AI/polyworld/blob/main/examples/gods_of_the_arena/generation/configs.nim),
and [BASIC host](https://github.com/Metta-AI/polyworld/blob/main/examples/gods_of_the_arena/bots.nim).

Maintained by Codex, an automated agent working for James Boggs.

[Game guide](https://softmax.com/gods-of-the-arena/wiki/game-guide) · [Hero statistics](https://softmax.com/gods-of-the-arena/wiki/hero-statistics) · [Player standings](https://softmax.com/gods-of-the-arena/wiki/player-standings)
