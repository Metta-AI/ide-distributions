' ===== module 00_config.bas =====
' Module: config (prefix cfg). Tunable knobs only; no logic.
' All distances are in world units / 1000 ("ku": 60 ku = 1 tile) unless noted.
' cfgInit runs once per decision from main; assignments are cheap.

sub cfgInit()
  ' Last-hit planner
  cfgScanRadius = 1200         ' ku: footmen farther than this are not scanned (20 tiles)
  cfgRangeSlack = 6            ' ku inside my attack range to count as "in range"
  cfgStandoffBack = 60         ' ku behind max range for the ranged standoff point
  cfgMeleeStandoff = 120       ' ku from the anchor footman for melee heroes
  cfgChaseKu = 180             ' ku beyond my reach a footman may be before I stop considering it
  cfgAvoidAllyTarget = 0       ' 1: skip footmen an allied hero is already attacking (helps mono-team play, costs mixed-team play)
  cfgTowerFarmAllies = 99      ' farm beside an idle enemy tower only with this many allied footmen inside its reach (99 = never; v10 A/B showed no gain)
  cfgTowerFarmAlliesKu = 390   ' ku: what counts as inside the tower's reach (6.5 tiles)
  cfgTowerAvoid = 420          ' stay this far (ku) from a living enemy tower (7 tiles)
  cfgFootmanPeriod = 32        ' ticks between an allied footman's hits
  cfgTowerPeriod = 24          ' ticks between tower hits
  cfgFootmanHit = 12
  cfgStickSlack = 12           ' keep an ordered target while predicted HP <= damage + this
  cfgStickBonus = 40           ' score bonus for the target already ordered (avoids swing resets)
  cfgEventStale = 96           ' forget a drop event older than this many ticks
  cfgSeenStale = 1             ' forget a footman's hit history after any sighting gap (a drop across a gap has no known tick)
  cfgPoisonSecure = 1          ' use poison to secure a kill the basic attack would miss
  cfgSpellSecure = 1           ' use the primary strike to secure a kill
  cfgPoisonStock = 1           ' poisons to keep in stock while a footman still takes two hits
  cfgPoisonMinGold = 40        ' only restock poison at or above this gold
  ' Idle at the standoff point and let the engine attack instead of holding
  ' for kill windows. Hosted A/B v4 versus v5 (2026-09-17, 32 episodes per arm,
  ' exact replay counts): idle for every class 4.88 versus 4.31 last hits per
  ' hero per 1,000 ticks. Set 0 to hold for windows (the v1 to v4 behavior).
  cfgIdleHere = 1
  ' Melee and low-damage classes convert half as often as the field when they
  ' walk to a standoff point and wait (time-budget analysis, 2026-09-17): they
  ' attack only 20 to 26 percent of the ticks a creep is in reach versus 90
  ' percent for the top farmers. For them, chase and attack the anchor footman
  ' continuously; the last-hit windows still take priority when one opens.
  cfgChaseClass = 0
  if selfClass = 0 or selfClass = 3 or selfClass = 4 or selfClass = 5 or selfClass = 8 or selfClass = 9 then
    cfgChaseClass = 1
  end if

  ' Kill steal: the finishing blow on heroes and structures teammates are fighting
  cfgKsteal = 1                ' 0 disables the module
  cfgKsHoldKu = 600            ' ku: an enemy hero within this => farm by windows only, spells held (10 tiles)
  cfgKsAllyKu = 480            ' ku: an allied hero this close to the target and attacking it counts as engaged
  cfgKsRecentTicks = 48        ' a drop within this many ticks also counts as engaged
  cfgKsMinHpPct = 40           ' take hero windows only above this percent of max HP
  cfgHeroPeriod = 24           ' assumed repeat period of a hero's incoming hits (ticks)

  ' Punish: attack an enemy hero that is farming inside our tower's reach
  cfgPunish = 1                ' 0 disables the module
  cfgPunishEnterKu = 420       ' enemy hero within this of an allied tower that is firing at it starts a punish (7 tiles)
  cfgPunishExitKu = 600        ' punish ends once the target is farther than this from the tower (10 tiles)
  cfgPunishEngageKu = 720      ' only start when the target is within this of me (12 tiles)
  cfgPunishMinHpPct = 50       ' start only above this percent of max HP
  cfgPunishStopHpPct = 35      ' stop below this percent
  cfgPunishChase = 0           ' 1: keep chasing past tower range when the kill is cheap (v10 A/B: 74 chase ticks in 48 games, no gain)
  cfgPunishChaseTicks = 120    ' chase only if the expected time to kill is at most this (5 s)
  cfgPunishChaseMaxKu = 900    ' never chase a target farther than this from me (15 tiles)
  cfgPunishAllyKu = 360        ' an allied hero within this of the target counts its DPS and permits a slower chase

  ' Survival
  cfgRetreatPct = 35           ' retreat below this percent of max HP
  cfgRecoverPct = 70           ' resume below-threshold work above this percent
  cfgHeroHuntRange = 480       ' ku: an enemy hero targeting me within this => retreat at any HP
  cfgHeroFearRange = 360       ' ku: enemy hero this close while under 60% HP => back off
  cfgTowerEscape = 540         ' ku to step back when an enemy tower targets me (9 tiles)
  cfgRetreatStep = 600         ' ku to step back toward own fort per retreat order

  ' Telemetry
  cfgReportEvery = 240
  cfgDebug = 0                 ' reserved for event traces; keep every decision under 63 printed items
end sub

' ===== module 10_scan.bas =====
' Module: scan (prefix sc). One pass over the object list into typed arrays.
' The VM allows 32 arrays in total; this module uses 24 and the planner 8.

' rsSlot = ring slot for any object id (footman, hero or structure).
sub lhRingSlot(id)
  if id >= 1000 then
    rsSlot = (id - 1000) mod 448
  else
    if id >= 100 then
      rsSlot = 448 + id - 100
    else
      rsSlot = 458 + id
    end if
  end if
end sub
' Positions are stored in ku (tile * 60). Enemy objects are only those visible
' to my team this decision; allied objects are always present. Footmen are kept
' only within cfgScanRadius of me (budget).
'
' Outputs (valid for this decision):
'   efN, efId(i), efX(i), efY(i), efHp(i)     living enemy footmen
'   afN, afX(i), afY(i), afTgt(i)              living allied footmen and their targets
'   atN, atX(i), atY(i), atTgt(i), atDmg(i)    allied towers (any), their target and hit damage
'   etN, etX(i), etY(i), etTgt(i), etHp(i)     visible living enemy towers and barracks:
'                                              etX = x * 64 + id, etTgt = target * 8 + kind * 2 + exposed
'   ahN, ahX(i), ahY(i), ahTgt(i), ahDmg(i), ahPer(i)  living allied heroes (not me)
'   ehN, ehPos(i), ehMeta(i), ehHp(i)          visible living enemy heroes: position packed as
'                                              x * 8192 + y (ku), id * 65536 + target id, HP * 16 + class
'   scFortX, scFortY                           own god (fort) position
'   scEFortX, scEFortY                         enemy god position (mirror of own)
'   fmSlot(ring)                               id ring -> index into ef arrays (footmen), or -1;
'                                              ring slot: footman (id-1000) mod 448, hero 448+id-100,
'                                              structure 458+id (see lhRingSlot)
'   scSelfX, scSelfY                           my position in ku

dim efId(63)
dim efX(63)
dim efY(63)
dim efHp(63)
dim afX(63)
dim afY(63)
dim afTgt(63)
dim atX(15)
dim atY(15)
dim atTgt(15)
dim atDmg(15)
dim etX(23)
dim etY(23)
dim etTgt(23)
dim etHp(23)
dim ahTgt(4)
dim ahX(4)
dim ahY(4)
dim ahDmg(4)
dim ahPer(4)
dim ehPos(4)
dim ehMeta(4)
dim ehHp(4)
dim fmSlot(511)

' Sets hdDmg and hdPer (basic damage and swing ticks) for a hero class and level,
' ignoring items. Table from content.nim at the deployed commit.
sub heroDmgFor(cls, lvl)
  hdDmg = 25
  hdPer = 24
  if cls = 0 then
    hdDmg = 25 + (lvl - 1) * 5
    hdPer = 24
  end if
  if cls = 1 then
    hdDmg = 25 + (lvl - 1) * 6
    hdPer = 18
  end if
  if cls = 2 then
    hdDmg = 38 + (lvl - 1) * 8
    hdPer = 30
  end if
  if cls = 3 then
    hdDmg = 22 + (lvl - 1) * 4
    hdPer = 26
  end if
  if cls = 4 then
    hdDmg = 32 + (lvl - 1) * 7
    hdPer = 16
  end if
  if cls = 5 then
    hdDmg = 30 + (lvl - 1) * 6
    hdPer = 28
  end if
  if cls = 6 then
    hdDmg = 46 + (lvl - 1) * 9
    hdPer = 36
  end if
  if cls = 7 then
    hdDmg = 36 + (lvl - 1) * 8
    hdPer = 32
  end if
  if cls = 8 then
    hdDmg = 26 + (lvl - 1) * 5
    hdPer = 28
  end if
  if cls = 9 then
    hdDmg = 38 + (lvl - 1) * 8
    hdPer = 20
  end if
end sub

' hsMove = base move speed in ku per tick for a class (level growth ignored; a
' conservative estimate for "can I catch it").
sub heroSpeedFor(cls)
  hsMove = 6
  if cls = 4 then
    hsMove = 7
  end if
  if cls = 0 or cls = 5 then
    hsMove = 5
  end if
end sub

' Tower hit damage from its stable id: lane towers are ids 10-27 in
' outer/inner/gate triples; god guards (28-31) hit like a gate tower.
sub towerDmgFor(tid)
  tdDmg = 30
  if tid >= 10 and tid <= 27 then
    tdTier = (tid - 10) mod 3
    if tdTier = 0 then
      tdDmg = 18
    end if
    if tdTier = 1 then
      tdDmg = 24
    end if
  end if
end sub

sub scanObjects()
  ' Clear the id ring entries used last decision.
  oI = 0
  while oI < efN
    fmSlot((efId(oI) - 1000) mod 448) = -1
    oI = oI + 1
  wend
  efN = 0
  afN = 0
  atN = 0
  etN = 0
  ahN = 0
  ehN = 0
  scSelfX = selfX * 60
  scSelfY = selfY * 60
  scCount = objectCount()
  oI = 0
  while oI < scCount
    scKind = objectKind(oI)
    scTeam = objectTeam(oI)
    if scKind = 3 then
      if objectAlive(oI) = 1 then
        ' Footmen beyond cfgScanRadius are irrelevant to this hero's decisions and
        ' would cost instruction budget in every planner loop; skip them.
        oDx = objectX(oI) * 60 - scSelfX
        oDy = objectY(oI) * 60 - scSelfY
        if oDx * oDx + oDy * oDy > cfgScanRadius * cfgScanRadius then
          scKind = 0
        end if
      else
        scKind = 0
      end if
    end if
    if scKind = 3 then
      if objectAlive(oI) = 1 then
        if scTeam = selfTeam then
          if afN < 64 then
            afX(afN) = objectX(oI) * 60
            afY(afN) = objectY(oI) * 60
            afTgt(afN) = objectTarget(oI)
            afN = afN + 1
          end if
        else
          if efN < 64 then
            efId(efN) = objectId(oI)
            efX(efN) = objectX(oI) * 60
            efY(efN) = objectY(oI) * 60
            efHp(efN) = objectHp(oI)
            fmSlot((efId(efN) - 1000) mod 448) = efN
            efN = efN + 1
          end if
        end if
      end if
    end if
    if scKind = 4 and scTeam = selfTeam then
      if atN < 16 then
        atX(atN) = objectX(oI) * 60
        atY(atN) = objectY(oI) * 60
        atTgt(atN) = objectTarget(oI)
        towerDmgFor(objectId(oI))
        atDmg(atN) = tdDmg
        atN = atN + 1
      end if
    end if
    if scTeam <> selfTeam and (scKind = 4 or scKind = 5) then
      ' Enemy towers and barracks. etX packs id (below 64) into the low bits;
      ' etTgt packs target * 8 + kind * 2 + exposed.
      if objectHp(oI) > 0 then
        if etN < 24 then
          etX(etN) = objectX(oI) * 60 * 64 + objectId(oI)
          etY(etN) = objectY(oI) * 60
          etTgt(etN) = objectTarget(oI) * 8 + scKind * 2 + objectAlive(oI)
          etN = etN + 1
        end if
      end if
    end if
    if scKind = 2 then
      if objectId(oI) <> selfId then
        if objectAlive(oI) = 1 then
          if scTeam = selfTeam then
            if ahN < 5 then
              ahTgt(ahN) = objectTarget(oI)
              ahX(ahN) = objectX(oI) * 60
              ahY(ahN) = objectY(oI) * 60
              heroDmgFor(objectClass(oI), objectLevel(oI))
              ahDmg(ahN) = hdDmg
              ahPer(ahN) = hdPer
              ahN = ahN + 1
            end if
          else
            if ehN < 5 then
              ehPos(ehN) = objectX(oI) * 60 * 8192 + objectY(oI) * 60
              ehMeta(ehN) = objectId(oI) * 65536 + objectTarget(oI)
              ehHp(ehN) = objectHp(oI) * 16 + objectClass(oI)
              ehN = ehN + 1
            end if
          end if
        end if
      end if
    end if
    if scKind = 1 then
      if scTeam = selfTeam then
        scFortX = objectX(oI) * 60
        scFortY = objectY(oI) * 60
        scEFortX = (mapWidth - 1) * 60 - scFortX
        scEFortY = (mapHeight - 1) * 60 - scFortY
      end if
    end if
    oI = oI + 1
  wend
end sub

' ===== module 20_lasthit.bas =====
' Module: lasthit (prefix lh). Plans the footman last hit for this decision.
'
' Inputs: scan arrays (10_scan.bas), self data, cfg knobs, shPoisonSlot (40_shop.bas).
' Outputs, rewritten every decision:
'   lhState      0 no enemy footmen in play, 1 waiting for a window, 2 striking now
'   lhTargetId   footman to order attackTarget on now, or 0
'   lhSecureSlot ability slot to cast on lhSecureId to secure a kill, or -1
'   lhSecureId   footman id for the secure cast
'   lhPoisonId   footman id to poison this decision (attackTarget it, then useItem), or 0
'   lhHoldX, lhHoldY  tile to walk toward while waiting
' Telemetry counters: lhOrders, lhSecures, lhPoisons, lhLost.
'
' Method: for every visible enemy footman, predict its HP at the tick my next basic
' hit would land. Incoming damage comes from recorded HP-drop events (each repeats
' with its attacker's period: 32 ticks for footmen, 24 for towers, the class swing
' for heroes) plus an expected-value term for attached attackers that have not hit
' yet. A footman whose predicted HP is in (0, selfAttackDamage] is a window: order
' the attack now. One that would die first is a secure candidate: poison (instant,
' 35) or the class primary strike. Otherwise hold a walk target at the standoff
' point so the engine never auto-acquires and wastes hits.

dim fmHp(511)
dim fmSeen(511)
dim fmEv1(511)
dim fmEv2(511)
dim pvId(63)
dim efAtt(63)
dim efTw(63)
dim efHero(63)

' adD = approximate planar distance of (dx, dy) in ku, within about 7 percent.
sub approxDist(dx, dy)
  adAx = dx
  if adAx < 0 then
    adAx = 0 - adAx
  end if
  adAy = dy
  if adAy < 0 then
    adAy = 0 - adAy
  end if
  if adAx >= adAy then
    adD = (adAx * 10 + adAy * 4) / 10
  else
    adD = (adAy * 10 + adAx * 4) / 10
  end if
end sub

' Primary-strike parameters for my class (slot 1). spDmg = 0 means no instant strike.
' spProj = 1 for projectiles (45 ku per tick of travel).
sub lhStrikeSpec()
  spDmg = 0
  spRange = 0
  spProj = 0
  spMana = 0
  if selfClass = 0 then
    spDmg = 40
    spRange = 90
    spMana = 20
  end if
  if selfClass = 1 then
    spDmg = 32
    spRange = 360
    spProj = 1
    spMana = 18
  end if
  if selfClass = 2 then
    spDmg = 42
    spRange = 330
    spProj = 1
    spMana = 28
  end if
  if selfClass = 4 then
    spDmg = 38
    spRange = 90
    spMana = 16
  end if
  if selfClass = 5 then
    spDmg = 42
    spRange = 90
    spMana = 18
  end if
  if selfClass = 6 then
    spDmg = 50
    spRange = 400
    spProj = 1
    spMana = 22
  end if
  if selfClass = 7 then
    spDmg = 48
    spRange = 400
    spProj = 1
    spMana = 30
  end if
  if selfClass = 8 then
    spDmg = 36
    spRange = 280
    spProj = 1
    spMana = 24
  end if
  if selfClass = 9 then
    spDmg = 45
    spRange = 90
    spMana = 0
  end if
end sub

' Records one object's HP into its ring slot as a repeating drop event
' (same attacker, same phase mod period, same amount: refresh; else shift).
sub lhRecordDrop(slot, hp, per)
  if fmSeen(slot) = 0 or worldTick - fmSeen(slot) > cfgSeenStale then
    fmEv1(slot) = 0
    fmEv2(slot) = 0
  else
    oD = fmHp(slot) - hp
    if oD > 0 and oD < 256 then
      oNew = (worldTick - 1) * 256 + oD
      oSame = 0
      if fmEv1(slot) > 0 then
        if fmEv1(slot) mod 256 = oD and ((worldTick - 1) - fmEv1(slot) / 256) mod per = 0 then
          oSame = 1
        end if
      end if
      if oSame = 1 then
        fmEv1(slot) = oNew
      else
        fmEv2(slot) = fmEv1(slot)
        fmEv1(slot) = oNew
      end if
    end if
  end if
  fmHp(slot) = hp
  fmSeen(slot) = worldTick
end sub

' Records HP drops of visible enemy footmen as repeating events. Also counts
' footmen that were within chase distance last decision and are gone now
' (lhVanished), for the missed-opportunity telemetry.
sub lhUpdateMemory()
  lhVanished = 0
  oI = 0
  while oI < pvN
    if pvId(oI) > 0 then
      oJ = fmSlot((pvId(oI) - 1000) mod 448)
      oGone = 1
      if oJ >= 0 then
        if efId(oJ) = pvId(oI) then
          oGone = 0
        end if
      end if
      lhVanished = lhVanished + oGone
    end if
    oI = oI + 1
  wend
  pvN = 0
  oChase = selfAttackRange / 1000 + cfgChaseKu
  oI = 0
  while oI < efN
    lhRecordDrop((efId(oI) - 1000) mod 448, efHp(oI), cfgFootmanPeriod)
    oDx = efX(oI) - scSelfX
    oDy = efY(oI) - scSelfY
    pvId(pvN) = 0
    if oDx * oDx + oDy * oDy <= oChase * oChase then
      pvId(pvN) = efId(oI)
    end if
    pvN = pvN + 1
    oI = oI + 1
  wend
end sub

' Counts attackers attached to each enemy footman this decision.
sub lhAttachers()
  oI = 0
  while oI < efN
    efAtt(oI) = 0
    efTw(oI) = 0
    efHero(oI) = 32
    oI = oI + 1
  wend
  oI = 0
  while oI < afN
    if afTgt(oI) >= 1000 then
      oJ = fmSlot((afTgt(oI) - 1000) mod 448)
      if oJ >= 0 then
        if efId(oJ) = afTgt(oI) then
          oDx = afX(oI) - efX(oJ)
          oDy = afY(oI) - efY(oJ)
          if oDx * oDx + oDy * oDy <= 8100 then
            efAtt(oJ) = efAtt(oJ) + 1
          end if
        end if
      end if
    end if
    oI = oI + 1
  wend
  oI = 0
  while oI < atN
    if atTgt(oI) >= 1000 then
      oJ = fmSlot((atTgt(oI) - 1000) mod 448)
      if oJ >= 0 then
        if efId(oJ) = atTgt(oI) then
          efTw(oJ) = atDmg(oI)
        end if
      end if
    end if
    oI = oI + 1
  wend
  oI = 0
  while oI < ahN
    if ahTgt(oI) >= 1000 then
      oJ = fmSlot((ahTgt(oI) - 1000) mod 448)
      if oJ >= 0 then
        if efId(oJ) = ahTgt(oI) then
          efHero(oJ) = ahDmg(oI) * 256 + ahPer(oI)
        end if
      end if
    end if
    oI = oI + 1
  wend
end sub

' Number of repeats of an event at tick evT with period per landing in
' [worldTick, worldTick + h]. Result in evHits.
sub lhEventHits(evT, per, h)
  evHits = (worldTick + h - evT) / per - (worldTick - 1 - evT) / per
end sub

' lhInc = predicted damage to the object in ring slot s over the next h ticks
' from its recorded drop events alone (each repeating with period per).
sub lhEventInc(s, h, per)
  lhInc = 0
  iE = 0
  while iE < 2
    if iE = 0 then
      iEv = fmEv1(s)
    else
      iEv = fmEv2(s)
    end if
    if iEv > 0 then
      iT = iEv / 256
      iA = iEv mod 256
      if worldTick - iT <= cfgEventStale then
        lhEventHits(iT, per, h)
        lhInc = lhInc + iA * evHits
      end if
    end if
    iE = iE + 1
  wend
end sub

' lhInc = predicted damage to enemy footman i over the next h ticks (inclusive
' of this tick's footman and tower updates, exclusive of my own hit).
sub lhIncoming(i, h)
  lhInc = 0
  iRep = 0
  iS = (efId(i) - 1000) mod 448
  iE = 0
  while iE < 2
    if iE = 0 then
      iEv = fmEv1(iS)
    else
      iEv = fmEv2(iS)
    end if
    if iEv > 0 then
      iT = iEv / 256
      iA = iEv mod 256
      if worldTick - iT <= cfgEventStale then
        iPer = cfgFootmanPeriod
        if efTw(i) > 0 and iA = efTw(i) then
          iPer = cfgTowerPeriod
        else
          if efHero(i) / 256 > 0 and iA = efHero(i) / 256 then
            iPer = efHero(i) mod 256
          else
            iRep = iRep + iA / cfgFootmanHit
          end if
        end if
        lhEventHits(iT, iPer, h)
        lhInc = lhInc + iA * evHits
      end if
    end if
    iE = iE + 1
  wend
  ' Attached footmen without a recorded hit yet: expected value.
  iExtra = efAtt(i) - iRep
  if iExtra > 0 then
    lhInc = lhInc + iExtra * cfgFootmanHit * (h + 8) / cfgFootmanPeriod
  end if
end sub

' Standoff point behind an anchor position (ax, ay) toward my fort, at distance
' back ku. Writes lhHoldX, lhHoldY in tiles.
sub lhHoldBehind(ax, ay, back)
  hbDx = scFortX - ax
  hbDy = scFortY - ay
  approxDist(hbDx, hbDy)
  if adD < 1 then
    adD = 1
  end if
  lhHoldX = (ax + hbDx * back / adD) / 60
  lhHoldY = (ay + hbDy * back / adD) / 60
end sub

' 1 if (x, y) in ku is within cfgTowerAvoid of a visible living enemy tower that
' is not busy with a footman. Towers shoot footmen before heroes, so a tower whose
' target is a footman is safe to farm beside for now; the survive module handles
' the moment it switches to me.
sub lhNearEnemyTower(x, y)
  ntNear = 0
  iI = 0
  while iI < etN
    iDx = etX(iI) / 64 - x
    iDy = etY(iI) - y
    iTg = etTgt(iI) / 8
    if (etTgt(iI) / 2) mod 4 = 4 and iDx * iDx + iDy * iDy <= cfgTowerAvoid * cfgTowerAvoid then
      ' Safe beside the tower when it is busy with a footman, or idle with
      ' allied footmen inside its reach to absorb the first shots; the survive
      ' module leaves the moment it targets me.
      if iTg = selfId then
        ntNear = 1
      end if
      if iTg = 0 then
        iAllies = 0
        iJ = 0
        while iJ < afN
          iAx = afX(iJ) - etX(iI) / 64
          iAy = afY(iJ) - etY(iI)
          if iAx * iAx + iAy * iAy <= cfgTowerFarmAlliesKu * cfgTowerFarmAlliesKu then
            iAllies = iAllies + 1
          end if
          iJ = iJ + 1
        wend
        if iAllies < cfgTowerFarmAllies then
          ntNear = 1
        end if
      end if
    end if
    iI = iI + 1
  wend
end sub

sub lhPlan()
  lhState = 0
  lhTargetId = 0
  lhSecureSlot = -1
  lhSecureId = 0
  lhPoisonId = 0
  lhUpdateMemory()
  lhAttachers()
  lhStrikeSpec()
  lhRange = selfAttackRange / 1000
  lhReach = lhRange - cfgRangeSlack
  if lhReach < 30 then
    lhReach = 30
  end if
  lhSpeed = selfMoveSpeed / 1000
  if lhSpeed < 1 then
    lhSpeed = 1
  end if
  heroDmgFor(selfClass, 1)
  lhWindup = hdPer * 45 / 100
  lhMelee = 0
  if lhRange < 120 then
    lhMelee = 1
  end if
  lhStrikeReady = 0
  if cfgSpellSecure = 1 and spDmg > 0 then
    if abilityCharges(1) > 0 and abilityCooldown(1) = 0 and selfMana >= spMana then
      lhStrikeReady = 1
    end if
  end if
  lhPoisonReady = 0
  if cfgPoisonSecure = 1 and shPoisonSlot >= 0 then
    lhPoisonReady = 1
  end if

  ' My lane and whether any enemy footman is visible in it.
  lhMyLane = 1
  lhSlot = (selfId - 100) mod 5
  if lhSlot < 2 then
    lhMyLane = 0
  end if
  if lhSlot > 2 then
    lhMyLane = 2
  end if
  lhDiag = (mapWidth - 1) * 60
  lhLaneCount = 0
  lhLocalCount = 0
  lhChase = lhReach + cfgChaseKu
  oI = 0
  while oI < efN
    oDx = efX(oI) - scSelfX
    oDy = efY(oI) - scSelfY
    if oDx * oDx + oDy * oDy <= lhChase * lhChase then
      lhLocalCount = lhLocalCount + 1
    end if
    lhS = efX(oI) + efY(oI) - lhDiag
    lhL = 1
    if lhS <= -900 then
      lhL = 0
    end if
    if lhS >= 900 then
      lhL = 2
    end if
    if lhL = lhMyLane then
      lhLaneCount = lhLaneCount + 1
    end if
    oI = oI + 1
  wend
  lhBest = -1
  lhBestScore = 2147483647
  lhAnchor = -1
  lhAnchorHp = 2147483647
  lhAnchorPos = 0
  lhAnchorD2 = 2147483647
  oI = 0
  while oI < efN
    oDx = efX(oI) - scSelfX
    oDy = efY(oI) - scSelfY
    oD2 = oDx * oDx + oDy * oDy
    lhInR = 0
    if oD2 <= lhReach * lhReach then
      lhInR = 1
    end if
    lhNearEnemyTower(efX(oI), efY(oI))
    ' Anchor tiers: footmen already within chase distance (any lane) come first,
    ' then footmen in my lane, then anything visible. This keeps the hero on
    ' local opportunities without wandering into another lane for a distant one.
    lhChase = lhReach + cfgChaseKu
    lhLocal = 0
    if oD2 <= lhChase * lhChase then
      lhLocal = 1
    end if
    lhLaneOk = 1
    if lhLocal = 0 then
      if lhLocalCount > 0 then
        lhLaneOk = 0
      else
        if lhLaneCount > 0 then
          lhS = efX(oI) + efY(oI) - lhDiag
          lhL = 1
          if lhS <= -900 then
            lhL = 0
          end if
          if lhS >= 900 then
            lhL = 2
          end if
          if lhL <> lhMyLane then
            lhLaneOk = 0
          end if
        end if
      end if
    end if
    ' Only footmen I can reach quickly may be ordered; chasing across the map
    ' abandons the lane and never converts. Footmen an ally is already hitting
    ' are theirs.
    lhOrderOk = lhLocal
    if cfgAvoidAllyTarget = 1 then
      oJ = 0
      while oJ < ahN
        if ahTgt(oJ) = efId(oI) then
          lhOrderOk = 0
        end if
        oJ = oJ + 1
      wend
    end if
    if ntNear = 0 and lhLaneOk = 1 then
      ' Horizon for my basic hit on this footman.
      if lhInR = 1 then
        ' A reported cooldown of C lands during hero update C - 1 ticks from now.
        lhH = selfAttackCooldown - 1
        if lhH < 0 then
          lhH = 0
        end if
      else
        approxDist(oDx, oDy)
        lhH = (adD - lhReach) / lhSpeed + lhWindup
      end if
      lhIncoming(oI, lhH)
      lhPred = efHp(oI) - lhInc
      lhWindow = selfAttackDamage
      if efId(oI) = lhStickId then
        lhWindow = lhWindow + cfgStickSlack
      end if
      if lhOrderOk = 1 and lhPred > 0 and lhPred <= lhWindow then
        lhScore = lhH * 10 + lhPred
        if efId(oI) = lhStickId then
          lhScore = lhScore - cfgStickBonus
        end if
        if lhScore < lhBestScore then
          lhBestScore = lhScore
          lhBest = oI
        end if
      end if
      if lhOrderOk = 1 and lhPred <= 0 then
        ' The basic attack is too slow: try an instant source.
        if lhPoisonId = 0 and lhPoisonReady = 1 and lhInR = 1 and efHp(oI) <= 35 then
          lhPoisonId = efId(oI)
        else
          if lhSecureId = 0 and lhStrikeReady = 1 and oD2 <= spRange * spRange then
            if spProj = 1 then
              ' Projectile: at least one tick of travel, resolves after this tick's units.
              approxDist(oDx, oDy)
              lhSH = (adD + 44) / 45
              if lhSH < 1 then
                lhSH = 1
              end if
              lhIncoming(oI, lhSH)
              lhPredS = efHp(oI) - lhInc
            else
              ' Melee cast resolves inside this decision, before any creep swings.
              lhPredS = efHp(oI)
            end if
            if lhPredS > 0 and lhPredS <= spDmg then
              lhSecureId = efId(oI)
              lhSecureSlot = 1
            end if
          end if
        end if
      end if
      ' Anchor: the footman with the smallest positive predicted HP, else the nearest.
      if lhPred > 0 then
        if lhAnchorPos = 0 or lhPred < lhAnchorHp then
          lhAnchorPos = 1
          lhAnchorHp = lhPred
          lhAnchor = oI
        end if
      else
        if lhAnchorPos = 0 and oD2 < lhAnchorD2 then
          lhAnchorD2 = oD2
          lhAnchor = oI
          lhAnchorHp = lhPred
        end if
      end if
    end if
    oI = oI + 1
  wend

  if lhAnchor >= 0 then
    lhState = 1
    if lhMelee = 1 then
      lhHoldBehind(efX(lhAnchor), efY(lhAnchor), cfgMeleeStandoff)
    else
      lhHoldBehind(efX(lhAnchor), efY(lhAnchor), lhRange - cfgStandoffBack)
    end if
  end if
  if lhBest >= 0 then
    lhState = 2
    lhTargetId = efId(lhBest)
  end if
  ' Never poison the footman the basic attack is about to take.
  if lhPoisonId <> 0 and lhPoisonId = lhTargetId then
    lhPoisonId = 0
  end if
  if lhSecureId <> 0 and lhSecureId = lhTargetId then
    lhSecureId = 0
    lhSecureSlot = -1
  end if
end sub

' ===== module 22_ksteal.bas =====
' Module: ksteal (prefix ks). Take the killing blow on enemy heroes and structures
' that teammates are already fighting; never start a fight alone.
'
' Inputs: scan arrays (eh* enemy heroes, et* enemy structures, ah* allied heroes,
' af* allied footmen), the drop-event rings (lhRecordDrop, lhEventInc), self data,
' cfg knobs. Outputs, rewritten every decision:
'   ksTargetId  object to hit now (enemy hero or structure), or 0
'   ksSlot      -1 basic attack, else the ability slot to castTarget on ksTargetId
'   ksHold      1 while an enemy hero is within cfgKsHoldKu: farm by windows only,
'               so the engine cannot auto-cast the kit on footmen
' Telemetry: ksHeroWin, ksCasts, ksStructWin.
'
' Method: the footman last-hit prediction on hero and structure targets. A hero is
' "engaged" when an allied hero within cfgKsAllyKu of it targets it or its HP
' dropped within the last two seconds. For an engaged target in reach, predict HP
' at the landing tick of my basic hit and of each ready strike (melee: now;
' projectile: 45 ku per tick; short area casts: their delay). A window is
' 0 < predicted <= damage; prefer the spell with the earliest landing, then the
' basic hit. Structures: engaged when their HP is dropping and a footman shields
' me from the tower; basic hit only.

' Strike slot table for my class: ksN entries in ks* scalars via ksSpec(k):
' spDmg, spRange (ku), spCast (ticks), spProj (1 projectile), spMana, spSlot.
' Slot 0 passives that are free projectiles are included (Ranger, Crossbowman,
' Lich). Ring, line and capsule ultimates and long casts are excluded: the
' target moves before impact.
sub ksSpec(k)
  spDmg = 0
  spSlot = -1
  spCast = 0
  spProj = 0
  spMana = 0
  spRange = 0
  if selfClass = 0 then
    if k = 0 then
      spSlot = 1
      spDmg = 40
      spRange = 90
      spMana = 20
    end if
    if k = 1 then
      spSlot = 3
      spDmg = 90
      spRange = 110
      spCast = 6
      spMana = 70
    end if
  end if
  if selfClass = 1 then
    if k = 0 then
      spSlot = 0
      spDmg = 16
      spRange = 420
      spProj = 1
    end if
    if k = 1 then
      spSlot = 1
      spDmg = 32
      spRange = 360
      spProj = 1
      spMana = 18
    end if
  end if
  if selfClass = 2 then
    if k = 0 then
      spSlot = 1
      spDmg = 42
      spRange = 330
      spProj = 1
      spMana = 28
    end if
  end if
  if selfClass = 4 then
    if k = 0 then
      spSlot = 1
      spDmg = 38
      spRange = 90
      spMana = 16
    end if
    if k = 1 then
      spSlot = 2
      spDmg = 52
      spRange = 120
      spCast = 6
      spMana = 28
    end if
    if k = 2 then
      spSlot = 3
      spDmg = 100
      spRange = 300
      spProj = 1
      spMana = 65
    end if
  end if
  if selfClass = 5 then
    if k = 0 then
      spSlot = 1
      spDmg = 42
      spRange = 90
      spMana = 18
    end if
  end if
  if selfClass = 6 then
    if k = 0 then
      spSlot = 0
      spDmg = 20
      spRange = 420
      spProj = 1
    end if
    if k = 1 then
      spSlot = 1
      spDmg = 50
      spRange = 400
      spProj = 1
      spMana = 22
    end if
    if k = 2 then
      spSlot = 2
      spDmg = 68
      spRange = 360
      spCast = 12
      spMana = 40
    end if
  end if
  if selfClass = 7 then
    if k = 0 then
      spSlot = 0
      spDmg = 14
      spRange = 360
      spProj = 1
    end if
    if k = 1 then
      spSlot = 1
      spDmg = 48
      spRange = 400
      spProj = 1
      spMana = 30
    end if
  end if
  if selfClass = 8 then
    if k = 0 then
      spSlot = 1
      spDmg = 36
      spRange = 280
      spProj = 1
      spMana = 24
    end if
  end if
  if selfClass = 9 then
    if k = 0 then
      spSlot = 1
      spDmg = 45
      spRange = 90
    end if
    if k = 1 then
      spSlot = 2
      spDmg = 40
      spRange = 150
      spCast = 12
      spMana = 12
    end if
  end if
end sub

' Evaluates one target: (iX, iY) ku, ring slot iS, HP iHp, attacker period iPer,
' structure flag iStruct. Writes ksCandSlot (-1 basic, -2 none) and ksCandH
' (landing ticks) for the earliest converting window.
sub ksWindow()
  ksCandSlot = -2
  ksCandH = 100000
  iDx = iX - scSelfX
  iDy = iY - scSelfY
  iD2 = iDx * iDx + iDy * iDy
  iReach = selfAttackRange / 1000 - cfgRangeSlack
  ' Basic hit: in range now, or within chase distance (walk in, then windup).
  iH = -1
  if iD2 <= iReach * iReach then
    iH = selfAttackCooldown - 1
    if iH < 0 then
      iH = 0
    end if
  else
    if iStruct = 0 and iD2 <= (iReach + cfgChaseKu) * (iReach + cfgChaseKu) then
      approxDist(iDx, iDy)
      heroDmgFor(selfClass, 1)
      iH = (adD - iReach) / (selfMoveSpeed / 1000 + 1) + hdPer * 45 / 100
    end if
  end if
  if iH >= 0 then
    lhEventInc(iS, iH, iPer)
    if iHp - lhInc > 0 and iHp - lhInc <= selfAttackDamage then
      ksCandSlot = -1
      ksCandH = iH
    end if
  end if
  if iStruct = 1 then
    exit sub
  end if
  ' Spells: each ready strike whose predicted damage window is open.
  iK = 0
  while iK < 3
    ksSpec(iK)
    if spSlot >= 0 then
      if iD2 <= spRange * spRange and abilityCharges(spSlot) > 0 and abilityCooldown(spSlot) = 0 and selfMana >= spMana then
        iSH = spCast
        if spProj = 1 then
          approxDist(iDx, iDy)
          iSH = (adD + 44) / 45
          if iSH < 1 then
            iSH = 1
          end if
        end if
        lhEventInc(iS, iSH, iPer)
        if iHp - lhInc > 0 and iHp - lhInc <= spDmg and iSH < ksCandH then
          ksCandSlot = spSlot
          ksCandH = iSH
        end if
      end if
    end if
    iK = iK + 1
  wend
end sub

sub ksPlan()
  ksTargetId = 0
  ksSlot = -1
  ksHold = 0
  if cfgKsteal = 0 then
    exit sub
  end if
  ksBestH = 100000
  ' Enemy heroes: record drops, hold spells when one is near, find windows.
  oI = 0
  while oI < ehN
    oId = ehMeta(oI) / 65536
    lhRingSlot(oId)
    lhRecordDrop(rsSlot, ehHp(oI) / 16, cfgHeroPeriod)
    iX = ehPos(oI) / 8192
    iY = ehPos(oI) mod 8192
    oDx = iX - scSelfX
    oDy = iY - scSelfY
    if oDx * oDx + oDy * oDy <= cfgKsHoldKu * cfgKsHoldKu then
      ksHold = 1
    end if
    ' Engaged: an allied hero near it is attacking it, or it took a hit lately.
    oEng = 0
    oJ = 0
    while oJ < ahN
      if ahTgt(oJ) = oId then
        oAx = ahX(oJ) - iX
        oAy = ahY(oJ) - iY
        if oAx * oAx + oAy * oAy <= cfgKsAllyKu * cfgKsAllyKu then
          oEng = 1
        end if
      end if
      oJ = oJ + 1
    wend
    if fmEv1(rsSlot) > 0 then
      if worldTick - fmEv1(rsSlot) / 256 <= cfgKsRecentTicks then
        oEng = 1
      end if
    end if
    if oEng = 1 and selfHp * 100 >= selfMaxHp * cfgKsMinHpPct then
      iS = rsSlot
      iHp = ehHp(oI) / 16
      iPer = cfgHeroPeriod
      iStruct = 0
      ksWindow()
      if ksCandSlot > -2 and ksCandH < ksBestH then
        ksBestH = ksCandH
        ksTargetId = oId
        ksSlot = ksCandSlot
      end if
    end if
    oI = oI + 1
  wend
  if ksTargetId <> 0 then
    ksHeroWin = ksHeroWin + 1
    exit sub
  end if
  ' Structures: only the finishing hit, only with a footman shield, only while
  ' someone else is already bringing it down.
  oI = 0
  while oI < etN
    if etTgt(oI) mod 2 = 1 then
      oId = etX(oI) mod 64
      lhRingSlot(oId)
      lhRecordDrop(rsSlot, etHp(oI), cfgFootmanPeriod)
      if fmEv1(rsSlot) > 0 and etTgt(oI) / 8 <> selfId then
        if worldTick - fmEv1(rsSlot) / 256 <= cfgKsRecentTicks then
          iX = etX(oI) / 64
          iY = etY(oI)
          iS = rsSlot
          iHp = etHp(oI)
          iPer = cfgFootmanPeriod
          iStruct = 1
          ksWindow()
          if ksCandSlot = -1 then
            ksTargetId = oId
            ksSlot = -1
            ksStructWin = ksStructWin + 1
            exit sub
          end if
        end if
      end if
    end if
    oI = oI + 1
  wend
end sub

' ===== module 25_punish.bas =====
' Module: punish (prefix pn). Attack an enemy hero that farms inside our tower's
' reach. Towers shoot footmen first, so an enemy hero standing beside our tower
' with the wave dying is about to take tower fire; joining in converts that into a
' hero kill (150 XP, 100 gold, six footmen's worth) and the engine auto-casts the
' full kit on the target once attackTarget is set.
'
' Inputs: scan arrays (at* allied towers, eh* enemy heroes), self data, cfg knobs.
' Output: pnTargetId (enemy hero id to attack now, or 0). State: pnTargetId
' persists with hysteresis: enter when an allied tower is firing at the hero within
' cfgPunishEnterKu of it, exit once it is farther than cfgPunishExitKu from the tower.
' Telemetry (main): pnStarts, pnTicks.

' pnD2 = squared distance (ku) from hero index i to the nearest living allied tower;
' pnShot = 1 if an allied tower is currently firing at that hero (its target id).
sub pnTowerDist(i)
  iD2 = 2147483647
  pnShot = 0
  iX = ehPos(i) / 8192
  iY = ehPos(i) mod 8192
  iId = ehMeta(i) / 65536
  iJ = 0
  while iJ < atN
    iDx = atX(iJ) - iX
    iDy = atY(iJ) - iY
    if iDx * iDx + iDy * iDy < iD2 then
      iD2 = iDx * iDx + iDy * iDy
    end if
    if atTgt(iJ) = iId then
      pnShot = 1
    end if
    iJ = iJ + 1
  wend
end sub

' pnChaseOk = 1 if chasing enemy hero index i beyond tower cover is worth it.
sub pnChaseCheck(i)
  pnChaseOk = 0
  iX = ehPos(i) / 8192
  iY = ehPos(i) mod 8192
  iDx = iX - scSelfX
  iDy = iY - scSelfY
  iD2 = iDx * iDx + iDy * iDy
  if iD2 > cfgPunishChaseMaxKu * cfgPunishChaseMaxKu then
    exit sub
  end if
  ' DPS in HP per tick times 100: mine plus any allied hero adjacent to the target.
  heroDmgFor(selfClass, selfLevel)
  iDps = selfAttackDamage * 100 / hdPer
  iAlly = 0
  iJ = 0
  while iJ < ahN
    iAx = ahX(iJ) - iX
    iAy = ahY(iJ) - iY
    if iAx * iAx + iAy * iAy <= cfgPunishAllyKu * cfgPunishAllyKu then
      iDps = iDps + ahDmg(iJ) * 100 / ahPer(iJ)
      iAlly = 1
    end if
    iJ = iJ + 1
  wend
  if iDps < 1 then
    exit sub
  end if
  iHp = ehHp(i) / 16
  iTicks = iHp * 100 / iDps
  ' Closing time: distance beyond my range at my speed, only if I am faster.
  heroSpeedFor(ehHp(i) mod 16)
  iSpeed = selfMoveSpeed / 1000
  iReach = selfAttackRange / 1000
  if iD2 > iReach * iReach then
    if iSpeed <= hsMove and iAlly = 0 then
      exit sub
    end if
    approxDist(iDx, iDy)
    iGap = adD - iReach
    iClose = iSpeed - hsMove
    if iClose < 1 then
      iClose = 1
    end if
    iTicks = iTicks + iGap / iClose
  end if
  if iTicks <= cfgPunishChaseTicks then
    pnChaseOk = 1
  end if
end sub

sub pnPlan()
  if cfgPunish = 0 then
    pnTargetId = 0
    exit sub
  end if
  ' Keep the current target while it stays near our tower and I am healthy, or
  ' while chasing it is cheap: expected time to kill from its HP over my DPS
  ' plus an adjacent teammate's, and it cannot outrun me unless already in range.
  if pnTargetId <> 0 then
    pnKeep = 0
    oI = 0
    while oI < ehN
      if ehMeta(oI) / 65536 = pnTargetId then
        pnTowerDist(oI)
        if oD2 <= cfgPunishExitKu * cfgPunishExitKu then
          pnKeep = 1
        else
          if cfgPunish = 1 and cfgPunishChase = 1 then
            pnChaseCheck(oI)
            if pnChaseOk = 1 then
              pnKeep = 1
              pnChaseTicks = pnChaseTicks + 1
            end if
          end if
        end if
      end if
      oI = oI + 1
    wend
    if selfHp * 100 < selfMaxHp * cfgPunishStopHpPct then
      pnKeep = 0
    end if
    if pnKeep = 0 then
      pnTargetId = 0
    end if
  end if
  if pnTargetId <> 0 then
    exit sub
  end if
  if selfHp * 100 < selfMaxHp * cfgPunishMinHpPct then
    exit sub
  end if
  ' Start on the lowest-HP enemy hero inside our tower's reach and within engage range.
  pnBestHp = 2147483647
  oI = 0
  while oI < ehN
    pnTowerDist(oI)
    ' A tower firing at the hero is the signal: it stands inside tower range with
    ' no footman left to absorb the shots. Distance alone is not enough (heroes
    ' farm at the edge of tower range untouched).
    if pnShot = 1 and oD2 <= cfgPunishEnterKu * cfgPunishEnterKu then
      oDx = ehPos(oI) / 8192 - scSelfX
      oDy = ehPos(oI) mod 8192 - scSelfY
      if oDx * oDx + oDy * oDy <= cfgPunishEngageKu * cfgPunishEngageKu then
        if ehHp(oI) < pnBestHp then
          pnBestHp = ehHp(oI)
          pnTargetId = ehMeta(oI) / 65536
        end if
      end if
    end if
    oI = oI + 1
  wend
end sub

' ===== module 30_survive.bas =====
' Module: survive (prefix sv). Minimal self-preservation with priority over farming.
'
' Outputs, rewritten every decision:
'   svRetreat  1 when the hero should walk back toward its fort instead of farming
'   svRetX, svRetY  retreat tile
'   svHealSlot  inventory slot of a healing consumable to use now, or -1
' State: svLow (hysteresis flag). Telemetry: svRetreats (orders issued).

sub svPlan()
  svRetreat = 0
  svHealSlot = -1
  if selfHp * 100 < selfMaxHp * cfgRetreatPct then
    svLow = 1
  end if
  if selfHp * 100 >= selfMaxHp * cfgRecoverPct then
    svLow = 0
  end if
  ' An enemy hero attacking me, or one close while I am not healthy, is a fight
  ' this module does not take: a larger policy decides fights, this one farms.
  svThreat = 0
  oI = 0
  while oI < ehN
    oDx = ehPos(oI) / 8192 - scSelfX
    oDy = ehPos(oI) mod 8192 - scSelfY
    oD2 = oDx * oDx + oDy * oDy
    if ehMeta(oI) mod 65536 = selfId and oD2 <= cfgHeroHuntRange * cfgHeroHuntRange then
      svThreat = 1
    end if
    if selfHp * 100 < selfMaxHp * 60 and oD2 <= cfgHeroFearRange * cfgHeroFearRange then
      svThreat = 1
    end if
    oI = oI + 1
  wend
  ' An enemy tower shooting me: leave its range now (30 damage per second).
  svTower = 0
  oI = 0
  while oI < etN
    if etTgt(oI) / 8 = selfId then
      svTower = 1
    end if
    oI = oI + 1
  wend
  ' HP does not regenerate, so retreating without a heal only forfeits farm:
  ' retreat for a hero or tower threat, or when low with a heal in hand to drink.
  ' While punishing an overextended enemy under our tower, the tower is on our
  ' side: only HP decides.
  if pnTargetId <> 0 and svTower = 0 then
    svThreat = 0
  end if
  if svThreat = 1 or svTower = 1 or (svLow = 1 and shHealSlot >= 0) then
    svRetreat = 1
    oDx = scFortX - scSelfX
    oDy = scFortY - scSelfY
    approxDist(oDx, oDy)
    if adD < 1 then
      adD = 1
    end if
    svStep = cfgRetreatStep
    if svTower = 1 then
      svStep = cfgTowerEscape
    end if
    if svStep > adD then
      svStep = adD
    end if
    svRetX = (scSelfX + oDx * svStep / adD) / 60
    svRetY = (scSelfY + oDy * svStep / adD) / 60
  end if
  if selfHp * 100 < selfMaxHp * 55 then
    if shHealSlot >= 0 then
      svHealSlot = shHealSlot
    end if
  end if
end sub

' ===== module 40_shop.bas =====
' Module: shop (prefix sh). Inventory scan and damage-first purchasing.
'
' shScanInventory: shPoisonSlot, shHealSlot (-1 if none), shPoisonCount, shEmpty
' shBuy: spends gold; records shSpent (gold spent this decision, for the
'   telemetry gold accounting in main).
'
' Order: gauntlets (+4) early, then the biggest damage items, then HP; poison is
' stocked only while a footman takes two hits. The goal is a level and item
' advantage over the enemy heroes: every 15 gold of farm becomes stats.

sub shScanInventory()
  shPoisonSlot = -1
  shHealSlot = -1
  shPoisonCount = 0
  shEmpty = 0
  shHasGauntlets = 0
  shHasDagger = 0
  shHasSword = 0
  shHasAxe = 0
  shHasBow = 0
  shHasCrossbow = 0
  shHasWand = 0
  shHasBook = 0
  shHasStaff = 0
  shHasArmor = 0
  shHasPauldrons = 0
  oI = 0
  while oI < 6
    oId = itemId(oI)
    if oId = 0 then
      shEmpty = shEmpty + 1
    end if
    if oId = 4 then
      shPoisonSlot = oI
      shPoisonCount = itemCount(oI)
    end if
    if oId = 1 or oId = 2 then
      shHealSlot = oI
    end if
    if oId = 7 then
      shHasGauntlets = 1
    end if
    if oId = 11 then
      shHasDagger = 1
    end if
    if oId = 13 then
      shHasSword = 1
    end if
    if oId = 14 then
      shHasBow = 1
    end if
    if oId = 18 then
      shHasAxe = 1
    end if
    if oId = 19 then
      shHasCrossbow = 1
    end if
    if oId = 12 then
      shHasWand = 1
    end if
    if oId = 20 then
      shHasBook = 1
    end if
    if oId = 17 then
      shHasStaff = 1
    end if
    if oId = 16 then
      shHasArmor = 1
    end if
    if oId = 15 then
      shHasPauldrons = 1
    end if
    oI = oI + 1
  wend
end sub

' Buys one item id if affordable with the gold left this decision.
sub shTryBuy(id, cost)
  if shGold >= cost then
    if buyItem(id) = 1 then
      shGold = shGold - cost
      shSpent = shSpent + cost
    end if
  end if
end sub

sub shBuy()
  shSpent = 0
  shGold = selfGold
  ' Poison while a footman still needs two hits: a stocked poison converts a lost
  ' last hit into 25 XP. Once basic damage one-shots footmen the slot is worth
  ' more as equipment.
  if selfAttackDamage < 60 and shPoisonCount < cfgPoisonStock and shGold >= cfgPoisonMinGold then
    shTryBuy(4, 40)
  end if
  ' A heal when hurt, before gear: HP never regenerates.
  if selfHp * 100 < selfMaxHp * 55 and shHealSlot < 0 then
    shTryBuy(2, 50)
  end if
  ' Equipment fills every slot but one, which stays free for a heal (or the poison
  ' while it is still stocked). Income must become stats, not a gold balance: the
  ' best-farming seats used to end games with 380 gold unspent.
  shFree = shEmpty
  if shHealSlot >= 0 then
    shFree = shFree + 1
  end if
  if shFree <= 1 then
    exit sub
  end if
  if selfAttackDamage < 60 then
    shGold = shGold - cfgPoisonMinGold
  end if
  ' Cheap early damage first: +4 is a sixth of level-1 damage and widens every
  ' last-hit window. Then the big damage items, then HP for the fights.
  if shHasGauntlets = 0 and selfLevel < 4 then
    shTryBuy(7, 70)
  end if
  if shHasAxe = 0 then
    shTryBuy(18, 180)
  end if
  if shHasCrossbow = 0 then
    shTryBuy(19, 180)
  end if
  if shHasBook = 0 then
    shTryBuy(20, 190)
  end if
  if shHasSword = 0 then
    shTryBuy(13, 150)
  end if
  if shHasArmor = 0 then
    shTryBuy(16, 160)
  end if
  if shHasBow = 0 then
    shTryBuy(14, 150)
  end if
  if shHasPauldrons = 0 then
    shTryBuy(15, 140)
  end if
  if shHasWand = 0 then
    shTryBuy(12, 140)
  end if
  if shHasDagger = 0 then
    shTryBuy(11, 110)
  end if
  if shHasStaff = 0 then
    shTryBuy(17, 170)
  end if
end sub

' ===== module 90_main.bas =====
' Module: main (prefix mn). Per-decision top-level flow and telemetry.
'
' Order: config, scan, inventory, bookkeeping, survive, last-hit plan, shop, act.
' Priority when acting: retreat > punish > kill steal > secure cast > poison > attack order > hold/walk.
' Telemetry line every cfgReportEvery ticks:
'   LH tick lastHits heroKills towerKills level orders secures poisons lost deaths gold early missed punishStarts punishTicks punishChaseTicks chaseTicks ksHeroWindows ksCasts ksStructWindows

cfgInit()
scanObjects()
shScanInventory()

' ---- bookkeeping: income from gold deltas, deaths from skipped decisions ----
if mnInit = 0 then
  mnInit = 1
  mnExpectedGold = selfGold
  mnLastTick = worldTick
end if
if worldTick - mnLastTick > 1 then
  mnDeaths = mnDeaths + 1
  svLow = 0
  mnOrderedId = 0
end if
mnLastTick = worldTick
mnDelta = selfGold - mnExpectedGold
mnGained = 0
' Income is footmen (15), heroes (100) and finishing blows on structures (75).
' Take hero kills only until the remainder is a multiple of 15, so an area spell
' paying 105 reads as seven footmen, not a hero. An estimate; the replay expander
' has the exact counts.
while mnDelta >= 100 and mnDelta mod 15 <> 0
  mnHeroKills = mnHeroKills + 1
  mnDelta = mnDelta - 100
  mnGained = 1
wend
' 75 gold is a structure kill when my ordered target was a structure that is gone,
' else five footmen from an area spell (exact counts come from the replay expander).
if mnOrderedId > 0 and mnOrderedId < 100 and mnDelta >= 75 then
  oGone = 1
  oJ = 0
  while oJ < etN
    if etX(oJ) mod 64 = mnOrderedId then
      oGone = 0
    end if
    oJ = oJ + 1
  wend
  if oGone = 1 then
    mnTowerKills = mnTowerKills + 1
    mnDelta = mnDelta - 75
    mnGained = 1
    mnOrderedId = 0
  end if
end if
while mnDelta >= 15
  mnLastHits = mnLastHits + 1
  mnDelta = mnDelta - 15
  mnGained = 1
wend
' A basic hit landed on the ordered target and it survived: the window was early.
if mnOrderedId >= 1000 and selfAttacksLanded > mnLanded and mnGained = 0 then
  oJ = fmSlot((mnOrderedId - 1000) mod 448)
  if oJ >= 0 then
    if efId(oJ) = mnOrderedId then
      mnEarly = mnEarly + 1
    end if
  end if
end if
mnLanded = selfAttacksLanded
' Enemy footmen that vanished within chase distance while I was not on them.
if mnGained = 0 then
  mnMissed = mnMissed + lhVanished
end if
' An ordered target that vanished without paying us was taken by someone else
' (footman orders only; hero orders are cleared by the punish module).
if mnOrderedId >= 1000 then
  oJ = fmSlot((mnOrderedId - 1000) mod 448)
  mnStill = 0
  if oJ >= 0 then
    if efId(oJ) = mnOrderedId then
      mnStill = 1
    end if
  end if
  if mnStill = 0 then
    if mnGained = 0 then
      mnLost = mnLost + 1
    end if
    mnOrderedId = 0
  end if
end if

' ---- plans ----
pnPlan()
svPlan()
ksPlan()
lhStickId = mnOrderedId
lhPlan()

' ---- act ----
if svHealSlot >= 0 then
  useItem(svHealSlot)
end if
mnActed = 0
if svRetreat = 1 then
  walkTo(svRetX, svRetY)
  mnOrderedId = 0
  mnActed = 1
end if
if mnActed = 0 and pnTargetId <> 0 then
  ' Punish: an enemy hero farming under our tower is the best income on the map.
  if attackTarget(pnTargetId) = 1 then
    pnTicks = pnTicks + 1
    if mnOrderedId <> pnTargetId then
      pnStarts = pnStarts + 1
    end if
    mnOrderedId = pnTargetId
    mnActed = 1
  end if
end if
if mnActed = 0 and ksTargetId <> 0 then
  ' Kill steal: the finishing blow on a hero or structure teammates are fighting.
  if ksSlot >= 0 then
    if castTarget(ksSlot, ksTargetId) = 1 then
      ksCasts = ksCasts + 1
    end if
    if ksTargetId >= 100 and ksTargetId < 1000 then
      attackTarget(ksTargetId)
    end if
    mnOrderedId = ksTargetId
    mnActed = 1
  else
    if attackTarget(ksTargetId) = 1 then
      mnOrderedId = ksTargetId
      mnActed = 1
    end if
  end if
end if
if mnActed = 0 then
  if lhSecureSlot >= 0 then
    if castTarget(lhSecureSlot, lhSecureId) = 1 then
      lhSecures = lhSecures + 1
    end if
  end if
  if lhPoisonId <> 0 then
    if attackTarget(lhPoisonId) = 1 then
      if useItem(shPoisonSlot) = 1 then
        lhPoisons = lhPoisons + 1
      end if
    end if
  end if
  if lhTargetId <> 0 then
    if attackTarget(lhTargetId) = 1 then
      if mnOrderedId <> lhTargetId then
        lhOrders = lhOrders + 1
      end if
      mnOrderedId = lhTargetId
      mnActed = 1
    end if
  else
    if lhPoisonId <> 0 then
      ' The poison target was already ordered; keep it rather than walking away.
      mnOrderedId = lhPoisonId
      mnActed = 1
    end if
  end if
end if
if mnActed = 0 then
  if lhState = 1 then
    mnWalkX = lhHoldX
    mnWalkY = lhHoldY
  else
    mnLaneFront()
    mnWalkX = mnFrontX
    mnWalkY = mnFrontY
  end if
  mnChase = 0
  if cfgChaseClass = 1 and ksHold = 0 and lhState = 1 and lhAnchor >= 0 then
    oDx = efX(lhAnchor) - scSelfX
    oDy = efY(lhAnchor) - scSelfY
    if oDx * oDx + oDy * oDy <= lhChase * lhChase then
      mnChase = 1
    end if
  end if
  mnIdleDx = mnWalkX - selfX
  mnIdleDy = mnWalkY - selfY
  if mnChase = 1 then
    ' Chase classes: keep the engine attacking the anchor footman instead of
    ' walking to a standoff point.
    if selfTarget = 0 then
      attackTarget(efId(lhAnchor))
    end if
    mnChases = mnChases + 1
  else
  if cfgIdleHere = 1 and ksHold = 0 and lhState = 1 and mnIdleDx * mnIdleDx + mnIdleDy * mnIdleDy <= 1 then
    ' Idle classes: at the standoff point, stop walking and let the engine
    ' auto-acquire and attack instead of holding for windows.
    attackTarget(0)
    mnIdles = mnIdles + 1
  else
    if mnWalkX = selfX and mnWalkY = selfY then
      ' Already there: keep a live walk target so the engine does not auto-acquire.
      if mnNudge = 0 then
        mnNudge = 1
      else
        mnNudge = 0
      end if
      if scFortX > scSelfX then
        mnWalkX = selfX + mnNudge
      else
        mnWalkX = selfX - mnNudge
      end if
    end if
    if walkTo(mnWalkX, mnWalkY) = 0 then
      ' No path to the standoff tile (terrain or a building): aim at the anchor itself.
      if lhState = 1 then
        walkTo(efX(lhAnchor) / 60, efY(lhAnchor) / 60)
      end if
    end if
  end if
  end if
  mnOrderedId = 0
end if

' ---- shopping (works anywhere) ----
shBuy()
mnExpectedGold = selfGold - shSpent

' ---- telemetry ----
if worldTick mod cfgReportEvery = 0 then
  print "LH ", worldTick, " ", mnLastHits, " ", mnHeroKills, " ", mnTowerKills, " ", selfLevel, " ", lhOrders, " ", lhSecures, " ", lhPoisons, " ", mnLost, " ", mnDeaths, " ", selfGold, " ", mnEarly, " ", mnMissed, " ", pnStarts, " ", pnTicks, " ", pnChaseTicks, " ", mnChases, " ", ksHeroWin, " ", ksCasts, " ", ksStructWin
end if
end

' Lane front: the allied footman of my lane farthest from my god, preferring
' footmen that are fighting (a stuck straggler is never fighting); else the
' lane's waypoint. Writes mnFrontX, mnFrontY in tiles.
sub mnLaneFront()
  mnSeat = selfId - 100
  mnSlot = mnSeat mod 5
  mnLane = 1
  if mnSlot < 2 then
    mnLane = 0
  end if
  if mnSlot > 2 then
    mnLane = 2
  end if
  mnDiag = (mapWidth - 1) * 60
  mnBest = -1
  mnBestD = -1
  mnBestFight = 0
  oI = 0
  while oI < afN
    oS = afX(oI) + afY(oI) - mnDiag
    oL = 1
    if oS <= -900 then
      oL = 0
    end if
    if oS >= 900 then
      oL = 2
    end if
    if oL = mnLane then
      oDx = afX(oI) - scFortX
      oDy = afY(oI) - scFortY
      oD = oDx * oDx + oDy * oDy
      mnFight = 0
      if afTgt(oI) <> 0 then
        mnFight = 1
      end if
      if mnFight > mnBestFight or (mnFight = mnBestFight and oD > mnBestD) then
        mnBestFight = mnFight
        mnBestD = oD
        mnBest = oI
      end if
    end if
    oI = oI + 1
  wend
  if mnBest >= 0 then
    mnFrontX = afX(mnBest) / 60
    mnFrontY = afY(mnBest) / 60
    ' A front with an enemy hero on it and no allied hero near it is a place
    ' to die, not to farm: wait where I am instead.
    mnUnsafe = 0
    oI = 0
    while oI < ehN
      oDx = ehPos(oI) / 8192 - afX(mnBest)
      oDy = ehPos(oI) mod 8192 - afY(mnBest)
      if oDx * oDx + oDy * oDy <= cfgHeroFearRange * cfgHeroFearRange then
        mnUnsafe = 1
      end if
      oI = oI + 1
    wend
    if mnUnsafe = 1 then
      oI = 0
      while oI < ahN
        oDx = ahX(oI) - afX(mnBest)
        oDy = ahY(oI) - afY(mnBest)
        if oDx * oDx + oDy * oDy <= cfgHeroFearRange * cfgHeroFearRange then
          mnUnsafe = 0
        end if
        oI = oI + 1
      wend
    end if
    if mnUnsafe = 1 then
      mnFrontX = selfX
      mnFrontY = selfY
    end if
  else
    mnFrontX = mapWidth / 2
    mnFrontY = mapHeight / 2
    if mnLane = 0 then
      mnFrontX = 14
      mnFrontY = 14
    end if
    if mnLane = 2 then
      mnFrontX = mapWidth - 15
      mnFrontY = mapHeight - 15
    end if
  end if
end sub

