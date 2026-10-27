' Pudge Wars berserker — pushes the river, stacks radius and
' distance, and brawls in melee whenever an enemy is in reach.
'
' Rot: blight any unshielded butcher in the 8-tile disk while HP is
' above 40% and we are not in the river; walk inland out of anyone
' else's cloud (blink is dead inside Rot).
'
' See base.bas for the full host API.

' Spend every kill point this tick. selfPoints is a snapshot.
left = selfPoints
while left > 0
  got = 0
  if buyUpgrade(3) = 1 then
    got = 1
  else
    if buyUpgrade(1) = 1 then
      got = 1
    else
      if buyUpgrade(2) = 1 then
        got = 1
      else
        if buyUpgrade(0) = 1 then
          got = 1
        end if
      end if
    end if
  end if
  if got = 1 then
    left = left - 1
  else
    left = 0
  end if
wend
if selfInShop = 1 then
  if selfHp * 2 < selfMaxHp then
    buyPower(0)
  end if
  if itemRank(1) < 5 then
    buyPower(6)
  end if
  if itemRank(0) < 5 then
    buyPower(5)
  end if
  buyPower(3)
  if itemRank(2) < 5 then
    buyPower(7)
  end if
  if itemRank(3) < 5 then
    buyPower(8)
  end if
end if
reachNow = 52 + 4 * upgradeLevel(1) + upgradeLevel(3)
if selfBuff = 4 then
  reachNow = reachNow + reachNow \ 2
end if
leadNow = 9 + upgradeLevel(2)
if leadNow < 1 then
  leadNow = 1
end if

headId = 0
headX = 0
headY = 0
headLayer = 0
lowId = 0
lowX = 0
lowY = 0
lowHp = 2147483647
bestId = 0
bestX = 0
bestY = 0
bestDistance = 2147483647
closeId = 0
closeDistance = 2147483647
saveId = 0
saveX = 0
saveY = 0
preyRot = 0
foreignRot = 0
index = 0
while index < objectCount()
  id = objectId(index)
  if objectAlive(index) then
    dx = objectX(index) - selfX
    dy = objectY(index) - selfY
    distance = dx * dx + dy * dy
    if objectTeam(index) <> selfTeam then
      if objectKind(index) = 1 then
        if objectRot(index) = 1 then
          if distance <= 64 then
            foreignRot = 1
          end if
        end if
      end if
      if objectShielded(index) = 0 then
        hp = objectHp(index)
        if objectHooked(index) = 1 then
          if headId = 0 then
            headId = id
            headX = objectX(index)
            headY = objectY(index)
            headLayer = objectLayer(index)
          end if
        end if
        if hp < lowHp then
          lowHp = hp
          lowId = id
          lowX = objectX(index)
          lowY = objectY(index)
        end if
        if distance < bestDistance then
          bestDistance = distance
          bestId = id
          bestX = objectX(index)
          bestY = objectY(index)
        end if
        if distance < 49 then
          if distance < closeDistance then
            closeDistance = distance
            closeId = id
          end if
        end if
        if objectKind(index) = 1 then
          if distance <= 64 then
            preyRot = 1
          end if
        end if
      end if
    else
      if id <> selfId then
        if objectRot(index) = 1 then
          if distance <= 64 then
            foreignRot = 1
          end if
        end if
        if objectHooked(index) = 1 or objectLayer(index) = 2 then
          saveId = id
          saveX = objectX(index)
          saveY = objectY(index)
        end if
      end if
    end if
  end if
  index = index + 1
wend

if bestId <> 0 then
  lastEx = bestX
  lastEy = bestY
  lastSeen = worldTick
end if
if bestId <> 0 then
if trackId <> bestId then
  trackId = bestId
  velTick = 0
  evx = 0
  evy = 0
end if
if worldTick - velTick >= 8 then
  if velTick > 0 then
    evx = bestX - pex
    evy = bestY - pey
  end if
  pex = bestX
  pey = bestY
  velTick = worldTick
end if
end if
span = 24
dx = bestX - selfX
if dx < 0 then
  dx = 0 - dx
end if
dy = bestY - selfY
if dy < 0 then
  dy = 0 - dy
end if
if dx > span then
  span = dx
end if
if dy > span then
  span = dy
end if
aimX = bestX + evx * span \ leadNow
aimY = bestY + evy * span \ leadNow
if aimX < 8 then
  aimX = 8
end if
if aimX > 119 then
  aimX = 119
end if
if aimY < 8 then
  aimY = 8
end if
if aimY > 119 then
  aimY = 119
end if

if selfTeam = 0 then
  lineX = riverWest + 8
  pokeX = riverEast + 8
else
  lineX = riverEast - 8
  pokeX = riverWest - 8
end if
padX = homeX
lineY = homeY
if lineY < 8 then
  lineY = 8
end if
if lineY > 119 then
  lineY = 119
end if
pokeY = lineY
if lastEy <> 0 then
  age = worldTick - lastSeen
  pokeY = lastEy + evy * age \ 8
  if pokeY < 16 then
    pokeY = 16
  end if
  if pokeY > 111 then
    pokeY = 111
  end if
  if bestId = 0 then
    if selfTeam = 0 then
      pokeX = riverEast + 2 + evx * age \ 16
      if pokeX < riverEast + 1 then
        pokeX = riverEast + 2
      end if
    else
      pokeX = riverWest - 2 + evx * age \ 16
      if pokeX > riverWest - 1 then
        pokeX = riverWest - 2
      end if
    end if
  end if
end if

cutX = -1
cutY = -1
nearX = -1
nearY = -1
ownX = -1
ownY = -1
h = 0
while h < hookCount()
  if hookFlying(h) = 1 then
    if hookTeam(h) <> selfTeam then
      hdx = hookX(h) - selfX
      if hdx < 0 then
        hdx = 0 - hdx
      end if
      hdy = hookY(h) - selfY
      if hdy < 0 then
        hdy = 0 - hdy
      end if
      if hdx * hdx + hdy * hdy <= 484 then
        nearX = hookX(h)
        nearY = hookY(h)
      end if
      if hookVictim(h) <> 0 then
        if hdx <= 30 and hdy <= 30 then
          cutX = hookX(h)
          cutY = hookY(h)
        end if
      end if
    else
      if hookOwner(h) = selfId then
        if hookVictim(h) = 0 then
          ownX = hookX(h)
          ownY = hookY(h)
        end if
      end if
    end if
  end if
  h = h + 1
wend

' ---- rot: blight a close fight, leave anyone else's cloud -----------
wantRot = 0
if preyRot = 1 and selfLayer <> 2 then
  if selfHp * 5 >= selfMaxHp * 2 then
    wantRot = 1
  end if
end if
if wantRot = 1 then
  if selfRot = 0 then
    rotToggle()
  end if
else
  if selfRot = 1 then
    rotToggle()
  end if
end if
fleeRot = 0
if foreignRot = 1 then
  if wantRot = 0 then
    fleeRot = 1
  end if
end if

' ---- blink: dodge inland, dump-chase into water, stretch on our bank
flashed = 0
dodgeFlash = 0
if blinkReady = 1 then
  if selfLayer = 2 then
    stayDrink = 0
    if headLayer = 2 then
      stayDrink = 1
    end if
    if stayDrink = 0 then
      if selfTeam = 0 then
        flashed = blinkTo(riverWest, selfY)
      else
        flashed = blinkTo(riverEast, selfY)
      end if
    end if
  else
    if nearX >= 0 then
      if nearY > selfY then
        hopY = selfY - 36
      else
        hopY = selfY + 36
      end if
      if hopY < 16 then
        hopY = 16
      end if
      if hopY > 111 then
        hopY = 111
      end if
      if selfTeam = 0 then
        hopX = selfX - 16
        if hopX > riverWest then
          hopX = riverWest
        end if
      else
        hopX = selfX + 16
        if hopX < riverEast then
          hopX = riverEast
        end if
      end if
      flashed = blinkTo(hopX, hopY)
      if flashed = 1 then
        dodgeFlash = 1
      end if
    else
      if ownX >= 0 then
        edx = ownX - selfX
        edy = ownY - selfY
        if edx < 0 then
          adx = 0 - edx
        else
          adx = edx
        end if
        if edy < 0 then
          ady = 0 - edy
        else
          ady = edy
        end if
        if adx >= 12 or ady >= 12 then
          hopX = ownX + edx
          hopY = ownY + edy
          if selfTeam = 0 then
            if hopX > riverWest then
              hopX = riverWest
            end if
          else
            if hopX < riverEast then
              hopX = riverEast
            end if
          end if
          flashed = blinkTo(hopX, hopY)
        end if
      end if
      if flashed = 0 then
        if headId <> 0 then
          hx = headX
          hy = headY
          if headLayer = 2 then
            flashed = blinkTo(hx, hy)
          else
            cdx = hx - selfX
            if cdx < 0 then
              cdx = 0 - cdx
            end if
            cdy = hy - selfY
            if cdy < 0 then
              cdy = 0 - cdy
            end if
            if cdx > 8 or cdy > 8 then
              if headLayer <> selfLayer then
                if selfTeam = 0 then
                  hx = 106
                else
                  hx = 22
                end if
              end if
              flashed = blinkTo(hx, hy)
            end if
          end if
        end if
      end if
    end if
  end if
end if

mood = 0
if dodgeFlash = 1 then
  mood = 7
  if hookReady = 1 then
    if bestId <> 0 then
      hookTo(aimX, aimY)
      mood = 4
    else
      hookTo(pokeX, pokeY)
      mood = 4
    end if
  end if
else
if fleeRot = 0 and closeId <> 0 then
  attackTarget(closeId)
  mood = 1
else
  if hookReady = 1 then
    if headId <> 0 then
      hx = headX + (homeX - headX) \ 2
      hy = headY + (homeY - headY) \ 2
      hookTo(hx, hy)
      mood = 2
    else
    if cutX >= 0 then
      hookTo(cutX, cutY)
      mood = 8
    else
      if saveId <> 0 then
        hookTo(saveX, saveY)
        mood = 9
      else
          if lowId <> 0 then
            if lowHp * 3 <= selfMaxHp then
              hookTo(lowX, aimY)
              mood = 3
            end if
          end if
          if mood = 0 then
            if bestId <> 0 then
              if dx <= reachNow then
                hookTo(aimX, aimY)
                mood = 4
              end if
            else
              if worldTick > 8 then
                hookTo(pokeX, pokeY)
                mood = 4
              end if
            end if
          end if
      end if
    end if
    end if
  end if
end if
end if

if flashed = 0 then
  if selfLayer <> 2 then
    if selfHooked = 0 then
      if fleeRot = 1 then
        walkTo(padX, lineY)
        if mood = 0 then
          mood = 10
        end if
      end if
      needCore = 0
      if itemRank(1) < 5 then
        needCore = 1
      end if
      if itemRank(0) < 5 then
        needCore = 1
      end if
      if needCore = 1 then
        if selfGold >= 10 then
          if nearX < 0 then
            if headId = 0 then
              walkTo(padX, lineY)
              if mood = 0 then
                mood = 6
              end if
            end if
          end if
        end if
      end if
      if mood <> 6 and fleeRot = 0 then
        if bestId <> 0 then
          if dx <= 52 then
            walkTo(bestX, bestY)
            if mood = 0 then
              mood = 5
            end if
          else
            walkTo(lineX, lineY)
            if mood = 0 then
              mood = 6
            end if
          end if
        else
          walkTo(lineX, lineY)
          if mood = 0 then
            mood = 6
          end if
        end if
      end if
    end if
    if selfTeam = 0 then
      wallX = selfX + 3
      if wallX > riverWest then
        wallX = riverWest
      end if
    else
      wallX = selfX - 3
      if wallX < riverEast then
        wallX = riverEast
      end if
    end if
    placeWall(wallX, selfY)
    mineY = selfY + 3
    if laneSlot = 1 then
      mineY = selfY - 3
    end if
    placeMine(wallX, mineY)
  end if
end if

if mood <> saidMood then
  saidMood = mood
  if mood = 1 then
    print "meat for the grinder"
  end if
  if mood = 2 then
    print "no escape"
  end if
  if mood = 3 then
    print "finish the wounded"
  end if
  if mood = 4 then
    print "hook and charge"
  end if
  if mood = 5 then
    print "closing in"
  end if
  if mood = 6 then
    print "press the river"
  end if
  if mood = 7 then
    print "dodge and hook"
  end if
  if mood = 8 then
    print "snip that hook"
  end if
  if mood = 9 then
    print "got you, hang on"
  end if
  if mood = 10 then
    print "backing off rot"
  end if
end if
