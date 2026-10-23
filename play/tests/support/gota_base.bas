' @gota-structures
' GotA reference policy: draft, farm, push, heal, resupply, and finish the god.
' Structured snapshots provide observations; commands remain explicit.
' Object indices last only for this decision. IDs may be remembered.
' Read the bot guide for units, LOS restrictions, and action error constants.
' Abilities and items are used only by our explicit policy commands.

dim owned(22)
dim inventorySlot(22)
dim allyIds(9)
dim seenMaxHp(9)
dim castRange(3)
dim castDelay(3)
dim castGround(3)
dim castMinimum(3)

sub chooseHero()
  if draft.turnId <> self.id then
    exit sub
  end if
  bestClass = -1
  bestScore = -10000
  for candidate = 0 to 9
    if heroChoices(candidate).available then
      role = heroChoices(candidate).role
      score = 100
      for player = 0 to draft.playerCount - 1
        if players(player).team = self.team then
          picked = players(player).class
          if picked >= 0 then
            if heroChoices(picked).role = role then
              score = score - 100
            end if
          end if
        end if
      next player
      if score > bestScore then
        bestScore = score
        bestClass = candidate
      end if
    end if
  next candidate
  if bestClass >= 0 then
    accepted = draftHero(bestClass)
    actionError = lastAction.error
  end if
end sub

sub learnAbilities()
  ' Rank requirements and effects come from the host, not a stat table.
  for upgrade = 1 to 4
    if self.abilityPoints = 0 then
      exit sub
    end if
    upgradeSlot = -1
    upgradeScore = -1
    for spellSlot = 0 to 3
      rank = abilities(spellSlot).level
      if rank < abilities(spellSlot).maxLevel then
        if self.level >= abilities(spellSlot).requiredLevel then
          if abilities(spellSlot).canLevel then
            ' Prefer R, W, E, Q whenever the next rank is legal.
            score = spellSlot
            if spellSlot = 1 then
              score = 2
            elseif spellSlot = 2 then
              score = 1
            end if
            if score > upgradeScore then
              upgradeScore = score
              upgradeSlot = spellSlot
            end if
          end if
        end if
      end if
    next spellSlot
    if upgradeSlot < 0 then
      exit sub
    end if
    accepted = levelAbility(upgradeSlot)
    actionError = lastAction.error
  next upgrade
end sub

sub readObject(index)
  id = objects(index).id
  kind = objects(index).kind
  team = objects(index).team
  hp = objects(index).hp
  if hp <= 0 then
    exit sub
  end if
  x = (originX + floor(side * objects(index).position.x + .5))
  y = (originY + floor(side * objects(index).position.y + .5))
  dx = x - myX
  dy = y - myY
  distance = dx * dx + dy * dy
  if kind = 6 then
    ' Camps never distract from lane combat or count as enemy heroes.
    if objects(index).returning or objects(index).alive = 0 then
      exit sub
    end if
    camp = objects(index).campId
    if camp < 0 or camp >= match.campCount then
      exit sub
    end if
    tier = camps(camp).tier
    campDx = (originX + floor(side * camps(camp).position.x + .5)) - myX
    campDy = (originY + floor(side * camps(camp).position.y + .5)) - myY
    if campDx * campDx + campDy * campDy > 100 then
      exit sub
    end if
    enoughHealth = self.hp * 10 >= self.maxHp * 7
    if self.targetId = id then
      enoughHealth = self.hp * 10 >= self.maxHp * 4
    end if
    if camp >= 0 and camp < match.campCount and enoughHealth then
      if self.level >= 1 + (tier - 1) * 3 and distance <= 64 then
        score = 100 - distance
        if objects(index).leader then
          score = score - 10
        end if
        if self.targetId = id then
          score = score + 100
        end if
        if score > campScore then
          campScore = score
          campIndex = index
          campId = id
          campHp = hp
          campXpos = x
          campYpos = y
          campDistance = distance
        end if
      end if
    end if
    exit sub
  end if
  if team = self.team then
    if kind = 1 then
      homeX = x
      homeY = y
      enemyX = map.width - 1 - x
      enemyY = map.height - 1 - y
    elseif kind = 4 then
      ' Protected allied towers still serve as portal anchors.
      dx = x - enemyX
      dy = y - enemyY
      score = dx * dx + dy * dy
      if score < forwardDistance then
        forwardDistance = score
        forwardX = x
        forwardY = y
      end if
    elseif kind = 2 then
      allyIds(allies) = id
      allies = allies + 1
      class = objects(index).class
      if hp > seenMaxHp(class) then
        seenMaxHp(class) = hp
      end if
      if distance <= 100 then
        friendlyPower = friendlyPower + objects(index).level + 2
      end if
      missing = seenMaxHp(class) - hp
      if distance <= healRange * healRange and missing > healMissing then
        healMissing = missing
        healId = id
      end if
    elseif kind = 3 and distance <= 64 then
      tanks = tanks + 1
    end if
    exit sub
  end if
  if kind = 1 then
    enemyX = x
    enemyY = y
  end if
  if kind = 2 and distance <= 144 then
    enemyPower = enemyPower + objects(index).level + 2
    if objects(index).mana >= 25 and objects(index).controls.silenceTicks = 0 then
      enemyPower = enemyPower + 2
    end if
  end if
  if distance < threatDistance then
    threatDistance = distance
    threatX = x
    threatY = y
  end if
  if distance > 324 or objects(index).alive = 0 then
    exit sub
  end if
  target = objects(index).targetId
  if kind = 4 and target = self.id then
    towerAggro = 1
  end if
  score = 1000 - distance * 2
  if kind = 1 then
    score = score + 500
  elseif kind = 2 then
    score = score + 60 - objects(index).level * 8
    if hp < self.attackDamage * 4 then
      score = score + 250
    end if
  elseif kind = 3 then
    score = score + 100
    if hp <= self.attackDamage and self.attackCooldownTicks <= match.tickRate \ 2 then
      score = score + 400
    end if
  elseif kind = 5 then
    score = score + 40
  end if
  if target = self.id then
    score = score + 40
  end if
  if id = self.targetId then
    score = score + 60
  end if
  if id = blockedId and match.tick < blockedUntil then
    exit sub
  end if
  if score > bestScore then
    bestScore = score
    bestIndex = index
    bestId = id
    bestKind = kind
    bestHp = hp
    bestX = x
    bestY = y
    bestDistance = distance
  end if
end sub

sub observe()
  campScore = -10000
  campId = 0
  bestScore = -10000
  bestId = 0
  bestDistance = 1000000
  threatDistance = 1000000
  forwardDistance = 1000000
  friendlyPower = 0
  enemyPower = 0
  allies = 0
  tanks = 0
  towerAggro = 0
  healId = self.id
  healMissing = self.maxHp - self.hp
  seenMaxHp(self.class) = self.maxHp
  visibleCount = match.objectCount
  ' Buildings and heroes precede creeps. Rotate the large creep tail so a
  ' crowded battlefield cannot exhaust the per-decision VM budget.
  for scan = 0 to 95
    index = scan
    if scan >= 48 then
      index = scan + scanOffset
    end if
    if index < visibleCount then
      readObject(index)
    end if
  next scan
  ' Retain a creep target outside this scan window only after validating
  ' its remembered index against the stable ID in the fresh observation.
  if targetIndex >= 48 and targetIndex < visibleCount and self.targetId <> 0 then
    if targetIndex < 48 + scanOffset or targetIndex >= 96 + scanOffset then
      if objects(targetIndex).id = self.targetId then
        readObject(targetIndex)
      end if
    end if
  end if
  scanOffset = scanOffset + 48
  if scanOffset >= visibleCount - 48 then
    scanOffset = 0
  end if
  if bestId = 0 and campId <> 0 and towerAggro = 0 and enemyPower = 0 then
    bestId = campId
    bestIndex = campIndex
    bestKind = 6
    bestHp = campHp
    bestX = campXpos
    bestY = campYpos
    bestDistance = campDistance
  end if
  if bestId = 0 then
    exit sub
  end if
  targetIndex = bestIndex
  ' Structured motion and facing already use fixed-point tile units.
  velocityX = side * objects(bestIndex).velocity.x
  velocityY = side * objects(bestIndex).velocity.y
  ' Do not lead a unit whose control lasts through the predicted impact.
  targetHeld = objects(bestIndex).controls.stunTicks
  targetRoot = objects(bestIndex).controls.rootTicks
  if targetRoot > targetHeld then
    targetHeld = targetRoot
  end if
  facingX = side * objects(bestIndex).facing.x
  facingY = side * objects(bestIndex).facing.y
  aimedAtUs = facingX * (myX - bestX) + facingY * (myY - bestY)
  if bestKind = 2 then
    ' Visible equipment and potion stacks help judge a close duel.
    for inspectSlot = 0 to 5
      gear = objectItems(bestIndex * 6 + inspectSlot).id
      quantity = objectItems(bestIndex * 6 + inspectSlot).count
      if quantity > 0 and bestDistance <= 144 then
        if gear >= 5 and gear <= 20 then
          enemyPower = enemyPower + 1
        elseif gear = 1 or gear = 2 then
          enemyPower = enemyPower + 2
        end if
      end if
    next inspectSlot
  end if
end sub

sub buy(id, price, quantity)
  if owned(id) >= quantity or budget < price then
    exit sub
  end if
  if owned(id) = 0 and emptySlots = 0 then
    exit sub
  end if
  accepted = buyItem(id)
  actionError = lastAction.error
  if accepted then
    if owned(id) = 0 then
      emptySlots = emptySlots - 1
    end if
    owned(id) = owned(id) + 1
    budget = budget - price
    for boughtSlot = 0 to 5
      if items(boughtSlot).id = id then
        inventorySlot(id) = boughtSlot
      end if
    next boughtSlot
  end if
end sub

sub inventory()
  for id = 0 to 22
    owned(id) = 0
    inventorySlot(id) = -1
  next id
  emptySlots = 0
  for itemSlot = 0 to 5
    id = items(itemSlot).id
    if id = 0 then
      emptySlots = emptySlots + 1
    else
      owned(id) = items(itemSlot).count
      inventorySlot(id) = itemSlot
      if items(itemSlot).cooldownTicks = 0 and self.inOwnSpawn = 0 then
        consume = 0
        if id = 1 and self.maxHp - self.hp >= 60 then
          consume = threatDistance > 100 and match.tick - hurtTick > match.tickRate
        elseif id = 2 and self.hp * 2 < self.maxHp then
          consume = 1
        elseif id = 22 and self.maxMana - self.mana >= 45 then
          consume = threatDistance > 100 and match.tick - hurtTick > match.tickRate
        elseif id = 3 and self.mana * 3 < self.maxMana then
          consume = bestId <> 0
        elseif id = 4 and self.targetId = bestId and bestId <> 0 then
          consume = bestDistance <= attackRange * attackRange
        end if
        if consume then
          accepted = useItem(itemSlot)
          actionError = lastAction.error
          if accepted then
            owned(id) = owned(id) - 1
            if owned(id) = 0 then
              emptySlots = emptySlots + 1
              inventorySlot(id) = -1
            end if
          end if
        end if
      end if
    end if
  next itemSlot
  if self.canShop = 0 then
    exit sub
  end if
  ' Reserve three slots for recovery and travel, two for useful equipment,
  ' and one for a role-specific burst consumable. Stacks top up on return.
  budget = self.gold
  buy(8, 100, 1)
  buy(1, 30, 2)
  buy(21, 100, 2)
  buy(22, 45, 2)
  if role = 0 or role = 4 then
    buy(16, 160, 1)
    buy(2, 75, 2)
  elseif role = 1 then
    buy(19, 180, 1)
    buy(4, 40, 2)
  else
    buy(20, 190, 1)
    buy(3, 90, 2)
  end if
end sub

sub dodgeWarnings()
  dodge = 0
  warnings = match.spellCount
  ' Rotate unusually busy spell lists instead of starving later warnings.
  for warning = warningOffset to warningOffset + 11
    if warning < warnings then
      spell = spells(warning).abilityId
      caster = spells(warning).casterId
      hostile = caster <> self.id
      for ally = 0 to allies - 1
        if caster = allyIds(ally) then
          hostile = 0
        end if
      next ally
      ' Recovery effects are harmless, including those with hidden casters.
      if spell = 0 or spell = 2 or spell = 8 or spell = 12 then
        hostile = 0
      end if
      if spell = 16 or spell = 20 then
        hostile = 0
      end if
      if spell = 32 or spell = 36 then
        hostile = 0
      end if
      impact = spells(warning).impactTick - match.tick
      warningX = (originX + floor(side * spells(warning).position.x + .5))
      warningY = (originY + floor(side * spells(warning).position.y + .5))
      dx = myX - warningX
      dy = myY - warningY
      if hostile and impact > 0 and impact <= match.tickRate * 3 then
        if dx * dx + dy * dy <= 9 then
          dodge = 1
          dodgeX = myX + 3
          dodgeY = myY + 3
          if dx < 0 then
            dodgeX = myX - 3
          end if
          if dy < 0 then
            dodgeY = myY - 3
          end if
        end if
      end if
    end if
  next warning
  warningOffset = warningOffset + 12
  if warningOffset >= warnings then
    warningOffset = 0
  end if
end sub

sub moveTo(goalX, goalY, marching)
  if self.controls.rootTicks > 0 then
    exit sub
  end if
  if goalX = orderX and goalY = orderY and marching = orderMarch then
    if match.tick - orderTick < match.tickRate * 2 then
      exit sub
    end if
  end if
  ' Snap the requested destination to an open nearby surface. A* in the
  ' host still owns the complete route and cliff/ramp collision checks.
  routeScore = 1000000
  routeFound = 0
  readTile(originX + side * myX, originY + side * myY, self.layer)
  floorHeight = tile.height
  for offsetY = -1 to 1
    for offsetX = -1 to 1
      tileX = goalX + offsetX
      tileY = goalY + offsetY
      worldTileX = originX + side * tileX
      worldTileY = originY + side * tileY
      if tileX >= 0 and tileX < map.width then
        if tileY >= 0 and tileY < map.height then
          readTile(worldTileX, worldTileY, self.layer)
          open = tile.walkable
          ground = tile.kind
          height = tile.height
          depth = tile.waterDepth
          if open = 0 then
            for layer = 0 to map.layers - 1
              worldLayer = layer
              if layer = RedFortLayer or layer = BlueFortLayer then
                worldLayer = layer + self.team * (RedFortLayer + BlueFortLayer - 2 * layer)
              end if
              readTile(worldTileX, worldTileY, worldLayer)
              if tile.walkable then
                open = 1
                ground = tile.kind
                height = tile.height
                depth = tile.waterDepth
                exit for
              end if
            next layer
          end if
          if open and ground <> TerrainNone then
            elevation = height - floorHeight
            if elevation < 0 then
              elevation = -elevation
            end if
            score = (offsetX * offsetX + offsetY * offsetY) * 20
            score = score + depth * 2 + elevation
            if ground = TerrainRoad then
              score = score - 5
            end if
            if score < routeScore then
              routeScore = score
              routeX = tileX
              routeY = tileY
              routeFound = 1
            end if
          end if
        end if
      end if
    next offsetX
  next offsetY
  if routeFound = 0 then
    exit sub
  end if
  if marching then
    accepted = attackMove(originX + side * routeX, originY + side * routeY)
  else
    accepted = walkTo(originX + side * routeX, originY + side * routeY)
  end if
  actionError = lastAction.error
  orderTick = match.tick
  if accepted then
    orderX = goalX
    orderY = goalY
    orderMarch = marching
  elseif actionError = ActionNoRoute then
    ' Try the lane center on the next decision rather than retrying a wall.
    crossedMiddle = 0
    blockedId = bestId
    blockedUntil = match.tick + match.tickRate * 3
  end if
end sub

sub castAbilities()
  if self.controls.silenceTicks > 0 then
    exit sub
  end if
  for spellSlot = 0 to 3
    castRange(spellSlot) = abilities(spellSlot).range
    charges = abilities(spellSlot).charges
    recharge = abilities(spellSlot).rechargeTicks
    damage = abilities(spellSlot).damage
    healing = abilities(spellSlot).heal
    restore = abilities(spellSlot).restore
    cost = abilities(spellSlot).manaCost
    if abilities(spellSlot).level > 0 and charges > 0 then
      if abilities(spellSlot).cooldownTicks = 0 and self.mana >= cost then
        castId = 0
        if healing > 0 and healMissing >= healing \ 2 then
          if (self.class = DruidWarden or self.class = Warlock) and spellSlot > 0 then
            castId = healId
          elseif self.class = VanguardKnight and spellSlot = 2 then
            ' Aegis heals around us, even when only an ally is wounded.
            castId = self.id
          elseif self.maxHp - self.hp >= healing \ 2 then
            castId = self.id
          end if
        elseif restore > 0 and self.maxMana - self.mana >= restore then
          castId = self.id
        elseif damage > 0 and bestId <> 0 then
          if bestDistance <= castRange(spellSlot) * castRange(spellSlot) then
            if bestDistance >= castMinimum(spellSlot) * castMinimum(spellSlot) then
              ' Save the last recharging charge for valuable targets.
              if bestKind <> 3 or charges > 1 or recharge <= match.tickRate then
                castId = bestId
              elseif bestHp <= damage then
                castId = bestId
              end if
            end if
          end if
        end if
        if castId <> 0 then
          if castId = bestId and castGround(spellSlot) then
            ' Area spells lead the observed movement, with a bounded lead.
            leadX = velocityX * castDelay(spellSlot)
            leadY = velocityY * castDelay(spellSlot)
            if targetHeld >= castDelay(spellSlot) then
              leadX = 0
              leadY = 0
            end if
            if leadX > 2 then
              leadX = 2
            elseif leadX < -2 then
              leadX = -2
            end if
            if leadY > 2 then
              leadY = 2
            elseif leadY < -2 then
              leadY = -2
            end if
            aimX = bestX + leadX
            aimY = bestY + leadY
            if aimX >= 0 and aimX < map.width - 1 then
              if aimY >= 0 and aimY < map.height - 1 then
                accepted = castPoint(spellSlot, originX + side * aimX, originY + side * aimY)
                actionError = lastAction.error
                if accepted then
                  exit sub
                end if
              end if
            end if
          end if
          ' Targeted projectiles track their target. Targeted ground rings
          ' offset their center so the enemy is inside the damaging band.
          ' Also fall back here if a led point is outside the map or vision.
          accepted = castTarget(spellSlot, castId)
          actionError = lastAction.error
          if accepted then
            exit sub
          end if
        end if
      end if
    end if
  next spellSlot
end sub

if draft.active then
  chooseHero()
  end
end if

' Buy back immediately whenever affordable, including during a long respawn.
if self.hp <= 0 then
  price = self.buybackPrice
  if price > 0 and self.gold >= price then
    accepted = buyback()
    actionError = lastAction.error
  end if
  initialized = 0
  end
end if
if self.channelTicks > 0 or self.controls.stunTicks > 0 then
  end
end if
if match.tick < nextThink then
  end
end if
' Use the same team-relative coordinates for every spatial decision.
side = 1 - self.team * 2
originX = self.team * (map.width - 1)
originY = self.team * (map.height - 1)
myX = (originX + floor(side * self.position.x + .5))
myY = (originY + floor(side * self.position.y + .5))

nextThink = match.tick + 6
role = heroChoices(self.class).role
attackRange = self.attackRange
speed = self.moveSpeed

if initialized = 0 then
  initialized = 1
  spawnX = myX
  spawnY = myY
  homeX = myX
  homeY = myY
  enemyX = map.width - 1 - myX
  enemyY = map.height - 1 - myY
  previousHp = self.hp
  progressTick = match.tick
  previousX = myX
  previousY = myY
  crossedMiddle = 0
  retreating = 0
  ' Keep tactical lead times here; read current ranges from the host below.
  castDelay(2) = 24
  castDelay(3) = 24
  healRange = 0
  for spellSlot = 0 to 3
    castGround(spellSlot) = spellSlot >= 2
    castMinimum(spellSlot) = 0
    if abilities(spellSlot).heal > 0 and abilities(spellSlot).range > healRange then
      healRange = abilities(spellSlot).range
    end if
  next spellSlot
  if self.class = VanguardKnight then
    castDelay(2) = 12
    castDelay(3) = 6
  elseif self.class = Arcanist then
    castDelay(2) = 24
    castDelay(3) = 36
  elseif self.class = DruidWarden then
    castDelay(2) = 12
  elseif self.class = DemonHunter then
    castDelay(2) = 6
    castGround(3) = 0
  elseif self.class = DeathKnight then
    castDelay(3) = 12
    castGround(3) = 0
    castMinimum(3) = 2 / 3
  elseif self.class = Crossbowman then
    castDelay(2) = 12
  elseif self.class = Lich then
    castGround(3) = 0
  elseif self.class = Warlock then
    castDelay(2) = 18
    castGround(2) = 0
    castGround(3) = 0
  elseif self.class = Berserker then
    castDelay(2) = 12
    castDelay(3) = 24
  end if
end if

if self.hp < previousHp then
  hurtTick = match.tick
end if
previousHp = self.hp
if self.attacksLanded <> previousHits or myX <> previousX or myY <> previousY then
  progressTick = match.tick
end if
previousHits = self.attacksLanded
previousX = myX
previousY = myY
if match.tick - progressTick > match.tickRate * 6 and self.targetId <> 0 then
  blockedId = self.targetId
  blockedUntil = match.tick + match.tickRate * 3
  progressTick = match.tick
end if

learnAbilities()
observe()
inventory()
castAbilities()
dodgeWarnings()

if self.hp * 4 < self.maxHp or (bestKind = 6 and self.hp * 10 < self.maxHp * 4) then
  retreating = 1
end if
if self.mana * 8 < self.maxMana and bestId = 0 then
  retreating = 1
end if
if self.inOwnSpawn then
  if self.hp * 10 < self.maxHp * 9 or self.mana * 10 < self.maxMana * 9 then
    moveTo(spawnX, spawnY, 0)
    end
  end if
  retreating = 0
end if

if dodge and self.controls.rootTicks = 0 then
  moveTo(dodgeX, dodgeY, 0)
  end
end if
if retreating then
  ' A safe scroll saves the long return trip; damage and control can punish it.
  if owned(21) > 0 and self.portalCooldownTicks = 0 and self.controls.rootTicks = 0 then
    dx = myX - homeX
    dy = myY - homeY
    if dx * dx + dy * dy > 400 and threatDistance > 144 then
      accepted = useItemAt(inventorySlot(21), originX + side * spawnX, originY + side * spawnY)
      actionError = lastAction.error
      if accepted then
        end
      end if
    end if
  end if
  moveTo(spawnX, spawnY, 0)
  end
end if

if towerAggro and self.hp * 3 < self.maxHp * 2 and tanks = 0 then
  moveTo(homeX, homeY, 0)
  end
end if
if enemyPower > friendlyPower + 6 and threatDistance < 64 then
  if self.hp * 4 < self.maxHp * 3 then
    moveTo(homeX, homeY, 0)
    end
  end if
end if

if bestId <> 0 then
  ' Finish a windup before kiting; never cancel every swing with movement.
  if bestKind = 2 and aimedAtUs > 0 and attackRange >= 3 then
    if bestDistance < 4 and self.attackCooldownTicks > match.tickRate \ 2 then
      if self.attacksLanded > 0 and speed > 0 then
        kiteStep = floor(self.moveSpeed * match.tickRate)
        if kiteStep < 1 then
          kiteStep = 1
        elseif kiteStep > 4 then
          kiteStep = 4
        end if
        kiteX = myX + kiteStep
        kiteY = myY + kiteStep
        if bestX >= myX then
          kiteX = myX - kiteStep
        end if
        if bestY >= myY then
          kiteY = myY - kiteStep
        end if
        moveTo(kiteX, kiteY, 0)
        end
      end if
    end if
  end if
  if self.targetId <> bestId then
    accepted = attackTarget(bestId)
    actionError = lastAction.error
    if accepted = 0 then
      blockedId = bestId
      blockedUntil = match.tick + match.tickRate * 3
    else
      orderTick = 0
    end if
  end if
  end
end if

' Farm separate lanes early, then converge on the enemy god to finish.
middleX = map.width \ 2
middleY = map.height \ 2
if self.level < 6 then
  if role = 0 or role = 2 then
    middleX = map.width \ 10
    middleY = map.height \ 10
  elseif role = 1 or role = 3 then
    middleX = map.width * 9 \ 10
    middleY = map.height * 9 \ 10
  end if
end if
dx = myX - middleX
dy = myY - middleY
if dx * dx + dy * dy <= 36 then
  crossedMiddle = 1
end if
goalX = middleX
goalY = middleY
if crossedMiddle then
  goalX = enemyX
  goalY = enemyY
end if
if self.canShop and owned(21) > 0 and self.portalCooldownTicks = 0 then
  dx = myX - forwardX
  dy = myY - forwardY
  if forwardDistance < 1000000 and dx * dx + dy * dy > 400 then
    if threatDistance > 144 then
      accepted = useItemAt(inventorySlot(21), originX + side * forwardX, originY + side * forwardY)
      actionError = lastAction.error
      if accepted then
        end
      end if
    end if
  end if
end if
moveTo(goalX, goalY, 1)
