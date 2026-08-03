# Pending actions -- gap analysis vs. mindcraft

mindcraft (`/home/colaila/git/mindcraft`, a Node.js/mineflayer bot with an
LLM in the loop) is the reference project this bot's command grammar and
Action-layer pattern are modeled on (see `FINDINGS.md`). This file tracks
every command mindcraft implements, which ones this project already has,
and where to look (both in mindcraft's source and in this repo) when
picking up a pending one.

Source of truth on the mindcraft side: `src/agent/commands/actions.js`
(mutating actions) and `src/agent/commands/queries.js` (read-only
queries) are the only two files that feed the command list --
`src/agent/commands/index.js:7` (`commandList = queryList.concat
(actionsList)`) confirms nothing else is merged in. Most actions delegate
their real logic to `src/agent/library/skills.js`; the command definition
itself is usually just param parsing + a `skills.xxx(...)` call.

Source of truth on this project's side for "already done": `minebot/bot/
movement.py` and `minebot/bot/inventory.py` are the only two files
registering `Action`s right now (`grep -n 'name="' minebot/bot/*.py` to
recheck). Everything not in that list is pending.

Legend: ✅ done · ⬜ not started · 🟡 partial (see note)

## Movement

- 🟡 `!followPlayer` → **`follow`**, `minebot/bot/movement.py:34` (chat
  action) + `minebot-mod`'s `FOLLOW` goal, `ControlState.java`. mindcraft
  ref: `actions.js:103`, delegates to `skills.followPlayer`,
  `skills.js:1331`. Partial: minebot's version also auto-resumes on
  reconnect (a fix mindcraft doesn't need, no persistent world-side
  entity id problem there) and looks at the followed player once close
  (`NearbyPlayerLookAt.java`, generalized beyond just FOLLOW) -- but has
  no separate `follow_dist` param the way mindcraft's does (stop distance
  is a fixed constant, `FOLLOW_STOP_DISTANCE` in `movement.py:26`).
- ✅ `!stop` → **`stop`**, `movement.py:51`. mindcraft ref: `actions.js:53`
  (note: mindcraft's `!stop` is a broader "cancel everything" than just
  movement -- this project's `stop` only clears the mod's movement goal;
  worth reconciling once there are other running actions to cancel).
- ⬜ `!goToPlayer` -- go to a player without continuing to follow them
  (one-shot, unlike `!followPlayer`). mindcraft ref: `actions.js:92`,
  → `skills.goToPlayer`, `skills.js:1294`. Land in `movement.py`; the mod
  side already supports a one-shot `GOTO` goal to arbitrary coordinates
  (`ControlState.setGoto`, `MinebotMod.java` `handleMessage`'s `"goto"`
  case) -- this would resolve a player name to their current position via
  `EntityTracker` and send that, no new mod-side work needed.
- ⬜ `!goToCoordinates` -- go to explicit x,y,z. mindcraft ref:
  `actions.js:114`, → `skills.goToPosition`, `skills.js:1181`. The mod's
  `goto` wire command + `ModBridge.send_goto` (`minebot/bridge/
  client.py:141`) already exist end-to-end -- this is purely a missing
  Python-side chat `Action` wrapper in `movement.py`, no mod work at all.
- ⬜ `!searchForBlock` -- find and go to nearest block of a given type
  within range. mindcraft ref: `actions.js:127`, →
  `skills.goToNearestBlock`, `skills.js:1237`. No block-scanning exists
  on the mod side at all yet (the mod only tracks *entities*, not block
  positions by type -- `InventoryReporter`/`broadcastEntityEvents` cover
  items/players, nothing scans the world for a block type). Needs new
  mod-side work: a chunk/block scan within radius, likely in a new
  `minebot-mod` class alongside `pathfinding/`.
- ⬜ `!searchForEntity` -- find and go to nearest entity of a given type.
  mindcraft ref: `actions.js:142`, → `skills.goToNearestEntity`,
  `skills.js:1274`. Closer to already-possible: `broadcastEntityEvents`
  (`MinebotMod.java`) currently only tracks *players*, not mobs/animals
  ("Only players are tracked (not every entity type): the only thing
  minebot currently needs to path toward is another player" --
  `FINDINGS.md`) -- would need widening that scan to other entity types
  first.
- ⬜ `!moveAway` -- move away from current position by a distance in any
  direction. mindcraft ref: `actions.js:153`, → `skills.moveAway`,
  `skills.js:1397`. No mod-side equivalent; would need a new `ControlState`
  mode or reuse `GOTO` with a computed away-from-here point.
- ⬜ `!rememberHere` / `!goToRememberedPlace` -- save/recall a named
  location. mindcraft ref: `actions.js:161` / `actions.js:171`, backed by
  `agent.memory_bank` (in-memory + persisted, not skills.js). No
  persistence layer exists in this project at all yet -- would be a new
  Python-side store (e.g. a simple JSON file or in-memory dict on
  `BotConfig`/a new module), paired with the existing `goto` wire command
  for recall.
- ⬜ `!goToBed` -- go to nearest bed and sleep. mindcraft ref:
  `actions.js:332`, → `skills.goToBed`, `skills.js:1543`. No bed-finding
  exists; would need the same block-scan capability `!searchForBlock`
  needs, plus a real "use bed" interaction (likely following
  `DoorOpener.java`'s `useItemOn` pattern for the actual sleep
  interaction).
- ⬜ `!stay` -- stay in place, pausing all autonomous behavior, for N
  seconds (-1 = forever). mindcraft ref: `actions.js:339`, →
  `skills.stay`, `skills.js:1475`. No autonomous "modes" system exists
  in this project to pause yet (mindcraft's modes are its own separate
  concept, `modes.js`) -- lower priority until there's actual autonomous
  behavior worth pausing.
- ⬜ `!goToSurface` -- move to the highest block directly above (surface).
  mindcraft ref: `actions.js:484`, → `skills.goToSurface`,
  `skills.js:1963`. Straightforward once basic Y-scanning exists;
  no current mod-side equivalent.

## Mining / digging

- ⬜ `!collectBlocks` -- collect the nearest N blocks of a given type
  (mindcraft gives this a 10-minute timeout, `actions.js:264`). mindcraft
  ref: `actions.js:256`, → `skills.collectBlock`, `skills.js:417`. **No
  mining/digging exists on the mod side at all** -- this is the biggest
  single gap. Per `FINDINGS.md`'s known gaps: "pathfinding is walk/
  climb/parkour only (no dig/place moves)" -- the Java A* port
  (`minebot-mod/src/main/java/minebot/mod/pathfinding/Movements.java`)
  would need a real dig-cost branch (mirroring mineflayer-pathfinder's
  own dig-enabled movement, already ported once for the abandoned
  pure-protocol approach -- see the `pure-protocol-backend` branch in
  this repo for a reference Python A* implementation that *did* include
  dig moves, if useful for a port), plus a real block-breaking
  interaction (`MultiPlayerGameMode`-based, likely a new
  `BlockBreaker.java` alongside `DoorOpener.java`).
- ⬜ `!digDown` -- dig straight down N blocks, stopping at lava/water/big
  drops. mindcraft ref: `actions.js:476`, → `skills.digDown`,
  `skills.js:1906`. Same block-breaking prerequisite as `!collectBlocks`
  above, but simpler (no pathfinding needed, just straight down).

## Building / placing

- ⬜ `!placeHere` -- place a given block/item at the bot's current
  location (single blocks/torches, not structures). mindcraft ref:
  `actions.js:302`, → `skills.placeBlock`, `skills.js:611`. No
  block-placing interaction exists yet -- would follow the same real-
  interaction pattern as `DoorOpener`'s `useItemOn` call
  (`minebot-mod/src/main/java/minebot/mod/pathfinding/DoorOpener.java:83`),
  generalized to arbitrary blocks instead of doors specifically, plus the
  hotbar-selection logic `FoodEater`/`InventoryActions` already have for
  "get the right item into hand first."

## Combat

- ⬜ `!attack` -- attack and kill the nearest entity of a given type.
  mindcraft ref: `actions.js:311`, → `skills.attackNearest`,
  `skills.js:313`. **No combat exists at all yet** -- per the original
  project brief (`prompt.txt`), "basic combat" was one of the three MVP
  asks (navigate, inventory management, basic combat) alongside
  inventory (done) and navigate (done for follow/goto). Would need: a
  real attack interaction (`MultiPlayerGameMode.attack(Entity)`, distinct
  from the `useItem`/`useItemOn` interactions already used elsewhere),
  a "move toward + attack when in range" loop (reusable pathfinding via
  `PathTracker`), and a target-selection query (nearest hostile of a
  type -- needs the same entity-type-widening `!searchForEntity` needs,
  since `broadcastEntityEvents` only tracks players today).
- ⬜ `!attackPlayer` -- attack a specific player by name until death or
  they flee. mindcraft ref: `actions.js:319`, → `skills.attackEntity`,
  `skills.js:334`. Same prerequisite work as `!attack` above, but target
  resolution is easier (already-tracked players via `EntityTracker`,
  no entity-type widening needed) -- **plausibly the easiest combat
  entry point to build first** for exactly that reason. Consider
  authorization/griefing implications before implementing --
  attacking players unprompted (vs. only when instructed) needs care.

## World interaction

- ⬜ `!useOn` -- use (right-click) a given tool/item on the nearest target
  of a given type (entity, block, or "nothing" for no target; "hand" for
  no tool). mindcraft ref: `actions.js:492`, → `skills.useToolOn`,
  `skills.js:1982`. A generalization of interactions this project
  already does in narrower forms -- `DoorOpener.java`'s `useItemOn` for
  doors specifically, `FoodEater`'s `keyUse`-hold for eating specifically
  -- a real port would need to unify "right-click on X with Y in hand"
  into one reusable interaction, then layer the narrower existing
  behaviors on top of it (or leave them as-is and add this as a fourth,
  more general case for anything not covered by a dedicated action).

## Inventory / items

- ✅ `!inventory` → **`inventory`**, `minebot/bot/inventory.py:76`.
  mindcraft ref: `queries.js:66` (mindcraft's version also reports worn
  armor slots explicitly; this project's doesn't break those out
  separately, though they'd show up in the flat item list since
  `InventoryReporter.java` reports armor/offhand slots too).
- ✅ `!equip` → **`equip`**, `inventory.py:82`. mindcraft ref:
  `actions.js:204`, → `skills.equip`, `skills.js:791`.
- 🟡 `!discard` → **`drop`**, `inventory.py:90`. mindcraft ref:
  `actions.js:242` (mindcraft's version walks 5 blocks away first, drops,
  then walks back -- this project's `drop` just drops in place, a
  deliberate simplification chosen earlier this session: "2 commands,
  drop (in place), and give (walk to player and drop)").
- 🟡 `!givePlayer` → **`give`**, `inventory.py:98`. mindcraft ref:
  `actions.js:184`, → `skills.giveToPlayer`, `skills.js:998` (mineflayer
  can do an actual player-to-player item transfer via a plugin/protocol
  trick; this project's `give` walks to the player and drops instead,
  since real vanilla client interactions have no direct transfer
  primitive -- see `FINDINGS.md`'s note on `GIVE` mode).
- ✅ `!consume` → folded into **`FoodEater.java`**'s autonomous
  health-triggered eating (`minebot-mod/src/main/java/minebot/mod/
  FoodEater.java`), not a standalone chat-triggered action. mindcraft
  ref: `actions.js:196`, → `skills.consume`, `skills.js:973`. Gap: there's
  no *chat-triggerable* "eat this specific item now" command, only the
  automatic below-20%-health behavior -- worth adding a standalone
  `eat`/`consume` action wrapping the same `keyUse`-hold mechanism
  `FoodEater` uses (see `FoodEater.holdUseKey`/`releaseUseKey`) if a
  player should be able to explicitly tell the bot to eat on demand.
- ⬜ `!putInChest` / `!takeFromChest` / `!viewChest` -- chest interaction.
  mindcraft ref: `actions.js:212` / `223` / `234`, → `skills.putInChest`
  (`skills.js:869`) / `skills.takeFromChest` (`skills.js:898`) /
  `skills.viewChest` (`skills.js:944`). No container-opening exists at
  all -- `InventoryActions.java`'s container-click mechanism
  (`handleContainerInput`, confirmed working for the player's own
  inventory) doesn't extend to *other* containers (chests) yet; opening
  a chest requires a real `useItemOn`-style interaction first (see
  `DoorOpener.java` for the pattern) to get the container menu open
  server-side before container clicks against it would work.

## Crafting

- ⬜ `!craftRecipe` -- craft a recipe N times. mindcraft ref:
  `actions.js:267`, → `skills.craftRecipe`, `skills.js:36`. No crafting
  exists at all -- needs a crafting-table interaction (or 2x2 inventory
  crafting for simple recipes) plus recipe-lookup logic; likely the
  single largest remaining feature area after mining/combat.
- ⬜ `!smeltItem` -- smelt an item N times via furnace. mindcraft ref:
  `actions.js:278`, → `skills.smeltItem`, `skills.js:142`. Depends on
  furnace container interaction, same prerequisite as chest interaction
  above.
- ⬜ `!clearFurnace` -- empty all items from the nearest furnace.
  mindcraft ref: `actions.js:294`, → `skills.clearNearestFurnace`,
  `skills.js:275`. Same furnace-container prerequisite.
- ⬜ `!craftable` (query) -- list items craftable with current inventory.
  mindcraft ref: `queries.js:132`, → `world.getCraftableItems`. Doesn't
  need any new interaction, just recipe-database knowledge cross-
  referenced against `InventoryTracker`'s known contents -- could be
  built before real crafting is implemented, as a standalone query.
- ⬜ `!getCraftingPlan` (query) -- full ingredient breakdown / missing-
  items analysis for crafting a target item. mindcraft ref:
  `queries.js:269`, → `mc.getDetailedCraftingPlan`
  (`src/utils/mcdata.js`). Same recipe-database prerequisite as
  `!craftable`.

## Communication / social

- ⬜ `!startConversation` / `!endConversation` -- bot-to-bot conversation
  management. mindcraft ref: `actions.js:406` / `423`, →
  `convoManager` (`src/agent/conversation.js`), not skills.js. Only
  relevant once multiple bot instances exist and need to coordinate --
  this project currently runs a single bot; low priority until that
  changes.
- ⬜ `!lookAtPlayer` -- look at a player (or match their look direction).
  mindcraft ref: `actions.js:436`, → `agent.vision_interpreter.
  lookAtPlayer` (not skills.js). Partially superseded already:
  `NearbyPlayerLookAt.java` (`minebot-mod/src/main/java/minebot/mod/
  NearbyPlayerLookAt.java`) makes the bot look at whoever's closest
  automatically, unprompted -- an explicit "!lookAtPlayer X" chat command
  for a *specific* named player (not just "closest") would still be a
  useful, easy addition reusing the same eye-level math already proven
  there.
- ⬜ `!lookAtPosition` -- look at explicit x,y,z. mindcraft ref:
  `actions.js:459`. Same eye-level-math reuse as above, aimed at a fixed
  point instead of a tracked entity.
- ⬜ `!stfu` -- stop all chat/self-prompting, keep current action running.
  mindcraft ref: `actions.js:68`. No self-prompting/autonomous-chat
  system exists in this project yet (`LLMController` is reactive only,
  triggered per-message -- see `FINDINGS.md`'s LLM trigger section) --
  not yet applicable until that exists.
- ⬜ `!clearChat` -- clear conversation history. mindcraft ref:
  `actions.js:84`. Not yet applicable: `LLMController.handle_chat` has
  no conversation history at all yet (a known gap already listed in
  `FINDINGS.md`) -- there's nothing to clear until that's built.

## Villager trading

- ⬜ `!showVillagerTrades` / `!tradeWithVillager` -- inspect and execute
  villager trades. mindcraft ref: `actions.js:387` / `395`, →
  `skills.showVillagerTrades` (`skills.js:1748`) / `skills.
  tradeWithVillager` (`skills.js:1789`). Notably, the sibling
  `/home/colaila/git/mods/VillagerHelper` mod (referenced throughout
  `FINDINGS.md` as the decompiled-source reference project) already
  does real villager-trade UI work and would be a natural source to
  study for the actual trade-menu interaction mechanics, even though
  it's a human-facing helper mod rather than a bot-control one.

## Meta / control

- ⬜ `!newAction` -- LLM-generated novel behavior via code generation.
  mindcraft ref: `actions.js:30`, → `agent.coder.generateCode`. Out of
  scope until there's a real LLM provider wired up at all (`FINDINGS.md`:
  "No real `LLMProvider` is wired up yet") -- and even then, this is a
  materially riskier feature (arbitrary code execution) worth a
  deliberate decision, not an assumed port.
- ⬜ `!setMode` / `!modes` (query) -- toggle/list autonomous behavior
  modes. mindcraft ref: `actions.js:347` / `queries.js:216`, →
  `agent.bot.modes` (`modes.js`). This project's closest equivalent is
  `FoodEater`'s always-on health-triggered eating -- there's no general
  "modes" framework (enable/disable named autonomous behaviors) yet;
  worth designing once there's more than one autonomous behavior to
  toggle.
- ⬜ `!goal` / `!endGoal` -- self-prompting continuous goal-pursuit.
  mindcraft ref: `actions.js:364` / `379`, → `agent.self_prompter`. Not
  applicable until `LLMController` supports multi-turn autonomous
  behavior beyond single-message reactive responses.
- ⬜ `!restart` -- restart the agent process. mindcraft ref:
  `actions.js:77`, → `agent.cleanKill()`. Straightforward if ever
  needed -- Python's own process could just `os.execv`/exit-and-let-a-
  supervisor-restart-it; no mod-side work.

## Info queries (read-only)

- ⬜ `!stats` -- position, gamemode, health, hunger, biome, weather, time
  of day, current action, nearby players. mindcraft ref: `queries.js:15`.
  Most of the underlying data already flows over the wire (`position`,
  `health`, `inventory` events -- see `FINDINGS.md`'s wire-format
  section) -- this would mostly be a Python-side query action that reads
  already-tracked state and formats it, not new mod-side work. Biome/
  weather/time-of-day aren't currently broadcast at all though, so a
  full port needs a small mod-side addition for those specifically.
- ⬜ `!nearbyBlocks` -- list nearby block types. mindcraft ref:
  `queries.js:103`. Needs the same block-scanning capability
  `!searchForBlock` needs -- no block-type awareness exists on the mod
  side yet at all (only entities/inventory are tracked).
- ⬜ `!entities` -- list nearby players/entities (with villager
  profession). mindcraft ref: `queries.js:147`. Python already has
  `EntityTracker`'s live player list (`minebot/bridge/entities.py`) --
  this could ship today as a query action reading it directly, no new
  mod-side work, *except* it currently only covers players, not other
  entity types (mobs/animals/villagers) -- same widening
  `!searchForEntity` needs for full parity.
- ⬜ `!savedPlaces` -- list saved location names. mindcraft ref:
  `queries.js:222`. Depends on the same location-memory prerequisite as
  `!rememberHere`/`!goToRememberedPlace` above.
- ⬜ `!checkBlueprintLevel` / `!checkBlueprint` / `!getBlueprint` /
  `!getBlueprintLevel` -- building-task blueprint progress/inspection.
  mindcraft ref: `queries.js:229`/`241`/`249`/`257`, →
  `tasks/construction_tasks.js`. Tied to mindcraft's own structured
  "building task" system, which has no equivalent here at all -- lowest
  priority, only relevant if this project ever gets a similar guided-
  building-task feature.
- ⬜ `!searchWiki` -- fetch/scrape a Minecraft Wiki page for a query.
  mindcraft ref: `queries.js:311`, raw `fetch()` + cheerio parsing, no
  skills.js. Self-contained, no mod-side dependency at all -- could be
  built as a pure Python HTTP-fetch query action independent of
  everything else in this list, whenever useful.
- ⬜ `!help` -- list all available commands + descriptions. mindcraft ref:
  `queries.js:340`, → `getCommandDocs` (`commands/index.js:232`).
  Straightforward: `ActionRegistry.list_actions()` (`minebot/actions/
  registry.py:31`) already returns every registered `Action` with its
  `description`/`params` -- this is close to a one-function port, no
  mod-side work, and arguably should exist soon since it's also exactly
  the data the (still-unbuilt) LLM tool-schema adapter will need to
  consume.

## Not exposed as commands in mindcraft either (informational only)

These `skills.js` functions exist but aren't wired to any `!command` in
mindcraft -- called internally by `modes.js` or other skills instead.
Not part of the command-parity gap (nothing to "catch up" to on the
command surface), but worth knowing about if this project ever builds
its own autonomous "modes" layer, since they're mindcraft's reference
implementations for exactly that kind of always-on behavior (this
project's own `FoodEater`/`RespawnHandler` are the analogous pattern
already in use here):

- `activateNearestBlock` -- skills.js:1652
- `tillAndSow` -- skills.js:1575
- `useDoor` -- skills.js:1499 (this project already has its own
  `DoorOpener.java`, built independently rather than ported)
- `moveAwayFromEntity` -- skills.js:1430
- `avoidEnemies` -- skills.js:1445
- `pickupNearbyItems` -- skills.js:531
- `defendSelf` -- skills.js:370
- `breakBlockAt` -- skills.js:561
- `wait` -- skills.js:117
