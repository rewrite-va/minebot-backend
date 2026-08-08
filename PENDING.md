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
movement.py`, `minebot/bot/inventory.py`, `minebot/bot/mining.py`,
`minebot/bot/combat.py`, and `minebot/bot/help.py` are the files
registering `Action`s right now
(`grep -n 'name="' minebot/bot/*.py` to recheck). Everything not in that
list is pending.

Legend: ✅ done · [] not started · 🟡 partial (see note)

Each pending entry has a **Want it?** line -- add your yes/no/notes there.

## Movement

- [] **`!goToPlayer`** / **`!goToCoordinates`** -- walk to a player once
  (no continued following), or to explicit x,y,z. → **`goto`**,
  `minebot/bot/movement.py:84` (commit `c75685b`). mindcraft ref:
  `actions.js:92` / `114`, → `skills.goToPlayer`/`skills.goToPosition`,
  `skills.js:1294`/`1181`. Implements the player-name and explicit-
  coordinate legs of the universal `!goto` you asked for (below), plus
  remembered-place resolution. **Still missing**: block-type and
  entity-type resolution (`!goto stone`, `!goto cow`) -- blocked on
  `!searchForBlock`/`!searchForEntity`'s own prerequisites below; the
  handler is already structured to add those as two more resolution
  branches with no interface change once those exist.
  **Want it?** universal `!goto` (player/block/entity/coords/remembered
  place) -- player+coords+remembered-place done, block/entity pending
  their own prerequisites.

- [] **`!searchForBlock`** -- find and walk to the nearest block of a
  given type within a search range. mindcraft ref: `actions.js:127`, →
  `skills.goToNearestBlock`, `skills.js:1237`. Implemented as part of the
  unified `!find` below (`BlockFinder.java`, `BlockPos.findClosestMatch`).
  **Want it?**
  > yes

- [] **`!searchForEntity`** -- find and walk to the nearest entity of a
  given type (mob, animal, etc.). mindcraft ref: `actions.js:142`, →
  `skills.goToNearestEntity`, `skills.js:1274`. Implemented as part of the
  unified `!find` below (`EntityFinder.java`, a one-shot on-demand scan --
  the always-on `broadcastEntityEvents` player tracking was left
  untouched, this is separate).
  **Want it?**
  > yes, implements aliases: `!findEntity`, `!findMob`, `!findAnimal`
  > actually I want an universal find, so `!find` should be the alias for both `!searchForBlock` and `!searchForEntity`, with a type param to distinguish which one to use.
  > for example, !find cow, is the entity one, there is no cow block, so it will use the entity one, but !find stone will use the block one.
  >
  > **Done**: `!find <query>` (`minebot/bot/movement.py`'s `find`,
  > `MinebotMod.handleFind`/`runFind`) tries entity type first, falls
  > back to block type, and walks to the result -- matching mindcraft's
  > behavior per the "Walk there" decision. Wire protocol: Python sends
  > `{"type": "find", "query", "radius"}`, mod replies with a
  > `find_result` event (`found`, `kind`, `x/y/z`); `MovementController`
  > awaits it via a single-slot pending future (`_pending_find`) since
  > only one `!find` is ever in flight at a time.

- ✅ **`!goToBed`** -- walk to the nearest bed and sleep in it. mindcraft
  ref: `actions.js:332`, → `skills.goToBed`, `skills.js:1543`.
  **Want it?**
  > yes, but !sleep would be a better alias for it, since it's shorter and more intuitive.
  >
  > **Done**: `!sleep` (`minebot/bot/movement.py`'s `MovementController.
  > sleep`) -- a one-shot trigger, no arguments, same shape as `!pickup`.
  > Wire protocol: Python sends `{"type": "sleep"}`, no reply event (the
  > mod's real vanilla chat system message, if sleep is rejected for a
  > real in-game reason, is relayed back the same way any other chat line
  > already is). Mod-side: `BlockFinder.findNearestBed`
  > (`minebot-mod/.../pathfinding/BlockFinder.java`, the first real
  > block-scan helper in the mod -- `BlockPos.betweenClosed` over a cube,
  > filtered by real Euclidean distance, same shape `EntityFinder`'s own
  > entity scans use) finds the nearest bed; `SleepTask`
  > (`minebot-mod/.../task/SleepTask.java`) walks to it via the same
  > NavIntent.NAV_TARGET/NAV_ARRIVED channel FOLLOW/KILL/GiveTask already
  > publish through, then right-clicks it once in range via `useItemOn` --
  > the same real interaction `HandsOpenDoorNode` uses for doors (that
  > class is `DoorOpener.java`'s actual replacement; `DoorOpener` itself
  > no longer exists, deleted in the state-machine rewrite). `!sleep` is a
  > queued `TaskController` `Task`, the same home `!give`'s `GiveTask`
  > already established, NOT a `PlayerIntentionState` -- an earlier
  > version made it a `PlayerIntentionState.SLEEP` peer-SM node mirroring
  > KILL's one-shot-trigger shape, reworked per explicit direction
  > ("implement it more like !give, which is a task in a queue"): "walk to
  > a bed and sleep" has no need to interrupt or be resumed by
  > IDLE/FOLLOW/DEFEND the way a real fight does, it's just a queued unit
  > of work. `TaskController.isBusy()`'s existing DEFEND-with-nearby-
  > hostile check already holds off dequeuing it during a real fight, the
  > same protection the old SLEEP state's KILL-interrupt edges existed
  > to provide.

## Mining / digging

- ✅ **`!collectBlocks`** -- collect the nearest N blocks of a given type.
  mindcraft ref: `actions.js:256`, → `skills.collectBlock`,
  `skills.js:417`. Implemented as the universal `!collect <query> <count>`
  requested below -- entity-then-block resolution (same order `!find`
  uses), real block-breaking (`BlockBreaker.java`, the multi-tick
  `startDestroyBlock`/`continueDestroyBlock` sequence, not a single API
  call -- see `FINDINGS.md`'s "Mining / digging: real dig-through-
  obstacles pathfinding" section), a minimal kill loop for entities, and
  a real dig-cost A* branch (`Movements.java`) so the bot can tunnel
  through a wall to reach a target with no walkable route. See
  `FINDINGS.md` for the full writeup.
  **Want it?**
  > yes, but I want it to be a universal `!collect` command that can take either a block type or an entity type, and collect the nearest N of that type. For example, `!collect stone 10` will collect 10 stone blocks, and `!collect cow 5` will collect 5 cows (by killing them and collecting their drops).
  >
  > **Done**: `!collect <query> <count>` (`minebot/bot/mining.py`'s
  > `MiningController.collect`) tries `query` as an entity type first,
  > block type as fallback. The counted loop lives entirely in Python
  > now, not mod-side -- the mod's own `collect` command (`MinebotMod.
  > tickCollect`) is a single atomic attempt (find nearest match, walk to
  > it, break/kill it once, no count at all), redesigned from an earlier
  > version where the mod itself looped against a requested count and
  > reported per-item `collect_progress` (see `FINDINGS.md`'s "!collect
  > redesigned" section for the full why -- short version: the mod's old
  > "destroyed/killed" signal fired before any drop was actually in
  > inventory, so per-item progress could claim success on something not
  > yet possessed; moving the loop to Python is also what let each
  > individual attempt become small/atomic enough to interrupt cleanly).
  > `query` also now resolves through a `source_for`/`drops_from` lookup
  > (`DropTable.java`, hardcoded stopgap -- real loot-table data is
  > architecturally unavailable client-side, see `FINDINGS.md`) so asking
  > for a raw item that isn't itself minable/huntable (e.g. "cobblestone")
  > still works -- resolves to "stone" and collects that. Chat
  > announcements of real inventory gains ("I got a stone (12 total)")
  > are now independent of `!collect` entirely -- see `InventoryAnnouncer`
  > below and in `FINDINGS.md`. Auto-selects the fastest available tool
  > from inventory before each new block (real `ItemStack.getDestroySpeed`,
  > not a category guess). Combat for the entity case is intentionally
  > minimal (walk in, repeated `MultiPlayerGameMode.attack` until dead) --
  > not the full `!attack`/`!kill` feature, which is still pending below.

- ✅ **`!digDown`** -- dig straight down N blocks, stopping at
  lava/water/a big drop. mindcraft ref: `actions.js:476`, →
  `skills.digDown`, `skills.js:1906`.
  **Want it?**
  > yes
  >
  > **Done**: `!dig [count]` (default 10, shorter alias than
  > `!digDown` -- yes, I want it to just be named `!dig`) --
  > `minebot/bot/mining.py`'s
  > `MiningController.dig_down`, mod-side `MinebotMod.tickDigDown`. No
  > pathfinding involved (stationary loop, breaks the block directly
  > below the player each tick); stops early and reports why if the next
  > block down is lava/water, or if there's no floor within a safety
  > margin below the block just broken (a "big drop"). Reports how many
  > were actually broken either way.

## Building / placing

- ⬜ **`!placeHere`** -- place a given block/item at the bot's current
  location (single blocks/torches, not whole structures). mindcraft ref:
  `actions.js:302`, → `skills.placeBlock`, `skills.js:611`. No
  block-placing interaction exists yet -- would follow the same real-
  interaction pattern as `DoorOpener`'s `useItemOn` call
  (`minebot-mod/src/main/java/minebot/mod/pathfinding/DoorOpener.java:83`),
  generalized to arbitrary blocks instead of doors specifically, plus the
  hotbar-selection logic `FoodEater`/`InventoryActions` already have for
  "get the right item into hand first."
  **Want it?**
  > yes, shorter alias: `!place <block>` would be nice, since it's shorter and more intuitive, or just `!place` if the block type is already in hand.

## Combat

- ✅ **`!attack`** -- attack and kill the nearest entity of a given type
  (e.g. nearest zombie, nearest cow). mindcraft ref: `actions.js:311`, →
  `skills.attackNearest`, `skills.js:313`. Per the original project brief
  (`prompt.txt`), "basic combat" was one of the three MVP asks (navigate,
  inventory management, basic combat) alongside inventory (done) and
  navigate (done). Built on the same real attack interaction
  `!collect <entity>`'s minimal kill loop already proved out
  (`MultiPlayerGameMode.attack(Entity)` + `player.swing`, distinct from
  the `useItem`/`useItemOn` interactions used elsewhere) -- generalized
  into its own standalone `ControlState.Mode.ATTACK` (`MinebotMod.
  tickAttack`) rather than reusing COLLECT's mode, since attack has no
  drop-confirmation/counted-loop concept at all, just "fight until dead
  or abandoned".
  **Want it?**
  > yes, but only hostiles by default if no argument passed, so `!attack` will attack the nearest hostile mob, and `!attack cow` will attack the nearest cow. Also, I want an alias `!kill` for it, since it's shorter and more intuitive
  >
  > **Done**: `!attack [query]` / `!kill [query]`
  > (`minebot/bot/combat.py`'s `CombatController.attack`) -- `query`
  > omitted resolves to the nearest real hostile (any entity implementing
  > vanilla's `Enemy` marker interface, confirmed via decompiled source
  > to be the correct general check rather than `instanceof Monster`:
  > every `Monster` implements `Enemy`, but at least one real hostile
  > (`EnderDragon`) implements `Enemy` without extending `Monster` at
  > all -- see `EntityFinder.findNearestHostile`). A given `query` skips
  > the hostility check entirely and fights that entity type regardless
  > (`!attack cow` works, matching mindcraft's own `attackNearest` having
  > no such restriction). No counted loop the way `!collect` has --
  > `!attack`/`!kill` is single-target, "fight until it dies or the
  > attempt is abandoned", per PENDING's own phrasing (counted
  > kills-for-drops is already `!collect <entity> <count>`'s job).
  > Two abandonment paths beyond "target died": a give-up-on-unreachable
  > timeout (same 200-tick shape `!collect` already established) and a
  > self-preservation health check (aborts and reports "had to retreat"
  > if the bot's own health drops to 25% of max mid-fight, checked every
  > tick, not just once) -- the latter added on top of mindcraft's own
  > `attackNearest` shape since a real fight can go badly in ways mining
  > never does. Wire protocol: Python sends
  > `{"type":"attack","query":null|"zombie","radius":64}`, mod replies
  > with a single fire-and-forget `attack_result` event (`success`,
  > `query`, `reason` on failure) -- same one-attempt-in-flight-at-a-time
  > shape as `find_result`/`collect_result`.
  >
  > **Weapon selection added**: the first version fought with whatever
  > happened to already be in hand -- no weapon selection at all.
  > `WeaponSelector` (new, mod-side) now picks a real bow (if carried
  > with arrows -- `Player.getProjectile`, the same real ammo lookup
  > vanilla's own bow-use logic uses) over the best melee weapon by real
  > attack damage (`ItemAttributeModifiers`' `ATTACK_DAMAGE`, since
  > modern vanilla has no per-item damage field on `Item` at all) over
  > bare hands, per the explicit ask ("prefer bows, then melee").
  > `BowShooter` (new) draws and fires a real bow via direct
  > `MultiPlayerGameMode.useItem()`/`releaseUsingItem()` calls, tracking
  > draw progress with its own local tick counter rather than polling
  > `isUsingItem()`/`getTicksUsingItem()` (confirmed unreliable for the
  > local player over the network) -- but still also holds the real
  > `keyUse` keybind down for the draw's duration, the same mechanism
  > `FoodEater`/`BlockBreaker` use, not to trigger the interaction (the
  > direct calls do that) but because vanilla's own `Minecraft.
  > handleKeybinds()` force-releases `isUsingItem()` every tick
  > `keyUse.isDown()` is false -- see `FINDINGS.md`'s "Bow-drawing never
  > actually fired an arrow" section for the full, long investigation
  > this took to actually confirm. A second, independent contributor to
  > the same symptom: `FoodEater` used to release `keyUse`
  > unconditionally every tick it wasn't actively eating, clobbering
  > `BowShooter`'s own hold -- `FoodEater` now tracks whether it's the
  > one actually holding the key and only releases what it itself set
  > (see `FINDINGS.md`, same section). Always holding a full draw
  > (`BowItem.MAX_DRAW_DURATION`, 20 ticks) for
  > maximum damage/accuracy rather than firing faster/weaker partial-
  > draw shots. A bow user keeps distance (`stopDistance` tracks
  > `BowItem.DEFAULT_RANGE`, 15 blocks) and shoots instead of closing to
  > melee range; running out of arrows mid-fight falls back to melee
  > (and starts closing distance again) on the very next tick, since
  > `WeaponSelector` is re-checked every tick, not just once per target.
  > Aim direction is ported directly from `AbstractSkeleton.
  > performRangedAttack` (decompiled) rather than a from-scratch
  > ballistic solve -- vanilla's own ranged-mob AI aims the raw vector to
  > the target with a fixed, distance-proportional lift added to the Y
  > component, not a solved launch angle; see `FINDINGS.md` for the full
  > decompiled-source trail this came from.

- ⬜ **`!attackPlayer`** -- attack a specific player by name until they
  die or run away. mindcraft ref: `actions.js:319`, →
  `skills.attackEntity`, `skills.js:334`. Same prerequisite work as
  `!attack` above, but target resolution is easier (already-tracked
  players via `EntityTracker`, no entity-type widening needed) --
  **plausibly the easiest combat entry point to build first** for
  exactly that reason. Consider authorization/griefing implications
  before implementing -- attacking players unprompted (vs. only when
  instructed) needs care.
  **Want it?**
  > no

## World interaction

- ⬜ **`!useOn`** -- use (right-click) a given tool/item on the nearest
  target of a given type (an entity, a block, or "nothing" for no
  target; "hand" for no tool). mindcraft ref: `actions.js:492`, →
  `skills.useToolOn`, `skills.js:1982`. A generalization of interactions
  this project already does in narrower forms -- `DoorOpener.java`'s
  `useItemOn` for doors specifically, `FoodEater`'s `keyUse`-hold for
  eating specifically -- a real port would need to unify "right-click on
  X with Y in hand" into one reusable interaction, then layer the
  narrower existing behaviors on top of it (or leave them as-is and add
  this as a fourth, more general case for anything not covered by a
  dedicated action).
  **Want it?**
  > yes, but I want it to be a universal `!use` command that can take either a block type, an entity type, or "hand" for no tool. For example, `!use lever` will use the nearest lever, `!use cow` will use the nearest cow (e.g. milk it), and `!use hand` will use the item in hand on the nearest target.

## Inventory / items

- ✅ **`!inventory`** -- report what the bot is currently carrying.
  → **`inventory`**, `minebot/bot/inventory.py:76`. mindcraft ref:
  `queries.js:66` (mindcraft's version also reports worn armor slots
  explicitly; this project's doesn't break those out separately, though
  they'd show up in the flat item list since `InventoryReporter.java`
  reports armor/offhand slots too).
  **Want it?** already done.
  > yes

- ✅ **`!equip`** -- equip a given item (armor/offhand). → **`equip`**,
  `inventory.py:82`. mindcraft ref: `actions.js:204`, → `skills.equip`,
  `skills.js:791`.
  **Want it?** already done.

- 🟡 **`!discard`** -- drop item(s) from inventory onto the ground.
  → **`drop`**, `inventory.py:90`. mindcraft ref: `actions.js:242`
  (mindcraft's version walks 5 blocks away first, drops, then walks back
  -- this project's `drop` just drops in place, a deliberate
  simplification chosen earlier this session: "2 commands, drop (in
  place), and give (walk to player and drop)").
  **Want it?** already done.

- 🟡 **`!givePlayer`** -- give item(s) to a player. → **`give`**,
  `inventory.py:98`. mindcraft ref: `actions.js:184`, →
  `skills.giveToPlayer`, `skills.js:998` (mineflayer can do an actual
  player-to-player item transfer via a plugin/protocol trick; this
  project's `give` walks to the player and drops instead, since real
  vanilla client interactions have no direct transfer primitive -- see
  `FINDINGS.md`'s note on `GIVE` mode).
  **Want it?** already done.

- ✅ **`!consume`** -- eat/drink a given item. Folded into
  **`FoodEater.java`**'s autonomous health-triggered eating
  (`minebot-mod/src/main/java/minebot/mod/FoodEater.java`), not a
  standalone chat-triggered action. mindcraft ref: `actions.js:196`, →
  `skills.consume`, `skills.js:973`. Gap: there's no *chat-triggerable*
  "eat this specific item now" command, only the automatic
  below-20%-health behavior -- worth adding a standalone
  `eat`/`consume` action wrapping the same `keyUse`-hold mechanism
  `FoodEater` uses (see `FoodEater.holdUseKey`/`releaseUseKey`) if a
  player should be able to explicitly tell the bot to eat on demand.
  **Want it?** already done (auto-eat); on-demand chat trigger --
  > yes, I want a `!eat <item>` command that will eat the specified item from the inventory, if it's edible, if no argument is passed, it will eat the item in hand, if edible

- ⬜ **`!putInChest`** / **`!takeFromChest`** / **`!viewChest`** -- put
  items into, take items from, or list the contents of the nearest
  chest. mindcraft ref: `actions.js:212` / `223` / `234`, →
  `skills.putInChest` (`skills.js:869`) / `skills.takeFromChest`
  (`skills.js:898`) / `skills.viewChest` (`skills.js:944`). No
  container-opening exists at all -- `InventoryActions.java`'s
  container-click mechanism (`handleContainerInput`, confirmed working
  for the player's own inventory) doesn't extend to *other* containers
  (chests) yet; opening a chest requires a real `useItemOn`-style
  interaction first (see `DoorOpener.java` for the pattern) to get the
  container menu open server-side before container clicks against it
  would work.
  **Want it?**
  > yes, but I want a universal `!chest` command that can take either `put`, `take`, or `view` as the first argument, and the item type and quantity as the second and third arguments. For example, `!chest put stone 10` will put 10 stone blocks into the nearest chest, `!chest take cow 5` will take 5 cows from the nearest chest (if they are stored as spawn eggs), and `!chest view` will list the contents of the nearest chest.

## Crafting

- ⬜ **`!craftRecipe`** -- craft a recipe N times. mindcraft ref:
  `actions.js:267`, → `skills.craftRecipe`, `skills.js:36`. No crafting
  exists at all -- needs a crafting-table interaction (or 2x2 inventory
  crafting for simple recipes) plus recipe-lookup logic; likely the
  single largest remaining feature area after mining/combat.
  **Want it?**
  > yes, but I want a universal `!craft` command that can take either a recipe name or an item name as the first argument, and the quantity as the second argument. For example, `!craft stone_pickaxe 1` will craft 1 stone pickaxe, and `!craft stone 10` will craft 10 stone blocks (if the recipe exists).

- ⬜ **`!smeltItem`** -- smelt an item N times via furnace. mindcraft
  ref: `actions.js:278`, → `skills.smeltItem`, `skills.js:142`. Depends
  on furnace container interaction, same prerequisite as chest
  interaction above.
  **Want it?**
  > yes, but I want a universal the `!craft` command to also handle smelting, so `!craft iron_ingot 10` will smelt 10 iron ore into 10 iron ingots, if the bot has a furnace and the necessary fuel, explain back why it can not craft it if there is no materials or furnaces, same as craft

- ⬜ **`!clearFurnace`** -- empty all items out of the nearest furnace.
  mindcraft ref: `actions.js:294`, → `skills.clearNearestFurnace`,
  `skills.js:275`. Same furnace-container prerequisite.
  **Want it?**
  > yes, but I want a universal `!clear` command that can take either `furnace`, `chest`, or `inventory` as the first argument, and will clear the specified container. For example, `!clear furnace` will empty all items out of the nearest furnace, `!clear chest` will empty all items out of the nearest chest, and `!clear inventory` will drop all items from the bot's inventory onto the ground. also, when interacting with chests or anything, we need an argument to specify which one, wither in coordinates or by name, so `!clear chest 10 64 -5` will clear the chest at those coordinates, and if not specified, it will clear the nearest one.

- ⬜ **`!craftable`** (query) -- list items craftable with the bot's
  current inventory. mindcraft ref: `queries.js:132`, →
  `world.getCraftableItems`. Doesn't need any new interaction, just
  recipe-database knowledge cross-referenced against `InventoryTracker`'s
  known contents -- could be built before real crafting is implemented,
  as a standalone query.
  **Want it?**
  > yes

- ⬜ **`!getCraftingPlan`** (query) -- full ingredient breakdown /
  missing-items analysis for crafting a target item. mindcraft ref:
  `queries.js:269`, → `mc.getDetailedCraftingPlan`
  (`src/utils/mcdata.js`). Same recipe-database prerequisite as
  `!craftable`.
  **Want it?**
  > yes, only if needed, since `!craftable` already gives a list of what can be crafted, and `!craft` will fail if the materials are not enough, also, craft should have access to a recipe database to know what materials are needed for a specific item, and if the bot does not have enough materials, it should explain back what is missing.

## Communication / social

- ⬜ **`!startConversation`** / **`!endConversation`** -- start/end a
  conversation with another bot instance. mindcraft ref: `actions.js:406`
  / `423`, → `convoManager` (`src/agent/conversation.js`), not skills.js.
  Only relevant once multiple bot instances exist and need to coordinate
  -- this project currently runs a single bot; low priority until that
  changes.
  **Want it?**
  > no for now

- ⬜ **`!lookAtPlayer`** -- look at a specific player, or match their
  look direction. mindcraft ref: `actions.js:436`, →
  `agent.vision_interpreter.lookAtPlayer` (not skills.js). Partially
  superseded already: `NearbyPlayerLookAt.java`
  (`minebot-mod/src/main/java/minebot/mod/NearbyPlayerLookAt.java`)
  makes the bot look at whoever's closest automatically, unprompted --
  an explicit "!lookAtPlayer X" chat command for a *specific* named
  player (not just "closest") would still be a useful, easy addition
  reusing the same eye-level math already proven there.
  **Want it?**
  > yes, but I want a universal `!look` command that can take either a player name, an entity type, or explicit coordinates as the first argument. For example, `!look Steve` will look at the player named Steve, `!look cow` will look at the nearest cow, and `!look 10 64 -5` will look at the coordinates (10, 64, -5), and if no argument is passed, it will look at the nearest player, also it can look at places, so `!look home` will look at the remembered place called "home".

- ⬜ **`!lookAtPosition`** -- look at an explicit x,y,z point. mindcraft
  ref: `actions.js:459`. Same eye-level-math reuse as above, aimed at a
  fixed point instead of a tracked entity.
  **Want it?**
  > yes, check universal `!look` command above

- ⬜ **`!stfu`** -- stop all chat/self-prompting, but keep the current
  action running. mindcraft ref: `actions.js:68`. No self-prompting/
  autonomous-chat system exists in this project yet (`LLMController` is
  reactive only, triggered per-message -- see `FINDINGS.md`'s LLM
  trigger section) -- not yet applicable until that exists.
  **Want it?**
  > I want a universal `!ai` command that can take either `on` or `off` as the first argument, to turn the LLM self-prompting on or off. For example, `!ai on` will enable the LLM self-prompting, and `!ai off` will disable it, `on` will be the default state, and if no argument is passed, it will toggle the current state.

- ⬜ **`!clearChat`** -- clear the bot's conversation history. mindcraft
  ref: `actions.js:84`. Not yet applicable: `LLMController.handle_chat`
  has no conversation history at all yet (a known gap already listed in
  `FINDINGS.md`) -- there's nothing to clear until that's built.
  **Want it?**
  > no, I prefer to pass the entire chat to llm, but only the last 10 messages, so it can have context, but not too much context to be confused, configurable

## Villager trading

- ⬜ **`!showVillagerTrades`** / **`!tradeWithVillager`** -- inspect a
  villager's trade offers, and execute a specific trade. mindcraft ref:
  `actions.js:387` / `395`, → `skills.showVillagerTrades`
  (`skills.js:1748`) / `skills.tradeWithVillager` (`skills.js:1789`).
  Notably, the sibling `/home/colaila/git/mods/VillagerHelper` mod
  (referenced throughout `FINDINGS.md` as the decompiled-source
  reference project) already does real villager-trade UI work and would
  be a natural source to study for the actual trade-menu interaction
  mechanics, even though it's a human-facing helper mod rather than a
  bot-control one.
  **Want it?**
  > yes

## Meta / control

- ⬜ **`!newAction`** -- LLM-generated novel behavior via arbitrary code
  generation. mindcraft ref: `actions.js:30`, →
  `agent.coder.generateCode`. Out of scope until there's a real LLM
  provider wired up at all (`FINDINGS.md`: "No real `LLMProvider` is
  wired up yet") -- and even then, this is a materially riskier feature
  (arbitrary code execution) worth a deliberate decision, not an assumed
  port.
  **Want it?**
  > no, never

- ⬜ **`!setMode`** / **`!modes`** (query) -- toggle a named autonomous
  behavior mode on/off, or list all modes and their state. mindcraft
  ref: `actions.js:347` / `queries.js:216`, → `agent.bot.modes`
  (`modes.js`). This project's closest equivalent is `FoodEater`'s
  always-on health-triggered eating -- there's no general "modes"
  framework (enable/disable named autonomous behaviors) yet; worth
  designing once there's more than one autonomous behavior to toggle.
  **Want it?**
  > no, not yet, but I want a universal `!mode` command that can take either `!mode <mode_name> on` or `!mode <mode_name> off` to enable or disable a specific mode, and `!mode list` to list all available modes and their current state, example models: `defense`, `farming`, `exploration`, `building`, `navigation`

- ⬜ **`!goal`** / **`!endGoal`** -- start/stop self-prompting toward a
  continuous goal, without needing a human to keep prompting. mindcraft
  ref: `actions.js:364` / `379`, → `agent.self_prompter`. Not applicable
  until `LLMController` supports multi-turn autonomous behavior beyond
  single-message reactive responses.
  **Want it?**
  > not sure, not for now

- ⬜ **`!restart`** -- restart the whole bot process. mindcraft ref:
  `actions.js:77`, → `agent.cleanKill()`. Straightforward if ever
  needed -- Python's own process could just `os.execv`/exit-and-let-a-
  supervisor-restart-it; no mod-side work.
  **Want it?**
  > yes

## Info queries (read-only)

- ⬜ **`!stats`** -- report position, gamemode, health, hunger, biome,
  weather, time of day, current action, and nearby players. mindcraft
  ref: `queries.js:15`. Most of the underlying data already flows over
  the wire (`position`, `health`, `inventory` events -- see
  `FINDINGS.md`'s wire-format section) -- this would mostly be a
  Python-side query action that reads already-tracked state and formats
  it, not new mod-side work. Biome/weather/time-of-day aren't currently
  broadcast at all though, so a full port needs a small mod-side
  addition for those specifically.
  **Want it?**
  > yes, but I want a universal `!info` command that can take either `stats`, `inventory`, `nearby`, or `location` as the first argument, and will return the corresponding information. For example, `!info stats` will return the bot's current stats, `!info inventory` will return the bot's current inventory, `!info nearby` will return a list of nearby players and entities, and `!info location` will return the bot's current coordinates and biome, `info` will be the default command if no argument is passed, and it will return the bot's current stats, also `!info players` will return a list of all nearby players, and `!info entities` will return a list of all nearby entities, and `!info blocks` will return a list of all nearby blocks.

- ⬜ **`!nearbyBlocks`** -- list nearby block types. mindcraft ref:
  `queries.js:103`. Needs the same block-scanning capability
  `!searchForBlock` needs -- no block-type awareness exists on the mod
  side yet at all (only entities/inventory are tracked).
  **Want it?**
  > yes, but I want the universal `!info` command to also handle nearby blocks, so `!info nearby blocks` will return a list of nearby block types and their coordinates.

- ⬜ **`!entities`** -- list nearby players/entities (with villager
  profession info). mindcraft ref: `queries.js:147`. Python already has
  `EntityTracker`'s live player list (`minebot/bridge/entities.py`) --
  this could ship today as a query action reading it directly, no new
  mod-side work, *except* it currently only covers players, not other
  entity types (mobs/animals/villagers) -- same widening
  `!searchForEntity` needs for full parity.
  **Want it?**
  > check universal `!info` command above, so `!info nearby entities` will return a list of nearby players and entities, with their coordinates and types.

- ⬜ **`!savedPlaces`** -- list all saved location names. mindcraft ref:
  `queries.js:222`. Depends on the same location-memory prerequisite as
  `!rememberHere`/`!goToRememberedPlace` above.
  **Want it?**
  > check universal `!info` command above, so `!info saved` will return a list of all saved location names and their coordinates.

- ⬜ **`!checkBlueprintLevel`** / **`!checkBlueprint`** /
  **`!getBlueprint`** / **`!getBlueprintLevel`** -- check progress on, or
  get the full explanation for, a structured building-task blueprint (a
  whole guided-construction feature). mindcraft ref: `queries.js:229` /
  `241` / `249` / `257`, → `tasks/construction_tasks.js`. Tied to
  mindcraft's own structured "building task" system, which has no
  equivalent here at all -- lowest priority, only relevant if this
  project ever gets a similar guided-building-task feature.
  **Want it?**
  > no, not for now

- ⬜ **`!searchWiki`** -- fetch and summarize a Minecraft Wiki page for a
  query term. mindcraft ref: `queries.js:311`, raw `fetch()` + cheerio
  parsing, no skills.js. Self-contained, no mod-side dependency at all
  -- could be built as a pure Python HTTP-fetch query action independent
  of everything else in this list, whenever useful.
  **Want it?**
  > no, llm will respond with wiki information if needed, so no need for a separate command.

- ✅ **`!help`** -- list all available commands and their descriptions.
  mindcraft ref: `queries.js:340`, → `getCommandDocs`
  (`commands/index.js:232`). Straightforward: `ActionRegistry.
  list_actions()` (`minebot/actions/registry.py:31`) already returns
  every registered `Action` with its `description`/`params` -- this is
  close to a one-function port, no mod-side work, and arguably should
  exist soon since it's also exactly the data the (still-unbuilt) LLM
  tool-schema adapter will need to consume.
  **Want it?**
  > yes, but I want a universal `!help` command that can take either no arguments, or a specific command name as the first argument. If no arguments are passed, it will return a list of all available commands and their descriptions. If a specific command name is passed, it will return the description and usage of that command.

## Not exposed as commands in mindcraft either (informational only)

These `skills.js` functions exist but aren't wired to any `!command` in
mindcraft -- called internally by `modes.js` or other skills instead.
Not part of the command-parity gap (nothing to "catch up" to on the
command surface), but worth knowing about if this project ever builds
its own autonomous "modes" layer, since they're mindcraft's reference
implementations for exactly that kind of always-on behavior (this
project's own `FoodEater`/`RespawnHandler` are the analogous pattern
already in use here). No **Want it?** lines here -- these aren't
commands to add, just background/reference:

- `activateNearestBlock` -- automatically use/activate the nearest usable
  block (e.g. a lever, button). skills.js:1652
- `tillAndSow` -- till farmland and plant seeds. skills.js:1575
- `useDoor` -- open/close a door automatically. skills.js:1499 (this
  project already has its own `DoorOpener.java`, built independently
  rather than ported)
- `moveAwayFromEntity` -- back away from a specific entity. skills.js:1430
- `avoidEnemies` -- flee from nearby hostile mobs. skills.js:1445
- `pickupNearbyItems` -- auto-collect dropped items on the ground.
  skills.js:531
- `defendSelf` -- fight back when attacked. skills.js:370
- `breakBlockAt` -- break a specific block by coordinates. skills.js:561
- `wait` -- pause for a number of ticks. skills.js:117
- `waitUntil` -- pause until a condition is met
