' TEST ONLY: mechanically namespaced exact v13-live; never a production compiler input.
sub k_config__update()
  ' Last-hit planner
  k_config__cfgscanradius = 1200         ' ku: footmen farther than this are not scanned (20 tiles)
  k_config__cfgrangeslack = 6            ' ku inside my attack range to count as "in range"
  k_config__cfgstandoffback = 60         ' ku behind max range for the ranged standoff point
  k_config__cfgmeleestandoff = 120       ' ku from the anchor footman for melee heroes
  k_config__cfgchaseku = 180             ' ku beyond my reach a footman may be before I stop considering c_farm__it
  k_config__cfgavoidallytarget = 0       ' 1: skip footmen an allied hero is already attacking (helps mono-team play, costs mixed-team play)
  k_config__cfgtowerfarmallies = 99      ' farm beside an idle enemy tower only with this many allied footmen inside its reach (99 = never; v10 A/B showed no gain)
  k_config__cfgtowerfarmalliesku = 390   ' ku: what counts as inside the tower's reach (6.5 tiles)
  k_config__cfgtoweravoid = 420          ' stay this far (ku) from a living enemy tower (7 tiles)
  k_config__cfgfootmanperiod = 32        ' ticks between an allied footman's hits
  k_config__cfgtowerperiod = 24          ' ticks between tower hits
  k_config__cfgfootmanhit = 12
  k_config__cfgstickslack = 12           ' keep an ordered target while predicted HP <= damage + this
  k_config__cfgstickbonus = 40           ' score bonus for the target already ordered (avoids swing resets)
  k_config__cfgeventstale = 96           ' forget a drop event older than this many ticks
  k_config__cfgseenstale = 1             ' forget a footman's hit history after any sighting gap (a drop across a gap has no known tick)
  k_config__cfgpoisonsecure = 1          ' use poison to secure a kill the basic attack would miss
  k_config__cfgspellsecure = 1           ' use the primary strike to secure a kill
  k_config__cfgpoisonstock = 1           ' poisons to keep in stock while a footman still takes two hits
  k_config__cfgpoisonmingold = 40        ' only restock poison at or above this gold
  ' Idle at the standoff point and let the engine attack instead of holding
  ' for kill windows. Hosted A/B v4 versus v5 (2026-09-17, 32 episodes per arm,
  ' exact replay counts): idle for every class 4.88 versus 4.31 last hits per
  ' hero per 1,000 ticks. Set 0 to hold for windows (the v1 to v4 behavior).
  k_config__cfgidlehere = 1
  ' Melee and low-damage classes convert half as often as the field when they
  ' walk to a standoff point and wait (time-budget analysis, 2026-09-17): they
  ' attack only 20 to 26 percent of the ticks a creep is in reach versus 90
  ' percent for the top farmers. For them, chase and attack the anchor footman
  ' continuously; the last-hit windows still take priority when one opens.
  k_config__cfgchaseclass = 0
  if selfClass = 0 or selfClass = 3 or selfClass = 4 or selfClass = 5 or selfClass = 8 or selfClass = 9 then
    k_config__cfgchaseclass = 1
  end if

  ' Kill steal: the finishing blow on heroes and structures teammates are fighting
  k_config__cfgksteal = 1                ' 0 disables the module
  k_config__cfgksholdku = 600            ' ku: an enemy hero within this => farm by windows only, spells held (10 tiles)
  k_config__cfgksallyku = 480            ' ku: an allied hero this close to the target and attacking c_farm__it counts as engaged
  k_config__cfgksrecentticks = 48        ' a drop within this many ticks also counts as engaged
  k_config__cfgksminhppct = 40           ' take hero windows only above this percent of max HP
  k_config__cfgheroperiod = 24           ' assumed repeat period of a hero's incoming hits (ticks)

  ' Punish: attack an enemy hero that is farming inside our tower's reach
  k_config__cfgpunish = 1                ' 0 disables the module
  k_config__cfgpunishenterku = 420       ' enemy hero within this of an allied tower that is firing at c_farm__it starts a punish (7 tiles)
  k_config__cfgpunishexitku = 600        ' punish ends once the target is farther than this from the tower (10 tiles)
  k_config__cfgpunishengageku = 720      ' only start when the target is within this of me (12 tiles)
  k_config__cfgpunishminhppct = 50       ' start only above this percent of max HP
  k_config__cfgpunishstophppct = 35      ' stop below this percent
  k_config__cfgpunishchase = 0           ' 1: keep chasing past tower range when the kill is cheap (v10 A/B: 74 chase ticks in 48 games, no gain)
  k_config__cfgpunishchaseticks = 120    ' chase only if the expected time to kill is at most this (5 s)
  k_config__cfgpunishchasemaxku = 900    ' never chase a target farther than this from me (15 tiles)
  k_config__cfgpunishallyku = 360        ' an allied hero within this of the target counts its DPS and permits a slower chase

  ' Survival
  k_config__cfgretreatpct = 35           ' retreat below this percent of max HP
  k_config__cfgrecoverpct = 70           ' resume below-threshold work above this percent
  k_config__cfgherohuntrange = 480       ' ku: an enemy hero targeting me within this => retreat at any HP
  k_config__cfgherofearrange = 360       ' ku: enemy hero this close while under 60% HP => back off
  k_config__cfgtowerescape = 540         ' ku to step back when an enemy tower targets me (9 tiles)
  k_config__cfgretreatstep = 600         ' ku to step back toward own fort per retreat order

  ' Telemetry
  k_config__cfgreportevery = 240
  k_config__cfgdebug = 0                 ' reserved for event traces; keep every decision under 63 printed items
end sub
