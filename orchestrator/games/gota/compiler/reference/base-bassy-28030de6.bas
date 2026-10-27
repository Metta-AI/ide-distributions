' Paintbot PW baseline. Every cog runs this file on its own: no shared memory, fog-gated
' Vision uses typed integer records and explicit integer division.
' Territory play is built from four habits that measurably win fights:
'   1. lead the target by the full gun windup and cancel out our own movement,
'   2. never walk in a straight line while an opponent can see us,
'   3. move in squads of four that agree on a heart without talking,
'   4. refuse a fight we are visibly losing.
' Budget: 50,000 instructions per decision; an overrun disables the cog, so every loop here
' is bounded by the 16 seats, the heart count, or a fixed iteration count.
dim avoidUntil(16)
TYPE SupplyMemory
  x AS INTEGER
  y AS INTEGER
  kind AS INTEGER
  tick AS INTEGER
END TYPE
TYPE MotionMemory
  x AS INTEGER
  y AS INTEGER
  seen AS INTEGER
END TYPE
DIM supplies(32) AS SupplyMemory
DIM motion(16) AS MotionMemory
dim drF(6)

' Integer square root by Newton's method from above. 23170^2 exceeds any squared map distance.
sub isqrt(n)
  root = 0
  if n <= 0 then
    exit sub
  end if
  root = 23170
  guess = (root + n \ root) \ 2
  iterations = 0
  while guess < root and iterations < 24
    root = guess
    guess = (root + n \ root) \ 2
    iterations = iterations + 1
  wend
end sub

' How much of the straight line between two points is under water, in ten samples: into wet.
sub wetLine(ax, ay, bx, by)
  wet = 0
  s3 = 1
  while s3 <= 10
    if waterAt(ax + (bx - ax) * s3 \ 10, ay + (by - ay) * s3 \ 10) then
      wet = wet + 1
    end if
    s3 = s3 + 1
  wend
end sub

' Time to walk a leg, in metres of dry walking: a wet metre costs kWetCost dry ones. Into legCost.
sub legTime(ax, ay, bx, by)
  wetLine(ax, ay, bx, by)
  isqrt((bx - ax) * (bx - ax) + (by - ay) * (by - ay))
  legCost = root \ 100 + root \ 100 * wet * (kWetCost - 1) \ 10
end sub

' Small linear congruential generator; every product stays far inside int32.
sub nextRandom()
  rngState = (rngState * 75 + 74) mod 65537
end sub

' Pick the next dodge leg: mostly reverse across the line to the threat, keep some progress
' toward the goal, and hold the leg for legTicks so a shot that starts now flies true.
sub planLeg(minTicks, maxTicks)
  nextRandom()
  if rngState mod 5 <> 0 then
    zig = 0 - zig
  end if
  if zig = 0 then
    zig = 1
  end if
  nextRandom()
  legTicks = minTicks + rngState mod (maxTicks - minTicks + 1)
  tx = threatX - me.x
  ty = threatY - me.y
  isqrt(tx * tx + ty * ty)
  legX = 0
  legY = 0
  if root > 0 then
    ' Perpendicular to the threat, scaled to 100.
    legX = (0 - ty) * 100 * zig \ root
    legY = tx * 100 * zig \ root
  end if
  if holding = 0 then
    fx = goalX - me.x
    fy = goalY - me.y
    isqrt(fx * fx + fy * fy)
    if root > 60 then
      legX = legX * 3 \ 4 + fx * 100 \ root
      legY = legY * 3 \ 4 + fy * 100 \ root
    end if
  end if
  isqrt(legX * legX + legY * legY)
  if root > 0 then
    legX = legX * 28 \ root
    legY = legY * 28 \ root
  end if
end sub

if started = 0 then
  started = 1
  rosterLimit = 16
  if me.seats < rosterLimit then
    rosterLimit = me.seats
  end if
  ' Detour offsets beside a wet route, as tenths of the route's length (see Dry route below).
  drF(0) = -10
  drF(1) = -6
  drF(2) = -3
  drF(3) = 3
  drF(4) = 6
  drF(5) = 10
  kWetCost = 6
  rngState = me.id * 4099 + 977
  zig = 1
  if me.id mod 4 >= 2 then
    zig = -1
  end if
  lastX = me.x
  lastY = me.y
end if
myVX = me.x - lastX
myVY = me.y - lastY
if myVX > 60 or myVX < -60 or myVY > 60 or myVY < -60 then
  ' A respawn teleports us; that is not a velocity.
  myVX = 0
  myVY = 0
  gunWait = 0
end if
if gunWait > 0 then
  gunWait = gunWait - 1
end if

' Drop an unreachable assignment after three seconds without meaningful progress.
if me.tick mod 72 = 0 then
  dxProgress = me.x - progressX
  dyProgress = me.y - progressY
  if dxProgress * dxProgress + dyProgress * dyProgress < 40000 and objective >= 0 and objective < 16 then
    dxHeart = controlX(objective) - me.x
    dyHeart = controlY(objective) - me.y
    if dxHeart * dxHeart + dyHeart * dyHeart > 160000 then
      avoidUntil(objective) = me.tick + 360
    end if
  end if
  progressX = me.x
  progressY = me.y
end if

' Our HP cap is the most we have seen (spawn HP): 3 before rules 49, 10 from them.
if me.hp > hpCap then
  hpCap = me.hp
end if

' Opponents and teammates in view. Every query is fog gated.
best = -1
bestCost = 2147483647
thief = -1
foesNear = 0
friendsNear = 1
foeSumX = 0
foeSumY = 0
foesSeen = 0
i = 0
while i < rosterLimit
  if i <> me.id and agents(i).visible then
    dx = agents(i).x - me.x
    dy = agents(i).y - me.y
    d2 = dx * dx + dy * dy
    if i mod 2 <> me.team then
      cost = d2 - (3 - agents(i).hp) * 160000
      if agents(i).carrying then
        cost = cost - 2500000
        thief = i
      end if
      if cost < bestCost and d2 <= gunRange() * gunRange() then
        best = i
        bestCost = cost
      end if
      foesSeen = foesSeen + 1
      foeSumX = foeSumX + agents(i).x
      foeSumY = foeSumY + agents(i).y
      if d2 < 6760000 then
        foesNear = foesNear + 1
      end if
    else
      if d2 < 1440000 then
        friendsNear = friendsNear + 1
      end if
    end if
  end if
  i = i + 1
wend

' Where we want to be. Later rules override earlier ones; one walkTo is issued at the end.
goalX = me.x
goalY = me.y
holding = 0
if me.carrying then
  if me.ownHeartStolen and thief >= 0 then
    goalX = agents(thief).x
    goalY = agents(thief).y
  else
    goalX = me.homeX
    goalY = me.homeY
  end if
else
  if thief >= 0 then
    goalX = agents(thief).x
    goalY = agents(thief).y
  else
    goalX = me.heartX
    goalY = me.heartY
  end if
end if

' Territory: two squads of four. The target is a pure function of public heart ownership and
' the squad number, so all four members (even one that has just respawned) choose the same
' heart with no communication. Squad 0 works outward from above home, squad 1 from below
' (mirrored for blue, so the two teams play the half turn of each other).
objective = -1
if heartCount() > 0 then
  member = (me.id \ 2) mod 8
  squad = member \ 4
  seat = member mod 4
  otherTarget = -1
  pass = 0
  while pass < 2
    refY = me.homeY - 1500
    if pass = 1 then
      refY = me.homeY + 1500
    end if
    if me.team = 1 then
      ' Mirror play: blue's first squad works from below home, the half turn of red's.
      refY = 4000 - refY
    end if
    choice = -1
    choiceCost = 2147483647
    j = 0
    while j < heartCount() and j < 16
      if controlOwner(j) <> me.team and j <> otherTarget then
        dx = (controlX(j) - me.homeX) \ 8
        dy = (controlY(j) - refY) \ 8
        cost = dx * dx + dy * dy
        if controlOwner(j) = -1 then
          cost = cost - 20000
        end if
        if pass = squad and avoidUntil(j) > me.tick then
          cost = cost + 4000000
        end if
        if cost < choiceCost then
          choice = j
          choiceCost = cost
        end if
      end if
      j = j + 1
    wend
    if pass = squad then
      objective = choice
    else
      if pass < squad then
        otherTarget = choice
      end if
    end if
    pass = pass + 1
  wend
  if objective < 0 and otherTarget >= 0 then
    objective = otherTarget
  end if
  if objective >= 0 then
    hx = controlX(objective)
    hy = controlY(objective)
    goalX = hx
    goalY = hy
    ' Seats 0 and 1 stand in the ring (capturer and backup). Seats 2 and 3 cover from outside
    ' it on the opposing side, and step in if nobody on our team is capturing.
    if seat >= 2 then
      capturing = controlCaptureTeam(objective) = me.team
      if capturing then
        idleCapture = 0
      else
        idleCapture = idleCapture + 1
      end if
      dx = hx - me.x
      dy = hy - me.y
      if dx * dx + dy * dy > 640000 or idleCapture < 96 then
        side = 1
        if seat = 3 then
          side = -1
        end if
        ax = 3200 - hx
        ay = 2000 - hy
        if me.team = 0 then
          ax = ax + 1200
        else
          ax = ax - 1200
        end if
        isqrt(ax * ax + ay * ay)
        if root > 0 then
          goalX = hx + (ax * 3 - ay * 2 * side) * 90 \ root
          goalY = hy + (ay * 3 + ax * 2 * side) * 90 \ root
        end if
      end if
    end if
    dx = goalX - me.x
    dy = goalY - me.y
    if dx * dx + dy * dy < 8100 then
      holding = 1
    end if
  end if
end if

' Rules 49: a misting or radar cog cannot attack, so it keeps beside its nearest teammate,
' inside the mister's heal (500) or the radar's boost (800).
if (mistingTicks() > 0 or radarTicks() > 0) and me.carrying = 0 then
  mate = -1
  mateD = 2147483647
  i = 0
  while i < rosterLimit
    if i <> me.id and i mod 2 = me.team and agents(i).visible then
      dx = agents(i).x - me.x
      dy = agents(i).y - me.y
      if dx * dx + dy * dy < mateD then
        mate = i
        mateD = dx * dx + dy * dy
      end if
    end if
    i = i + 1
  wend
  if mate >= 0 and mateD > 90000 then
    goalX = agents(mate).x
    goalY = agents(mate).y
    holding = 0
  end if
end if

' Remember seen supplies for ten seconds and equip when it is safe to.
i = 0
while i < pickupCount() and i < 32
  if pickupVisible(i) then
    supplies(i).x = pickupX(i)
    supplies(i).y = pickupY(i)
    supplies(i).kind = pickupKind(i)
    supplies(i).tick = me.tick + 1
  end if
  i = i + 1
wend
if (me.carrying = 0) and thief < 0 then
  nearest = -1
  nearestCost = 4840000
  j = 0
  while j < pickupCount() and j < 32
    if supplies(j).tick > 0 and me.tick - supplies(j).tick < 240 then
      kind = supplies(j).kind
      wanted = (kind = 0 and (me.hasGrenade = 0)) or (kind = 2 and me.hp < hpCap) or (kind = 3 and me.armorHp < 3 and me.hp = hpCap)
      ' Rules 49 items: a sniper when we carry no spray, the mister when half hurt, the radar
      ' among friends. Any pickup ends a radar, so its carrier takes only a medkit it needs.
      wanted = wanted or (kind = 6 and hasSniper() = 0 and me.hasSpray = 0) or (kind = 5 and me.hp * 2 <= hpCap and mistingTicks() = 0) or (kind = 7 and friendsNear >= 3 and radarTicks() = 0)
      if radarTicks() > 0 and not (kind = 2 and me.hp * 3 <= hpCap) then
        wanted = 0
      end if
      if wanted then
        dx = supplies(j).x - me.x
        dy = supplies(j).y - me.y
        cost = dx * dx + dy * dy
        if kind = 2 and me.hp > 0 and me.hp * 3 <= hpCap then
          ' A medkit is worth a whole life to a cog on one hit point.
          cost = cost \ 4
        end if
        if cost < 10000 and (pickupVisible(j) = 0) then
          supplies(j).tick = 0
        else
          if cost < nearestCost then
            nearest = j
            nearestCost = cost
          end if
        end if
      end if
    end if
    j = j + 1
  wend
  if nearest >= 0 and (best < 0 or bestCost > 1440000 or (me.hp > 0 and me.hp * 3 <= hpCap)) then
    goalX = supplies(nearest).x
    goalY = supplies(nearest).y
    holding = 0
  end if
end if

' Refuse a fight we are visibly losing: head for the heart that is far from them and near us.
if foesNear - friendsNear >= 1 and (me.carrying = 0) then
  cx = foeSumX \ foesSeen
  cy = foeSumY \ foesSeen
  away = -1
  awayScore = -2147483647
  j = 0
  while j < heartCount() and j < 16
    ex = (controlX(j) - cx) \ 16
    ey = (controlY(j) - cy) \ 16
    mx = (controlX(j) - me.x) \ 16
    my = (controlY(j) - me.y) \ 16
    score = ex * ex + ey * ey - (mx * mx + my * my) \ 2
    if score > awayScore then
      away = j
      awayScore = score
    end if
    j = j + 1
  wend
  if away >= 0 then
    goalX = controlX(away)
    goalY = controlY(away)
    holding = 0
  end if
end if

' Facing with nothing to shoot: sweep, then turn to speech and sound.
if best < 0 then
  scan = (me.tick \ 24 + me.id) mod 4
  lookX = goalX
  lookY = goalY
  if holding or scan = 1 then
    ' Sweep toward the enemy side first; blue's sweep is the half turn of red's.
    facing = 1 - 2 * me.team
    lookX = me.x + 2000 * facing
    lookY = me.y
    if scan = 1 then
      lookX = me.x
      lookY = me.y + 2000 * facing
    end if
    if scan = 2 then
      lookX = me.x - 2000 * facing
    end if
    if scan = 3 then
      lookX = me.x
      lookY = me.y - 2000 * facing
    end if
  end if
  if heardCount() > 0 then
    lookX = heardX(0)
    lookY = heardY(0)
  end if
  if soundCount() > 0 then
    soundBest = -1
    soundCost = 2147483647
    j = 0
    while j < soundCount() and j < 12
      cost = soundAge(j)
      if soundKind(j) = 1 or soundKind(j) = 2 then
        cost = cost - 48
      end if
      if cost < soundCost then
        soundBest = j
        soundCost = cost
      end if
      j = j + 1
    wend
    if soundBest >= 0 then
      bearing = soundDirection(soundBest)
      dxSound = 0
      dySound = 0
      if bearing = 0 or bearing = 1 or bearing = 7 then
        dxSound = 1000
      end if
      if bearing = 3 or bearing = 4 or bearing = 5 then
        dxSound = -1000
      end if
      if bearing = 1 or bearing = 2 or bearing = 3 then
        dySound = 1000
      end if
      if bearing = 5 or bearing = 6 or bearing = 7 then
        dySound = -1000
      end if
      lookX = me.x + dxSound
      lookY = me.y + dySound
    end if
  end if
  lookAt(lookX, lookY)
end if

if me.tick mod 360 = me.id * 21 then
  if best >= 0 then
    shout(strNew("Contact! Cover this lane."))
  else
    if foesNear - friendsNear >= 1 then
      shout(strNew("Too many. Falling back."))
    else
      shout(strNew("Moving with the squad."))
    end if
  end if
end if

' Footwork. In contact, move in short random legs across the line to the threat. A shot is only
' ordered at the start of a leg that lasts the whole windup, so our own movement is known.
moveX = goalX
moveY = goalY
inContact = best >= 0 and me.trenchId < 0
if inContact then
  threatX = agents(best).x
  threatY = agents(best).y
  ' Blocked for three ticks: let the engine's pathing take over for a second.
  if legTicks > 0 and myVX * myVX + myVY * myVY < 64 then
    stalled = stalled + 1
  else
    stalled = 0
  end if
  if stalled >= 3 then
    pathUntil = me.tick + 24
    stalled = 0
    legTicks = 0
  end if
  wantShot = gunWait = 0 and (me.hasSpray = 0 or bestCost < 640000)
  if me.tick >= pathUntil then
    if legTicks <= 0 or (wantShot and legTicks < 6) then
      if wantShot then
        planLeg(6, 9)
      else
        planLeg(3, 6)
      end if
    end if
    legTicks = legTicks - 1
    moveX = me.x + legX * 4
    moveY = me.y + legY * 4
    if holding then
      ' Stay inside the ring: turn back toward its centre when the leg would leave it.
      dx = me.x + legX * 2 - goalX
      dy = me.y + legY * 2 - goalY
      if dx * dx + dy * dy > 9000 then
        moveX = goalX
        moveY = goalY
        legTicks = 0
      end if
    end if
  end if
else
  legTicks = 0
  stalled = 0
end if
' Dry route. The navigator walks straight at any goal no wall blocks, and water blocks nothing,
' so an objective across the lake is reached by wading at a quarter speed. Against the league
' leader that is where 73% of our deaths happened. When the straight way to a target more than
' 8 m off crosses water, walk first to whichever of six points beside the route is quickest to
' go through, a wet metre costing kWetCost dry ones, and keep that point until it is reached,
' the target moves 10 m, or ten seconds pass. Footwork is left where the dodge put it.
drWalk = 0
drTx = moveX
drTy = moveY
drDx = drTx - me.x
drDy = drTy - me.y
if drDx * drDx + drDy * drDy > 640000 then
  if drActive then
    drEx = drTx - drGx
    drEy = drTy - drGy
    if drEx * drEx + drEy * drEy > 1000000 then
      drActive = 0
    end if
    drEx = drWx - me.x
    drEy = drWy - me.y
    if drEx * drEx + drEy * drEy < 90000 then
      drActive = 0
    end if
    if me.tick - drTick > 240 then
      drActive = 0
    end if
  end if
  if drActive = 0 and me.tick - drTick >= 12 then
    drTick = me.tick
    legTime(me.x, me.y, drTx, drTy)
    if wet > 0 then
      drBest = legCost
      drBestK = -1
      drMx = me.x + drDx \ 2
      drMy = me.y + drDy \ 2
      drK = 0
      while drK < 6
        drCx = drMx - drDy * drF(drK) \ 10
        drCy = drMy + drDx * drF(drK) \ 10
        if drCx > mapMinX() + 200 and drCx < mapMaxX() - 200 and drCy > mapMinY() + 200 and drCy < mapMaxY() - 200 then
          if waterAt(drCx, drCy) = 0 then
            legTime(me.x, me.y, drCx, drCy)
            drSc = legCost
            legTime(drCx, drCy, drTx, drTy)
            drSc = drSc + legCost
            if drSc < drBest then
              drBest = drSc
              drBestK = drK
              drWx = drCx
              drWy = drCy
            end if
          end if
        end if
        drK = drK + 1
      wend
      if drBestK >= 0 then
        drActive = 1
        drGx = drTx
        drGy = drTy
      end if
    end if
  end if
  if drActive then
    drWalk = 1
  end if
end if
if drWalk then
  walkTo(drWx, drWy)
else
  walkTo(moveX, moveY)
end if

' Gun: the ray leaves six moves after the order, from wherever we then stand, along the
' direction locked one move from now. Aim where they will be, minus our own drift.
if best >= 0 then
  tx = agents(best).x
  ty = agents(best).y
  if motion(best).seen = me.tick - 1 then
    tx = tx + (tx - motion(best).x) * 6
    ty = ty + (ty - motion(best).y) * 6
  end if
  if inContact and me.tick >= pathUntil then
    tx = tx - legX * 5
    ty = ty - legY * 5
  else
    tx = tx - myVX * 5
    ty = ty - myVY * 5
  end if
  ' Hold fire when a visible teammate stands in the line.
  clear = 1
  sx = tx - me.x
  sy = ty - me.y
  isqrt(sx * sx + sy * sy)
  reach = root
  if reach > 0 then
    i = 0
    while i < rosterLimit
      if i <> me.id and i mod 2 = me.team and agents(i).visible then
        ox = agents(i).x - me.x
        oy = agents(i).y - me.y
        along = (ox * sx + oy * sy) \ reach
        across = (ox * sy - oy * sx) \ reach
        if across < 0 then
          across = 0 - across
        end if
        if along > 0 and along < reach and across < 95 then
          clear = 0
        end if
      end if
      i = i + 1
    wend
  end if
  if me.hasSpray = 0 or bestCost < 640000 then
    if clear and gunWait = 0 then
      shootAt(tx, ty)
      gunWait = 25
      if me.armorHp > 0 or me.trenchId >= 0 or me.carrying then
        gunWait = 73
      end if
      if me.hasSpray then
        gunWait = 25
      end if
    else
      lookAt(tx, ty)
    end if
  else
    lookAt(tx, ty)
  end if
end if

i = 0
while i < rosterLimit
  if agents(i).visible then
    motion(i).x = agents(i).x
    motion(i).y = agents(i).y
    motion(i).seen = me.tick
  end if
  i = i + 1
wend

' Grenade: match the charge to the distance, never onto a visible teammate.
if me.hasGrenade and best >= 0 then
  nx = agents(best).x
  ny = agents(best).y
  dx = nx - me.x
  dy = ny - me.y
  d2 = dx * dx + dy * dy
  safe = 1
  i = 0
  while i < rosterLimit
    if i mod 2 = me.team and agents(i).visible then
      fx = agents(i).x - nx
      fy = agents(i).y - ny
      if fx * fx + fy * fy < 202500 then
        safe = 0
      end if
    end if
    i = i + 1
  wend
  if safe and d2 > 160000 and d2 < 1562500 then
    isqrt(d2)
    need = (root - 150) * 24 \ 1130 + 1
    if need < 1 then
      need = 1
    end if
    lookAt(nx, ny)
    chargeGrenade(me.grenadeCharge < need)
    if me.grenadeCharge >= need then
      shout(strNew("Grenade out!"))
    end if
  end if
end if

' Quiet approach to the objective when nothing is in sight but something was heard.
if best < 0 and soundCount() > 0 and objective >= 0 then
  dx = controlX(objective) - me.x
  dy = controlY(objective) - me.y
  if dx * dx + dy * dy < 810000 then
    sneak(1)
  end if
end if

' Rules 49 self-destruct: a dying cog with no teammate in the blast (270) takes a foe it can kill.
if me.hp > 0 and me.hp * 3 <= hpCap and mistingTicks() = 0 and radarTicks() = 0 then
  kills = 0
  i = 0
  while i < rosterLimit
    if i <> me.id and agents(i).visible then
      dx = agents(i).x - me.x
      dy = agents(i).y - me.y
      if dx * dx + dy * dy <= 72900 then
        if i mod 2 = me.team then
          kills = -100
        else
          if agents(i).hp <= me.hp then
            kills = kills + 1
          end if
        end if
      end if
    end if
    i = i + 1
  wend
  if kills > 0 then
    selfDestruct()
  end if
end if

lastX = me.x
lastY = me.y
