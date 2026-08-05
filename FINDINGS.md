# Project findings / handoff notes

Context for a future session picking this up cold. See `prompt.txt` for the
original ask: a Python Minecraft bot (mindcraft-alike, but no LLM in the loop
for basic actions) that connects to a real server, parses chat commands, and
does movement/mining/placing/combat/inventory.

## Architecture pivot: client mod + Python brain, not raw protocol

This project spent its first several sessions building a from-scratch Python
implementation of the Minecraft protocol (protocol 775 / version 26.1.2,
online-mode auth, chunk parsing, A* pathfinding, a full physics-simulation
port of prismarine-physics for movement execution...). That work is fully
preserved on the **`pure-protocol-backend` branch** if this architecture is
ever abandoned and that approach needs to be picked back up.

It was abandoned for a simpler, more robust design: **a real Minecraft
client + a Fabric mod does the actual playing** (movement, physics,
collision -- all real game code, not a reimplementation of it), and Python
is purely the brain/controller, talking to the mod over a local WebSocket
control channel. Motivation: repeatedly re-deriving vanilla's exact
movement-validation and physics behavior from a decompiled-source
reimplementation kept surfacing new live-only edge cases (server rejecting
subtly-wrong reported positions, diagonal moves clipping through geometry
under a naive movement model, ...) -- using the real client sidesteps that
whole class of problem entirely, since there's no possibility of the physics
being subtly wrong when it's the actual game's own code running.

Two repos now make up this project:
- **`/home/colaila/git/minebot`** (this repo) -- the Python side. No longer
  speaks the Minecraft protocol at all; connects to the mod's local
  WebSocket and does chat-command parsing/dispatch and decision-making
  (currently: `!follow`/`!stop`).
- **`/home/colaila/git/mods/minebot-mod`** -- a separate Fabric mod repo.
  Runs inside a real Minecraft client, logged into the bot's account via
  normal Microsoft/Mojang auth (nothing custom -- just sign into the actual
  launcher/client like a human would). It's the only thing that actually
  connects to the Minecraft server. It's the WebSocket *client*
  (`ControlClient`), connecting out to Python's WebSocket server
  (`ModBridge`, `0.0.0.0:47893` by default).

**Why Python is the WebSocket server and the mod is the client** (this was
originally the other way round): the Python backend commonly runs inside
WSL2, and WSL2's default (NAT) networking mode only forwards `localhost`
connections *from Windows into WSL2*, not the reverse -- confirmed live: a
mod-side server bound to `127.0.0.1` got `ConnectionRefusedError` from
WSL2, and rebinding it to `0.0.0.0` instead got a silent TCP timeout
(Windows Firewall dropping the inbound connection). A plain
`python3 -m http.server` run inside WSL2, by contrast, was reachable from
Windows via plain `http://localhost:PORT/` with zero configuration. So
Python (in WSL2) now runs the WebSocket server, and the mod (on Windows)
makes an *outbound* connection to `localhost:47893`, which WSL2 forwards
transparently -- no IP-passing or firewall changes needed either side.

## How the mod drives real movement

The key technical unlock (confirmed by reading the 26.1.2 decompiled source,
via the same Fabric Loom setup as `mods/VillagerHelper`): `LocalPlayer`
(`net.minecraft.client.player.LocalPlayer`) has a public `input` field of
type `ClientInput`. Every client tick, `LocalPlayer.aiStep()` calls
`this.input.tick()` unconditionally, then reads the resulting
`keyPresses`/`moveVector` and feeds them through the *exact same* real
physics/collision pipeline (`Entity.moveRelative` -> real gravity/friction/
collision) that a human pressing W/Space would drive. Normally `input` is a
`KeyboardInput` that fills those fields from real keybind state
(`Options.keyUp.isDown()` etc.) every tick.

`minebot-mod`'s `MinebotInput` (`ClientInput` subclass) replaces that: each
tick, it first checks a *real* `KeyboardInput` delegate's own key state --
if the human is actually pressing anything (forward/strafe/jump/sneak/
sprint), that wins and passes straight through unmodified (**manual
override** -- found necessary live: the first version locked out WASD
entirely, which the user immediately flagged). Only when the keyboard is
idle does the bot's own resolved `MovementIntent` (forward/jump, computed
from the current goal) drive input instead. Yaw/pitch aren't part of
`Input` at all in this version -- they're set directly via
`Entity.setYRot()`/`setXRot()` on the player each tick.

Goal resolution (`MinebotMod.resolveMovementIntent`, called every client
tick): Python sends a high-level goal over the WebSocket
(`{"type":"goto",x,y,z}` or `{"type":"follow",entity_id}`, both with a
`stop_distance`), and the mod's own tick loop resolves it against live
game state. Real A* pathfinding now runs on the mod side
(`minebot/mod/pathfinding/`) -- a direct Java port of the same
mineflayer-pathfinder cost model already validated as the Python port on
`pure-protocol-backend` (walk/climb/parkour moves, no dig/place, since
the mod can't dig/place). `ControlState.pathTracker` (a `PathTracker`)
replans a path only once the target has moved far enough from where the
current one was aimed, and `resolveMovementIntent` aims yaw/forward/jump
at the next unreached waypoint along that path instead of the raw target
position -- this is what makes `follow` actually route the bot down to a
different floor instead of stalling at the edge (the straight-line-plus-
step-height approach it replaced only handled ledges within vanilla's
0.6-block auto-step, and otherwise just relied on gravity, which left the
bot standing at a floor's edge instead of finding a way down). Falls back
to the raw target position if no path is found (e.g. unloaded chunks) so
the bot still makes some progress rather than freezing.

Entity/chat/health awareness: the mod diffs its own live `ClientLevel`
player list every tick (`ClientLevel.players()`/`.getEntity(int)`,
confirmed via decompiled source) and broadcasts add/move/remove JSON
events with id/name/position -- this is how Python resolves a chat-typed
name ("!follow Steve") to an entity id, without needing any packet parsing
of its own. Chat is forwarded via Fabric API's
`ClientReceiveMessageEvents.CHAT`/`GAME` (confirmed present in the pinned
`fabric-api:0.151.0+26.1.2`). Health changes are polled once per tick
(`LivingEntity.getHealth()`) and broadcast on change.

## Control channel wire format (WebSocket, one JSON object per message)

Commands, Python -> mod:
- `{"type":"goto","x":..,"y":..,"z":..,"stop_distance":2.0}`
- `{"type":"follow","entity_id":..,"stop_distance":2.0}`
- `{"type":"stop"}`
- `{"type":"chat","text":".."}`
- `{"type":"move_to_hotbar","slot":..,"hotbar_slot":..}` -- moves/swaps
  `slot`'s contents into hotbar slot `hotbar_slot` (0-8), selecting it.
- `{"type":"equip","slot":..}` -- shift-click-equivalent: routes `slot`'s
  contents into its matching armor/offhand slot automatically.
- `{"type":"drop","slot":..,"count":..}` -- drops up to `count` of
  `slot`'s contents (capped at the actual stack size).
- `{"type":"give","entity_id":..,"slot":..,"count":..,"stop_distance":2.0}`
  -- walks toward `entity_id` (FOLLOW-style pathing) and, once within
  `stop_distance`, drops `count` of `slot`'s contents and returns to IDLE.
- `{"type":"find","query":"cow","radius":64}` -- backs `!find`: `query`
  is tried as an entity type first (`minecraft:` prefix added if not
  already namespaced), falling back to a block type if no entity type by
  that name exists. Answered asynchronously by a `find_result` event (see
  below), not a direct reply -- see "The find_result deadlock" below for
  why that matters. Runs the actual `BlockPos.findClosestMatch`/
  `getEntitiesOfClass` scan via `Minecraft.getInstance().execute(...)`,
  not inline in `handleMessage` -- these iterate live chunk/entity
  collections, which (unlike the simpler inventory commands) is genuinely
  unsafe off the render/tick thread.
- `{"type":"dig_down","count":..}` -- backs `!dig`: breaks the block
  directly below the player, `count` times, stopping early on lava/water
  or a "big drop" (see "Mining / digging" below). No thread-hop needed
  (unlike `find`) -- only mutates `ControlState`'s volatile fields, same
  as `goto`/`follow`/`give`.
- `{"type":"collect","query":"stone","radius":64}` -- backs `!collect`:
  same entity-then-block resolution as `find`'s `query`, but a single
  atomic attempt (find nearest match, walk to it, break/kill it once) --
  no `count` at all anymore. See "!collect redesigned: single-item mod
  commands, counted loop moved to Python" below for why; the short
  version is that the requested count now lives entirely in
  `MiningController.collect`'s own loop, which sends this exact message
  once per item it still needs.
- `{"type":"query","sub_type":"drops_from"|"source_for","arguments":[".."],"key":".."}`
  -- a generic, potentially-overlapping request/reply mechanism (unlike
  `find`/`collect`, which assume only one is ever in flight at a time):
  `key` is a client-generated correlation id (a UUID in practice, but
  treated as an opaque string on both sides) threaded back unchanged on
  the matching `query_result`, since queries can take a while and
  legitimately resolve out of order. `drops_from(X)` asks "what would
  collecting X actually drop" (e.g. `drops_from("cow")` ->
  `["beef","leather"]`); `source_for(X)` asks the reverse, "what should I
  go collect to eventually get X" (e.g. `source_for("cobblestone")` ->
  `["stone"]`, since there's no cobblestone block or mob of its own).
  Answered by `DropTable`'s hardcoded maps -- see "!collect redesigned"
  below for why this isn't (yet) backed by real loot-table data.

All four inventory commands address slots using `Inventory`'s own 0-42
numbering (0-8 hotbar, 9-35 main storage, 36-42 armor/offhand/body/
saddle) -- the same numbering the `inventory` event below reports, *not*
`InventoryMenu`'s different internal numbering (see `InventoryActions`'s
own docstring on the mod side for the exact translation between the two,
confirmed by disassembling `AbstractContainerMenu`).

Events, mod -> Python:
- `{"type":"hello","commit":"..","built_at":".."}` -- sent once, the
  moment the control channel connects, reporting exactly what code the
  connected mod is running (its git commit + build timestamp, baked into
  the jar at build time -- see `BuildInfo`/`generateBuildInfo` below).
  The backend compares `commit` against `minebot-mod`'s own current
  `git rev-parse HEAD` (see `minebot/mod_version.py`) and logs a loud
  WARNING on mismatch.
- `{"type":"position","name":"..","x":..,"y":..,"z":..,"yaw":..,"pitch":..,"on_ground":..}`
  (every client tick) -- `name` is the bot's own account name
  (`player.getScoreboardName()`), tracked Python-side by
  `SelfPositionTracker.own_name` so the run loop can recognize and ignore
  the bot's *own* chat messages (see "The chat self-echo loop" below).
- `{"type":"find_result","query":"..","found":true|false,"recognized":true|false,"kind":"entity"|"block","x":..,"y":..,"z":..}`
  -- reply to a `find` command; `kind`/`x`/`y`/`z` are omitted when
  `found` is `false`. Fire-and-forget, no request id: only one `!find` is
  ever in flight at a time (chat commands are dispatched one at a time on
  the Python side), so `MovementController` just resolves whichever
  single pending future is waiting, if any (`_pending_find`).
  `recognized` distinguishes "that's a real block/entity type, just none
  within range" from "that's not a registered type at all" -- `BLOCK`/
  `ENTITY_TYPE` are `DefaultedRegistry`, so a lookup for an unknown key
  silently falls back to a default instead of failing, and both cases
  used to report the identical "not found nearby" (a player asked for
  these to be told apart live: `!find aaa` vs `!find allay` with none
  around). Checked via `Registry.containsKey`, independent of the
  defaulting behavior.
- `{"type":"chat","sender":"name-or-omitted","text":".."}`
- `{"type":"entity","action":"add"|"move"|"remove","id":..,"name":"...","x":..,"y":..,"z":..}`
  (name/position omitted on `remove`; name omitted on `move`, since it never
  changes)
- `{"type":"health","health":..}`
- `{"type":"death"}` -- fires exactly once when the bot dies (distinct
  from `health` hitting 0, which can be transient/edge-casey on its own);
  the mod auto-respawns immediately when this fires (see `RespawnHandler`
  below), no manual "click Respawn" needed.
- `{"type":"respawn"}` -- fires exactly once after a `death` when the bot
  has actually respawned (health back above 0).
- `{"type":"arrived"}` -- fires exactly once when a GOTO goal's distance-
  to-target first drops under `stop_distance` (`ControlState.gotoArrived`,
  an `EdgeTrigger`; reset on every `setGoto`/`setFollow`/`setGive`/`clear`
  so a fresh goal always reports its own arrival, not a stale one from a
  previous goal). Deliberately general-purpose, not `!find`-specific --
  FOLLOW/GIVE never fire it (no single "arrival": FOLLOW tracks a moving
  target forever, GIVE's completion is `maybeCompleteGive` dropping the
  item, a different concept). `!find` is the first consumer: it sends an
  immediate "found X at (coords), going there" chat message when
  `find_result` resolves, then separately awaits `arrived` before sending
  a final "here is the X" (falling back to "having trouble reaching X,
  might be stuck" after `FIND_ARRIVAL_TIMEOUT`, 60s) -- added after a
  player watched the bot walk toward a found block with no way to tell
  whether it had arrived or was stuck.
- `{"type":"inventory","selected_slot":..,"slots":[{"slot":..,"item":"minecraft:...","count":..,"damage":..,"max_damage":..}, ...]}`
  -- a full snapshot (only non-empty slots listed), broadcast whenever it
  differs from the last one sent (change-only, same shape as `health`).
  `damage`/`max_damage` are only present for damaged (not full-durability)
  items.
- `{"type":"dig_down_result","broken":..,"reason":".."}` -- reply to a
  `dig_down` command; `reason` (e.g. `"hit lava/water"`, `"big drop
  ahead"`) is only present on an early stop, omitted on full completion.
  Fire-and-forget like `find_result`, same one-in-flight-at-a-time
  reasoning.
- `{"type":"collect_result","success":true|false,"query":"..","reason":".."}`
  -- reply to a single-item `collect` command; `reason` (e.g. `"no more
  stone found nearby"`) is only present when `success` is `false`. No
  `collected`/`requested` counts anymore (see "!collect redesigned"
  below) -- fire-and-forget like `find_result`/`dig_down_result`, same
  one-attempt-in-flight-at-a-time reasoning. Note this fires the instant
  the block/entity is destroyed/killed, *not* once any drop is actually
  sitting in inventory -- see the same section for why `MiningController`
  never treats this alone as proof of possession.
- `{"type":"query_result","key":"..","result":["..", ...]}` -- reply to a
  `query` command, `key` copied back unchanged from the request so the
  asker can match it to the right pending call even if several are in
  flight and resolve out of order. `result` is a plain list of item/block/
  entity ids (e.g. `drops_from("cow")` -> `{"result":["beef","leather"]}`);
  an empty list means the query genuinely has nothing to report (not
  distinguished from "unknown query subject" -- both currently fall back
  to treating the original argument as if drops_from/source_for were the
  identity function, see `DropTable`'s own docstring).
- `{"type":"item_drop","action":"add"|"remove","id":..,"item":"..","count":..,"x":..,"y":..,"z":..}`
  -- ground-truth tracking of real dropped-item entities near the bot
  (`ItemDropTracker`, mirroring `broadcastEntityEvents`'s own add/remove
  diffing but for `ItemEntity` instead of players); position fields
  omitted on `remove`. Currently unconsumed on the Python side -- `!collect`'s
  own drop confirmation reads `InventoryTracker.count_of` directly instead
  (see "!collect redesigned" below) -- kept as a documented, intentional
  gap/future hook (`run_loop.py`'s `_read_events` docstring calls this out
  explicitly) rather than silently dropped, in case a future feature
  (e.g. "go pick up that specific dropped diamond") needs it.

## The find_result deadlock (run_loop.py's reader/processor split)

`!find`'s handler (`MovementController.find`) sends a `find` command and
then suspends, awaiting a future that only gets resolved when a later
`find_result` event is processed. The very first implementation just
awaited `dispatch_chat` inline inside `run()`'s single
`async for event in bridge.events()` loop -- which means the coroutine
reading events off the socket was the *same* coroutine suspended waiting
for one of those events to arrive. The mod actually replies within
~100-200ms in practice (confirmed live with `time.monotonic()`
instrumentation on every send/recv), but Python never looked at the
already-buffered reply until `find()`'s own 10s timeout gave up and the
loop finally got back around to reading the next message -- a true
self-deadlock, not a real latency problem. `!find` always failed with
"no response" live even though the mod never failed to reply.

Fixed by splitting `run()` into two concurrent tasks joined by an
`asyncio.Queue` (see `run_loop.py`'s `_read_events`/`_process_event`):
- `_read_events` is the *only* coroutine that ever awaits the next raw
  event off `bridge.events()`. It never blocks on anything that itself
  waits for a later event. `find_result` gets a fast-path here --
  resolved into `MovementController._pending_find` the instant it's
  read, before the event even reaches the queue.
- The main loop in `run()` drains that queue and processes everything
  else exactly as before (one event at a time, in order), so ordering
  guarantees other logic depends on (e.g. `!follow`'s `send_follow`
  happens-before a later entity-reconnect event is processed) are
  unchanged.

A background-task approach (running each chat command as its own
`asyncio.Task` instead of awaiting it inline) was considered and
rejected *at the time*: it also unblocks `find_result`, but lets a slow
command's side effects land *after* later events have already been
processed, which broke the `!follow`-resumes-after-reconnect ordering
guarantee in practice (verified by writing that version and watching a
real test fail on event order, not just correctness). **This was
revisited and made to work later** -- see "Chat commands are now
interruptible" below for the version that fixes the ordering hazard
properly instead of avoiding background tasks altogether.

`tests/test_run_loop.py::test_run_loop_delivers_find_result_without_deadlocking`
is a regression test for this specifically -- it uses a `FindReplyBridge`
that only yields its canned `find_result` event once `send_find` has
actually been called (modeling the real request/response causality a
plain canned event list can't express), and fails via `asyncio.wait_for`'s
2s timeout if the deadlock ever comes back.

## Chat commands are now interruptible

Reported live: `!collect stone 10` got stuck with no reply at all (the
mod-side collect loop apparently never found/reached enough stone and
never sent a `collect_result` back -- see the client log excerpt that
surfaced this, `!collect stone 10` at 21:01:38 with zero response ever).
Every command typed after it -- three separate `!follow` attempts,
`!stop`, a typo'd `!atop` -- silently did nothing, because `run()`'s main
loop awaited each chat command's *entire* dispatch inline before looking
at the next queued event (see "The find_result deadlock" above's
original design). A stuck long-running command didn't just fail to
finish -- it blocked every other command behind it too, including `!stop`,
for as long as `COLLECT_INACTIVITY_TIMEOUT` (180s) or until the stuck
call somehow resolved on its own.

Explicitly requested as the fix, not just "make `!stop` special": *every*
chat command should be async and interruptible -- issuing a new command
mid-`!collect` should both run the new command and stop the old one, not
require a dedicated cancel command or wait out a timeout.

**The earlier rejection of background-task chat dispatch (see above) was
correct at the time but solvable, not a dead end.** Re-examining why it
broke `!follow`-resumes-after-reconnect: that ordering guarantee only
ever needed `MovementController._following_name` (set synchronously,
first line of `follow()`) to be visible to a later `entity` "add" event's
`on_entity_added` check. The old combined design bundled *all* event
handling -- both state-tracking (`EntityTracker.handle_event`,
`InventoryTracker.handle_event`, `SelfPositionTracker.handle_event`) and
side-effecting command dispatch -- into the same sequential path, so
backgrounding "chat dispatch" as a whole accidentally also backgrounded
the entity-tracking update the ordering guarantee depended on.

The fix: split those two concerns properly instead of keeping them
coupled.
- `entity`/`inventory`/`position` events (pure state updates, plus
  `entity` "add"'s `on_entity_added` reaction) now get the exact same
  synchronous fast-path treatment `find_result`/`arrived`/
  `dig_down_result`/`collect_progress`/`collect_result` already had in
  `_read_events` -- handled the instant they're read off the socket,
  never touching the queue at all. Every tracker read any command
  handler ever does is therefore guaranteed current as of every event
  received so far, independent of how concurrently chat commands
  themselves end up scheduled.
- Chat commands now run as their own `asyncio.Task` (`_dispatch_chat_command`
  in `run_loop.py`), tracked in a single `current_command_task` slot in
  `run()`. A newly-arrived chat command cancels whatever's currently in
  that slot before starting its own -- `asyncio.CancelledError` (a
  `BaseException`, not caught by `ActionRegistry.dispatch_chat`'s
  `except Exception`) propagates cleanly through a superseded
  `!collect`/`!digDown`/`!find`'s pending-future wait, and each of those
  handlers' own `finally` blocks (clearing `_pending_collect_result`
  etc.) still run on the way out, so nothing leaks. No mod-side "cancel"
  message is needed either: the *new* command's own wire send
  (`send_follow`/`send_goto`/`send_collect`/...) already overwrites
  whatever `ControlState.mode` the old goal had set (confirmed in
  `ControlState.java` -- every `set*` method unconditionally replaces
  `mode`), so "stop collecting, follow instead" falls out naturally from
  "the newest goal always wins mod-side" without any new protocol.

**One genuine, accepted tradeoff**: with chat commands now independently
concurrent, there's no longer a hard guarantee about which of two
*near-simultaneous* things happens first -- e.g. `!follow`'s own
`_following_name` assignment vs. an `entity` reconnect event's
`on_entity_added` check, if both land in the same instant. In the
"losing" order, `on_entity_added` simply no-ops (the name doesn't match
yet), but `follow()` itself still succeeds moments later using
`EntityTracker`'s already-current state (which reflects the reconnect
regardless, since entity events are always fast-pathed synchronously) --
so the net outcome is unaffected, just reached via a different code path.
This only matters for two events arriving in the exact same tick window,
which real player typing speed vs. network/game-tick timing makes very
unlikely; `test_run_loop_resumes_follow_after_target_reconnects_with_a_new_entity_id`
was updated to assert on the outcome (ends up following the reconnected
id) rather than the exact wire-command sequence, since which of the two
paths wins that race is no longer meaningful to pin down.

`test_run_loop_lets_a_new_chat_command_interrupt_a_stuck_one` is the
regression test for the actual reported bug -- a `!collect` that sends
its command but never gets any reply, followed by a `!follow` chat
message; asserts `!follow` still gets dispatched and replied to promptly
(the test itself completes in milliseconds, not anywhere near
`COLLECT_INACTIVITY_TIMEOUT`), and that the stuck `collect()` call's
pending-future state was actually cleaned up by cancellation.

`run()` also had to change its own exit condition: since a chat command
is now a task tracked separately from the event queue, "the reader
finished and the queue is empty" is no longer sufficient to know
everything is done -- it now also awaits (not cancels) `current_command_task`
before returning, so a graceful stream end lets the last dispatched
command actually finish rather than yanking it mid-flight. Only an
abnormal exit (an exception, or the reader itself crashing) cancels an
in-flight command, in `run()`'s own `finally` block.

## !collect: line-of-sight, tool-switching, and give-up-and-retry fixes

Reported live (via `!collect stone 10` against a real world, after the
interruptibility fix above was already deployed): the bot got stuck
trying to mine blocks obstructed by other blocks -- "trying to mine
across another block" -- and separately, "the bot has a pickaxe, but is
not using it when mining stone." Both root-caused and fixed together
since they live in the same file.

**No line-of-sight check anywhere in the mining path.** `BlockFinder.
findNearestBlock` is a pure Manhattan-distance scan (`BlockPos.
findClosestMatch`) with zero awareness of what's actually visible from
the bot's position -- it can (and did) return a block that's the
*nearest by distance* but sitting behind another solid block from
wherever the bot ends up standing. `BlockBreaker.tryBreak`'s only
reachability gate was a raw 3D Euclidean distance check
(`INTERACT_RANGE`, 4.5 blocks) -- nothing stopped it from happily
swinging at a target with a wall directly in between, something a real
player physically cannot do (the crosshair can never land on a hidden
block). Fixed with a real raycast: `hasLineOfSight` casts from
`player.getEyePosition()` to the target block's center via `Level.clip
(new ClipContext(from, to, ClipContext.Block.OUTLINE, ClipContext.Fluid.NONE,
player))` -- confirmed via `javap` against the actual mapped 26.1.2 jar
(`minecraftMaven/.../minecraft-merged-deobf-26.1.2.jar`) that this is
the same real primitive vanilla itself uses for the crosshair pick
(`Entity.pick`/`hasLineOfSight` call sites all construct a `ClipContext`
the same way). `tryBreak` now refuses to mine (returns `false`, same
"not ready" signal as out-of-range) whenever the clip doesn't land on
the exact target position.

**Refusing to mine an obstructed block is necessary but not sufficient
on its own** -- without a way to give up, it just changes "stuck mining
through a wall forever" into "stuck standing next to the wall forever",
the same net symptom via a different mechanism. `ControlState` gained
`collectTargetStuckTicks` (reset to 0 whenever a fresh target is picked,
incremented every tick `tickCollect` makes no progress on the current
one) -- once it exceeds `COLLECT_TARGET_TIMEOUT_TICKS` (200 ticks, 10s;
generous enough that legitimately walking there via pathfinding never
trips it, bounded so a genuinely unreachable target doesn't strand the
whole run), the target is abandoned and the next tick searches for a
different one.

**Abandoning a target isn't enough by itself either** -- `BlockFinder.
findNearestBlock` is fully deterministic for a fixed center/predicate,
so re-searching from roughly the same position after giving up would
just find the exact same unreachable block again, forever. Fixed with a
new exclusion-set overload on both `BlockFinder.findNearestBlock` and
`MinebotMod.resolveNearest` (the shared !find/!collect search helper) --
`ControlState.collectExcludedPositions` accumulates every abandoned
block position for the duration of one `!collect` run (cleared only by
`setCollect` starting a fresh run, not between individual items, since a
position unreachable for one item is still unreachable for the next
search too), and both search paths skip anything in it.

**Tool-switching only ran once per target, so it never self-corrected
if something else changed the selected hotbar slot mid-break.**
Confirmed via `javap` against the real 26.1.2 jar that vanilla
tools are no longer separate item subclasses in this version --
`PickaxeItem`/`DiggerItem` don't exist at all; mining speed is entirely
data-driven through a `Tool` data component (`net.minecraft.world.item.
component.Tool`, a list of `Tool.Rule`s matched against `BlockState`).
`ItemStack.getDestroySpeed` already correctly reads this (confirmed via
bytecode: an item with no `Tool` component, e.g. bare hands, returns a
flat `1.0F`; a real pickaxe returns whatever its matching `Rule` speed
is, well above that for stone) -- the tool-selection *logic* itself
(`maybeSwitchToBestTool`, comparing real `getDestroySpeed` across
inventory) was already correct. The bug was call frequency:
`maybeSwitchToBestTool` only ran once, gated on `isNewTarget`, when a
break first started. `FoodEater`'s own auto-eat logic ticks *after*
`tickCollect`/`tickDigDown` in `MinebotMod.onClientTick`'s ordering, and
can hotbar-swap the selected slot to a food item mid-break if health
drops low enough -- silently stomping over whatever tool was selected,
with nothing ever re-checking it for the rest of that block. Fixed by
calling `maybeSwitchToBestTool` every tick instead of only on a new
target -- it's already a no-op once the best tool is selected (`bestSlot`
stays `-1`), so this doesn't thrash the hotbar under normal conditions,
it only acts when something else actually changed the selection. Note:
a tool swap forced mid-break by this still resets `MultiPlayerGameMode`'s
own destroy progress to zero either way (`sameDestroyTarget` compares
the held `ItemStack`), so this doesn't avoid that reset -- it just
re-corrects to the right tool on the very next tick instead of silently
mining the rest of that block with whatever was left selected.

**First live test caught a real overcorrection in the line-of-sight
check.** Deployed the fixes above and ran `!collect stone 10` -- the
bot stood still "looking down" and gave up on target after target after
exactly 201 ticks each (`collect: giving up on unreachable target after
201 ticks`, repeating every ~10s in the client log), never collecting
anything at all. The single-center-point raycast was too strict: mining
a block *adjacent to* (not behind) the bot's own standing block, a
straight ray from eye height to that block's exact center can clip the
corner of the block the bot is standing on first -- geometrically
correct for that one exact point, but a real player isn't restricted to
aiming at dead center either; they'd naturally find a clean angle to
*some* point on the target's near face. Since natural stone terrain
often puts several nearby candidates in this exact adjacent-to-standing-
block geometry, `BlockFinder`'s excluded-position retry kept finding a
different stone block with the identical problem, explaining the
repeated identical-looking failures rather than eventual success on a
farther block.

Fixed by sampling several points on the target block (center, near each
face, `BlockBreaker.SIGHT_SAMPLE_OFFSETS`) instead of only the exact
center -- `hasLineOfSight` now succeeds if *any* sampled point clips
cleanly to the target, matching a real player's actual freedom to aim
anywhere on a visible face rather than requiring the literal center to
be unobstructed. A genuinely walled-off target (no sample point visible
from any angle within `INTERACT_RANGE`) still correctly fails all of
them.

**Confirmed live after this correction**: a follow-up `!collect stone 10`
run gave up on one target (201-tick timeout, as expected -- a genuinely
obstructed block still correctly refuses to be mined) then went on to
actually collect (`got 1/10 stone`, `got 2/10 stone`, `got 3/10 stone`,
...) -- the multi-sample-point fix resolved the overcorrection without
reintroducing the original mine-through-a-wall bug.

## InventoryActions.moveToHotbar never actually worked for a non-empty destination slot

Reported live, separately from the line-of-sight work above and *not*
fixed by it: "still not selecting the pickaxe" even after
`maybeSwitchToBestTool` was re-checking every tick (the earlier fix for
`FoodEater` stomping the selection mid-break). Root cause was one level
deeper and had nothing to do with mining specifically -- `Inventory.
pickSlot(slot)`, which `moveToHotbar` (and `FoodEater.selectSlot`'s
main-storage branch) both relied on, **cannot actually be steered to a
chosen destination slot at all**. Confirmed by reading the real
decompiled source (`Inventory.java`): `pickSlot`'s very first line is
`this.setSelectedSlot(this.getSuitableHotbarSlot())` -- it immediately
overwrites whatever slot the caller just explicitly selected with the
result of its own internal search (`getSuitableHotbarSlot`: the first
*empty* hotbar slot, scanning from whatever's currently selected and
wrapping around; falling back to the first non-enchanted item if none
are empty). There is no parameter or prior call that changes which slot
`pickSlot` actually lands on -- the `slot` argument only controls *which
other slot gets swapped in*, never *where*.

This means `moveToHotbar(player, bestSlot, 8)` only ever worked by
coincidence, when hotbar slot 8 happened to already be empty (`getSuitableHotbarSlot`'s
scan starts exactly at the already-selected slot 8 and finds it
immediately in that case). The moment slot 8 held anything else -- the
overwhelmingly common case in practice -- the swap silently landed
somewhere else entirely, and the "selected" slot ended up being whatever
`getSuitableHotbarSlot` picked, not the pickaxe. `FoodEater.selectSlot`'s
own main-storage branch had the exact same latent bug (it never even
called `setSelectedSlot` first, just `pickSlot(slot)` directly, relying
on "whichever slot happens to already be selected" as the implicit
destination -- which `pickSlot` also doesn't honor). `FoodEater`'s
extensive live-testing (see the `keyUse`-hold investigation elsewhere in
this file) apparently never surfaced this specifically, most plausibly
because the bot's already-selected hotbar slot was empty often enough in
those test sessions for the bug to stay invisible.

Fixed in both places by abandoning `pickSlot` entirely in favor of a
direct, controllable swap via `Inventory.getItem`/`setItem` (already a
public, side-effect-free pair -- confirmed via decompiled source:
`setItem` just writes `this.items.set(slot, itemStack)` plus an
equipment-slot mirror that doesn't apply to hotbar/main-storage indices)
followed by an explicit `setSelectedSlot`. Not a protocol-reimplementation
regression -- this is exactly the same "local Inventory state, synced to
the server automatically next tick via `ensureHasSentCarriedItem`"
mechanism `pickSlot` itself used internally, just steered correctly.

**Confirmed live**: the `getItem`/`setItem` swap fix works -- tool
selection now correctly picks up a carried pickaxe when mining stone.

Also added debug-tier logging for the tool-switch decision, temporarily
promoted to `info` (`BlockBreaker.maybeSwitchToBestTool`) -- confirmed
this client's default log4j config filters `debug` output entirely (no
`LOGGER.debug` line from this mod, despite several existing call sites
in `DoorOpener`/`Movements`/`PathTracker`, had ever actually appeared in
a real log). The per-slot scan itself stays at `debug`; only the two
summary lines (what got selected, what it was before) are at `info`,
throttled to once per new mining target rather than every tick, except
a genuine switch (rare, always meaningful -- e.g. `FoodEater` stealing
the selection mid-break) which always logs regardless.

## !collect redesigned: single-item mod commands, counted loop moved to Python

Reported live, after the tool-selection and line-of-sight fixes above
were both confirmed working: "!give right after a !collect-reported
success said I haven't picked up anything" -- a real gap in the original
design, not a regression. The mod's `collect_result`/`collect_progress`
fired the instant a block was destroyed or an entity killed, which is
*not* the same moment as the resulting item actually landing in
inventory (a dropped item takes a tick or more to spawn and be walked
over/picked up). The original design conflated those two moments, so a
"got 1/10 stone" progress message could -- and did -- fire before any
stone was actually in the bot's inventory.

Chasing that report surfaced a second, more fundamental question the
user asked directly: given that Python must now watch real inventory
counts to confirm a collect actually produced something, does the mod
still need to be the thing that loops N times at all? Decided no --
**the counted loop moved entirely to Python, and the mod's own `collect`
command is now a single atomic attempt** (find nearest match, walk to
it, break/kill it once, report `collect_result` either way, back to
IDLE). `ControlState` lost `collectTotal`/`collectRemaining` entirely;
`collectQuery`/`collectRadius`/`collectHasTarget`/
`collectTargetStuckTicks`/`collectExcludedPositions` are all that's left,
scoped to one attempt. `tickCollect` no longer has a "loop exit" check at
all -- it completes (or fails) and clears back to `IDLE` after exactly
one item, same shape `tickDigDown`'s per-block loop never had to begin
with.

**Why moving the count to Python is also what makes `!collect` properly
interruptible at the granularity that matters.** Before this, a single
mod-side `collect` command already represented the *entire* requested
count -- interrupting it (a new chat command) could only ever cancel
"the whole run of N", never "just this one item, resume differently".
With the mod doing one item per command, each individual attempt is
small and atomic enough that a superseded chat command naturally
interrupts "walking to this one block" rather than "the 6th of 10
total" -- consistent with the same interruptibility goal "Chat commands
are now interruptible" above already established for the run loop as a
whole.

**Python's counted loop (`MiningController.collect`, `minebot/bot/
mining.py`) confirms a real inventory gain per attempt, not the mod's
own destroyed/killed signal.** Each iteration: send one single-item
`collect`, await `collect_result`; if it reports success, snapshot the
expected-drop item counts *before* the attempt (`InventoryTracker.
count_of`) and wait up to `DROP_CONFIRMATION_TIMEOUT` (5s) for any of
them to actually rise (`_wait_for_any_gain`, via `InventoryTracker.
add_change_listener`/`remove_change_listener` -- a plain callback list,
not tied to any one consumer, so this and the independent
`InventoryAnnouncer` below can both react to the same underlying signal
without knowing about each other). A destroyed/killed target that never
produces the expected drop within the timeout (real vanilla randomness --
a cow can drop 0 leather, an enderman doesn't always drop a pearl) counts
as an empty attempt and retries the same target type without advancing
the requested count, up to `MAX_CONSECUTIVE_EMPTY_DROPS` (10) before
giving up entirely on genuinely unlucky RNG.

Deliberately *not* using `InventoryTracker.gained_items()` for this
confirmation -- that method's 2-snapshot window (`_previous`/`_current`,
see "!give redesigned" above) is real but too narrow here: an unrelated
inventory broadcast landing between the confirmed pickup and whenever
`_wait_for_any_gain` next checks (e.g. a hotbar tool-switch for the
*next* attempt, itself an `inventory` event) can evict the pickup's
"gained" status before this code ever sees it, even though the item is
still sitting right there in inventory -- exactly the failure mode the
live "!give ... haven't picked up anything" report traced back to.
Snapshotting `count_of(item)` directly before the attempt and comparing
against the live count afterward has no such window: it's a direct
comparison against a value this code chose itself, immune to whatever
else changes inventory state in between.

**Resolving what to collect: `query` (drops_from/source_for) and
`DropTable`.** The user's own framing: "if I pickup a beef, check if
beef can come from cows... this is also good for when I want cobblestone,
since naturally there is no cobblestone [block or mob]... use this
dictionary to search where can we get cobblestone and we will see that
can come from stone." `collect(query, count)` first resolves `query`
through `source_for` (asking the mod "what should I actually go
collect to eventually get this" -- `source_for("cobblestone")` ->
`"stone"`), then asks `drops_from` on the *resolved* target for the list
of items to watch for the drop-confirmation step above (`drops_from("stone")`
-> `["cobblestone"]`; note this can legitimately differ from the original
query, which is exactly the cobblestone/stone case). Both are generic
`query`/`query_result` round trips (see the wire-format section above),
correlated by a client-generated UUID key -- genuinely necessary here
(unlike `collect`/`dig_down`, which only ever have one attempt in flight
at a time by construction) since nothing stops two unrelated queries
from overlapping in principle, so `MiningController._pending_queries` is
a dict keyed by that id, not a single-slot future the way
`_pending_dig_down`/`_pending_collect_result` are.

**`DropTable.java` (mod side) is a hardcoded stopgap, not real loot-table
data -- and that gap is a known, deliberate limitation, not an oversight.**
Investigated real loot-table resolution first: `Block.getLootTable()`
only returns a `ResourceKey<LootTable>` *reference*; actually resolving
it to real drop entries requires `level.getServer().reloadableRegistries()`,
which doesn't exist on `ClientLevel` at all -- there is no server
instance to ask on the client side of a real multiplayer connection, so
this is an architectural wall, not a missing API call to go find. Per
explicit instruction ("add a comment that we need to get this somehow
and re-evaluate"), `DropTable` documents this gap prominently in its own
docstring rather than silently shipping a permanent-feeling hardcoded
table. It provides two hand-maintained maps: `DROPS_FROM` (block/entity
-> its real items, e.g. `stone`->`cobblestone`, `cow`->`[beef,leather]`)
and a reverse `SOURCE_FOR` built from it automatically (`buildReverseMap`,
`putIfAbsent` so the first block/entity registered for a given item wins
ties, e.g. multiple ores could theoretically drop the same item).
`dropsFrom`/`sourceFor` both fall back to treating the argument as its
own answer (identity function) when it's not in the map at all, so an
unrecognized query still produces a sane, actionable single-item result
rather than an empty one.

**`ItemDropTracker.java` (mod side, new) gives real ground-truth
visibility into dropped items**, mirroring `broadcastEntityEvents`'s own
add/remove diffing pattern but scanning `ClientLevel.entitiesForRendering()`
filtered to `ItemEntity` instead of players. Broadcasts `item_drop`
add/remove events (see wire-format section above) -- currently
*unconsumed* on the Python side; `MiningController`'s drop confirmation
reads `InventoryTracker` directly rather than cross-referencing sightings
of the dropped item itself landing/vanishing on the ground. This was a
deliberate scope call, not an oversight: watching real inventory counts
already answers the only question `!collect` actually needs ("did I get
one"), and a future feature that cares about ground items specifically
(e.g. "go pick up that one" without having caused the drop) has a real
event stream ready to consume without any mod-side work needed first.

**`InventoryAnnouncer` (Python side, new, `minebot/bot/
inventory_announcer.py`) replaces per-command progress messages with an
independent, command-agnostic observer.** Explicit user redesign
request: rather than the mod (or `MiningController`) deciding when to
say "got N/total X" tied to a specific command's progress, the backend
now announces *any* real inventory gain to chat ("I got a stone (12
total)") purely by reacting to `InventoryTracker`'s own change signal --
whether the gain came from mining, killing something, being given an
item, or anything else, with no notion of "what command is currently
running" at all. Registers itself via `add_change_listener` in
`main.py`, not otherwise referenced afterward (the same "construct it,
let it wire itself up" shape `RespawnHandler`-equivalents use mod-side).
The very first connection's snapshot legitimately announces the bot's
*entire* starting inventory at once (see `InventoryTracker`'s own
docstring on why there's nothing to diff against yet at that point) --
accepted as an unavoidable one-time side effect of establishing a
baseline, not a bug.

**`InventoryTracker` gained a proper multi-consumer change-listener
mechanism** (`add_change_listener`/`remove_change_listener`, a plain
`list[Callable[[], None]]`) specifically because two independent
consumers now need the same "inventory just changed" signal --
`MiningController`'s short-lived per-attempt wait and `InventoryAnnouncer`'s
permanent chat-announcement listener -- without either one owning or
routing through the other. Callers that add a short-lived listener (only
`MiningController` does today) must remove it once done waiting, in a
`try`/`finally` around the wait itself, or the list would grow unbounded
across a long-running process; `remove_change_listener` is a plain
`list.remove`, so removing something never added would raise, same
resource-cleanup discipline as anything else in this codebase.

**`InventoryReporter`'s wire event shape is unchanged** by any of the
above -- this section's changes are purely about who consumes the
existing `inventory` event and how "did the count actually go up" gets
confirmed, not about the event itself.

## NearbyPlayerLookAt was fighting BlockBreaker's aim while mining

Reported live, immediately after the tool-selection fix was confirmed
working: mining visibly slowed down whenever a nearby player walked
close to the bot. Root cause: `NearbyPlayerLookAt`'s override in
`MinebotMod.onClientTick` was gated on `intent.yaw == null` alone (see
its own original docstring, "only applied when resolveMovementIntent
didn't already set a yaw for this tick") -- but `BlockBreaker.aimAt`
sets yaw/pitch **directly on the player** (`player.setYRot`/`setXRot`),
never through `MovementIntent` at all, so `resolveMovementIntent` still
reported `yaw == null` on every single tick spent mining, and the
look-at-nearby-player override fired right after, undoing `aimAt`'s work
the instant someone got close. `BlockState.getDestroyProgress` only
accumulates on ticks the target is actually being looked at/swung at, so
this directly slowed down (or effectively paused) mining progress
whenever the check kept re-triggering.

Requested fix, and the one implemented: gate the override on
`ControlState.mode == Mode.IDLE` instead of the yaw-was-set signal --
`ControlState.mode` is the actual "is there a current goal" tracker
already used everywhere else in this file (`GOTO`/`FOLLOW`/`GIVE`/
`DIG_DOWN`/`COLLECT` all count as "not idle", regardless of whether
that specific tick happens to be setting yaw via `MovementIntent` or,
like mining, directly). This is a real behavior change beyond just
mining: the bot no longer glances at a nearby player while genuinely
paused mid-`FOLLOW`/`GOTO` (e.g. standing at `stopDistance` from a
target) either -- confirmed as the intended scope in the request itself
("when the follow ends, now is idle, and look to players should work
anyways"), i.e. auto-look correctly resumes the moment `ControlState.
clear()` returns the mode to `IDLE`, with no special-casing needed since
that's the exact same field either way.

## InventoryReporter's change detection was doing a JSON round-trip every tick just to compare

Investigating the `!give`-after-`!collect` "haven't picked up anything"
report (see the debug-logging addition just above), the added logging
made a second, unrelated issue obvious: `InventoryReporter.maybeBroadcast`
built a full `JsonObject` (allocating a `JsonArray` plus one `JsonObject`
per carried item) and serialized it to a string via `.toString()`,
*every single client tick*, purely to `.equals()` that string against
the previous tick's -- i.e. using JSON serialization as the mechanism
for "did anything change", when JSON is a wire-transfer format, not a
comparison primitive. Flagged directly: "json is not for this, is for
serialization... using json is wasteful."

Fixed by replacing the string-diff with a real structural comparison:
`InventoryReporter` now builds a lightweight `Snapshot` record (a
`selectedSlot` int plus a `List<SlotEntry>`, `SlotEntry` itself a record
of `slot`/`item`/`count`/`damage`/`maxDamage`) directly from the live
`Inventory` each tick -- no Gson involved at all in this path -- and
compares that against the previous tick's `Snapshot` via Java records'
free structural `equals()`/`hashCode()`. The actual `JsonObject`/
`JsonArray` construction (`buildEvent`) now only ever runs when a
broadcast is actually about to be sent (i.e. at most once per *change*,
not once per *tick*), the same "only pay for it when it's actually
needed" shape the rest of this class already uses for the broadcast
itself.

Wire event shape is completely unchanged (same `slot`/`item`/`count`/
`damage`/`max_damage` field names, same conditional inclusion of
`damage`/`max_damage` only for a damaged item) -- confirmed against
`InventoryTracker.handle_event`'s own parsing on the Python side, which
reads those exact keys and needed no changes. `Snapshot`'s entry list
preserves slot order (iterating `0..getContainerSize()-1` same as
before), so a pure slot-position swap (e.g. `InventoryActions.
moveToHotbar`'s tool-switch swap, same item/count/damage, just a
different index) still correctly compares unequal -- unlike a hash-based
approach, which risks (however unlikely) a collision silently treating a
real change as no change; exact structural equality on a small,
already-filtered (empty slots skipped) list is just as cheap and
strictly correct.

## The chat self-echo loop

The mod's `ClientReceiveMessageEvents.CHAT` listener hears the bot's
*own* chat messages the same as any other player's (they go through the
normal server chat broadcast) -- discovered live when a command's own
error reply ("something went wrong running !find") got re-parsed as a
fresh `!find` with no arguments, which itself errored and replied again,
forever. Fixed by having `position` events also report the bot's own
account name (`SelfPositionTracker.own_name`, sourced from
`player.getScoreboardName()`), and `run_loop.py`'s chat handling ignores
any chat event whose `sender` matches it.

Note: this server also appears to render the `<username> ` prefix into
the message text itself (`message.getString()` returns
`"<riterite> !find cow"`, not just `"!find cow"`, alongside a separately-
correct `sender: "riterite"` field) -- harmless for the current parser
(`!name` is found by regex search anywhere in the string), but worth
knowing about if a future change ever assumes `text` is just the raw
command with no prefix.

## Chat command grammar simplified to bare args only

`actions/parser.py` used to accept two forms: `!name("arg1", 2)`
(parenthesized, mirroring mindcraft's own parser) and `!name arg1 arg2`
(bare space-separated, this project's own addition for how a human
actually types in chat). The parenthesized form was dropped entirely --
a chat-typing human never used it, and it only added grammar surface (a
more complex regex, `_ARG_RE`, an "which form is this" branch) with zero
benefit once the bare form covered every real use. `!name` is now just a
plain `!(\w+)` regex followed by the same bare-arg tokenizer as before;
quoted tokens (`"multi word"`) are still supported within the bare form
for a single arg containing spaces.

## Missing-required-argument errors are now caught before dispatch

`ActionRegistry.dispatch_chat`/`dispatch_tool_call` used to call the
handler directly and rely on catching whatever `Exception` fell out --
a missing required argument (e.g. `!find` with no query, `!give` when
its old signature required an item) crashed as a bare Python `TypeError`
from argument binding, reported only as a generic "something went wrong
running !X" with no indication of *what* was wrong. Both dispatch paths
now check `Action.params`' `required` flags against what was actually
given *before* calling the handler, and report exactly which argument(s)
are missing (`"!find needs query -- see !help find"`) instead.

## !give redesigned: can give back the last picked-up item, not just a chosen one

Originally `give(player_name, item, count=1)` -- a general-purpose "hand
this specific item to this specific player" command. Extended (not
replaced) to `give(item=None, player_name=None)`: `!give <item>
[player_name]` still works exactly as before, but `item` is now
optional -- omitting it drops back whatever the bot most recently
*gained* instead (a live report of the bot auto-picking up items and
slowly hoarding them prompted this -- a fast "give back what you just
grabbed" alongside the general form, not a replacement for it). A single
bare arg is ambiguous (item or player?), resolved the same way `!find`
resolves entity-vs-block: try it as a currently-visible player name
first, fall back to treating it as an item name if it isn't one. No
player given at all resolves to whoever's currently closest
(`InventoryController._closest_player`, needs `SelfPositionTracker`,
threaded into `InventoryController`'s constructor).

`InventoryTracker` gained `gained_items()`/`last_gained_item`: there's no
dedicated pickup event anywhere (checked -- no Fabric API hook exists
for real item pickups specifically, since they happen server-side and
the client only ever observes the resulting inventory change,
indistinguishable at that point from crafting/trading/being given
something). Approximated instead via two named pointers, `_previous`/
`_current` (each a per-item-total dict), updated on every `inventory`
event as `_previous = _current; _current = <new totals>` -- `gained_items()`
compares exactly these two snapshots on demand, returning every item
whose total is higher in `_current` than in `_previous`.

This replaced an earlier, buggier version that picked whichever item's
count increased *the most* as a tiebreak -- found live: the bot already
carried a large stack of golden carrots, was given a single phantom
membrane, and "biggest increase" mistakenly attributed the pre-existing
(unchanged) carrot stack as the latest gain, since its raw size
outranked the freshly-received membrane. The real bug wasn't same-tick
ambiguity (a single client tick is atomic enough that two unrelated
inventory changes essentially never land in the same broadcast) -- it
was that "biggest increase" is simply the wrong question; the right one
is "which item's count is different at all between exactly these two
snapshots," with no magnitude comparison. If more than one item
genuinely does increase between the same two snapshots, `!give` reports
the ambiguity explicitly and asks the player to specify which one
(`!give <item>`) rather than silently guessing again.

Also fixed as part of this: `InventoryReporter.maybeBroadcast` used to
dedupe by comparing the *serialized JSON string* against the last one
sent -- workable for "should I send this," but conflated serialization
with change-detection. `forceNextBroadcast()` (called from
`onControlChannelConnected`) now makes the mod send one full snapshot
immediately on every fresh connection, regardless of whether it matches
whatever was last sent to a *previous* (now-gone) connection -- without
this, a freshly (re)started Python backend had no way to learn the bot's
already-carried inventory until something *changed* after it connected,
during which `InventoryTracker` (and so `gained_items()`) reads as
carrying nothing at all.

## Repo layout (Python side, `master`)

- `minebot/bridge/client.py` -- `ModBridge`: the WebSocket connection to
  the mod, `events()` async-iterates parsed `ModEvent`s, `send_goto`/
  `send_follow`/`send_stop`/`send_chat`/`send_move_to_hotbar`/
  `send_equip`/`send_drop`/`send_give` for commands.
- `minebot/bridge/entities.py` -- `EntityTracker`: id/name -> position,
  fed purely from the mod's own `entity` events (no packet parsing).
- `minebot/bridge/inventory.py` -- `InventoryTracker`: mirrors the bot's
  own inventory, fed purely from the mod's `inventory` events. Unlike
  `EntityTracker` (incremental add/move/remove), each `inventory` event is
  a *full snapshot*, so `handle_event` just replaces state wholesale
  rather than reconciling diffs. `find_by_item`/`count_of` resolve a bare
  registry id (e.g. `"minecraft:bread"`) to a slot/total count.
## Action layer: one definition, usable as a chat command or an LLM tool

Every capability (follow/stop/inventory/equip/drop/give, and whatever
mining/placing/combat actions get added later) is declared *once* as an
`Action` (`minebot/actions/types.py`: name, description, typed `params`,
and an async `handler`) and registered into an `ActionRegistry`
(`minebot/actions/registry.py`). The registry offers two dispatch paths
into the same handler:

- `dispatch_chat(message, sender)` -- the chat grammar
  (`minebot/actions/parser.py`), positional args parsed from chat text.
  Supports both `!name("arg", 2)` (parenthesized, mirrors mindcraft's
  own grammar) and bare space-separated args, `!name arg1 arg2` -- the
  latter added after a real live bug: `!follow yayaeue` used to parse as
  zero-arg `!follow` (only the parenthesized form was recognized at
  all), silently discarding the given name so `!follow yayaeue` followed
  the chat *sender* instead of `yayaeue` with no error or indication
  anything was wrong. The bare form consumes simple word/number/bool
  tokens up to the next `!` or end of message (so trailing free-form
  chat gets captured as extra args too, rather than silently dropped --
  a fixed-arity handler will just error on the extra count, which is
  more honest than silently ignoring part of what the player typed).
  Returns `None` if the text wasn't a recognized command at all (vs. an
  `ActionResult` if a command ran), so callers can tell "not a command"
  apart from "a command ran and had nothing to say".
- `dispatch_tool_call(name, sender, **kwargs)` -- structured keyword
  args, for an LLM's tool-call arguments. Always returns an
  `ActionResult`, never `None` (the caller already knows `name` is a real
  tool it chose to call).

Handlers no longer reach for `ModBridge.send_chat` themselves to report
outcomes -- they return an `ActionResult(message=...)` instead (`message`
`None` means nothing worth saying). This is the piece that makes the same
handler usable from either caller: `run_loop.py` sends a chat-path
result's message to chat itself; `LLMController` does the same after a
tool call, so the model's tool-call loop sees a plain string result
rather than a side-effect chat message it has no way to observe.

`minebot/bot/movement.py` (`MovementController` -- `follow`/`stop`) and
`minebot/bot/inventory.py` (`InventoryController` -- `inventory`/`equip`/
`drop`/`give`) both register their actions this way now instead of
calling `registry.register(name, handler)` directly.  `!follow` with no
name follows the chat sender; `!give` requires an explicit player name
and item (no sender-as-default-recipient magic the way `!follow` has,
since "give to whoever's talking" isn't as safe a default as "follow
whoever's talking"). Items are named by bare id in chat (`"bread"`),
normalized to `"minecraft:bread"` to match `InventoryTracker`/the mod's
`inventory` events (already-namespaced ids pass through unchanged).
All of follow/stop/equip/drop/give now reply with a chat confirmation
("ok, following Alex", "ok, dropped 5x bread") on success too, not just
on failure -- previously a successful command was silent, giving no
feedback that it actually took effect.

`MovementController` also fixes a real live bug: minebot-mod's `FOLLOW`
goal is pinned to a fixed entity id (`ControlState.followEntityId`), but
a player who disconnects and reconnects gets a brand-new entity id on
the mod side -- `level.getEntity(oldId)` then never resolves again, so
the bot silently stood idle forever after the target rejoined, with no
error, until a human retyped `!follow`. `MovementController` now
remembers the *name* it's following (`_following_name`), not just the id
it last sent, and exposes `on_entity_added(name, entity_id)`; `run_loop.
run` calls this for every mod `entity` "add" event, and if the
reappearing name matches who's being followed, re-sends `follow` with
the fresh id automatically. `stop()` clears the remembered name so a
later reconnect of the same player doesn't unexpectedly resume
following them. Entirely a Python-side fix -- the mod already sent a
correct fresh `add` event with the right name on rejoin, Python just
wasn't listening for it to resume the goal.

`MovementController` also has `remember`/`goto` (see `PENDING.md` for
the full mindcraft-command-parity gap analysis this came out of --
tracks every mindcraft command, what's implemented here, and a
`**Want it?**` review line per pending item). `goto <target> [y z]`
resolves in order: explicit x y z coordinates (all three given as
numbers) -> a known player's current position (`EntityTracker`) -> a
remembered place -> gives up with a clear "I don't know where that is"
reply. `remember <name>` saves the bot's current position (read from
the new `minebot/bridge/self_position.py`'s `SelfPositionTracker`,
fed from the mod's per-tick `position` events -- nothing tracked the
bot's own live position before this) under a name in
`minebot/places.py`'s `PlaceMemory`, a small JSON-backed store
(`places.json`, gitignored -- the first persistence layer in this
project) so remembered places survive a backend restart. Both actions
were scoped deliberately narrow for their first pass: block-type and
entity-type resolution (`!goto stone`, `!goto cow`) aren't implemented,
since neither block-scanning nor entity-type-widening (the mod only
tracks *players*, not mobs/animals) exists on the mod side yet --
`goto`'s handler is structured so adding those later is just two more
resolution branches, no interface change needed. No mod-side changes
were needed for `goto`/`remember` at all -- the mod's existing one-shot
`GOTO` goal (`ControlState.setGoto`) already accepts arbitrary
coordinates.

## LLM trigger + brain layer (structure built, no provider wired up yet)

`minebot/llm/trigger.py` -- `should_trigger_llm(text, sender, bot_name,
trigger_words=())`: deliberately narrow, fires only when the bot's own
name *or* one of a configurable list of extra trigger words/phrases
(`MINEBOT_TRIGGER_WORDS`, comma-separated -- a nickname, a catch-all like
"hey bot", etc.) is mentioned (case-insensitive substring) in a message
with a real sender (not a system/game message). Ordinary chat between
other players is ignored, so the bot isn't calling out to a model on
every unrelated line. A real whisper/DM signal would be a stronger
trigger than a name/word mention, but the control channel doesn't carry
that distinction yet -- see Known gaps.

`minebot/llm/controller.py` -- `LLMController.handle_chat(sender, text)`:
calls an `LLMProvider` (a `Protocol`, not a concrete class) with the
conversation turn plus `registry.list_actions()`, gets back an
`LLMResponse` (optional `reply` text, plus any `LLMToolCall`s the model
made), executes each tool call through `ActionRegistry.dispatch_tool_call`
and sends its result message to chat if it has one, then sends the
model's own `reply` last. No real provider (Anthropic/OpenAI/etc.) is
wired up yet -- `NullLLMProvider` is the default and always declines,
so this plumbing is fully usable/testable today without an API key; a
real integration is just implementing `LLMProvider.respond` and building
that provider's own tool-schema format from the same `Action` list
(deliberately provider-agnostic for exactly this reason).

`minebot/bot/run_loop.py`'s chat handling order: try `dispatch_chat`
first (an actual `!command` always wins), then fall back to
`should_trigger_llm` for anything else. The mod side needed **no**
changes for this -- `MinebotMod.broadcastChatEvent` already forwards
every chat message unconditionally; the "does this deserve a response"
decision entirely lives in the Python backend, matching the design intent
(no brain logic on the mod side).

- `minebot/config.py` -- `MINEBOT_MOD_HOST`/`MINEBOT_MOD_PORT` (default
  `0.0.0.0:47893` -- Python binds as the server now; see the
  WSL2-networking note above) plus `MINEBOT_BOT_NAME` (default
  `"minebot"`) and `MINEBOT_TRIGGER_WORDS` (comma-separated, default
  empty), both used by the LLM trigger check above.
  `run_loop.run()` takes the whole `BotConfig`, not individual fields --
  threading each config value through as its own positional param got
  fragile once there were two, and there will likely be more (LLM
  provider/model selection, etc.) as the LLM piece grows.
- `minebot/logging_setup.py` -- `configure_logging(level)`: console
  output plus a timestamped file per run under `logs/<datetime>.log`
  (gitignored). Needs `logging.basicConfig(..., force=True)` to actually
  reconfigure if called more than once in the same process (its default
  no-op-if-handlers-already-exist behavior otherwise silently keeps a
  stale configuration -- caught by a real test failure where pytest's own
  log-capture plugin had already attached a root handler before either
  test in `test_logging_setup.py` ran).
- `minebot/mod_version.py` -- `check_hello(commit, built_at,
  mod_repo_path)`: logs the connected mod's reported build, and if
  `mod_repo_path` (config field `mod_repo_path`, env
  `MINEBOT_MOD_REPO_PATH`, defaults to the sibling `minebot-mod` repo
  path used throughout development) resolves to a real git repo,
  compares its current `git rev-parse HEAD` against the mod's reported
  commit (stripping a trailing `-dirty` suffix first) and logs a loud
  WARNING on mismatch. Called from `run_loop.run` on the mod's `hello`
  event. This is the fix for the stale-jar-deploy trap documented
  above and in `AGENTS.md` -- previously that failure mode was silent
  and required manually diffing jar files to diagnose.
- `minebot/main.py` -- wires it all together: configure logging, connect
  the bridge, build the `ActionRegistry`/trackers/movement+inventory
  controllers, build an `LLMController` (no provider configured), run
  the loop.

Deleted from `master` (fully preserved on `pure-protocol-backend`):
`minebot/protocol/` (packet parsing, chunk/block-registry, chat/NBT),
`minebot/net/` (raw TCP connection + wire-format primitives),
`minebot/auth/` (the full MSA device-code -> Xbox -> XSTS -> Mojang
online-mode login chain), `minebot/pathfinding/` (A* port of
mineflayer-pathfinder), `minebot/physics/` (prismarine-physics port),
`minebot/bot/play_loop.py`, the old `minebot/bot/movement.py` (physics-
based follow), `tools/extract_packet_ids.py`, and all their tests. None of
this is needed anymore since the mod is the thing that actually speaks the
protocol and runs physics now.

## Mining / digging: real dig-through-obstacles pathfinding

`!dig` and `!collect` (see `PENDING.md`'s "Mining / digging" section
for the full ask) close the biggest remaining gap called out repeatedly
elsewhere in this file: no mining/digging existed on the mod side at all,
and `Movements.java`'s A* cost model had zero dig-cost branches (walk/
climb/parkour moves only). This was implemented as the more ambitious of
two options the user was offered -- not just "walk to a block and break
it" but a real dig-cost A* branch, so the bot can tunnel through a wall to
reach a target with no walkable route to it, mirroring
`mineflayer-pathfinder`'s own `digCost`-gated move branches
(`lib/movements.js`, read in full as the porting source -- confirmed
against the actual npm package at `/home/colaila/git/mineflayer-
pathfinder`, pinned to the `2.4.5` tag).

**Real block-breaking is a multi-tick sequence, not a single API call --
the same lesson `FoodEater`'s `keyUse`-hold discovery already taught for
eating.** Confirmed by reading the decompiled `MultiPlayerGameMode.java`:
`destroyBlock(pos)` only actually removes a block; the real sequence a
human player's client runs every tick they hold left-click is
`startDestroyBlock` once, then `continueDestroyBlock` every following
tick (`Minecraft.continueAttack`'s own decompiled source, called from
`handleKeybinds`, confirmed to run every tick and only call
`player.swing()` when `continueDestroyBlock` itself returns `true` --
`BlockBreaker.tryBreak` mirrors this exactly, including the swing-only-
on-progress detail). `continueDestroyBlock` accumulates real per-tick
progress via `BlockState.getDestroyProgress(player, level, pos)` until it
reaches 1.0, at which point it calls `destroyBlock` itself. Unlike the
`FoodEater` eat-completion mystery (a direct `useItem()` call silently
never worked, for reasons that stayed unexplained even after six
bytecode-reading passes -- see "Known gaps" below), this one *is* fully
explained: `destroyBlock`/`startDestroyBlock`/`continueDestroyBlock` are
explicitly a three-part stateful sequence by design (see their own
decompiled bodies), not a single-call API that a naive caller might
mistakenly expect to just work -- there was no mystery to solve here, just
the real sequence to follow.

**No hand-rolled hardness/tool-effectiveness table needed.**
`mineflayer-pathfinder`'s own `safeOrBreak` (`movements.js:282-299`) has
to estimate `digTime` from a registry dump plus `bestHarvestTool` lookup,
because mineflayer has no real client to ask. This mod does:
`BlockState.getDestroyProgress(player, level, pos)` already folds in held-
tool speed, enchantments, and status effects (confirmed via
`BlockBehaviour.getDestroyProgress`'s decompiled source: `player.
getDestroySpeed(state) / destroySpeed / modifier`) -- the exact same call
`continueDestroyBlock` itself uses for real per-tick mining. `Movements.
safeOrBreak` (the new dig-cost port, mirroring `movements.js`'s function
of the same name) just asks the real game and derives an analogous
labor-cost formula from real per-tick progress instead of an estimated
`digTime`. `-1.0F` from `BlockState.getDestroySpeed` is the real
"can never be destroyed" sentinel (bedrock, barrier, ...), confirmed via
the same decompiled source -- no separate unbreakable-block list needed
beyond that check plus an explicit chest exclusion (breaking a container
mid-path would spill its contents as a side effect of routing, the same
reason `movements.js` explicitly excludes chests via its own
`blocksCantBreak` set).

**Scope deliberately excludes block placement.** `movements.js`'s dig
branches are paired with placement branches (`toPlace`, `remainingBlocks`,
scaffolding-item tracking) for filling gaps the bot can't otherwise
cross. This port keeps only the dig half -- a dig-only bot can always get
strictly further than the old walk-only one (breaking a wall it
previously couldn't pass) without ever needing to bridge a gap by placing
blocks under itself, and placement is its own separately-pending feature
(`!place`) with real prerequisites of its own (a "place this item against
that face" interaction, hotbar-aware scaffolding-item selection).
`Move.java` gained a `toBreak: List<BlockPos>` field (default empty, one
per move that requires digging through something) but no `toPlace`
counterpart.

**`BlockBreaker.java`** (new, `minebot/mod/pathfinding/`) is the
`DoorOpener`-shaped stateful helper that actually drives the sequence
above -- `tryBreak(player, level, pos)` starts or continues a break,
returns `true` once the block is actually gone. Also does **auto-tool-
switching**: before starting a *new* target (never mid-break on the same
one), scans main storage + hotbar for the item with the highest real
`ItemStack.getDestroySpeed(state)` against the target block and hotbar-
swaps to it (reusing `InventoryActions.moveToHotbar`'s existing local-
state-swap idiom) if it beats what's currently held. The "never mid-
break" constraint is load-bearing, not just tidiness: `MultiPlayerGameMode
.sameDestroyTarget`'s decompiled source compares the held `ItemStack`
against the one destroy progress started with (`ItemStack.
isSameItemSameComponents`), so a tool swap mid-break would silently reset
progress back to zero -- confirmed from reading that check, not
discovered live, but worth watching for in live testing regardless (see
"Known gaps").

Three separate `BlockBreaker` instances exist on `MinebotMod`
(`pathBlockBreaker`, `digDownBreaker`, `collectBreaker`) rather than one
shared one -- each tracks exactly one in-progress break at a time, and
pathfinding-through-a-wall, `!dig`, and `!collect` can all have a
*different* target block in play depending on what's currently running,
so sharing one instance would let one mode's break silently clobber
another's progress the moment they targeted different blocks.

**`Movements.java`'s dig-cost port** threads a `LocalPlayer` into its
constructor (previously just `ClientLevel`) so `safeOrBreak` can call the
real `getDestroyProgress`. Every move function that previously called
`safeOrBlocked` (a block is either free or `BLOCKED`, no in-between) now
calls the new `safeOrBreak` instead, mirroring exactly which functions
`movements.js` itself gates on `safeOrBreak` vs. leaves as hard blocks:
`getMoveForward`, `getMoveJumpUp` (only its B/H checks -- its
placement-needed C/D branch stays a hard block, out of scope per above),
`getMoveDiagonal`, `getMoveDropDown`, `getMoveDown`, `getMoveUp` (its B2
check). `getMoveParkourForward` is untouched -- `movements.js` doesn't
dig there either, a parkour move is only ever taken through already-safe
blocks.

**Pathfinding execution**: `MinebotMod.maybeBreakBlocksNear` (called from
`resolveMovementIntent`, same "does nothing most ticks" shape as
`doorOpener.maybeOpenDoorNear`) checks the current waypoint's `toBreak`
list and drives `pathBlockBreaker` against the first still-solid block in
it. While a required break is in progress, `resolveMovementIntent` holds
off setting `intent.forward`/`intent.yaw` toward that waypoint (a
`blockedByDig` flag) -- collision would mostly prevent walking into a
still-solid block anyway, but this keeps the aim `BlockBreaker` just set
from being fought by movement's own yaw-toward-waypoint logic.

**`!dig`** (`ControlState.Mode.DIG_DOWN`, `MinebotMod.tickDigDown`) is
deliberately *not* routed through the pathfinder at all -- it's a
stationary "break the block directly below me, repeat" loop with no
horizontal movement, matching `PENDING.md`'s explicit ask ("dig straight
down N blocks, stopping at lava/water/a big drop", mirroring mindcraft's
`skills.digDown`). Checks the block immediately below for a fluid before
digging (abort, `reason: "hit lava/water"`), and after each successful
break, scans up to `DIG_DOWN_DROP_SAFETY_MARGIN` (3) blocks further down
for a floor or liquid -- finding neither means a genuine "big drop"
(abort, `reason: "big drop ahead"`). Reports how many blocks were
actually broken either way via `dig_down_result`.

**`!collect`** (`ControlState.Mode.COLLECT`, `MinebotMod.tickCollect`) is
the universal block-or-entity collector the user asked for ("`!collect
stone 10` will collect 10 stone blocks, and `!collect cow 5` will collect
5 cows"): resolves `query` the same entity-then-block order `!find`
already established (`resolveNearest`, factored out of `runFind` so both
share one implementation), walks to each match via the normal GOTO-style
pathfinding (COLLECT is a `resolveMovementIntent` target-resolution case
alongside GOTO/FOLLOW/GIVE, reusing `gotoX/Y/Z` for a block target or
`followEntityId` for an entity one, `collectTargetIsEntity` distinguishing
which), then either mines it (`BlockBreaker`) or fights it once in melee
range. The entity-kill path is deliberately minimal -- repeated
`MultiPlayerGameMode.attack(player, entity)` calls (a real one-shot API,
unlike block-breaking, confirmed via the same decompiled source) until
the target's `isRemoved()` -- not the full `!attack`/`!kill` feature
(nearest-hostile-by-default, standalone command) that's still pending in
`PENDING.md`; that's a real gap, not an oversight, since combat target-
selection/aiming is a materially different problem than "walk up to this
specific already-known entity and hit it."

**Note: the paragraph that originally lived here (`!collect` reporting
per-item progress via a `collect_progress` event, and `MiningController`
looping mod-side against a requested count) describes the *original*
design, since replaced -- see "!collect redesigned: single-item mod
commands, counted loop moved to Python" below for the current
architecture. `collect_progress` no longer exists on the wire at all.**

Both `dig_down_result`/`collect_result`/`query_result` get the exact
same fast-path treatment in `run_loop.py`'s `_read_events` that
`find_result`/`arrived` already established (see "The find_result
deadlock" above) -- `dig_down()`/`collect()`/the query helpers suspend
awaiting a future only one of these events resolves, so the same
deadlock risk applies and the same fix pattern closes it.
`tests/test_run_loop.py::test_run_loop_delivers_dig_down_result_without_deadlocking`
and `::test_run_loop_delivers_collect_result_without_deadlocking` are the
regression tests, mirroring `FindReplyBridge`'s real request/response-
causality shape (a reply is only yielded once the triggering command was
actually sent, not from a plain canned event list).

## Jump-height cost penalty: a marginal jump shouldn't cost the same as an easy one

Reported live (unrelated to the dig-cost work above -- this was a
pre-existing gap in the original walk-only A* port): the bot sometimes
picked a step-up/jump move right at the edge of what `MAX_STEP_HEIGHT`
(1.2 blocks) allows over a longer but easy walk-around, then got stuck
repeatedly jumping in place instead of making progress. Root cause: every
step-up move (`getMoveJumpUp`, `getMoveDiagonal`'s stepping-up case,
`getMoveParkourForward`'s forward-and-up case) costed a fixed amount
regardless of *how tall* the step actually was, as long as it stayed
under the 1.2 cap -- a comfortable 1.0-block step (real vanilla jump
height works out to ~1.25 blocks continuous rise from `LivingEntity.
BASE_JUMP_POWER = 0.42F`, so a 1-block step clears with real margin to
spare) and a marginal 1.19-block step were completely cost-equivalent to
A*, even though the marginal one has far less room for error in actual
execution (exact timing, block-edge alignment, forward momentum needed
simultaneously) and is much more likely to fail and leave the bot stuck
bouncing against the ledge.

Fixed with `Movements.jumpHeightPenalty(heightDiff)`: 0 extra cost up to
`JUMP_HEIGHT_COMFORTABLE` (1.0 -- a plain full-block step), then scales
linearly up to `JUMP_HEIGHT_PENALTY_MAX` (6.0, chosen to comfortably
outweigh a several-block walk-around) right at `MAX_STEP_HEIGHT` itself.
`MAX_STEP_HEIGHT` stays as the hard reject cutoff (a jump that tall or
taller is never offered as a move at all, unchanged) -- this only changes
the *relative* cost of jumps already under that cap, so A* now prefers an
easy step or a walk-around whenever one exists, and only takes a marginal
jump when there's genuinely no cheaper route. Applied everywhere a
step-up height is computed: `getMoveJumpUp`'s own step, `getMoveDiagonal`'s
`y == 1` stepping-up branch, and `getMoveParkourForward`'s forward-and-up
branch (the last one is strictly harder to land than a plain step-up of
the same height, needing horizontal momentum and vertical clearance at
once, so it gets the same penalty on top of its own flat move cost).

Deliberately *not* applied to `getMoveDiagonal`'s `cost1`/`cost2` checks
at `blockD1.height() - block0.height() > MAX_STEP_HEIGHT` -- those decide
whether a *side* block needs digging through, not the diagonal move's own
landing height, a different concern from the one this penalty targets.

## The real fix: moveToHotbar now uses a real container-click swap, found by reading a working third-party mod's source directly

Rather than continuing to debug the desync in isolation, the user
pointed at **AutoTools** (`/home/colaila/git/mods/AutoTools`), a real,
published, working auto-tool-switching mod, and asked to compare its
implementation against ours directly. This found the actual gap
immediately.

**AutoTools' `selectItem` (`AutoTools.java`) does exactly the same
"bring an item from some slot into the hotbar" operation `InventoryActions.
moveToHotbar` does, but via a materially different mechanism for the
main-storage case**: for an item already in the hotbar (0-8), it just
calls `inventory.setSelectedSlot(sourceSlot)` -- no swap needed, the
item's already reachable. But for an item in main storage (9-35), it
calls `client.gameMode.handleContainerInput(containerId, sourceSlot,
destSlot, ContainerInput.SWAP, player)` -- a real container click. Our
own `moveToHotbar` never did this; it always did a direct `Inventory.
setItem`/`setItem` swap for both cases, with no packet at all beyond
whatever `MultiPlayerGameMode.ensureHasSentCarriedItem` separately
synced (just the *selected index*, never the actual slot *contents*
swap).

Confirmed via decompiled source exactly why this matters:
`MultiPlayerGameMode.handleContainerInput` does two things, not one --
it runs `containerMenu.clicked(...)` locally (the same kind of
prediction our direct mutation already achieved) **and** sends a real
`ServerboundContainerClickPacket` reporting every slot that actually
changed. The old `moveToHotbar` only ever did the local half. This is
exactly the missing piece the extensive live A/B test (see "the real
cause was InventoryActions.moveToHotbar's local-only swap genuinely
desyncing the server's view of the held item" below) already pointed
at, now with a concrete, working reference implementation confirming
both the diagnosis and the fix.

**Fix**: `InventoryActions.moveToHotbar` now matches AutoTools'
`selectItem` exactly -- a same-hotbar move is a plain `setSelectedSlot`
(no swap needed), a main-storage move goes through
`handleContainerInput`/`ContainerInput.SWAP` with the same slot-index
split AutoTools uses (`SWAP`'s `slotNum` needs the container-menu
+36 hotbar shift `mainInventorySlotToContainerSlot` already applies
elsewhere in this class; its `buttonNum`, which hotbar slot to swap
*into*, is a raw 0-8 `Inventory` index, confirmed via decompiled
`AbstractContainerMenu.clicked`).

**`FoodEater.selectSlot`'s own main-storage branch had the exact same
bug** (a direct `getItem`/`setItem` swap, added when the earlier
`pickSlot` bug was fixed -- see below) -- rather than duplicating the
now-correct swap logic a second time, `FoodEater` now just calls
`InventoryActions.moveToHotbar` directly (reusing hotbar slot 8, same
slot `BlockBreaker`'s own tool-switch already reuses), removing the
duplicated private `selectSlot` method entirely.

**Confirmed live**: "it works" -- the container-click-based `moveToHotbar`
eliminates the desync. This closes the entire investigation that started
with "!collect cobblestone 1 reports success but nothing ever drops":
the keyAttack-hold mining mechanism, completion detection, settle
window, and tool-switch timing were all already correct; the actual
remaining bug was specifically `moveToHotbar`'s missing
`ServerboundContainerClickPacket`, and switching to a real container
click (the same mechanism a working third-party mod, AutoTools, already
uses for the identical operation) fixed it completely. `!collect`/`!dig`/
pathfinding's tool-switching all go through this same `moveToHotbar`
call, so this fix applies to every mining path, not just the isolated
`!debug` test.

## !collect now actively walks to the dropped item, and a real drop-confirmation namespace bug found along the way

Reported live, after several successful `!collect cobblestone` runs:
"the bot mine the cobblestone, but makes no effort in picking the
drop." Correct diagnosis -- `!collect` never had explicit "go get the
item" logic at all. The original design (see "!collect redesigned..."
above) relied entirely on the bot already standing close enough from
mining/killing for vanilla's own pickup radius to grab the drop as an
incidental side effect. That works when the drop lands right underfoot,
but real item physics (a block's drop can roll/bounce a little) or
simply "close enough to mine" not being "close enough to actually walk
over the result" can leave it uncollected on the ground indefinitely,
with no code path that would ever notice or do anything about it.

**Fix**: `!collect` now has a real pickup phase. The moment a target is
destroyed/killed, instead of immediately reporting `collect_result` and
clearing to `IDLE`, `MinebotMod.tickCollect` searches for the nearest
real `ItemEntity` (via `level.entitiesForRendering()`, same live-entity
scan `ItemDropTracker` already uses) matching `DropTable.dropsFrom(query)`
within a small radius, and -- if one exists -- walks to it
(`ControlState.collectPickingUp`, reusing the same `gotoX/Y/Z`/
`pathTracker` machinery every other COLLECT phase already uses, just a
tight `stopDistance` of 0.5 since real vanilla pickup radius is small)
before completing the attempt. The walk re-aims at the item's live
position every tick (it can still be settling/rolling for a moment
after spawning) and gives up after `COLLECT_PICKUP_TIMEOUT_TICKS` (100
ticks, 5s) if it never disappears -- bounded the same way the mining
phase's own stuck-target timeout is, so a drop that rolled somewhere
genuinely unreachable can't stall the whole attempt forever. Either way
(real pickup, timeout, or no drop found at all) `!collect` still
reports success, since the block/entity really was destroyed/killed
regardless of whether this phase actually recovers the resulting item --
Python's own drop-confirmation (`MiningController.collect`, watching
real `InventoryTracker` counts) remains the actual authority on whether
to count it as a real gain, unchanged.

**A real, separate, more fundamental bug found implementing this**:
`DropTable.dropsFrom`/`source_for` answer in bare item ids (`"cobblestone"`,
not `"minecraft:cobblestone"` -- confirmed in `DropTable`'s own map
literals), sent over the wire unchanged. `MiningController.collect`
(Python side) was comparing these bare ids directly against
`InventoryTracker.count_of`, which only ever stores fully-namespaced
ids (`"minecraft:cobblestone"`) -- the exact same convention
`InventoryController`'s own `_normalize_item_id` already exists to
paper over elsewhere in this codebase, just never applied here. This
means `!collect`'s own drop-confirmation was comparing two values that
could **never** structurally match, regardless of whether a real drop
actually landed -- every single attempt's "did the count go up" check
was silently guaranteed to see "no gain." (Any earlier "!collect
succeeded" reports this session most plausibly happened via
`consecutive_empty_drops` never actually reaching `MAX_CONSECUTIVE_EMPTY_DROPS`
before the attempt's own 30s `COLLECT_ATTEMPT_TIMEOUT` or some other
incidental factor, not genuine drop confirmation -- not fully traced,
but the namespace mismatch itself is unambiguous from reading both
sides' code directly.) Fixed by normalizing `expected_drops` to full
`minecraft:<id>` form the moment they're received from the
`drops_from` query, before ever being compared against inventory
counts -- the same one-line normalization `_normalize_item_id`
already established as this codebase's convention.

`tests/test_mining_controller.py`'s own `_inventory_event` helper had
the identical latent bug (built bare-id inventory snapshots), which is
exactly why this went unnoticed by the test suite -- both sides of the
broken comparison used bare ids in tests, so they "matched" for the
wrong reason. Fixed to build realistic namespaced snapshots, matching
what `InventoryReporter` actually sends over the wire; all 4 tests that
depend on drop-confirmation actually completing now correctly require
the namespace-normalized comparison to work, not coincidentally pass
around it.

Not yet re-confirmed live -- both fixes (the pickup-walk phase and the
namespace normalization) are deployed together, ready for the next
`!collect` repro; worth watching specifically for the item actually
disappearing from the ground (not just the count changing) as
confirmation the walk-over is real, not just luck from standing close
enough already.

## maybeBreakBlocksNear could leave a stale target (and its gizmo) frozen forever if the waypoint changed out from under it

Live-confirmed the gizmo itself works ("I can see is targeted in
cyan") -- and immediately surfaced a real, different bug via exactly
what it was built to make visible: "now u are in front a dirt block, I
can see is targeted in cyan, but bot is not mining it", with zero
`mining[pathfinding]` log output at all for a stretch spanning that
report. `pathBlockBreaker.currentTarget` was still set (hence the
persistent cyan highlight), but nothing was calling `tryBreak` on it
anymore.

Root cause, in `maybeBreakBlocksNear`: this method has no memory of
what it was digging *last* tick, only what the *current* `waypoint`
says needs digging *this* tick. If the current waypoint changes (a
replan, or simply advancing to the next waypoint in the existing path)
such that `pathBlockBreaker`'s still-active target position is no
longer anywhere in the new waypoint's `toBreak` list, this method never
notices -- it just evaluates the new waypoint from scratch, and if that
new waypoint needs no digging at all (`toBreak` empty or null waypoint),
falls straight to `return pathBlockBreaker.isBusy()` without ever
calling `tryBreak` or `stopBreaking()` again for the orphaned target.
`isBusy()` then stays `true` forever (nothing left to ever clear it --
same root shape as the `hasActiveTarget()` deadlock class fixed earlier
in this file, here triggered by the *waypoint* moving on rather than a
tool switch), which keeps `resolveMovementIntent`'s `blockedByDig`
gate held, freezing all movement, while the gizmo keeps rendering a
target the bot had already, silently, stopped trying to reach.

Fixed: `maybeBreakBlocksNear` now explicitly checks, every tick, whether
`pathBlockBreaker`'s active target (if any) is still present in the
*current* waypoint's `toBreak` list -- if not, it calls `stopBreaking()`
on it directly (clearing `currentTarget`, releasing `keyAttack`, and so
also clearing the gizmo highlight) before falling through to the normal
per-tick logic. This is a real, structural gap distinct from the
`STUCK_TICKS_LIMIT` timeout added just above -- that one guards against
a target that's still genuinely being dug but never completes; this one
guards against a target that stopped being relevant entirely, which
would otherwise never even reach the stuck-ticks counter (it doesn't
time out, it just sits there, "busy" but untouched, forever).

Not yet re-confirmed live -- deployed in direct response to the report
above, ready for the next repro (which should now either show the
correct fresh target highlighted, or no highlight at all once
pathfinding genuinely has nothing left to dig).

## Pathfinding's dig-through-a-wall had no stuck-target timeout at all -- BlockBreaker now enforces one universally

Follow-up question after the stuck-`!collect` report above, and a good
one: pathfinding's whole dig-through-obstacles feature exists
specifically so an unreachable-by-walking target gets a real route dug
to it -- so why didn't that happen here? Checked the log directly: it
*did* -- `mining[pathfinding]` shows A* correctly finding a route
requiring a dig, and immediately starting to mine the blocking
`grass_block`. The bug was one level deeper: that dig held `keyAttack`
against the same block for 601 ticks (30 real seconds) straight, with
steady real `getDestroyProgress` accumulating every tick, in range,
with line of sight -- and never once completed, timed out, or gave up.
Cross-referencing `position` events pinned down what actually happened:
the bot fell (real gravity, ~2 blocks, `y: 87 -> 85`) in the first
handful of ticks of the dig, then sat perfectly still at the new
position for the remaining ~590 ticks, digging a target that stayed
in-range/visible from the new spot but apparently never actually
finished breaking -- the *exact* real cause of the stall past that point
wasn't fully pinned down (plausibly an aim/`hitResult` interpolation
edge case while the bot's rotation/position were still settling
immediately after the fall, similar in spirit to but distinct from the
tool-switch-reset and one-tick-late-completion bugs already found and
fixed earlier in this file), and pursuing it further wasn't the
practical fix.

**The real gap this exposed: `!collect`/`!dig` already have their own
give-up-after-N-ticks logic** (`ControlState.collectTargetStuckTicks`/
`COLLECT_TARGET_TIMEOUT_TICKS`, and digDown's analogous handling) **but
pathfinding's own `maybeBreakBlocksNear` had no equivalent at all** --
nothing in the pathfinding path was ever going to notice a stuck dig and
abandon it, regardless of the underlying cause. Fixed at the source
instead of in yet another caller-specific mechanism: `BlockBreaker`
itself now enforces `STUCK_TICKS_LIMIT` (1000 ticks, ~50s -- deliberately
well above the 601-tick failure that prompted this, generous enough
that no legitimately slow real break should ever trip it) universally,
inside `tryBreak` -- once a single target has been actively held that
long without completing, it releases `keyAttack`, clears its own
`currentTarget`, and returns `false` (a genuine give-up, not a
completion), regardless of which of the three callers (`!collect`,
`!dig`, pathfinding) is driving it. This means every current and future
caller of `BlockBreaker` gets this safety net automatically, rather
than needing to reimplement its own stuck-detection the way `!collect`/
`!dig` already had to.

Once a pathfinding dig gives up this way, `maybeBreakBlocksNear`'s own
`isBusy()` naturally returns false again, movement resumes, and
`PathTracker.maybeReplan`'s existing `selfDrift`/`targetMoved` checks
take over to find a different route on the bot's next real move --
no new coordination code needed between the two.

Not yet re-confirmed live -- the fix is deployed and ready for the next
repro of a stuck pathfinding dig; the new `BlockTargetVisualizer` gizmo
(directly below) should also make a stuck dig like this immediately
visible rather than needing log archaeology to diagnose.

## BlockTargetVisualizer: live cube-outline gizmo around whatever's actively being mined

Confirmed live: `!collect cobblestone 1` succeeded end-to-end after the
`moveToHotbar` fix (real cobblestone drop, "I got a cobblestone (7
total)") -- the whole mining/drop investigation is genuinely closed.
Immediately after, `!collect cobblestone 2` surfaced a real, separate
bug: stuck reporting `out of range` forever at a frozen distance
(5.38 blocks, `INTERACT_RANGE` is 4.5), the bot never moving any closer
-- "now bot is stuck... looking at grass_block, probably the stone
block it targeted is below it." Confirmed by mod log: `BlockFinder`'s
pure-distance search found a real stone block, but nothing ever walked
the bot within reach of it (very plausibly the same class of issue the
`maxDropDown` fix earlier in this file addresses -- a target only
reachable via a route the pathfinder now correctly refuses as unsafe,
with no fallback route found).

Requested directly: a live debug visualization -- highlight the
currently-targeted block with a rendered cube outline, referencing how
`VillagerHelperMod`'s own `PoiRenderer` does exactly this (`Gizmos.
cuboid(pos, GizmoStyle...)`, `.setAlwaysOnTop()` so it's visible through
terrain) for villager POI markers. This mod already has the exact same
infrastructure in place -- `PathVisualizer.java` already registers
`LevelRenderEvents.BEFORE_GIZMOS` and uses `Gizmos.line`/`Gizmos.point`
for the planned-path overlay -- so this is a direct extension of an
already-proven pattern, not new plumbing.

`BlockTargetVisualizer.java` (new) draws a stroke-only cube outline
around whichever `BlockPos` each of the three `BlockBreaker` instances
(`pathBlockBreaker`/`digDownBreaker`/`collectBreaker`) currently has
active (`BlockBreaker.currentTarget()`, a new public getter for the
previously-private field), color-coded per instance (cyan/orange/
magenta, matching each instance's own `label` used for log attribution)
so overlapping goals stay distinguishable. Registered alongside
`PathVisualizer` in `MinebotMod.onInitializeClient`. Purely a debugging
aid -- gizmos render only to this client's own local framebuffer, never
networked, matching `PathVisualizer`'s own docstring on why that's fine
for a bot-only debug overlay.

Not yet used live to diagnose the actual stuck-target bug above --
deployed in the same pass as the request, ready for the next repro.

## !debug repurposed: now isolates moveToHotbar itself, no mining involved

Follow-up to the desync finding directly below -- `!debug` no longer
breaks a block at all (that mechanism is confirmed working; see the
closed investigation below). It's now a narrower, purpose-built test:
find the diamond pickaxe wherever it is, shift-click it out of the
hotbar into main storage first if needed (`InventoryActions.
moveToMainStorage`, a real `ServerboundContainerClickPacket` -- the same
mechanism `equip` already uses, not a direct mutation), then call
`InventoryActions.moveToHotbar` to bring it into hotbar slot 0 and
select it -- the exact, single mechanism under suspicion, with every
other moving part (mining, pathfinding, `!collect`'s retry loop)
removed from the picture entirely. `ControlState.Mode.DEBUG_BREAK` and
the old `tickDebugBreak`/raycast-from-another-player's-eyes machinery
were removed along with the old behavior (no longer needed -- this new
test is a single instant action, not something that needs per-tick
continuation). `!debug` now maps to `debug_swap_test` on the wire
(`{"type":"debug_swap_test"}`, no arguments needed), handled instantly
in `MinebotMod.runDebugSwapTest` off `withPlayer` rather than through
`ControlState`/the tick loop at all.

The actual test isn't verifiable by the mod alone -- there's no way for
this client to observe what another client renders. The log records
every step's local state (where the pickaxe started, whether/where it
landed after the shift-click, what `moveToHotbar` did); confirming
whether the desync reproduces requires a human watching the bot from a
second account, same as the original live investigation.

## Investigation closed: cobblestone drops confirmed working end-to-end -- the real cause was InventoryActions.moveToHotbar's local-only swap genuinely desyncing the server's view of the held item

**Confirmed live, finally, after the settle-window-double-call fix
directly below**: `!debug` on a real stone block completed cleanly in
one pass -- real per-tick `getDestroyProgress` (0.178, diamond pickaxe
speed), a single clean settle cycle with no flicker/re-trigger, and a
genuine `item_drop` event for `minecraft:cobblestone` landing at the
block's exact position the instant it completed. "now it works!" --
first fully successful, fully verified tool-gated break-and-drop of the
entire investigation.

**What actually explains every single symptom from this whole
investigation, confirmed by an extensive live A/B test the user ran
personally**: the visual "bot appears to be holding wheat_seeds" report
that recurred throughout this investigation (see "the server was still
using the pre-switch tool..." below) was never a rendering artifact --
it was a real desync between the client's local `Inventory` state and
what the server (and therefore any other client, including a human
observer) actually believed the bot was holding. Proven directly: the
user physically took over the bot's own game window and pressed real
hotbar number keys themselves (bypassing this mod's own `moveToHotbar`
call entirely) -- and the desync still happened, in *both* directions
(the bot's own window showing a pickaxe while the human's client showed
seeds, and vice versa, across several repeated tests pressing different
slots). The desync only stopped once the user manually rearranged the
bot's inventory (dragging items in the inventory screen -- a real,
container-click-synced action) so the pickaxe was already sitting in
the slot about to be selected, rather than being hotbar-swapped into
place by `moveToHotbar`'s direct `Inventory.setItem` mutation
immediately beforehand. Their own live hypothesis, confirmed by this
result: *"maybe this was caused by inventory management? swapping items
caused this?"* -- yes. `moveToHotbar`'s local-only swap (see its own
docstring: `getItem`/`setItem` directly, not a container click) can
leave the *server's* copy of two slots' contents disagreeing with the
client's, for long enough that whatever destroy/attack packet follows
soon after resolves against the server's still-stale belief about which
item is selected -- exactly the class of bug the one-tick delay below
was a partial, narrower mitigation for (accounting for timing, but not
for the swap itself being unreliable in the first place).

**This is now a known, real, open gap in `InventoryActions.moveToHotbar`**
worth a proper fix in its own right (a real container-click-based
hotbar swap, matching `equip`'s own already-correct
`handleContainerInput`-based approach, rather than the current direct
local mutation) -- out of scope for this investigation's immediate goal
(confirming the keyAttack-hold mining mechanism works end-to-end, which
it now demonstrably does), but the next real target for anyone picking
up mining reliability work. The keyAttack-hold approach, the settle
window, the completion-detection fix, and the tool-switch-delay
mitigation are all confirmed correct and load-bearing -- this final
piece narrows the remaining risk down to specifically
`moveToHotbar`'s own sync reliability, not anything about how breaks
are driven or observed.

## The one-tick tool-switch delay reintroduced the exact deadlock class it was meant to avoid, in three of four callers

Live-tested immediately after deploying the one-tick tool-switch delay
(directly below): `!debug` got stuck with zero further log output right
after `"switched tool this tick, waiting one tick before holding
keyAttack..."` -- the deliberate one-tick skip never actually resumed.
Root cause, found by re-reading every caller's own gating logic against
the new "tryBreak can return false while leaving a real, in-progress
target behind" case that skip introduced: `!debug`, `!dig`, and
`!collect` all gated *whether to call `tryBreak` at all* behind
`BlockBreaker.isBusy()` -- and `isBusy()` is `true` for *both* "purely
settling, no active target" (safe to skip -- `tickSettle` handles that
case on its own) and "actively targeting something, tryBreak needs to
be called again to make progress" (never safe to skip -- `tryBreak` is
the *only* thing that ever advances a break: aims, holds `keyAttack`,
and notices completion). The moment a tool switch left `tryBreak`
returning `false` with a real target still set, `isBusy()` was already
`true` from that instant on, so all three callers stopped calling
`tryBreak` again -- and since nothing else could ever call it either,
the break could never actually start mining, let alone finish. A
structural deadlock, same class (though a different trigger) as the
"every caller's own gating prevented BlockBreaker from ever noticing"
bug fixed just above -- this was reintroduced by the very next change,
because that earlier fix only accounted for tryBreak *completing*
without being called again, not tryBreak *declining to progress at all*
without being called again.

**Fix**: added `BlockBreaker.hasActiveTarget()` (`currentTarget != null`,
distinct from the broader `isBusy()`) -- every caller's "should I skip
calling tryBreak this tick" check now requires *both* `isBusy()` AND
`!hasActiveTarget()`, so settling-with-no-target is still skippable, but
an active target (mid-break, including the deliberate one-tick
tool-switch pause) always keeps getting `tryBreak` called on it every
tick until it either completes or genuinely needs to stop. Pathfinding's
own `maybeBreakBlocksNear` needed no change -- its loop already re-checks
live block state directly (not `isBusy()`) to decide whether to call
`tryBreak` again, so it was never vulnerable to this particular
deadlock shape.

Not yet re-confirmed live -- found and fixed in direct response to the
very next `!debug` repro after deploying the tool-switch delay, in the
same session.

## The real drop mystery, finally solved: the server was still using the pre-switch tool when a break completed on the same tick as a hotbar swap

With the completion-detection deadlock fixed (see directly below), a
clean `!debug` repro finally showed a break completing correctly --
`tickSettle` observed it, settling ran, `debug_break: ... finished
settling -- done` -- with real diamond-pickaxe-speed
`getDestroyProgress` throughout. Still no drop. This time the player
watching supplied the decisive missing piece directly: *"the human
window sees the bot hitting the stone with a wheat seed... I suppose the
server also sees the bot is holding a wheat seed... if a block is broken
with that item, stone drops nothing... but there is a drop when breaking
dirt"* -- dirt drops regardless of what's in hand, stone requires a
pickaxe specifically, so a server that still believed the bot was
holding `wheat_seeds` at the moment of completion explains every single
observation from this entire investigation at once: real breaks, real
per-tick progress, real completions, zero drops, specifically and only
for tool-gated blocks.

**Root cause**: `InventoryActions.moveToHotbar` (used by `BlockBreaker.
maybeSwitchToBestTool` to swap in the best tool for a fresh target) is a
*local-only* `Inventory` mutation (`getItem`/`setItem`/`setSelectedSlot`
directly, confirmed in that method's own docstring) -- not itself a
server-synced action. The actual sync happens separately and
automatically, via `MultiPlayerGameMode.ensureHasSentCarriedItem`
(confirmed via decompiled source: called from `continueDestroyBlock`
and `tick()`, compares the live selected-slot index against its own
`carriedIndex` cache and sends a fresh `ServerboundSetCarriedItemPacket`
whenever they differ). This is a real, correct mechanism in isolation --
but `tryBreak`'s tool-switch fix earlier in this investigation (see
"BlockBreaker now enforces a settle window..." above) made the ordering
hazard concrete: a fresh target's tool switch and the very first
`holdAttackKey()`/`handleKeybinds()`-driven destroy packet both fire on
the *same* tick, back to back, with zero guarantee the server has
actually *processed* the carried-item sync packet before it receives
whatever destroy-related packet follows moments later over the same
connection. `sameDestroyTarget`'s real loot-table-relevant "what item is
this player holding" state lives entirely server-side and is exactly
the kind of state a race like this can leave stale for at least one
round trip -- explaining a client that locally believes (and logs,
correctly) `mainHand=diamond_pickaxe` the whole time, while the actual
break server-side still resolves against whatever was selected a moment
before.

**Fix**: `maybeSwitchToBestTool` now reports whether it actually
performed a switch; when it does, `tryBreak` deliberately skips calling
`aimAt`/`holdAttackKey` (and so `handleKeybinds` has nothing to act on)
for that exact tick, returning `false` without touching the keybind at
all -- `currentTarget` is still set to the new position, so the very
next tick's call correctly sees `isNewTarget == false` and proceeds
straight to actually mining, one tick after the carried-item sync packet
went out instead of the same tick. A one-tick real-world delay (50ms) is
a negligible cost against the alternative of every tool-gated block
silently dropping nothing forever.

Not yet re-confirmed live -- this was found and fixed in the same
session as the completion-detection deadlock fix, and the two changes
haven't been tested together against a fresh repro yet. Next `!debug`
test on a tool-gated block (stone, ore, etc.) should watch specifically
for a real `inventory` broadcast showing the mined item's count
actually rising after the break completes -- the first time in this
entire investigation all three pieces (real completion, real tool held
server-side, real settle time before anything else touches state) would
be correct simultaneously.

## The actual final fix: a break's real completion is only ever observed one tick late, and every caller's own gating prevented BlockBreaker from ever noticing

With `!debug` now correctly raycasting from the real chat sender's eyes
(see directly below) and locked onto a fixed target, a clean repro
finally showed the true remaining bug in full, unambiguous detail --
real, steady `getDestroyProgress` accumulation for several ticks with a
real diamond pickaxe, immediately followed by `already air on arrival
(was targeting X)`, immediately followed by `new target (was null)` for
that exact same block, forever, in a tight loop -- while real
`item_drop` events for the mined material *were* landing (confirmed via
wire log in the same session). The block was **genuinely breaking every
single cycle** -- this was never a drop problem, a client-prediction
problem, or a `sameDestroyTarget` reset problem. `BlockBreaker` itself
simply never learned that its own break had completed.

**Root cause, finally fully understood**: `BlockBreaker.tryBreak`'s
`state.isAir()` check at the top of the method was written under the old
mental model (this class directly drove `continueDestroyBlock` itself,
so a same-call observation of the solid->air transition was the normal
case, and "isAir already at entry" genuinely meant "something else
removed it before I ever touched it this call"). That model stopped
being true the moment this class switched to holding `keyAttack` and
letting vanilla's own `Minecraft.handleKeybinds()` drive the actual
destroy sequence (see the keyAttack-hold section above) --
`handleKeybinds()` always runs *earlier* in the same client tick than
this mod's own tick hook (`ClientTickEvents.END_CLIENT_TICK` fires at
the *end* of the tick). So a break that completes on tick N does so
entirely inside `handleKeybinds()`, before this mod's code runs at all
that tick -- by the time `tryBreak` (or anything else) next checks
`state.isAir()`, the transition has *already* happened, every single
time, with no exceptions. The old "return true from the `!stillThere`
check further down `tryBreak`, after actually holding the key this
call" branch could therefore never fire for a real completion anymore --
it was checking for something that had already become structurally
impossible to observe within a single call. Every genuine success was
landing in the *top* `isAir()` branch instead, which still had its old,
now-wrong meaning ("something unrelated must have removed this") and
discarded it as a non-event.

**First half of the fix**: teach the top `isAir()` branch to tell the
two cases apart -- if `currentTarget` still equals `pos` (i.e., this
exact instance was still actively holding `keyAttack` against exactly
this position as of the last call), the block turning to air between
calls *is* the real completion, one tick late, not an unrelated
disappearance. This alone fixed direct re-entry into `tryBreak` for the
same target -- but exposed a second, independent bug.

**Second half -- a genuine deadlock, found by re-tracing what actually
calls `tryBreak` on a completing tick.** All three real callers
(`!collect`'s `tickCollectBlock`, `!dig`'s `tickDigDown`, pathfinding's
`maybeBreakBlocksNear`) stop calling `tryBreak` again the moment they
see the target block report air -- `tickCollectBlock` short-circuits
into its own "abandon" branch before ever reaching `BlockBreaker` at
all; pathfinding's `toBreak` loop simply stops finding a still-solid
block to pass in; `!dig` gates entry behind `isBusy()` before calling
`tryBreak` at all. But `tryBreak` was the *only* method that ever
cleared `currentTarget` back to null -- so once no caller calls it
again, `currentTarget` stays set forever, `isBusy()` never returns
false, and nothing downstream (settling, `!collect`'s success reporting,
`!dig`'s next-block progression, pathfinding's resumed movement) can
ever proceed. A textbook deadlock: the one piece of state that unblocks
everything else can only be updated by a call every caller's own logic
was specifically designed to stop making once that state looked like it
needed updating.

**Fixed by moving the completion check into `tickSettle`** (renamed to
take a `ClientLevel` parameter) -- already called unconditionally, every
tick, by all three real callers (needed regardless, to count down the
settle window in real time) -- so completion detection no longer depends
on any caller choosing to call `tryBreak` again. `tickSettle` now checks
`currentTarget`'s live block state itself and performs the same
attribution/cleanup `tryBreak`'s own branch does (clear `currentTarget`,
start the settle window, record `lastCompletedTarget`, release
`keyAttack`) independent of whether `tryBreak` runs that tick at all.
`tryBreak`'s own `isAir()` branch keeps its fix too, as a second path to
the same outcome for a caller that does happen to call it again before
`tickSettle` gets there. `tickDebugBreak` gained its own `tickSettle`
call (it had none before, since `!debug` was written expecting
`tryBreak`'s return value alone to signal completion) plus a
`justFinishedSettling` check at the top of the method, matching the
shape `!collect` already had.

Confirmed this closes the loop conceptually (every caller's `isBusy()`/
`justFinishedSettling` now has a real, unconditional path to becoming
accurate every tick, regardless of whatever else that caller's own logic
decides to do) -- not yet re-confirmed against a fresh live repro, since
this was found and fixed in the same investigation session as the
`!debug` eyes-raycast fix below. Next test should watch for: `!debug`
actually reaching "done, clearing back to IDLE" (never observed once,
across every repro so far), and `!collect cobblestone 1` actually
reporting `collected 1/1` with a confirmed inventory-count increase.

## !debug raycasts from the chat sender's own eyes now, not the bot's crosshair

Reported live, immediately after the target-locking fix below: "seems
like !debug has no way to know where I am looking at?" -- exactly
right, and confirmed by re-running it: yaw/pitch frozen, `hitResult`
stuck at `MISS` for a full minute again, identical to the very first
`!debug` test. `!debug`'s raycast source was always `Minecraft.
getInstance().hitResult` -- the *bot's own client's* crosshair, which
only updates in response to real mouse input landing on the *bot's own,
usually-unfocused* game window. A player typing `!debug` from their own
separate window (the normal way this bot is actually operated -- see
"two more bugs found chasing it live" above, the human plays on a
`riterite` window, the bot runs unattended on its own) has no
connection to that at all; the bot's crosshair just sits wherever it
was last left.

Fixed by raycasting from the *chat sender's* own eyes instead --
`ControlState.debugLooker` carries the sender's name (Python's
`debug_break` action now requires a real `sender`, threaded through
`send_debug_break(looker)` over the wire as `{"type":"debug_break","looker":".."}`),
resolved to a real `Entity` via `level.players()` (the same lookup
`NearbyPlayerLookAt` already uses to find nearby players) and raycast
from there using `Level.clip(ClipContext)` -- the exact same real
primitive `BlockBreaker.hasLineOfSight` already uses, just from another
player's `getEyePosition()`/`getViewVector()` instead of the bot's own.
Both are ordinary, real, server-synced entity state (yaw/pitch/position
for *any* visible entity, not something private to whichever client
happens to be running the mod), so this works for any player the bot
can currently see, not just itself. `!debug` still locks onto whatever
block it first resolves and holds that exact position regardless of
further movement (see the target-locking fix directly below) -- this
change only replaces *whose* line of sight the initial raycast comes
from.

## The real root cause, finally confirmed: keyAttack-hold works fine -- BlockBreaker's completion check can miss a real break if the target changes on the exact tick it finishes

After the entire investigation above (client-prediction-vs-server-
authoritative theories, the settle window, tool-switch-only-on-new-target,
the oscillation/stuck-key fixes), a minimal isolated test finally pinned
down what was actually happening -- built specifically to rule everything
else out: `!debug` (`ControlState.Mode.DEBUG_BREAK`, `MinebotMod.
tickDebugBreak`), a one-shot command that breaks whatever block the
bot's own crosshair (`Minecraft.getInstance().hitResult`) is pointed at,
with heavy per-tick logging and zero pathfinding/`!collect` search/retry
logic in the way at all.

**First `!debug` test (bot's window not receiving real mouse input):**
yaw/pitch stayed frozen at the exact same value for a full minute
straight, `hitResult` was `MISS` (or, once something moved nearby, an
entity) the entire time -- never a real block. This confirmed
`Minecraft.pick()`'s per-frame raycast genuinely depends on the window
receiving real input to update anything at all (not literally mouse-grab
as first suspected -- see "two more bugs found chasing it live" above,
that theory turned out incomplete) -- with nothing moving the camera,
there was nothing new to hit test against.

**Second `!debug` test (mouse actually moved on the bot's own window):**
real mining, real `item_drop` events, real drops landing in inventory --
confirmed via wire log (`item_drop` add events for `minecraft:dirt`
appearing right where the bot was aimed). **This proves the keyAttack-hold
mechanism itself was correct all along** -- every earlier theory blaming
it (client-side prediction vs. server-authoritative completion,
`sameDestroyTarget` resets from tool-switching, the settle window) was
chasing a symptom of something else.

**What was actually still wrong, found by reading this successful run's
own log closely**: despite real drops landing, the log never once
contained a "broke it" completion line for `mining[debug]`. Instead,
`debug_break: targeting X -- new target (was null)` kept appearing
repeatedly for the same real-world block over a span of seconds, and
`already air on arrival (was targeting X)` showed up for a target that
had clearly just been mining normally (steady, real
`getDestroyProgress` accumulating tick over tick right beforehand).
Root cause: `!debug`'s first version re-derived its target block from
the live crosshair *every single tick*, with no locking -- harmless
while the human held perfectly still, but a live human's mouse is never
perfectly still. On the exact tick a break actually finished
server-side, if the crosshair had drifted even slightly (e.g. from one
face of a block to a neighboring one, or the mutable `BlockPos` `hitResult`
itself hands back happened to differ), `tickDebugBreak` computed a
*different* `pos` for that tick than the one that had actually just
completed. `BlockBreaker.tryBreak(newPos)` then found `newPos` already
air (true -- it *was* just broken, but under the *previous* pos value)
and took the "already air on arrival" branch -- which correctly refuses
to double-count a stale success, exactly as designed, but this meant the
*real* completion for the original target was never observed by
`tryBreak` at all: no "broke it" log, no `settleTicksRemaining`/
`lastCompletedTarget` set, nothing. The break genuinely succeeded (hence
the real drop), but `BlockBreaker`'s own bookkeeping never learned that.

**This is a design flaw specific to `!debug`'s own "re-derive target
every tick from a live, human-movable crosshair" approach -- not a bug
in `BlockBreaker`, the keyAttack-hold mechanism, or (most importantly)
the three real callers (`!collect`, `!dig`, pathfinding), all of which
already use a *fixed* target position computed once and held constant
until the break resolves, not re-derived from anything that moves
independently tick to tick.** Fixed for `!debug` itself by adding a
`debugTarget` lock: the first tick a real `BLOCK` hitResult is seen after
`!debug` starts, that exact position is captured (immutably copied, since
`hitResult`'s `BlockPos` can be a reused mutable instance) and reused for
every subsequent tick regardless of where the crosshair drifts
afterward, matching the fixed-target shape the other three callers
already had all along.

**Implication for the original, still-technically-open cobblestone-drop
mystery**: since `!collect`/`!dig`/pathfinding never had `!debug`'s
specific "target changes every tick" flaw to begin with, and the
keyAttack-hold mechanism itself is now confirmed to work correctly (real
break, real drop, via `!debug`), the settle-window and
tool-switch-only-on-new-target fixes made earlier in this investigation
were very plausibly sufficient on their own to fix the real `!collect`
report too -- they just hadn't been re-tested against a live repro since
being deployed, because attention shifted to chasing what turned out to
be `!debug`'s own separate bug. Worth a direct `!collect cobblestone 1`
re-test now that `!debug` itself is fixed and the underlying mechanism
is confirmed sound, rather than assuming either outcome.

## BlockBreaker now enforces a settle window before movement/completion resumes -- separating "walking" from "mining" waypoint behavior

Reported live, mid-pathfinding through a wall requiring a dig: the bot
kept trying to jump/walk while a `toBreak` block was still standing,
and doing so repeatedly interrupted mining -- explicit ask: "we need to
stop moving when mining anything, then we can continue moving, we need
to define waypoint types, like walking and mining. when mining: look at
the block, mine it, then continue to next waypoint" (this also directly
matches the still-open cobblestone-drop mystery from the sections above
-- resuming movement/state immediately on a break's client-predicted
completion was a real, general bug pattern, not specific to pathfinding).

**Root cause: every caller treated a break as fully finished the instant
`tryBreak`'s local `level.getBlockState(pos).isAir()` check went true --
but that's only ever a client *prediction* (see the section above), not
proof the server has actually confirmed the break.** Three separate call
sites all had this same premature-continuation bug:
- `maybeBreakBlocksNear` (pathfinding): `blockedByDig` flipped to `false`
  the instant the target went air, immediately letting
  `resolveMovementIntent`'s forward/jump/yaw resume that same tick.
- `tickCollect` (`!collect`): `collectedThisTick` went `true` the instant
  `tryBreak` returned `true`, immediately clearing `ControlState` back to
  `IDLE` and reporting `collect_result: success` in the same tick the
  block visually vanished.
- `tickDigDown` (`!dig`): `digDownRemaining--` and the next block's break
  attempt both happened immediately, with no gap between one block
  "finishing" and the next one starting.

**Fix: `BlockBreaker` now owns a settle window, not just a single-tick
completion signal.** `SETTLE_TICKS` (6, ~0.3s) starts counting down the
moment a break completes (`tryBreak` sets `settleTicksRemaining` instead
of leaving all state cleared); a new `isBusy()` method reports `true`
while either a break is actively in progress *or* still settling, and a
new `tickSettle()` must be called once per tick (regardless of whether
`tryBreak` itself runs that tick) to actually count the window down.
Every caller was updated to check `isBusy()` before treating a break as
done and resuming whatever comes next -- pathfinding holds movement,
`!dig` holds the next block's break, `!collect` holds its own
success-reporting.

**`!collect` needed one more piece beyond a simple `isBusy()` gate.**
Once settling elapses, `tickCollectBlock` would otherwise re-run its own
`level.getBlockState(pos).isAir()` check -- which is now correctly
`true` (the block really is gone), but that's exactly the same check the
*already-air abandonment* branch uses ("something else must have removed
this, exclude and re-search" -- see the oscillation-bug fix earlier in
this file). Without a way to tell these two "the position is air" cases
apart, a real just-completed break would get misclassified as an
abandoned target the moment settling finished, excluding its own
just-broken position and never reporting success at all. Fixed by adding
`lastCompletedTarget` (the position of the most recent genuine
completion) and `justFinishedSettling(pos)` (true once `!isBusy()` if
that position matches) -- `tickCollect` now checks this *before* ever
re-entering `tickCollectBlock`, so a just-settled success is recognized
directly rather than re-derived from ambiguous live block state.

**This also removes one more concrete way this mod's own immediate next
action (jumping, walking, starting the very next tickCollect/tickDigDown
iteration) could interrupt or interfere with whatever's still settling
server-side right as a break concludes** -- directly relevant to,
though not yet confirmed as fully explaining, the still-open
cobblestone-drop mystery elsewhere in this file. Not yet re-confirmed
live -- next repro should watch for: no more jump/walk interruption
while a pathfinding waypoint is mid-dig, and (still) whether a real
`inventory` broadcast showing cobblestone's count rising finally follows
a `!collect` break.

## The keyAttack-hold fix alone wasn't enough -- two more bugs found chasing it live

The keybind-hold change below (still the right architectural fix, kept)
did not, on its own, resolve the reported "stone reappears, never drops
cobblestone" symptom -- confirmed live immediately after deploying it:
"I can still see mining a block, it immediately reappears, then mine it
again, then disappears forever, no drops, even when using a pick." Two
further, real bugs were found chasing this, both worth fixing regardless
of whether they turn out to be the actual root cause of the drop
mystery:

**Bug: `!collect` could oscillate forever between two already-broken
blocks, never hitting its own stuck-target timeout.** `tickCollectBlock`'s
"target is already air" abandonment path (see "!digDown reported only 1
block..." above for when this check was first added) cleared
`collectHasTarget` and returned, but never added the position to
`ControlState.collectExcludedPositions` -- unlike the sibling give-up
path just below it (the 201-tick stuck-target timeout), which does
exclude. Reported live: the mod log showed the bot alternating between
exactly two coordinates, both reported "already air at start of
tickCollectBlock -- abandoning" on every single visit, forever, with
`collect: new target` firing again within the same second each time --
never enough elapsed ticks for `collectTargetStuckTicks` to reach its
201-tick threshold, since each cycle "succeeded" at finding *a* target
in under a tick, just never one that was actually still solid. Root
cause of *why* the search kept re-finding an already-air position at all
(instead of a fresh live scan naturally excluding it via the block-type
predicate) is a genuine TOCTOU race against `BlockBreaker`'s own
keyAttack hold: a break started against a previous target can land a
tick or more after the search that already moved on, so a position that
was genuinely solid stone at search time can turn to air before
`tickCollectBlock`'s very next check. Fixed by excluding on this path
too, same as the stuck-timeout path already does -- `BlockFinder.
findNearestBlock`/`resolveNearest` are fully deterministic for a fixed
center/predicate, so any known-bad position must be excluded or it just
gets found again.

**Bug: an abandoned already-air target could leave `keyAttack` stuck
held.** The same abandonment path never called `collectBreaker.
stopBreaking()` either -- it short-circuits *before* ever reaching
`BlockBreaker.tryBreak`, so none of `tryBreak`'s own key-release paths
(already-air-on-entry, out-of-range, no-line-of-sight, genuine
completion) ever ran for whatever `collectBreaker` was actually mid-break
on. Since `Options.keyAttack.setDown(true)` is sticky until explicitly
released, this could leave the real attack key held indefinitely,
silently continuing to swing at whatever the crosshair happened to be
resting on for however many ticks passed before some *other* `tryBreak`
call happened to overwrite it. Fixed by calling `collectBreaker.
stopBreaking()` on this path too.

**Mitigation applied (not yet confirmed as the actual root cause) for
the underlying drop mystery**: `BlockBreaker.maybeSwitchToBestTool` used
to run *every tick* of a break, not just when a fresh target starts
(deliberately, to react to `FoodEater` stealing the hotbar mid-break --
see "InventoryActions.moveToHotbar never actually worked..." above).
But `MultiPlayerGameMode.sameDestroyTarget` (confirmed via decompiled
source) requires the held `ItemStack` to stay identical by *full
component comparison* -- not just same item type -- for a destroy
sequence to be considered continuous; any hotbar swap mid-break, even
re-selecting the exact same logical pickaxe, resets real server-side
destroy progress back to zero. A break that's silently reset and
restarted this way, right as it was about to complete, is a concrete,
plausible way for the client's own prediction to show a block gone while
the server's real, independently-tracked progress never actually
reached completion -- which would produce exactly this bug's symptom
(visible break, sometimes appearing to need a second attempt, never a
drop). Restricted `maybeSwitchToBestTool` to only run when
`isNewTarget`, removing this specific mid-break reset risk that this
mod's own code could cause (FoodEater stealing the selection mid-break
remains a real, separate, accepted tradeoff, unchanged).

Not yet confirmed live whether this mitigation actually fixes the drop
issue -- next repro should watch specifically for: (1) no more oscillation
between already-air targets, (2) no attack-key-stuck symptoms, and (3)
whether a real `inventory` broadcast showing cobblestone's count rising
finally follows a break.

## Mined stone never dropped cobblestone -- fixed by holding the real attack keybind instead of calling the destroy-block API directly

**Follow-up to the "open bug" investigation directly below** (kept in
place for the full ruled-out list) -- the wire logging added there
confirmed conclusively, on the very next repro, that no `inventory`
broadcast *or* `item_drop` ground-item sighting ever followed a real
stone break: `collect_result` fired (success), the mod's own
`inventory: broadcasting change` log line simply never appeared again
in the whole window, and `item_drop`'s only nearby-timed event was an
unrelated pre-existing item despawning far away. Not a lost event, not
a tracking bug -- the server genuinely never rolled a drop for these
breaks at all.

**The fix, suggested directly by the user, based on the exact same
precedent `FoodEater` already established for eating**: rather than
continuing to chase *why* `BlockBreaker`'s direct
`MultiPlayerGameMode.startDestroyBlock`/`continueDestroyBlock` calls
differ from a real break server-side, stop calling them directly at
all -- hold the real `keyAttack` keybind
(`Options.keyAttack.setDown(true)`) instead, the same fix that resolved
`FoodEater`'s previously-unexplained `useItem()` mystery (see "Repo
layout (mod side...)" below for that full six-pass investigation).
Vanilla's own `Minecraft.handleKeybinds()` (called every client tick
regardless of this mod, from `Minecraft.tick()`) then drives the real
`startAttack()`/`continueAttack()` sequence exactly as it would for a
human physically holding left-click -- including whatever server-round-
trip nuance the direct API sequence was apparently missing (never fully
identified; `continueDestroyBlock`'s own decompiled logic looked
functionally equivalent to what `continueAttack` itself calls, so this
is the same class of mystery as `FoodEater`'s -- a real, confirmed live
difference between direct-API-call and genuine-input behavior, without
a fully satisfying explanation at the vanilla-source level for *why*
they differ).

This relies on `Minecraft`'s own per-frame crosshair raycast (`hitResult`,
recomputed every render frame from real player yaw/pitch via
`Player.raycastHitResult` -- confirmed via decompiled `Minecraft.pick`)
actually landing on the target block for `handleKeybinds`' held-key
handling to do anything -- satisfied automatically since `BlockBreaker.
aimAt` already sets real yaw/pitch at the target before the key gets
held, the same way a human turning to look at a block before clicking
would. One real ordering subtlety: this mod's own tick hook
(`ClientTickEvents.END_CLIENT_TICK`) fires *after* `handleKeybinds()`
already ran for that same tick, so aiming/holding the key on tick N only
actually gets consumed by `handleKeybinds` on tick N+1 -- a one-tick
lag, not a bug, and the same causality real human input has anyway
(aim this frame, the click registers using that aim next frame).

`BlockBreaker.tryBreak`'s outward contract is completely unchanged
(still returns `true` exactly once, on the tick the block genuinely
goes solid->air) -- only its internal mechanism changed, from directly
driving the destroy sequence to holding the keybind and letting vanilla
drive it. The key is released (`releaseAttackKey`) on every early-return
path (already air, out of range, no line of sight) and on genuine
completion, not just on `stopBreaking()`, so it's never left held while
`tryBreak` isn't actively making progress on something. A new static
`BlockBreaker.releaseAttackKeyIfHeld()` (mirroring `FoodEater.
releaseUseKeyIfHeld`) is called from `MinebotMod`'s death-tick handling
alongside the existing `FoodEater` one, so a break in progress at the
moment of death doesn't leave attack stuck held through death/respawn.

Not yet re-confirmed live after this fix (the wire-logged repro above
is what pinned down the actual symptom this addresses) -- worth a
follow-up `!collect cobblestone 1` test specifically watching for a
real `inventory` broadcast showing cobblestone's count actually rising
after a break, not just `collect_result` reporting success.

## Open bug: mined stone never drops cobblestone into inventory (wire logging added to chase it)

Reported live (`!collect cobblestone 1`): the mod log shows a real,
repeated cycle -- find a stone block, switch to the diamond pickaxe,
break it in ~1 tick (`destroySpeed was 8.0`), report `collect_result`
success, wait 5s (`MiningController.DROP_CONFIRMATION_TIMEOUT`), see no
cobblestone-count increase in any `inventory` broadcast, retry against a
*different* nearby stone block, same outcome -- ten times in a row,
until `MAX_CONSECUTIVE_EMPTY_DROPS` gives up (`collected 0/1 cobblestone
-- kept getting empty drops from stone`). The player watching live added
a detail the logs alone don't show: "I can see you mining a stone, but
the stone immediately reappears, then you mine it again, and it
disappears forever but does not drop, even when I see you having a
pickaxe in hand."

**Ruled out so far:**
- Not a query/resolution bug -- confirmed via the Python log
  (`minebot.mining`): `source_for("cobblestone")` correctly resolved to
  `"stone"`, and `drops_from("stone")` correctly resolved to
  `["cobblestone"]`. The collect loop is watching for exactly the right
  item.
- Not a missing tool-switch -- the mod log shows the diamond pickaxe
  already selected (`mining: keeping slot 8 (minecraft:diamond_pickaxe)
  -- nothing carried beats it`) for every attempt after the first.
- Not `InventoryReporter`'s own change-detection logic -- read closely
  (`Snapshot`/`SlotEntry` record comparison, "InventoryReporter's change
  detection..." section above): it broadcasts on any real count change,
  unconditionally, every tick `MinebotMod.onClientTick` runs
  (`inventoryReporter.maybeBroadcast`, no gating on `ControlState.mode`
  at all). If cobblestone's count had genuinely changed, this would have
  broadcast it. The mod log confirms exactly one `inventory: broadcasting
  change` line in the whole window -- the earlier tool-switch swap, not
  a later pickup -- so the mod itself never observed cobblestone's count
  rise, not just "the event got lost somewhere downstream."
- `BlockBreaker.tryBreak`'s destroy sequence (`continueDestroyBlock`
  every tick, no direct `startDestroyBlock` call) was re-checked against
  the real decompiled `MultiPlayerGameMode` source: `continueDestroyBlock`
  itself calls `startDestroyBlock` internally whenever
  `!sameDestroyTarget(pos)` (i.e., a fresh target), so the missing direct
  call isn't a functional gap -- this matches how a real client's own
  `Minecraft.continueAttack` drives the exact same sequence.

**Not yet confirmed, current leading hypothesis**: `destroyBlock`'s real
removal (`level.setBlock`) happens as a *client-side prediction*
(`startPrediction`/`BlockStatePredictionHandler`, confirmed in the
decompiled source) -- the client removes the block and shows it as gone
immediately, but the actual authoritative removal (and the loot-table
roll that produces a drop) only happens server-side once it processes
the corresponding `ServerboundPlayerActionPacket`. If the server ever
disagrees with the client's prediction (rejects/reverts it for a reason
not yet identified -- possibly related to how rapidly
`BlockBreaker`/`tickCollect`'s per-tick loop re-targets a *new* block
the instant the old one reports broken, with no pause at all), the
client would show the block as destroyed while the server quietly
reverts it and never actually runs a drop roll at all -- which would
also plausibly explain the player's live "the stone reappears" report
(a server-side revert restoring the block, observed by someone actually
watching in real time, is exactly what a rejected prediction looks like
from outside). This is a hypothesis, not a confirmed cause -- nothing
here has actually proven the server is rejecting anything yet.

**Wire-level logging added specifically to chase this further**:
`ControlClient.java`'s `sendEvent` (mod -> Python) and `Client.onMessage`
(Python -> mod) now both log every raw JSON message at `info` (`wire >>
...` / `wire << ...`) -- this client's default log4j config filters
`debug` entirely (confirmed elsewhere in this file), so `info` is the
only level that actually surfaces in a real log. The next live repro of
this bug will have the exact sequence of commands and events on both
sides of the socket, in order, timestamped -- enough to tell apart "the
mod never even tried to report a drop" from "a drop event fired but
something ignored it" without relying on decompiled-source speculation
alone. Not yet used to re-diagnose this specific bug -- added in the
same session the bug was reported, ready for the next repro.

## Jump-spam while mining mid-path, and pitch=0 fighting BlockBreaker's aim while walking

Reported live, during real `!follow`/pathfinding travel through terrain
requiring a dig-through-obstacles waypoint (see "Mining / digging: real
dig-through-obstacles pathfinding" above): "sometimes it can not stop
jumping" while mining a block mid-path, and separately, the bot's pitch
kept snapping level (not looking at the block) while mining resumed
after walking. Both root-caused to the same area of `resolveMovementIntent`
(`MinebotMod.java`) that the earlier "NearbyPlayerLookAt was fighting
BlockBreaker's aim" fix above already partially addressed, but didn't
fully close.

**Bug 1 -- jump wasn't gated on `blockedByDig` at all.** `resolveMovementIntent`
already holds off `intent.forward`/`yaw`/(then-)`pitch` while a waypoint's
`toBreak` list still has a solid block in it (`blockedByDig`, from
`maybeBreakBlocksNear`) -- walking into a block still being dug achieves
nothing. But the jump check right below it (`if (dy > MAX_STEP_HEIGHT_TRIGGER)
intent.jump = true`) was computed purely from the waypoint's height
difference, with no `blockedByDig` check at all. A waypoint that happens
to require a step-up *and* has a block still standing in its `toBreak`
list produced exactly this: `intent.jump` held `true` every single tick
spent mining, since the height difference driving it never went away
while blocked -- the bot hopped in place uselessly instead of just
standing still and finishing the break. Fixed by gating the jump check on
the same `walking` condition (`horizontalDistance > distanceToStopAt &&
!blockedByDig`) that already gates forward/yaw.

**Bug 2 -- a blanket "level the pitch while walking" fought
`BlockBreaker.aimAt`'s own direct aim the moment mining resumed.**
`resolveMovementIntent` used to unconditionally set `intent.pitch = 0f`
whenever the bot was actively walking toward a waypoint (added by the
earlier `NearbyPlayerLookAt` fix above, to stop pitch sitting wherever a
prior nearby-player glance left it). But `BlockBreaker.aimAt` sets pitch
*directly on the player* (not through `MovementIntent`) inside
`maybeBreakBlocksNear`, called earlier in the very same
`resolveMovementIntent` call -- so on a tick where mining had just
finished breaking one block in a multi-block `toBreak` list and moved on
to aiming at the next one, this later `intent.pitch = 0f` immediately
overwrote that fresh aim back to level, before the bot had actually
looked at (and made real `BlockState.getDestroyProgress` progress on)
the new target. Net effect: pitch visibly snapped back and forth between
"looking at the block" and "level" as mining and the blanket walking-pitch
alternated ownership tick to tick.

**Fix, matching the explicit design requested**: mining always looks at
the target block (`BlockBreaker.aimAt`, unchanged, still bypasses
`MovementIntent` entirely and sets the player's rotation directly), and
*walking* now looks at the next waypoint instead of forcing a level
horizon -- `intent.pitch` is computed from the same `aimY`/`horizontalDistance`
already used for `intent.yaw` (`atan2` against the waypoint's eye-relative
vertical offset), gated on the same `walking` flag as forward/yaw/jump.
Since `walking` and `blockedByDig` are mutually exclusive by construction
(`walking = horizontalDistance > distanceToStopAt && !blockedByDig`),
this new pitch-setting code structurally never runs on a tick
`BlockBreaker.aimAt` already owns pitch for -- no more alternating
ownership, no more overwrite race. `intent.pitch = 0f` is gone entirely;
nothing else needed it (`MovementIntent.pitch`'s own docstring already
noted pitch has zero effect on movement mechanics, purely cosmetic/aim).

Not yet re-confirmed live after this fix -- worth a follow-up
`!follow`/`!goto` test specifically through terrain requiring a
dig-through-obstacles waypoint with a step-up, watching for both no more
jump-spam while blocked mining, and pitch staying aimed at the block
throughout (not snapping level) whenever mining resumes mid-path.

## !follow got stuck digging straight down forever, one block at a time

Reported live: `!follow` got stuck in an endless loop digging straight
down through solid stone, never making horizontal progress toward the
followed player -- the mod log showed `tryBreak` repeatedly targeting
the block directly below the bot's feet, alternating between two
adjacent y-levels (`y=79`, `y=78`, `y=79`, `y=78`, ...) indefinitely.

Root cause: **`Movements.java`'s port of `getMoveDown`/`getMoveDropDown`
dropped upstream mineflayer-pathfinder's `maxDropDown` cap entirely.**
Confirmed by reading the actual upstream source
(`/home/colaila/git/mineflayer-pathfinder/lib/movements.js:462-521`):
`getLandingBlock` there only accepts a *physical* landing (a real floor,
as opposed to water) if `node.y - blockLand.position.y <= this.maxDropDown`
(default `4`) -- a floor found any farther below than that is rejected
(`return null`), refusing the move entirely rather than pretending it's
walkable. This mod's Java port of `getLandingBlock` had no such check at
all -- it just walked down up to 256 blocks and returned the *first*
physical floor found, no matter how far below, and neither `getMoveDown`
nor `getMoveDropDown` ever re-checked the distance afterward either.

**Why this specifically caused an endless loop, not just an occasional
bad route.** `getMoveDown`'s cost is `1.0 + safeOrBreak(block0)` --
only the *single* block directly below the current node factors into
both the cost and the `toBreak` list, regardless of how far below the
actual accepted landing spot (`blockLand`) is. So a landing spot 20
blocks straight down through solid stone got costed as though it were a
single cheap step, and the returned `Move` claimed the bot could reach
`blockLand` by digging exactly one block. In reality, executing that
"move" only digs `block0` and lets the bot fall exactly one block --
nowhere near the claimed destination. `PathTracker.nextWaypoint`'s
reached-check (`Math.abs(selfY - waypoint.y) < 1.0`) then correctly
says "not there yet" every tick, and once the bot has fallen far enough
that `SELF_DRIFT_REPLAN_DISTANCE` (4.0) is exceeded, `maybeReplan` fires
again -- A* runs fresh from the bot's new position, finds the exact same
kind of `getMoveDown` edge toward the same kind of distant floor below,
and the cycle repeats: dig one block, fall one block, replan, dig the
next block down, forever, never actually progressing toward the
followed player (who wasn't reachable by tunneling straight down at all
-- the "path" A* kept finding was never a real route to them, just an
artifact of an uncapped, mis-costed drop move being offered as if it
were a single valid step).

**Fix**: ported the missing `maxDropDown` cap exactly as upstream has
it -- `Movements.MAX_DROP_DOWN = 4` (matching movements.js's own
default), enforced inside `getLandingBlock` itself: a *physical* landing
more than `MAX_DROP_DOWN` blocks below the originating node's `y` is now
rejected (`return null`) instead of silently accepted, so `getMoveDown`/
`getMoveDropDown` simply aren't offered as moves at all when the real
landing is too far down -- A* now has to find (or fail to find) a
different route instead of being handed a move it can't actually
execute in one step. A liquid landing is still uncapped (matching
upstream's `infiniteLiquidDropdownDistance` default), since falling into
water is safe regardless of distance.

Not yet re-confirmed live after this fix (the report above is what
prompted it) -- worth a follow-up `!follow`/`!goto` test specifically
across terrain with a real multi-block drop nearby, to confirm the bot
now correctly walks around instead of attempting (and getting stuck on)
an unreachable straight-down "shortcut".

## !digDown reported only 1 block actually broken despite claiming 5

First live test after the mining/digging work above (`!dig 5`, see PENDING.md's
rename note -- `!digDown` is now just `!dig`): the bot replied "dug down 5
blocks" but a player watching reported it only actually dug one, and
never fell into the hole -- "you were partially on another block, maybe
you need to move to the center of the target block first and then dig."
That diagnosis was exactly right and pointed straight at two real,
compounding bugs.

**Bug 1 -- no centering, so the bot could straddle two columns while
digging.** `tickDigDown` always targeted `player.blockPosition().below()`
-- whatever integer block the bot's feet happened to occupy at that
instant, with zero attempt to ensure the bot was actually standing
*centered* over one specific column first. If the bot started digging
while straddling the edge between two blocks (a completely normal
resting position -- nothing before this ever centered the bot over
anything), it could end up balanced on the neighboring still-solid block
after the target broke, never triggering the fall into the hole it just
made.

**Bug 2 -- `BlockBreaker.tryBreak` conflated "already gone" with "I just
broke it," so bug 1's symptom got silently amplified into reporting way
more progress than was real.** `tryBreak`'s original semantics returned
`true` immediately whenever the target position was already air --
originally written as a defensive "something else must have removed it,
treat that as done" case. Combined with bug 1: once the bot got stuck
straddling and never fell, `player.blockPosition().below()` on the
*next* several ticks kept resolving to the exact same block -- now
already air from the one real break -- and `tryBreak` happily reported a
fresh "true" on every single one of those ticks, since nothing
distinguished "this call caused the block to disappear" from "the block
was already gone when I checked." `digDownRemaining` decremented once
per tick this way, racing to zero within a handful of ticks while only
one block had ever actually been broken -- exactly matching the reported
"claims 5, only 1 real."

**Fixes, both in this commit:**
- `BlockBreaker.tryBreak` now returns `false` immediately for an
  already-air target (`state.isAir()` on entry), instead of `true` --
  only the exact call that causes a transition from solid to air counts
  as a real success now. This is a real behavior-contract change, not
  just a docstring fix -- every caller was re-checked: `maybeBreakBlocksNear`
  (pathfinding's dig-waypoint execution) never used the return value for
  counting, unaffected; `tickCollectBlock` (`!collect`'s block case) *did*
  implicitly rely on the old "already gone counts as success" behavior,
  so a new guard was added there too (see below) so a `!collect` target
  that goes missing between being found and being reached (another player
  mined it, etc.) gets abandoned and re-searched instead of the loop
  silently getting stuck forever waiting for a break that will never
  happen against an already-air block.
- `resolveMovementIntent` gained a new early-return branch for
  `Mode.DIG_DOWN`: instead of yielding no movement target at all (the
  old behavior -- `DIG_DOWN` was treated as purely stationary), it now
  calls `centerDigDownIntent`, which walks the bot to the horizontal
  center of its *own current* column (`floor(x)+0.5, floor(z)+0.5` --
  not a target elsewhere, just wherever the bot already is) whenever
  it's more than `DIG_DOWN_CENTER_TOLERANCE` (0.15 blocks) off-center.
  `tickDigDown` gained a matching `isCenteredForDig` gate at its top --
  it won't start (or continue) a break until the bot is actually
  centered, so the column it targets stays consistent with where the bot
  will end up standing once it falls.
- `tickCollectBlock` (mentioned above) now explicitly checks
  `level.getBlockState(pos).isAir()` before calling `tryBreak`, and if
  true, clears `collectHasTarget` (abandoning that item) rather than
  looping forever on a target that's already gone for a reason unrelated
  to this run.

Not yet re-confirmed live after this fix (the report above is what
prompted it) -- worth a follow-up `!dig` test specifically checking that
the reported count now matches the actual number of new air blocks in
the column, and that the bot visibly walks to center before the first
swing on a straddled start position.

## Repo layout (mod side, `minebot-mod`, separate repo)

Scaffolded from the same Fabric Loom + Minecraft 26.1.2 pin already
working in the sibling `mods/VillagerHelper` project (same toolchain, same
one-shot Fabric-mod-dump tricks used earlier for the block registry --
see `pure-protocol-backend`'s FINDINGS.md if that's ever needed again).

- `ControlClient.java` -- embeds Java-WebSocket (shaded via Loom's
  jar-in-jar `include`, since nothing else provides it) as a *client*
  connecting out to Python's server (see the WSL2-networking note above
  for why the roles are this way round). Auto-reconnects every 2s.
  `onOpen` also resets `MinebotMod`'s `knownPlayerIds` so a freshly
  (re)started Python backend gets full `add` events again instead of
  only ever seeing silent `move` events for players it has no record of.
- `ControlState.java` -- the current goal (`IDLE`/`GOTO`/`FOLLOW` +
  target), set by incoming WebSocket commands. Owns a `PathTracker`
  (reset whenever the goal changes) holding the currently-planned A*
  route toward that goal.
- `pathfinding/` -- `Move`/`AStar`/`BlockInfo`/`Movements`/`GoalNear`/
  `PathTracker`: the Java A* port described above. Also
  `DoorOpener` -- treats closed doors as passable in the path cost model
  and right-clicks them open via the real `useItemOn` interaction as the
  bot approaches, called each tick from `resolveMovementIntent` against
  the current waypoint.
- `MovementIntent.java` -- the concrete per-tick forward/jump/yaw/pitch
  resolved from the current goal against live game state; separates goal
  resolution (needs live entity/player state) from input plumbing
  (doesn't). `resolveMovementIntent` (in `MinebotMod.java`) only sets yaw
  while actively walking toward a pathfinding waypoint (yaw doubles as
  "which way to walk forward" then) -- it no longer knows anything about
  looking at a FOLLOW/GIVE target specifically, see `NearbyPlayerLookAt`
  below for where look-at-a-player now lives instead.
- `NearbyPlayerLookAt.java` -- looks at whoever's closest within 8
  blocks, yaw+pitch aimed at their eye level (`Entity.getEyeY()` on both
  sides; pitch sign, positive = looking down, confirmed via decompiled
  `Entity.calculateViewVector`), entirely independent of any movement
  goal -- the bot glances at a nearby player whether idle, mid-`!goto`,
  or following someone else entirely, the way a real player naturally
  would. `MinebotMod.onClientTick` only applies this when
  `resolveMovementIntent` left yaw unset for the tick (i.e. not actively
  walking toward a waypoint), so it never fights the pathfinding --
  originally this lived inside `resolveMovementIntent` tied specifically
  to the `FOLLOW`/`GIVE` goal, then was pulled out and generalized after
  a follow-up request to decouple it from following specifically.
- `MinebotInput.java` -- the `ClientInput` replacement described above
  (keyboard-override + `MovementIntent`-driven fallback).
- `FoodEater.java` -- autonomous eating: every client tick, if health is
  at or below 20% of max, eats real food (offhand first, else the first
  edible+eatable-right-now item found in the main inventory, selected
  into the hotbar first if necessary). "Edible" is a `DataComponents.FOOD`
  presence check -- the older `Item.getFoodProperties()` API is gone in
  26.1.2. "Eatable right now" mirrors `Player.canEat(canAlwaysEat)`
  (hunger-gated, see below). Also sends real chat lines via
  `player.connection.sendChat`: once per low-health episode when it
  starts eating ("I have N.N hearts!, eating...", translated, one
  decimal place -- `Math.round` previously misreported a real nonzero
  0.5 HP as "0 hearts"), once if nothing edible is found at all
  ("oh, I have no food! aaaa"), and once if food exists but hunger is
  full and blocking every bit of it ("I have food but I'm not hungry...",
  `food_eater.hunger_full`) -- each gated by its own `util.EdgeTrigger`
  (see below) so it doesn't spam chat every tick while health stays low.
  Entirely autonomous on the mod side; no control-channel wire format
  changes, so Python has no visibility into any of this beyond the
  `health` events it already gets (and the chat lines showing up as
  regular `chat` events, same as any other player's chat).

  **How it actually triggers eating -- the hard-won part.** `FoodEater`
  does **not** call `MultiPlayerGameMode.useItem()` (the same client API
  `DoorOpener` uses for its instant door interaction) -- it holds the
  real `keyUse` keybind down instead
  (`Minecraft.getInstance().options.keyUse.setDown(true)`), letting
  vanilla's own per-tick `Minecraft.handleKeybinds()` drive the actual
  interaction exactly as it would for a human physically holding
  right-click, released (`setDown(false)`) once health recovers or
  nothing eatable remains. `MinebotMod.onClientTick` also releases it
  explicitly on death (`FoodEater.releaseUseKeyIfHeld()`), since
  `FoodEater.maybeEat` itself isn't ticked while dead and the key could
  otherwise stay stuck held through a respawn.

  This exists because calling `useItem()` directly -- what every earlier
  version of this class did -- **never actually completed a single eat**,
  discovered live and root-caused only after an extensive investigation
  (six separate decompiled-bytecode research passes over one debugging
  session). Each attempt individually *looked* successful: `useItem()`
  returned `InteractionResult.Success` and `player.isUsingItem()`
  briefly read `true` -- but was reset back to `false` exactly one tick
  later, every single time, forever, so the eat-duration timer never
  progressed and the item was never consumed. From the outside this
  looked exactly like "spamming right-click" (a player watching
  described it that way independently, before any explanation existed).
  Ruled out, one at a time, each confirmed via decompiled source/bytecode
  (not guessed):
  - Hunger gating (`Player.canEat()`) -- real, and now handled (see
    above), but not the cause of *this* symptom; confirmed hunger was
    genuinely not full during the failing attempts.
  - Reselecting an already-selected hotbar slot every tick -- removed
    entirely as a variable (eating only from whatever was already
    selected/offhand, no `setSelectedSlot`/`pickSlot` at all) -- bug
    persisted identically.
  - Call rate -- added a hard 10-tick cooldown between `useItem()`
    attempts (not just relying on `isUsingItem()`) -- bug persisted
    identically, just at a slower, still-broken cadence.
  - The mod's own synthetic movement input (`MinebotInput`, which
    replaces `player.input` every tick even when idle) -- fully disabled
    for a test build -- bug persisted identically.
  - Vanilla server-side rate-limiting/cooldowns on
    `ServerboundUseItemPacket` -- confirmed absent in
    `ServerGamePacketListenerImpl.handleUseItem`'s bytecode (exactly 3
    guards: client-loaded, item non-empty, feature-flag enabled).
  - The hotbar-carried-item sync packet
    (`MultiPlayerGameMode.ensureHasSentCarriedItem`, which *can* trigger
    `stopUsingItem()` server-side on a genuine slot change) -- confirmed
    inert here: it only sends a packet when the client's own
    `Inventory.selected` actually changes, which the logs proved wasn't
    happening.
  - Server-side plugins -- checked the server's actual mod list
    (`CustomPlayerModels`, `fabric-api`, `faster-copper-golem`,
    `Jade`/`jade_trades`, `Ping-Wheel`, `jei`, `rewrite-villager-helper`)
    and, for the two with local source access, confirmed neither touches
    item-use interactions at all.

  **The decisive test** (not bytecode -- a live A/B comparison): on the
  exact same running game client, same account, same session, same
  food item -- a human physically taking over mouse/keyboard control of
  that window and holding right-click ate normally, while the mod's
  direct `useItem()` call on the identical setup never completed a
  single eat. That pinned the difference to "real input" vs.
  "programmatic API call" specifically, which a follow-up bytecode pass
  confirmed *shouldn't* matter (`Minecraft.startUseItem()`, real input's
  own entry point, calls the exact same `MultiPlayerGameMode.useItem()`
  the mod does, no hidden setup) -- leaving the actual reason for the
  difference still unexplained at the vanilla-source level (see Known
  gaps). Switching to holding the real keybind sidesteps needing to
  know why; it was confirmed working by direct live observation (a
  human watching the bot hold right-click and the food item actually
  get consumed, health recovering).
- `util/EdgeTrigger.java` -- small reusable primitive extracted out of
  `FoodEater`'s original pair of hand-rolled `announcedX` booleans: feed
  it a per-tick boolean condition via `fire(condition)`, and it returns
  `true` exactly once, on the tick the condition transitions false->true,
  then stays quiet on every following tick the condition holds, and
  rearms itself the moment the condition goes false. This is the "state
  changed, announce it once, don't spam every tick it holds" shape, which
  is expected to recur for other bot behaviors beyond eating (e.g. "just
  died", "just got attacked", "just went idle") -- reuse this instead of
  growing another `announcedX`/`reset-in-the-else` boolean pair per case.
- `config/Messages.java` -- dictionary for the bot's own outgoing chat
  text (`FoodEater`'s lines above), backed by
  `assets/minebot-mod/messages/{en,es}.json` (flat key -> template maps,
  `{placeholder}` substitution), selected by `config.Configs.botLanguage`
  and cached per-language after first load. Deliberately separate from
  Fabric's own lang-file/`Component.translatable` system (used by
  `gui/ConfigScreen.java` for its own on-screen labels, under
  `assets/minebot-mod/lang/en_us.json`/`es_es.json`) -- that system
  follows the *viewing player's* client language setting, which is the
  wrong axis for text the bot itself sends; the bot's language is instead
  a settings toggle independent of whoever's looking at the screen.
- `config/Configs.java` -- in-memory mod settings (currently just
  `botLanguage`, `"en"`/`"es"`), not persisted to disk -- resets to the
  default every launch. Modeled directly on
  `/home/colaila/git/mods/VillagerHelper`'s `Configs`/`ConfigScreen`/
  `ModMenuIntegration` pattern (same layout, same optional
  `compileOnly`+`"suggests"` ModMenu dependency so the mod still works
  with ModMenu absent, just with no in-game way to reach the screen).
- `gui/ConfigScreen.java` / `config/ModMenuIntegration.java` -- a
  ModMenu-hosted settings screen (reached via ModMenu's mod list, if
  installed) with one button that cycles `Configs.botLanguage` between
  `en`/`es`.
- `InventoryReporter.java` -- broadcasts a full inventory snapshot
  whenever it changes (same change-only shape as health), by scanning
  `Inventory.getItem(slot)` across the whole `0..getContainerSize()-1`
  range -- confirmed in 26.1.2 this is a uniform accessor covering main
  storage/hotbar (0-35) *and* armor/offhand/body/saddle (36-42) alike,
  unlike some earlier MC versions where armor lived in a separately-
  addressed array.
- `InventoryActions.java` -- the command side: `moveToHotbar` (generalizes
  `FoodEater`'s existing `setSelectedSlot`/`pickSlot` idiom to an explicit
  target hotbar slot -- a local-state swap, not itself packet-synced,
  same as `FoodEater`'s use of it), `equip` (a real container `QUICK_MOVE`
  click via `MultiPlayerGameMode.handleContainerInput` -- the shift-
  click-to-equip equivalent, routes the item to its matching armor/
  offhand slot automatically), `drop` (real Q-drop via `LocalPlayer.drop`
  for the selected hotbar slot, or a container `THROW` click for any
  other slot -- loops one-at-a-time for counts under the full stack size,
  since real drop actions only support "drop one" or "drop the whole
  stack" per action, never an arbitrary count). Slot-index translation
  between `Inventory`'s numbering (0-8 hotbar, 9-35 main storage) and
  `InventoryMenu`'s different internal numbering (5-8 armor, 9-35 main
  storage, 36-44 hotbar) was confirmed by disassembling
  `AbstractContainerMenu.addStandardInventorySlots` directly (main
  storage needs +0, hotbar needs +36 -- NOT a uniform +9 some other MC
  versions' layout might suggest; verify against the real bytecode again
  if this ever needs revisiting on a version bump, don't assume the
  layout carries over).
- `RespawnHandler.java` -- detects death and auto-respawns. No
  client-side Fabric API event exists for either (confirmed:
  `ServerPlayerEvents.AFTER_RESPAWN`/`JOIN`/`LEAVE`/`ALLOW_DEATH` all take
  `ServerPlayer`, server-side only -- unusable since minebot-mod isn't
  the server; `ClientEntityEvents` only covers `ENTITY_LOAD`/`UNLOAD`),
  so this polls every tick, same as `MinebotMod` already does for health
  -- `LivingEntity.isDeadOrDying()` is itself just `getHealth() <= 0`
  under the hood (confirmed via decompiled bytecode), so this reads the
  same signal already being read every tick, just as an edge instead of
  a value-changed check. Two `EdgeTrigger`s (`dead`/`alive`) fire
  `onDeath`/`onRespawn` callbacks exactly once per transition; `onDeath`
  also calls `player.respawn()` immediately, which sends the exact same
  `ServerboundClientCommandPacket(PERFORM_RESPAWN)` the death screen's
  Respawn button does (confirmed via decompiled bytecode) -- no GUI
  interaction needed, so the bot no longer needs a human to click
  Respawn. `MinebotMod.onClientTick` also skips `FoodEater.maybeEat`
  while `isDeadOrDying()` is true (no point trying to eat at 0 health).
- `BuildInfo.java` -- reads `commit`/`built_at` out of a
  `minebot-mod-build-info.properties` file baked into the jar at build
  time (`build.gradle`'s `generateBuildInfo` task, which shells out to
  `git rev-parse HEAD`/`git status --porcelain` -- appends `-dirty` to
  the commit if the working tree had uncommitted changes at build time).
  `MinebotMod.onControlChannelConnected` broadcasts this as a one-shot
  `hello` event. Added directly in response to a real live-debugging
  session where a jar was rebuilt with a genuine fix (twice) but the
  running game client was never fully restarted to pick it up -- from
  the backend's logs alone that looked identical to "the fix doesn't
  work". **Gradle gotcha hit building this**: `generateBuildInfo`
  initially had no declared task inputs, so Gradle treated it as
  UP-TO-DATE after its first run and never re-executed -- every build
  after the first baked in the *same* stale commit forever, regardless
  of what actually changed. Fixed with `outputs.upToDateWhen { false }`
  to force it to always re-run (there's no meaningful "input" to declare
  here since the whole point is capturing current repo/build state, not
  reacting to a changed file).
- `StatusHud.java` -- a HUD text overlay showing whether the control
  channel is currently connected.
- `PathVisualizer.java` -- draws the currently planned A* path
  (`PathTracker.waypoints()`, a new read-only accessor) as a connected
  line through each waypoint, local-client-only debug visualization (see
  "Path visualization" below).
- `MinebotMod.java` -- entry point: starts the control client, registers
  the client-tick hook (resolves the goal via `PathTracker`, updates
  `MinebotInput`, sets yaw, checks GIVE-goal completion, ticks
  `RespawnHandler`, calls `FoodEater` unless dead, broadcasts
  position/entity/inventory/health/death/respawn events),
  registers chat-event forwarding, registers the HUD, broadcasts
  `hello` on connect (see `BuildInfo` above). `ControlState`
  gained a `GIVE` mode (walks toward a target entity like `FOLLOW`,
  reusing `followEntityId`; once within `stopDistance`,
  `maybeCompleteGive` drops the requested slot/count and clears back to
  `IDLE` -- "give to a player" has no direct Minecraft mechanic, so this
  is the closest real analog: walk over, then drop it at their feet).

`fabric.mod.json` declares `"environment": "client"` (no server-side
component -- this only makes sense running inside an actual client).

## Path visualization: MC 26.1.2's Gizmo debug-draw API

Requested: visualize the currently planned pathfinding waypoints in the
bot's own client, local-only (not visible to other players, not
networked). This MC version turned out to have moved to a genuinely
different world-render architecture than older Fabric-modding knowledge
assumes -- the classic `WorldRenderEvents.END` + immediate-mode
`Tesselator` pattern doesn't exist in `fabric-rendering-v1` here at all,
replaced by `net.fabricmc.fabric.api.client.rendering.v1.level.
LevelRenderEvents` (hooks: `AFTER_BLOCK_OUTLINE_EXTRACTION`,
`END_EXTRACTION`, `START_MAIN`, `AFTER_OPAQUE_TERRAIN`,
`COLLECT_SUBMITS`, `AFTER_SOLID_FEATURES`, `AFTER_TRANSLUCENT_FEATURES`,
`BEFORE_BLOCK_OUTLINE`, `BEFORE_GIZMOS`, `BEFORE_TRANSLUCENT_TERRAIN`,
`AFTER_TRANSLUCENT_TERRAIN`, `END_MAIN`).

Confirmed (by reading real decompiled vanilla source from Loom's
`genSources` cache, `~/.gradle/caches/fabric-loom/decompile/v1.zip`, not
guessed) that MC 26.1.2 has a first-class **Gizmo** debug-draw system
(`net.minecraft.gizmos.Gizmos`) -- this is the modern built-in equivalent
of hand-tesselating a line, and what vanilla itself uses for exactly
this kind of overlay. No manual `VertexConsumer`/`BufferBuilder` work is
needed for a simple colored line:

```java
LevelRenderEvents.BEFORE_GIZMOS.register(context -> {
    try (var ignored = context.levelRenderer().collectPerFrameGizmos()) {
        Gizmos.line(startVec3, endVec3, argbColor, width).setAlwaysOnTop();
    }
});
```

- `BEFORE_GIZMOS` fires immediately before vanilla's own
  `LevelRenderer.finalizeGizmoCollection()` drains its per-frame
  collector, so anything added during this callback renders that same
  frame. (`COLLECT_SUBMITS` is unrelated -- fires earlier, for
  `SubmitNodeCollector`-style entity/block-entity submissions, not
  gizmos.)
- `Gizmos.line`/`cuboid`/`point`/etc. require an active collector
  (a `ThreadLocal`) or they throw `IllegalStateException`;
  `LevelRenderer.collectPerFrameGizmos()` returns an `AutoCloseable` that
  sets one up for the try-with-resources block's duration.
- Gizmos default to depth-tested (hidden behind terrain) unless
  `.setAlwaysOnTop()` is called on the returned `GizmoProperties` --
  used here since a pathfinding overlay is only useful if visible
  through walls/underground, the same reason a route is often worth
  checking in the first place.
- Confirmed real (via `javap`) against this project's actual pinned
  `fabric-api` version (`0.151.0+26.1.2`, `gradle.properties`), not just
  the general MC version -- exact method signatures matched a fresh
  independent `javap` check before writing `PathVisualizer.java`.

`Gizmos.point(pos, argb, size)`'s `size` gotcha (found live, then root-
caused via decompiled shader source): unlike `Gizmos.line`'s `width`,
`point`'s `size` is **not** a world-space or even a generically-scaled
value -- it's assigned completely raw to `gl_PointSize` (a real GL point
sprite, `GL_POINTS`/`VertexFormat.Mode.POINTS`), in literal screen
pixels, with no division by screen size the way the line shader
(`rendertype_lines.vsh`) explicitly does to build real expanded-quad
line geometry from its own `width`. A `size` of `0.25f` (chosen by
analogy to `LineGizmo`'s `width=3.0f` default) requested a quarter-
pixel-diameter dot -- rasterizes to nothing on any real display. Fixed
by using `10.0f` instead (`PathVisualizer.MARKER_SIZE`) -- comparably
sized to how a small dot actually reads on screen. Side effect worth
knowing: `point` markers are constant-*pixel*-size regardless of camera
distance (a screen-space point sprite), unlike `line`/`cuboid` gizmos
which are real world-space geometry that shrinks with distance --
`Gizmos.cuboid` would be the alternative if a marker that scales
naturally with distance is ever wanted instead.

## Level horizon while walking a waypoint

`resolveMovementIntent` only ever set `intent.yaw` while actively
walking toward a pathfinding waypoint -- `intent.pitch` was simply never
touched there, so it stayed at whatever `NearbyPlayerLookAt`'s last
glance had left it at (tilted up/down toward whoever was nearby a moment
before movement started), rather than looking level. Fixed by explicitly
setting `intent.pitch = 0f` in the same branch that sets `intent.yaw`
(`MinebotMod.resolveMovementIntent`, guarded by
`horizontalDistance > distanceToStopAt` -- only while actually stepping
forward, not merely "a goal exists").

## !collect's mining phase never completed: it was walking/jumping into the block it was simultaneously mining

After the pickup-walk-phase fix (see the pickup-phase section above), a
live `!collect cobblestone 4` run showed a new, 100%-failure-rate
regression: every single break attempt logged real, steady
`getDestroyProgress` (e.g. 0.178/tick -- mathematically ~6 ticks to
completion) with no tool switch happening, yet none ever completed --
each one ran the full `COLLECT_TARGET_TIMEOUT_TICKS` (200 ticks/10s)
and gave up, repeatedly, on four different stone blocks in a row.

Root cause: `resolveMovementIntent`'s `walking` gate (the one that holds
off forward/yaw/pitch/jump intent while a block is being dug through --
see "Jump-spam while mining mid-path" above) was only ever driven by
`blockedByDig`, which itself only reflects `pathBlockBreaker` (the
breaker pathfinding uses to dig through obstacles blocking a waypoint).
`!collect`'s own mining phase uses a completely separate `BlockBreaker`
instance, `collectBreaker`, held and ticked from `tickCollect` --
`maybeBreakBlocksNear`/`blockedByDig` never knew it existed.

`ControlState.setCollect` sets `stopDistance = 2.5`, but
`BlockBreaker.INTERACT_RANGE` (how close the bot needs to be to actually
swing at something) is 4.5. Any target between 2.5 and 4.5 blocks away
is close enough for `collectBreaker.tryBreak` to start mining it, but
still farther than `stopDistance` -- so `walking` stayed `true` every
tick (`horizontalDistance > distanceToStopAt && !blockedByDig`, and
`blockedByDig` was unconditionally `false` here since only pathfinding's
breaker was ever consulted). The bot kept setting `intent.forward = true`
and, once close enough to trip the step-height check, `intent.jump =
true`, walking and hopping toward the exact block `collectBreaker` was
simultaneously holding `keyAttack` against -- constantly changing
position/rotation out from under the in-progress break, the same
"fighting the aim/interrupting a real interaction" shape as every other
bug in this investigation (see the tool-switch-mid-break and
settle-window fixes above), just never wired up for this specific
breaker instance.

Fixed by computing a second flag, `blockedByCollectMining` (true when in
`COLLECT` mode, not in the pickup-walking sub-phase, targeting a block
rather than an entity, and `collectBreaker.hasActiveTarget()`), and
OR-ing it into `blockedByDig` right after `maybeBreakBlocksNear` returns
-- same effect as pathfinding's own protection, just triggered by a
different breaker. Entities are excluded from the gate since
`tickCollectEntity`'s melee-attack loop has no equivalent "stand still
and mine" concern -- closing distance is exactly what it's supposed to
do.

Not yet re-confirmed live (this fix was deployed immediately after
discovery) -- next `!collect` test should show the bot standing
genuinely still (no forward/jump intent) for the whole duration of each
break, and real completions inside a handful of ticks rather than
hitting the 200-tick give-up every time.

## !collect's own progress replies were cancelling the !collect that sent them

Once the mining-phase movement fix above landed, a live `!collect
cobblestone 4` looked like it worked (chat showed "I got a cobblestone
(3 total)" then "(4 total)"), but the user immediately asked "why
stopped?, I wanted 4!" -- the Python backend's own log showed
`collect()` had only ever counted 1/4 confirmed gains before being
cancelled.

Root cause: `_dispatch_chat_command` (`run_loop.py`) unconditionally
cancelled whatever `current_command_task` was still running for *every*
incoming chat event, before checking whether the message was the bot's
own (that self-message check only ever gated re-dispatching a fresh
command, a few lines later -- see its own docstring on the infinite-
crash-loop bug it was originally added for). The mod relays the bot's
own chat back through the same server-chat-broadcast event stream as
any player's -- so every progress message `collect()` itself sent
("I got a cobblestone (3 total)") arrived back as a new chat event and
cancelled the very `collect()` call that had just sent it, after only
one real iteration.

Cancelling the Python task doesn't stop the mod: `ControlState.
setCollect` (mod-side) was never told to stop, so the mod kept mining
and killing/breaking targets for real, and `InventoryTracker`'s
`inventory_announcer` (a separate, unconditional inventory-gain
reporter -- see its own module) kept announcing each further genuine
drop independently of whether any `collect()` loop was still alive to
count it. The result looked indistinguishable from a real, correctly-
counted `collected 4/4` from outside (chat kept incrementing), but
`collect()`'s own count/timeout/empty-drop bookkeeping was silently
dead the entire time.

Fixed by moving the self-message check in `_dispatch_chat_command`
*before* the cancel, returning the still-running `current_command_task`
unchanged instead of cancelling it when the event is the bot's own
message. All 164 existing tests (including `test_run_loop.py`'s
self-message-ignored coverage) still pass -- the existing tests only
checked that a self-message doesn't get *re-dispatched*, not that it
doesn't cancel an in-flight command, so this class of bug had no test
coverage before now.

## The movement fix wasn't the whole story: !collect can still hold a stationary, correctly-aimed break for 200+ ticks without completing

After both fixes above landed (the mining-phase movement fix and the
self-cancelling-chat-reply fix), a fresh live `!collect cobblestone 4`
showed the bot standing genuinely still, yaw/pitch locked exactly on
target (confirmed via the position wire events -- identical rotation
logged tick after tick) -- the movement-fighting bug is confirmed fixed.
But mining itself still failed 100% of the time in this run: real,
steady `getDestroyProgress=0.17777778` every tick (independently
confirmed as the exact correct vanilla formula result for a diamond
pickaxe on stone -- `8.0 / 1.5 / 30 = 0.1778`, mathematically ~5.6 ticks
to complete), held continuously against one stationary, in-range,
line-of-sight target, for the entire 200-tick `COLLECT_TARGET_TIMEOUT_TICKS`
window, with no tool switch and no other `BlockBreaker` instance active
-- every previously-identified cause (aim/movement fighting the break,
mid-break tool switch, the isBusy()-deadlock class, a second BlockBreaker
racing on the same target) was directly ruled out for this specific
repro by reading the log evidence.

This is the same "real per-tick rate reported, but the break never
actually completes" shape as the original 601-tick pathfinding stall
(see STUCK_TICKS_LIMIT's own docstring) -- previously timeout-mitigated,
never root-caused. `getDestroyProgress` is a pure, stateless per-tick
*rate* calculation (recomputed fresh from `BlockState`/tool every call);
real completion depends on `MultiPlayerGameMode`'s own private
`destroyProgress` field, a running sum this class has no visibility
into -- decompiled source (`continueDestroyBlock`) confirms the sum only
survives between ticks while `sameDestroyTarget(pos)` holds (`pos`
unchanged AND `ItemStack.isSameItemSameComponents(current main-hand,
the item snapshotted at start-of-break)`), which resets to 0 and
restarts if either ever differs even once -- exactly the shape of bug
already found and fixed once for the tool-switch case, but evidently not
the whole story, since no tool switch happened here.

Since none of the client-visible signals (aim, position, tool, log
output) can currently distinguish "genuinely still accumulating
normally" from "silently resetting to 0 every tick", added a one-time
reflection-based diagnostic (`BlockBreaker.dumpRealDestroyState`) that
reads `MultiPlayerGameMode`'s actual private `destroyProgress`/
`destroyTicks`/`destroyBlockPos`/`destroyingItem` fields directly and
logs them alongside every existing per-tick `tryBreak` log line. Not a
fix -- this is purely to get real visibility into the one piece of state
that would finally confirm or rule out the reset-to-zero hypothesis (a
steadily *climbing* destroyProgress that never reaches 1.0 would point
somewhere else entirely, e.g. a real `getDestroyProgress` ceiling/
clamping bug; a destroyProgress stuck at/near 0 every tick would confirm
the reset theory and narrow the search to whatever's still changing
`destroyingItem`'s component comparison result despite no explicit tool
switch). Needs a fresh live `!collect` run (client already has the new
jar deployed) before this can go further.

## Root cause of the stationary-never-completes stall, finally confirmed: keyAttack-hold silently does nothing without real mouse capture

The reflection diagnostic (dumpRealDestroyState, see above) immediately
answered the question on the very next live run: `isDestroying=false`
and `destroyBlockPos=BlockPos{x=-1,y=-1,z=-1}` (the "nothing being
destroyed" sentinel) on every single logged tick, for the entire
200-tick stall, for both `!collect` and a separate pathfinding
mid-path dig in the same session -- despite keyAttack being held and
getDestroyProgress logging real, steady, correct-for-a-diamond-pickaxe-
on-stone numbers throughout. A real destroy sequence was never actually
starting server-side at all.

Traced through decompiled `Minecraft.handleKeybinds`/`continueAttack`:
`continueAttack`'s own `down` parameter -- which decides whether to call
`MultiPlayerGameMode.continueDestroyBlock` (real per-tick progress) or
`stopDestroyBlock()` (reset to zero) -- is:

```java
this.continueAttack(this.screen == null && !instantAttack
    && this.options.keyAttack.isDown() && this.mouseHandler.isMouseGrabbed());
```

`isMouseGrabbed()` requires real OS mouse capture (`MouseHandler.
grabMouse()`, itself a no-op unless `Minecraft.isWindowActive()` -- real
window focus -- is also true). Critically, `keyUse` (the keybind
FoodEater already holds successfully for eating, the whole reason this
class copied the same "hold the real keybind" approach in the first
place) has **no such gate** anywhere in `handleKeybinds` -- `keyUse.
isDown()` alone is sufficient there. That asymmetry between the two
keybinds is exactly why FoodEater's version of this pattern always
worked reliably while BlockBreaker's copy of the identical-looking idea
could silently fail depending on real mouse-capture state that has
nothing to do with anything this mod controls.

This does NOT contradict the earlier-in-this-same-session confirmed-
working run (the "I got a cobblestone (3 total)" -> "(4 total)" run,
completing real breaks in ~7-8 ticks) -- that was a different client
session (jar built_at 11:09:59, connected ~04:11-04:19) than the one
that produced this 200-tick stall (jar built_at 11:25:41, connected
04:27:03, world/resources reloaded seconds before the stalled attempt
started at 04:27:14). A freshly (re)launched client's window commonly
hasn't had its mouse re-captured yet (sitting on/just past a title or
pause screen) until a human actually clicks into it -- entirely
plausible for a session that had only been running ~10 seconds before
`!collect` was issued, and consistent with earlier live reports in this
same investigation about mining behavior depending on "the bot's window
genuinely focused."

Fixed by having `BlockBreaker.holdAttackKey()` call `Minecraft.
getInstance().mouseHandler.grabMouse()` every tick alongside setting
keyAttack down -- a no-op once already grabbed, and `grabMouse()`'s own
`isWindowActive()` guard means this still can't force real progress
through a genuinely unfocused/backgrounded window, but it recovers the
much more common "window is focused but the mouse was never clicked
into" case automatically instead of silently stalling for the full
200-tick give-up every time. Needs a fresh live run (client already has
the new jar deployed) to confirm.

## Mouse-grab fix confirmed working live -- and the pickup-walk phase's own namespace bug found right behind it

The `holdAttackKey`/`grabMouse` fix above was confirmed live immediately:
a fresh `!collect cobblestone 4` completed multiple real breaks in a
handful of ticks each, with real "I got a cobblestone (N total)"
confirmations -- the stationary-never-completes stall is resolved.

Next report: "mine works now, but is not walking to the drop, there is
one in front of the bot." Log evidence showed every single completion
going straight from `"collect: destroyed/killed the target"` to a
`collect_result` reply with no `"collect: found dropped ... walking to
pick it up"` line ever appearing -- despite a real matching `item_drop`
wire event landing right beside the target immediately beforehand each
time (confirmed: bot-to-drop distance ~1.7 blocks, well inside
COLLECT_PICKUP_SEARCH_RADIUS=6.0). `findNearestMatchingDrop` was
silently finding nothing every time.

Root cause: `BuiltInRegistries.ITEM.getKey(itemEntity.getItem().
getItem()).toString()` returns a full, namespaced `ResourceLocation`
string (`"minecraft:cobblestone"`), but `DropTable.dropsFrom(query)`
returns bare ids (`"cobblestone"` -- see DropTable's own class
docstring on this convention). `expectedDrops.contains(itemId)` was
therefore structurally guaranteed to always be false -- the exact same
namespace-mismatch bug class already found and fixed once on the Python
side (`MiningController.collect`'s `expected_drops` normalization), just
never applied to this mod-side comparison when the pickup phase was
first written. Fixed by using `.getPath()` instead of `.toString()`
(strips the `minecraft:` namespace, matching DropTable's bare-id
convention) -- confirmed by build/deploy, not yet re-tested live.

## A second, distinct never-completes cause: steep-pitch dig targets raycast onto the wrong block because of render-frame rotation interpolation

After the mouse-grab fix, `!collect` itself became fully reliable (see
above), but a fresh report -- "looking at his feet at a highlighted
green cube but not mining" (green = pathfinding's own dig-through-
obstacles gizmo, not collect's magenta) and separately "it only mines
stuff that's not obscured, once a stone is underground it just looks at
the dirt highlighted green but doesn't mine it" -- pointed at a second,
different cause of the same "real progress, never completes" symptom,
this time specific to pathfinding's dig-through-obstacles path.

The reflection diagnostic (dumpRealDestroyState) pinned it precisely:
`destroyBlockPos` was consistently ONE BLOCK OFF from the actual
tryBreak target -- e.g. tryBreak holding keyAttack against
`BlockPos{-456,87,-1428}` (dirt, real getDestroyProgress=0.0667/tick,
correct for a pickaxe's no-tool-bonus rate on dirt -- ruling out the
"needs a shovel" theory the live report guessed at) while
`destroyBlockPos` stayed locked on `{-456,88,-1428}`, exactly one block
above -- i.e. vanilla's own real attack raycast (`hitResult`) was
landing on a different block than the one this mod's own aim/line-of-
sight check agreed was the target. `hasLineOfSight` (Level.clip from
the logical eye position) never rejected these targets, so this wasn't
a genuine obstruction -- it was a disagreement between two different
raycasts of the *same* geometry.

Traced to decompiled `LocalPlayer.raycastHitResult`/`Entity.
getViewXRot`/`getViewYRot`: vanilla's real attack raycast uses
`cameraEntity.getEyePosition(partialTicks)` and picks along a view
vector interpolated between `xRotO`/`yRotO` (previous tick's rotation)
and the current tick's rotation, for smooth camera motion across
rendered frames -- not the same raw, discrete rotation `aimAt` sets via
`setXRot`/`setYRot` alone. A steep, sudden rotation snap (e.g. from
near-level walking pitch to ~85 degrees looking almost straight down at
a dig target directly below/beside the bot's feet, all in one tick) left
`xRotO` at its old, much shallower value -- so several rendered frames'
worth of the *real* raycast were still interpolating from the old angle
toward the new one, long enough to clip a neighboring block instead of
the intended target. Both live repros were exactly this shape: steep-
pitch, small-horizontal-offset targets (digging straight down/sideways
at the bot's own feet), never affecting `!collect`'s normal roughly-
eye-level targets.

Fixed by having `BlockBreaker.aimAt` also snap `player.yRotO`/`xRotO`
to the same values as the current-tick rotation it sets -- the same
technique `Entity.moveTo`'s teleport path already uses (confirmed via
decompiled source) to avoid exactly this kind of render-interpolation
lag. Removes any legitimate reason for this mod's own aim to ever be
smoothly eased toward, unlike a human's real mouse movement. Not yet
re-tested live.

## The mouse-grab fix doesn't hold across every session -- added isWindowActive/isMouseGrabbed to the diagnostic to find out why

A subsequent fresh session (`!collect cobblestone 1`, issued ~30s after
connecting, matching the same timing pattern as the original mouse-grab
stall) reproduced the exact original symptom again: `isDestroying=false`,
`destroyBlockPos` back to the `{-1,-1,-1}` sentinel (not the "locked
onto a neighboring block" shape the rotation-interpolation bug produces)
-- despite `holdAttackKey()` (which now also calls `mouseHandler.
grabMouse()` every tick, confirmed via the code path: grabMouse() is
only reached after every early-return guard in tryBreak passes, and this
run logged plenty of "holding keyAttack" lines) running every tick.

Two possibilities, not yet distinguished: (1) `Minecraft.isWindowActive()`
(real OS window focus) is genuinely false for this session -- grabMouse()
itself no-ops in that case, an environmental condition no in-mod fix can
override, or (2) the window IS active but grabMouse() is failing or
being undone by something else every tick regardless. Added `mc.
isWindowActive()`/`mc.mouseHandler.isMouseGrabbed()` directly to
dumpRealDestroyState's per-tick log line so the next live run answers
this definitively instead of guessing further. If (1), the practical
fix is process/workflow (keep the Minecraft window focused, or find a
way to force real capture independent of OS focus if one exists); if
(2), there's still a real bug left to find in why grabMouse() isn't
sticking.

## The mouse-grab theory ruled out by the user's own controlled test -- real cause was a numerically-degenerate aim angle for blocks directly underfoot

The user designed a clean isolating test to settle the open
isWindowActive/isMouseGrabbed question directly: place one stone in the
open in front of the bot (no dig needed to reach it) and one
underground (forces pathfinding to dig straight down through an
obstacle), then `!collect stone 2`. Result: the open stone mined
successfully every time (confirmed: "I got a cobblestone (13 total)");
the underground one never did.

The diagnostic's new isWindowActive/isMouseGrabbed fields then ruled out
the mouse-grab theory directly, not just by inference: the underground
target's tryBreak logged `isWindowActive=true, isMouseGrabbed=true` --
real OS focus and real mouse capture, both confirmed simultaneously --
and `isDestroying` was STILL false, `destroyBlockPos` STILL the
`{-1,-1,-1}` "nothing targeted" sentinel (not "locked onto a
neighboring block", which is what the earlier rotation-interpolation
fix addressed). So neither of the two previously-fixed causes (mouse
grab, rotation-interpolation lag landing on the wrong neighbor) was
responsible for this failure.

The actual cause: this target was only ~0.08 blocks of horizontal offset
from the bot's own position (a block essentially directly underfoot),
pitch ~87.8 degrees -- almost exactly vertical. At that geometry,
`atan2(dy, horizontalDistance)` (pitch) sits right at the numerically
degenerate edge of the ±90-degree singularity, and `atan2(-dx, dz)`
(yaw) becomes effectively arbitrary since both dx and dz are near zero
-- tiny floating-point noise swings the computed yaw unpredictably tick
to tick. This is exactly the kind of geometry a real human player
instinctively avoids (nobody stares perfectly straight down between
their own feet to mine -- they shift their view slightly off-center
first), but `aimAt` always pointed at the target block's exact
geometric center regardless.

Fixed by detecting this near-vertical case (horizontal offset from the
target's own center under 0.3 blocks) and aiming at a point offset
toward one corner of the target's top face instead of dead-center --
still guaranteed to land on the target block itself (the offset stays
within its own footprint), but restores a well-defined, non-degenerate
direction for both yaw and pitch, avoiding the singularity entirely
rather than trying to compute a stable angle through it. Not yet
re-tested live.

## The real missing piece: continueAttack also bails out on any open screen, independent of mouse-grab state entirely

The near-vertical-aim fix (previous section) didn't fully resolve
things either -- live re-testing still showed the original mouse-grab-
shaped stall recurring (`isDestroying=false`, destroyBlockPos back to
the `{-1,-1,-1}` sentinel) even on a plain, roughly-eye-level `!collect`
target with no near-vertical geometry involved at all, immediately after
a fresh client (re)connect. The user's own reaction -- "why this is so
inconsistent" -- was the right read: multiple different, real causes
were interacting, not one flaky mechanism.

Checked the diagnostic's isWindowActive/isMouseGrabbed breakdown across
this session: 1475 consecutive ticks logged `isWindowActive=false,
isMouseGrabbed=true` -- meaning grabMouse() clearly *had* succeeded at
some earlier point (mouseGrabbed is sticky, doesn't clear itself when
focus is later lost again) -- yet isDestroying stayed false the entire
stretch regardless. mouseGrabbed=true alone isn't sufficient.

Re-examined continueAttack's full gate (not just the isMouseGrabbed
clause already found): `this.continueAttack(this.screen == null &&
!instantAttack && this.options.keyAttack.isDown() && this.mouseHandler.
isMouseGrabbed())`. `this.screen == null` is a second, entirely
independent requirement -- losing real OS window focus typically
auto-opens Minecraft's own pause screen, and continueAttack bails out on
ANY open screen regardless of what mouseGrabbed says. MouseHandler.
grabMouse() already calls `setScreen(null)` internally, but only inside
its own `isWindowActive()` guard -- useless on any tick where focus is
still lost (grabMouse() doesn't even reach the setScreen(null) line).

Fixed by having `BlockBreaker.holdAttackKey()` call `Minecraft.
getInstance().setScreen(null)` directly and unconditionally (not gated
on isWindowActive), before grabMouse(), every tick a break is actively
held -- removes the screen-blocking cause on its own merits rather than
hoping grabMouse() clears it as a side effect. Also added `mc.screen` to
dumpRealDestroyState's diagnostic output to make this directly
observable going forward instead of inferring it. Not yet re-tested
live -- this is the third fix layered on top of the same underlying
"real progress, never completes" symptom family (mouse-grab, near-
vertical-aim singularity, now open-screen), each addressing a genuinely
different concrete cause found via the same reflection diagnostic.

## Screen/mouse-grab fix confirmed working, but the near-vertical-aim offset was too small to actually fix the geometry it targeted

The screen-clearing fix worked as intended: a subsequent live stall on a
dirt block logged `isMouseGrabbed=true, screen=null` -- both of
continueAttack's real gating conditions satisfied -- yet isDestroying
was still false, destroyBlockPos still one block off (locked onto
`{-463,88,-1426}` while the real target was `{-463,87,-1426}`, directly
above it -- the bot's own standing-height block, not a horizontal
neighbor). This confirmed the mouse-grab/screen causes are both
genuinely fixed now; what's left is purely the near-vertical-aim
geometry issue again, this time exposed on its own without the other
two masking it.

Checked the numbers precisely: the logged pitch (83.648544) matched
exactly what the near-vertical offset fix (previous section) would
produce for this exact position -- confirming the offset logic WAS
running -- but the offset itself (0.3 blocks toward one corner) was far
too small to matter against a ~2.12-block vertical drop to the target:
`atan2(2.12, 0.3ish)` only comes out a few degrees shallower than dead-
center's ~86.6 degrees, nowhere near enough to reliably clear the
degenerate near-vertical zone.

Fixed by using a much larger offset: 0.48 blocks on both axes (diagonal
toward one corner), the largest value that still guarantees staying
within the target block's own footprint (center 0.5 +/- 0.5 spans the
full unit range) -- for the same 2.12-block-drop case this brings pitch
down to ~72 degrees instead of ~83.6, a much more decisive move away
from the singularity rather than a token nudge. Also raised the
activation threshold from 0.3 to 0.5 blocks of horizontal offset so more
of these near-vertical cases actually trigger the correction. Not yet
re-tested live.

## Window-focus theory directly disproven by the user's own test -- and a real finding about setDown() vs. real clicks that still doesn't fully explain the dirt failure

The user ran the exact isolating test from before again and this time
explicitly confirmed via chat ("see? that was mined without focus") that
a real completion happened while isWindowActive was false the entire
surrounding session (1209/1239 ticks false) -- direct proof the
isWindowActive/mouse-grab theory was wrong as a blanket explanation, not
just unconfirmed. Instructed to stop iterating on the focus angle.

Re-examined the successful break's own diagnostic line and found
isDestroying=true appeared only twice in the *entire* session's log
(thousands of tryBreak calls) -- both for this one real success,
destroyProgress climbing 0.0 -> 0.7555555 in a single real tick,
completing on the next. So a real completion, when it happens, happens
almost instantly (1-2 real ticks) -- confirming the multi-tick "settle"
delays seen in earlier "working" runs were the settle window/log
cadence, not the actual mining duration.

Investigating why the *next* target (a dirt block, pathfinding digging
through an obstacle on the way to the next collect target) never got a
fresh startDestroyBlock/continueDestroyBlock call at all led to a real,
previously-unnoticed finding in decompiled KeyMapping: `consumeClick()`
decrements a `clickCount` field that is ONLY ever incremented by
`KeyMapping.click(key)`, itself only called from real GLFW input
callbacks -- `setDown(true)` (all this class has ever called) only ever
affects `isDown()`, never `clickCount`. This means `Minecraft.
startAttack()` (gated behind `keyAttack.consumeClick()`) can never fire
from `holdAttackKey()` alone -- only `continueAttack`'s own fallback
(`continueDestroyBlock`'s `else` branch, `return this.
startDestroyBlock(...)`, reached whenever `sameDestroyTarget(pos)` is
false) can ever start a *new* target through this mechanism. That
fallback does NOT depend on consumeClick, so on paper `continueAttack`
alone should still be sufficient -- and was, for the one real success
observed. But it explicitly requires `hitResult.getType() ==
HitResult.Type.BLOCK` (vanilla's own real per-frame raycast) to even be
called with a legitimate position at all.

Added `Minecraft.hitResult` itself (a public field, no reflection
needed) to dumpRealDestroyState's diagnostic output -- this is the one
remaining unknown: whether vanilla's real raycast is missing the dirt
target entirely (hitResult.getType() == MISS, explaining why neither
startDestroyBlock nor continueDestroyBlock's real branches ever fire) or
landing somewhere unexpected. The dirt target's own aim angle (~67
degrees pitch, horizontal offset ~0.81 blocks -- outside the near-
vertical-offset threshold, so aimAt used plain dead-center aiming) isn't
obviously extreme, so this needs direct hitResult visibility rather
than further inference from aim math alone. Not yet re-tested live.

## Pathfinding jumps never sprinted, consistently falling short on real gap jumps

Reported live: a saved-position layout (`!remember a`/`!remember b`)
with A low and B on a raised platform, connected by a real jump gap
(not a plain step-up) -- the bot walked to the takeoff point and jumped,
but consistently fell short, "not fast enough to make the jump."

`MovementIntent.sprint` (and the `sprint` field threaded all the way
through to the real `Input` record in `MinebotInput.tick()`) already
existed and was already fully wired -- but nothing anywhere in
`resolveMovementIntent` ever actually set `intent.sprint = true`. Every
jump this mod has ever executed, this entire project, used vanilla's
plain walking-jump distance (no forward speed built up beforehand), not
the meaningfully longer distance a real sprint-jump covers. Every move
in the A* graph (`Movements.getMoveJumpUp`/`getMoveDiagonal`) is a
single adjacent-cell step at most 1 block away horizontally -- there's
no explicit "long gap jump" move type -- so a waypoint that's only
reachable via a real running leap (not a plain step-up) was still
planned and accepted as valid (a real human player could clear it with
a sprint-jump), but this mod was only ever executing the shorter,
non-sprinting version of that same jump.

Fixed by setting `intent.sprint = true` alongside `intent.jump = true`
in `resolveMovementIntent` (same `dy > MAX_STEP_HEIGHT_TRIGGER` gate) --
sprinting into every jump waypoint, not just ones a planner might flag
as tight (there's no such flag, and sprinting into a jump that would
have succeeded anyway isn't harmful). Not yet re-tested live.

## The jump-gap failure wasn't just missing sprint -- mid-air Y volatility was racing the path plan ahead of the bot's real position

After the sprint fix (previous section), live re-testing of the
A(low)/B(raised-platform, reachable only by a real running jump) layout
still failed -- but the log data told a more specific story than "jump
too short": the bot correctly climbed the staircase toward B (x/y
tracking right), got within ~0.4 blocks of the target Z, then reversed
180 degrees mid-air and fell all the way back down, repeating the same
climb over and over rather than ever landing on B. The user confirmed
via their own manual test that the jump genuinely requires sprinting
(validating the sprint fix's premise) -- so this was a second, distinct
bug layered on top of the same live report, not evidence the sprint fix
was wrong.

Root cause: `PathTracker.nextWaypoint`'s "reached" check only compares
`Math.floor(selfX)/`Math.floor(selfZ)` against a waypoint's integer x/z
plus a loose `Math.abs(selfY - waypoint.y) < 1.0`. A straight-up
staircase climb has several consecutive waypoints sharing the exact
same (x, z) column, differing only by y -- so that loose Y tolerance was
the *only* thing distinguishing "reached step 3" from "reached step 5".
During a real jump's vertical arc, selfY swings by more than a full
block within a handful of ticks (real gravity, not a bug) -- easily
satisfying that 1.0 tolerance against several different waypoints in
quick succession while genuinely still airborne, well before landing on
any of them. Each spurious match popped a waypoint the bot hadn't
actually reached, racing `currentPath` far ahead of the bot's real
position -- exactly matching the observed symptom: aim reversing
mid-air chasing whatever got spuriously "reached" next, then falling
back down with no waypoint left to walk toward, restarting the whole
climb.

Fixed by threading `player.onGround()` through to `nextWaypoint`
(`MinebotMod.resolveMovementIntent`'s call site) and using a much
tighter Y tolerance (0.1, a real landed foothold) while airborne,
keeping the original 1.0 tolerance once back on solid ground (real
walking Y jitter is tiny, nothing like a jump arc's swing). Not yet
re-tested live -- this needs the same A/B jump-gap layout to confirm
both fixes together (sprint distance + waypoint-advance stability)
actually clear the jump now.

## The waypoint-Y fix didn't change the symptom either -- promoted the path-planning log to visible so the actual chosen route can finally be seen

A screenshot from the user clarified the real layout: X (a solid
platform) and B (a separate floating platform) are divided by genuine
open air with nothing in between, but a real, walkable detour exists
via a staircase structure off to the side (~7 blocks longer than the
direct jump). Re-tested live after the waypoint-Y-tolerance fix
(previous section) -- exact same repeating symptom (climb, bounce near
X/B's shared X coordinate, fall back, repeat), meaning that fix, while
plausibly a real improvement for genuine staircase climbs, wasn't what
was actually causing this specific failure.

Every move `Movements.getMoveJumpUp`/`getMoveDiagonal` can generate is
a single adjacent-cell step (dx/dz each in {-1, 0, 1}) -- there is no
move type in this A* graph capable of spanning a real multi-block open-
air gap at all, so on paper the direct "jump" from X to B should never
even exist as a graph edge, forcing A* to route via the real (longer)
detour instead. That it isn't doing so, given the user confirmed the
detour exists and is walkable, means either the detour itself isn't
being found/offered by `getNeighbors`, or something else is causing A*
to terminate the search at/near X thinking it's already close enough
(checked GoalNear.isEnd's straight-line distance against the bot's
observed bounce position near X -- didn't find it satisfied at either
X's resting position or the jump's peak height, so that specific theory
doesn't hold either, at least not with the approximate coordinates
available from position-log inference alone).

Rather than keep guessing at A* internals from position traces,
promoted `PathTracker.maybeReplan`'s existing (but debug-level, and so
invisible on this client's default log config) "planned path of N
waypoints: ..." / "no path found" log lines to info -- the next live
`!goto b` attempt will show the exact real route (or lack of one) A*
actually computed, which is the direct way to settle whether this is a
missing-detour-discovery bug, a goal-satisfied-too-early bug, or
something else in the graph/heuristic entirely.

## Root cause, finally confirmed with real path data: getMoveParkourForward priced every gap jump as cheap as walking one block, so A* always preferred the unreliable jump over the real detour

Promoting `PathTracker.maybeReplan`'s path-planning log to visible (see
previous section) immediately gave the real answer on the next live
`!goto b`: `pathfinding: planned path of 5 waypoints (status=SUCCESS,
cost=8.0): [Move(-545, 103, -1313, cost=1.0), Move(-545, 104, -1312,
cost=2.0), Move(-545, 105, -1311, cost=2.0), Move(-545, 106, -1310,
cost=2.0), Move(-545, 107, -1313, cost=1.0)]`. The final edge, from
`(-545, 106, -1310)` to `(-545, 107, -1313)`, is a 3-block horizontal
jump (`dz = -3`) at a mere `cost=1.0` -- this was the previously-missed
piece: `Movements.getMoveParkourForward` (gated behind `allowParkour`/
`allowSprinting`, both `true` by default) is a real, already-existing
long-jump move type spanning 2-4 blocks (`maxD = allowSprinting ? 4 :
2`), completely separate from `getMoveJumpUp`/`getMoveDiagonal` (both
genuinely limited to single adjacent cells, as originally assumed) --
this method was read past/missed in the earlier investigation of "is
there a gap-jump move type at all."

The actual bug: `getMoveParkourForward` priced every parkour jump at a
flat `cost = 1.0`, identical to `getMoveForward`'s cost for walking a
single ordinary block, regardless of jump distance `d` (2, 3, or 4
blocks). A* had no reason to ever prefer a real, longer, reliably-
walkable detour (the staircase the user described, ~7 blocks, cost
~7.0) over a "free" 3-block sprint-jump edge costing only 1.0 -- even
though this whole investigation already found several concrete, real
ways a sprint-jump's actual execution can fail (aim/waypoint-tracking
precision during the airborne arc, needing real run-up distance before
liftoff) that a plain walked step simply doesn't have.

Fixed by scaling the parkour move's cost quadratically with jump
distance (`cost = d * d` -- 4.0 for a 2-block jump, 9.0 for 3, 16.0 for
4), reflecting that landing precision gets meaningfully harder the
farther the jump, not just linearly harder. This should make A* only
choose a parkour jump when there's genuinely no reasonable walkable
alternative nearby, rather than always preferring it as strictly
cheaper than walking. Not yet re-tested live -- combined with the
earlier sprint-intent and waypoint-Y-tolerance fixes, the next `!goto
b` should either route via the real detour (if the quadratic cost now
makes it win) or, if a jump-based route is still genuinely cheapest,
actually execute it successfully now that sprint + waypoint-tracking
are both fixed.

## !goto stopped 2 blocks short of the exact requested coordinates by design -- tightened to 0.2

`GOTO_STOP_DISTANCE` (movement.py) and the mod-side `GoalNear` range it
drives (both movement's own stop condition and the A* goal radius --
see PathTracker.maybeReplan) were both 2.0 blocks, inherited from
`!follow`/`!give`'s own stop distances where "close enough" genuinely
is the right behavior (you don't want to walk into another player).
`!goto` was never meant to have that same slack -- a request for exact
coordinates should land there, not just "nearby". Tightened
`GOTO_STOP_DISTANCE` to 0.2 (tight enough to look/feel exact, loose
enough that collision/floating-point noise right at the target doesn't
leave the bot fighting to close the last hundredths of a block forever
-- `FOLLOW_STOP_DISTANCE`/`GIVE`'s own distances are untouched, still
2.0, since following/giving-to a moving player still wants real
standoff room). Since `send_goto` always includes `stop_distance`
explicitly, this is a pure Python-side change -- no mod rebuild needed,
just a backend restart. All 164 tests still pass.

## A jump falling consistently short by a fraction of a block -- stairs' real top surface is up to 0.5 lower than the pathfinder assumed

Reported live: a fresh `!goto b` (different A/B pair than the earlier
jump-gap layout) got stuck jumping in place forever at a single
ordinary (non-parkour, cost=2.0) step-up waypoint -- position logs
showed the bot's Z consistently falling ~0.4-0.5 blocks short of the
waypoint's own Z every single attempt, an oscillating loop rather than
progress. The user correctly diagnosed the real cause before any log
analysis pinned it down: the takeoff/landing block involved was a
stairs block, and pathfinding's block model treats every "walkable"
block as a full cube.

Confirmed in `Movements.getBlock`'s own pre-existing docstring: stairs
(and slabs) are deliberately classified `physical` (walkable, matching
real vanilla collision -- fixed once already for a live report of a
stairs block blocking a doorway being treated as an impassable wall),
but that same docstring already flagged, unaddressed until now, that
"Movements doesn't yet model the extra half-step height difference" --
`BlockInfo.height()` returned a flat `y + 1.0` for any physical block,
stairs included, even though a real stairs block's usable top surface
can sit at `y + 0.5` depending on which side of it a jump actually
lands on (not tracked -- would need real stair-facing/half geometry).
Every `stepHeight`/jump-height-difference calculation in `Movements.java`
(`getMoveJumpUp`, `getMoveDiagonal`, `getMoveParkourForward`) derives
from `BlockInfo.height()`, so a jump involving a stairs block was
planned assuming up to half a block more clearance/reach than actually
exists -- exactly matching a jump that looked feasible on paper but
fell short in practice, every time, identically.

Fixed by adding a `stairsOrSlab` flag to `BlockInfo`, set in
`Movements.getBlock` for any stairs block or single (non-double) slab
whose `isCollisionShapeFullBlock` is false, and having `height()`
report `y + 0.5` for those instead of `y + 1.0`. Deliberately
conservative rather than exact (this doesn't track stair facing or
`SlabType.TOP` vs `BOTTOM`, both of which can have a real top surface
at a different height than 0.5) -- always reports a surface at or below
the true height, so a jump the planner accepts is still guaranteed
physically possible; it may reject a few jumps that were actually fine,
which is a far smaller cost than repeatedly failing one it thought was
safe. Not yet re-tested live.

## !collect carrot always failed -- DropTable had no entry for any farmland crop

Reported live: `!collect carrot 1` reported "no more carrot found
nearby" every time, standing in a real carrot farm. `source_for
("carrot")` fell through DropTable's fallback (no entry -> returns the
query itself), so the mod searched for a block/entity literally named
"carrot" -- which doesn't exist; the real growing-crop block is
`minecraft:carrots` (plural), only the harvested item is singular
`carrot`. Same gap for potatoes (`potatoes` block) and beetroot
(`beetroots` block) -- wheat is unaffected since its block id and item
id are already both `wheat`.

Fixed by adding `carrots -> [carrot]`, `potatoes -> [potato]`,
`beetroots -> [beetroot]` to DROPS_FROM (SOURCE_FOR's reverse mapping
is auto-built from these, same as every other entry). Not yet re-tested
live.

Known gap not fixed here (out of scope for this report, noted for
later): `BlockFinder.findNearestBlock`'s match predicate only checks
block *type*, not growth-stage blockstate (`age` property) -- `!collect
carrots` could target an immature plant, break it, and yield nothing.
Not a crash (Python's own empty-drop retry counter already tolerates
an occasional miss -- see MiningController.collect), just wasteful
(destroys a still-growing plant instead of skipping it), and only
matters for farmland crops specifically among everything DropTable
currently covers.

## The carrot-recognition fix worked, but mining still didn't start -- the bot's own empty hand was never replaced

Confirmed the DropTable fix above worked live -- `!collect carrot 1`
correctly found and targeted a real carrots block this time -- but
never actually mined it. Log showed `mainHand=0 minecraft:air` and
`destroySpeed=0.0` (carrots are a real 0-hardness vanilla block,
breakable instantly by anything) with `mining: keeping slot 3
(minecraft:air) -- nothing carried beats it` logged every tick.

Root cause: `maybeSwitchToBestTool` seeded its `bestSpeed` baseline from
the *currently selected* item's own `getDestroySpeed`, without checking
whether that item was actually empty first. Slot 3 was selected but
genuinely empty (the bot had given away its `netherite_shovel` from
that slot via an earlier `!give` and never reselected anything real
afterward) -- `ItemStack.EMPTY.getDestroySpeed(state)` returns vanilla's
plain bare-hand rate (1.0), and since every real carried item also
scores exactly 1.0 against a 0-hardness block (no tool gets a bonus
against carrots), nothing in the scan ever strictly beat that baseline
-- a tie, not a loss, so the empty slot was never replaced. The scan
loop already excludes empty stacks from being *candidates* to switch
to; the actual selected-item baseline just never got the same
treatment.

Fixed by forcing `bestSpeed = -1.0f` whenever the currently-selected
stack is empty, so any real (non-empty) item in inventory always counts
as strictly better regardless of its own raw speed -- bare hands should
never be preferred over holding literally anything. Not yet re-tested
live.

## Walking to a crop trampled the farmland it stood on

Reported live, right after carrots collecting successfully: the bot was
"jumping on farmland, destroying it." Real vanilla mechanic
(FarmBlock.fallOn) -- landing on tilled soil rolls a chance to revert it
to plain dirt, scaling with fall distance; a plain walk across farmland
never triggers this (the roll is fall-distance-gated), but each real
jump lands and rolls independently, so repeated jumping while
pathfinding across a farm plot to reach a crop makes trampling likely
even if any single jump's own chance is low.

Fixed by suppressing `intent.jump` whenever the bot is currently
standing on farmland (`level.getBlockState(player.blockPosition().
below()).is(Blocks.FARMLAND)`), alongside the existing `walking`/
`MAX_STEP_HEIGHT_TRIGGER` gate -- walking across the plot is untouched
(never triggered trampling to begin with), only the jump itself is
held back while over farmland. Not yet re-tested live.

## Pathfinding was mining through farmland as a dig-through obstacle -- excluded unconditionally

The jump-suppression fix (previous section) worked, but surfaced a more
direct version of the same underlying "the bot damages farms" concern:
"is mining the farmland!, lets prohibit farmland mining at all."
Log confirmed real `mining[pathfinding]: tryBreak(...) -- new target
..., state=Block{minecraft:farmland}, destroySpeed=0.6` lines -- the
pathfinder was genuinely breaking farmland blocks, not just trampling
them.

Root cause: farmland has a real, positive `getDestroySpeed` (0.6, like
dirt), so `Movements.safeOrBreak`'s existing guards (the -1.0F
"unbreakable" sentinel check, the ChestBlock exclusion) never caught
it -- whenever a waypoint needed to pass *through* a farmland block at
head/body height (not just stand on it, which was already handled
correctly via isFullBlock/isSolid), it got added to toBreak like any
other diggable obstacle.

Fixed by adding an explicit `state.is(Blocks.FARMLAND)` exclusion to
`safeOrBreak`, right alongside the existing chest exclusion -- unlike a
chest (which just risks spilling contents), the reasoning here is that
digging through a real player's farm is destructive in a way ordinary
terrain isn't (a stone/dirt block regrows nothing when broken; a crop
plot represents deliberate player effort), so it's excluded
unconditionally rather than left to the normal cost-based obstacle
logic -- forces the pathfinder to route around instead. Not yet
re-tested live.

## !attack/!kill: combat as its own ControlState mode, not a reuse of COLLECT

PENDING.md's "Combat" section had been sitting at all-⬜ since the
architecture pivot -- `!collect <entity> <count>`'s minimal kill loop
(`tickCollectEntity`, walk into `COLLECT_MELEE_RANGE`, repeated
`MultiPlayerGameMode.attack(player, target)` + `player.swing` until the
target is removed) was explicitly scoped as "not the full !attack/!kill
feature" when it landed (see the `!collect` write-up above) -- this adds
that standalone feature.

**Reused the attack interaction itself, not the mode.** `tickCollectEntity`
already proved the real interaction works with zero keybind-hold tricks
(unlike `BlockBreaker`'s `keyAttack`-hold discovery, or `FoodEater`'s
`keyUse`-hold discovery) -- `MultiPlayerGameMode.attack(Entity)` is a
direct, complete, single-tick API call, since a melee hit (unlike a
multi-tick block break) has no client-side progress to accumulate across
ticks the way `startDestroyBlock`/`continueDestroyBlock` does. `tickAttack`
(new, `MinebotMod.java`) calls the exact same two lines COLLECT's entity
branch does.

The *mode* itself is new (`ControlState.Mode.ATTACK`, its own
`attackQuery`/`attackRadius`/`attackHasTarget`/`attackTargetStuckTicks`
fields), not a repurposing of `Mode.COLLECT` -- COLLECT's shape (single
atomic attempt, no count, Python's own drop-confirmation loop deciding
when to ask for another) is entirely about "did an item land in
inventory", a question `!attack` has no equivalent of. `!attack`/`!kill`
is single-target end-to-end, matching PENDING's own phrasing ("attack
and kill the nearest entity of a given type", not "kill N of them" --
that's already `!collect <entity> <count>`'s job for whoever wants
counted kills-for-drops instead of a fight).

**"Nearest hostile" resolution (`!attack` with no argument) needed a
real hostility check that didn't already exist anywhere in this
codebase.** Considered `instanceof Monster` first (the more obvious-
looking check) but confirmed via the real mapped 26.1.2 jar
(`javap` against `minecraft-merged-deobf-26.1.2.jar`) that `Monster` is
an abstract class implementing a separate `net.minecraft.world.entity.
monster.Enemy` marker interface -- and that at least one real vanilla
hostile, `EnderDragon`, implements `Enemy` directly without extending
`Monster` at all (`WitherBoss` and ordinary mobs like `Creaking` do
extend `Monster`, and so are covered either way). `instanceof Enemy` is
therefore the strictly more general, correct check -- everything
`instanceof Monster` would catch is also caught by it, plus the
`Monster`-less exceptions. `EntityFinder.findNearestHostile` (new) is
the same real-sphere-filtered-AABB-scan shape `findNearestEntity`
already used for `!find`/`!collect`'s type-based lookup, just with an
`Enemy`-membership predicate instead of a registry-id match. A `query`
given to `!attack` (`!attack cow`) skips this hostility check entirely
and resolves via the existing entity-only lookup instead (no block
fallback the way `!find`/`!collect` have -- `!attack <type>` only ever
means "fight that entity", never "mine that block") -- matching
mindcraft's own `attackNearest`, which has no such hostility restriction
either.

**Two ways to abandon an in-progress attack, one directly requested
beyond mindcraft's own shape.** A give-up-on-unreachable timeout
(`ATTACK_TARGET_TIMEOUT_TICKS`, 200 ticks/10s) mirrors
`COLLECT_TARGET_TIMEOUT_TICKS` exactly -- same reasoning: a target that's
fled out of pathfinding's reach, or stuck behind geometry the bot can't
route around, shouldn't strand the command forever. The second,
explicitly asked for beyond "just chase and melee until dead": a
self-preservation health check (`ATTACK_LOW_HEALTH_FRACTION`, 25% of max
health) that aborts and reports "had to retreat, health too low" the
moment the bot's own health drops that low, checked every tick `tickAttack`
runs (not just once at the start) so a fight that starts safe but turns
bad partway through still aborts promptly rather than only checking
health before engaging. This runs independently of (and doesn't replace)
`FoodEater`'s own auto-eat -- eating may not win the race at all (no food
carried, or hunger already full so eating is gated off entirely, see
`FoodEater`'s own docstring), so combat needing its own last-resort
abandon condition is a real, separate safety net rather than redundant
with auto-eat.

**Wire protocol**: `{"type":"attack","query":null|"zombie","radius":64}`
-- `query` omitted/`null` means "nearest hostile". Answered by a single
fire-and-forget `attack_result` event (`success`, `query` echoed back,
`reason` present on failure) -- same one-attempt-in-flight-at-a-time
shape `find_result`/`collect_result`/`dig_down_result` already
established, since chat commands are dispatched one at a time and
`CombatController.attack` (new, `minebot/bot/combat.py`) only ever has
one `_pending_attack_result` future outstanding. `!kill` is registered
as a second `Action` pointing at the exact same `CombatController.attack`
handler/params as `!attack` -- `ActionRegistry`'s plain name-keyed dict
has no dedicated alias concept, so two full `Action` entries sharing one
handler is the simplest way to get a second name without inventing one.

## !save refactored to take a kind (location|chest), and LookingAt extracted as a reusable raycast utility

`!save <name>` (the caller's current position) became `!save <kind> <name>`
-- `!save location a` is the old behavior unchanged; `!save chest a`
saves the position of whichever chest the *caller* is currently looking
at, not any chest near the bot.

**"What block is the caller looking at" needed a real per-player
raycast, which had existed once before and been deliberately deleted.**
An earlier `!debug` implementation (`ControlState.Mode.DEBUG_BREAK`,
long since removed -- see the "cobblestone drops confirmed working"
section above) raycast from the chat sender's own eyes specifically to
test block-breaking against a real player's aim rather than the bot's
own unfocused window's crosshair, using `Level.clip(ClipContext)` from
`Entity.getEyePosition()`/`getViewVector()` -- ordinary, real,
server-synced entity state available for *any* visible entity, not just
the bot's own `LocalPlayer`. That whole mechanism was intentionally
deleted once `!debug` was redesigned into a narrower hotbar-swap test
with no raycast involved at all. `!save chest` needs the exact same
capability again, so rather than re-deriving it inline a second time,
it's pulled out as a real standalone utility this time: `LookingAt.
blockPos(Entity, ClientLevel, maxDistance)` (new,
`minebot/mod/LookingAt.java`) -- a plain static method with no
mode/tick-loop involvement, since (unlike the old `!debug`) this is a
single instant lookup, not something that needs per-tick continuation.
Intended to be reused again for PENDING.md's own planned `!look`/`!use`
commands, both of which will need the same "what is this entity looking
at" question answered.

**Resolution is mod-side, not Python-side, for the same reason `!find`/
`!collect`/`!attack` all are.** `EntityTracker` only mirrors position
(x/y/z) for tracked players -- yaw/pitch were never broadcast, since
nothing needed them before this. Rather than adding yaw/pitch to the
`entity` wire event and reimplementing a raycast in Python (which has no
access to real chunk/block data at all -- the mod is the only thing
that ever touches the actual Minecraft world), `!save chest` sends the
caller's already-known entity id (`MovementController.save`, resolved
via the same `EntityTracker.find_by_name` lookup `!goto`'s player-name
case already uses) and lets the mod do the real lookup: `MinebotMod.
handleFindChest`/`runFindChest` resolves the id back to a live `Entity`
via `level.getEntity(id)`, raycasts via `LookingAt.blockPos` using
`FIND_CHEST_MAX_DISTANCE` (4.5 blocks -- matches `BlockBreaker`/
`DoorOpener`'s own real-interaction-reach convention), and confirms the
hit is actually a chest (`instanceof ChestBlock`, which also covers
`TrappedChestBlock` since it extends `ChestBlock`) before reporting
success. Same thread-hop-to-the-tick-thread pattern `handleFind` already
uses (`Minecraft.getInstance().execute(...)`), for the same reason:
iterating live entities/chunks off the network thread is unsafe.

**Wire protocol**: `{"type":"find_chest","entity_id":42}` -> a single
fire-and-forget `find_chest_result` event (`found`, `x`/`y`/`z` on
success) -- same one-in-flight-at-a-time shape `find_result` already
established, since `!save` is a chat command and only one is ever
dispatched at a time. `MovementController._pending_find_chest` is the
same single-slot pending-future pattern `_pending_find` already uses.

## A separate observer WebSocket for a live wire-message viewer (minebot-frontend)

A third repo, `minebot-frontend` (React/TypeScript/Tailwind), wants to
show every wire message flowing between this backend and minebot-mod
live in a browser -- not from the log files (gitignored, local-only,
and only timestamped at all when `DEBUG=true`; see `timing.py`'s own
docstring), a real live push.

**New, separate server, not reusing the control channel.** `ModBridge`'s
own WebSocket server (`bridge/client.py`) only ever expects exactly one
connection that matters -- the mod itself; `events()`'s reconnect
handling is explicitly built around "there is one connection, and it
may occasionally drop and come back," not "many independent clients."
Rather than bolt frontend-viewer semantics onto that, `ObserverServer`
(new, `minebot/bridge/observer.py`) is its own WebSocket server on a
separate port (`MINEBOT_OBSERVER_PORT`, default 47894, alongside the
existing `MINEBOT_MOD_PORT` 47893) -- any number of browser tabs can
connect, and zero connected is the common case (nobody has the frontend
open most of the time) and costs nothing (`broadcast` no-ops immediately
if `self._connections` is empty).

**Hooked at the two real chokepoints every wire message already passes
through.** `ModBridge._send` (every command Python sends) and
`ModBridge._parse_event` (every event the mod sends back) are the only
two places a wire message exists as a plain dict before being
serialized/after being deserialized -- `ObserverServer.broadcast` is
called from both, right next to the existing `log_timing` calls,
covering both directions (Python->mod and mod->Python) from the same
two spots that already had everything needed to log them. `ModBridge`
holds an optional `ObserverServer` (`None` if not configured, e.g. in
every existing test that constructs a bare `ModBridge` without one) --
every call site already treats "no observer" the same as "no observers
connected," so passing `None` doesn't need special-casing beyond the
constructor's own default.

**Deliberately fire-and-forget, never on the hot path.** `broadcast` is
a plain (non-async) method that schedules a send to each connected
client via `asyncio.ensure_future` and returns immediately -- a slow or
stuck browser tab must never be able to backpressure real command
dispatch or event processing, the actual game-facing behavior this
whole project exists for. A send that fails (client disconnected
mid-flight) is caught and logged at debug, not surfaced anywhere else.

**Wire shape sent to observers**: `{"direction":"sent"|"received","message":{...the original message, "type" included...},"timestamp":<wall-clock float>}`.
Uses `time.time()` (wall clock) rather than `bridge/timing.py`'s
monotonic `now()` -- a live viewer wants to display a real clock time,
not an offset from process start that's meaningless without knowing
when the process itself started.

## !attack never selected a weapon -- WeaponSelector/BowShooter added, bow aim ported from AbstractSkeleton's own AI

Reported live: `!attack`/`!kill` fought with whatever happened to
already be selected, no weapon selection at all -- the original
implementation only ever called `MultiPlayerGameMode.attack`, the same
minimal mechanism `!collect <entity>`'s kill loop already used, with no
equivalent of `BlockBreaker.maybeSwitchToBestTool`'s tool-selection for
mining. Explicit ask: prefer a real bow over melee if one is carried.

**Melee weapon scoring needed real attribute data, not an Item field.**
Confirmed via decompiled `ItemStack`/`ItemAttributeModifiers` source
that modern vanilla has no per-item "attack damage" field on `Item`
itself at all -- weapon damage is entirely data-driven through
`ItemStack.forEachModifier(EquipmentSlot.MAINHAND, ...)`, summing
whatever `Attributes.ATTACK_DAMAGE` modifiers a stack actually carries.
The exact same "data-driven component, not a subclass hierarchy" shape
`BlockBreaker`'s own docstring already found for mining tools
(`Tool` component replacing `PickaxeItem`/`DiggerItem` subclasses).
`WeaponSelector.attackDamageOf` (new) does this scan; `WeaponSelector.
choose` mirrors `maybeSwitchToBestTool`'s shape overall (scan inventory,
score each candidate, return the best) but scores real attack damage
instead of `getDestroySpeed`.

**A bow is only a real choice if there's also ammo -- `Player.
getProjectile(weapon)` is the exact same real lookup vanilla's own
`BowItem.releaseUsing` uses** to find matching ammo (confirmed via
decompiled bytecode) before it will actually fire, so `WeaponSelector.
findBowWithAmmo` asks the identical question a real draw attempt would,
rather than separately scanning for `Items.ARROW` and hoping that
matches whatever "counts" as ammo for a given bow (spectral arrows,
tipped arrows, etc. all still count).

**Real bow-drawing needed the same keybind-hold discovery FoodEater/
BlockBreaker already made, not a fresh investigation.** Both of those
classes independently found (via live A/B testing against real human
input) that a direct API call to start/complete a use-item interaction
does not reliably work, even though every individually-checked mechanism
looks correct on paper -- the fix both times was holding the real
`keyUse` keybind (`Options.keyUse.setDown(true/false)`) instead, letting
vanilla's own `Minecraft.handleKeybinds()` (called every tick regardless
of this mod) drive the actual interaction the same way a human's held
right-click would. `BowShooter` (new) applies this same fix
preemptively for drawing a bow, rather than re-discovering the identical
failure mode a third time.

**Full draw, every shot, per explicit instruction.** `BowItem.
getPowerForTime(ticks)` (decompiled: `min(1.0, ((t/20)² + (t/20)·2) / 3)`)
saturates at `t = MAX_DRAW_DURATION` (20 ticks/1s) -- `BowShooter` always
holds the full 20 ticks before releasing (checked via `LivingEntity.
getTicksUsingItem()`, the real elapsed-hold counter vanilla's own use-item
state machine maintains once `keyUse` is held), rather than releasing
earlier for a faster but weaker/less accurate partial-draw shot.

**Aim direction ported directly from `AbstractSkeleton.
performRangedAttack`, not solved from scratch -- confirmed live insight:
"skeletons already use bows, can we use that code?"** Investigated
whether real client-side arrow ballistics needed solving (arrows fall
under gravity -- confirmed `AbstractArrow.getDefaultGravity()` returns
`0.05` blocks/tick², decompiled) before realizing the *server's* own
ranged-mob AI already solves this exact problem and its formula is
directly readable from decompiled bytecode. `AbstractSkeleton.
performRangedAttack` (decompiled): computes `dx`/`dz` to the target,
`dy` to roughly a third of the target's body height
(`target.getY(0.333)`) from the arrow's own spawn position, and
`horizontalDistance = sqrt(dx² + dz²)` -- then calls
`Projectile.spawnProjectileUsingShoot(arrow, level, weapon, dx,
dy + horizontalDistance * 0.2, dz, 1.6f, inaccuracy)`. The key
realization: **vanilla doesn't compute a solved launch angle at all** --
it aims the *raw* direction vector to the target, just with a fixed,
distance-proportional lift added to the direction's own Y component
(more lift the farther away, compensating for the longer flight time
gravity has to act on), at a flat velocity (`1.6`, not distance-scaled).
`BowShooter.aimAt` ports this exact formula (`ARC_LIFT_PER_BLOCK = 0.2`)
converted to yaw/pitch via the same `atan2`-based convention already
established in `NearbyPlayerLookAt`/`BlockBreaker.aimAt` (both already
confirmed via decompiled `Entity.calculateViewVector`) -- reusing the
game's own already-tuned formula is simpler and more trustworthy than
re-deriving an equivalent one by hand, and this class of "borrow the
mob AI's own already-solved answer" is a new, generally-useful pattern
for future combat/ranged-interaction work in this mod.

**Real player release velocity differs from a skeleton's, confirmed via
`BowItem.releaseUsing`'s own decompiled bytecode** (not used directly,
but worth recording): a *player* shot's velocity is `power * 3.0`
(`power` from `getPowerForTime`, so `3.0` at full draw) with inaccuracy
`0` at exactly full power or `1` otherwise, whereas a *skeleton*'s is a
flat `1.6` regardless of draw (skeletons don't "charge" a shot the way
a player does) with inaccuracy scaling inversely with world difficulty
(`14 - 4 * difficultyId`). Both are ultimately server-authoritative --
the client (this mod, or a real human) only ever controls *when* to
release and *which direction the player entity is currently facing* at
that moment; `BowItem.shootProjectile`'s own decompiled bytecode
confirms the server reads the shooter's live `getXRot()`/`getYRot()` at
release time, not any client-passed direction vector, so aiming
correctly via `setYRot`/`setXRot` before releasing is both necessary and
sufficient -- there's no other hook for the client to influence the shot.

**Engagement range and weapon re-selection are both live, per-tick, not
fixed once per target.** `ControlState.stopDistance` (already a plain
mutable field every other goal-setter also assigns once) is set to
`ATTACK_BOW_RANGE` (15, matching the real `BowItem.DEFAULT_RANGE`) or
`ATTACK_MELEE_RANGE` (3) depending on `WeaponSelector.choose`'s result,
re-evaluated every tick `tickAttack` runs -- running out of arrows
mid-fight is detected on the very next tick (no separate "ammo depleted"
event/bookkeeping needed) and immediately starts closing the distance
to melee range instead of standing stranded at the old, now-wrong bow
range.

## Known gaps / next steps

- **Deploying a mod change requires a rebuild, a jar copy, AND a full
  client restart** -- editing the mod's source and even rebuilding it
  isn't enough on its own. Fabric loads mod jars once at client startup;
  a jar changed on disk while the client is already running has zero
  effect until the client is fully quit and relaunched. This bit a real
  session: FoodEater/chat-announcements/inventory-actions/i18n all
  worked and compiled correctly, but a live playtest showed *none* of it
  running (no eating, no chat lines, health silently reaching 2 hearts
  with nothing logged) because the installed jar in the PrismLauncher
  instance's `mods/` folder was hours stale relative to the rebuilt one.
  See `AGENTS.md` for the exact rebuild+copy+relaunch steps. **Now
  automatically detected**: the mod's `hello` event + `minebot/
  mod_version.py`'s check (see above) logs a WARNING the moment a
  mismatched build connects, instead of this needing manual jar-diffing
  to catch -- still requires the actual restart to fix, but no longer
  requires guessing whether that's the problem.
- No real `LLMProvider` is wired up yet (see the LLM trigger + brain
  layer section above) -- `NullLLMProvider` always declines, so
  addressing the bot by name in chat currently gets silence, not a
  response. Next step: implement `LLMProvider.respond` for a real
  provider (Anthropic/OpenAI/etc. -- deliberately not chosen yet) and
  build that provider's tool-schema format from `Action.params`.
- `should_trigger_llm` only checks for the bot's name or a configured
  trigger word being mentioned -- there's no way to trigger it via
  whisper/DM, since `MinebotMod`'s `CHAT`/`GAME` chat forwarding
  collapses every message into the same `{"type":"chat"}` shape with no
  message-type flag. Would need a mod-side change (a `whisper: bool`
  field on the `chat` event, sourced from Fabric API's chat-type info) if
  that turns out to matter in practice -- worth revisiting once a real
  LLM provider is live and it's clearer whether name/word-mention-only is
  too broad or too narrow.
- No conversation history/context -- `LLMController.handle_chat` is
  called fresh per message with no memory of prior turns from the same
  (or any) sender. A real provider integration will likely need some
  per-sender (or global) conversation state before responses feel
  coherent across multiple messages.
- The Java pathfinding port (`Move`/`AStar`/`BlockInfo`/`Movements`/
  `GoalNear`/`PathTracker`) has no unit tests yet, unlike its Python
  equivalent on `pure-protocol-backend` -- so far only validated by live
  testing `!follow` across a floor transition, not by an automated suite.
- Mining/digging is now implemented (`!dig`/`!collect`, real
  dig-through-obstacles pathfinding -- see "Mining / digging" above);
  placing and standalone combat (`!place`, `!attack`/`!kill`) are still
  not. Inventory (visibility + move-to-hotbar/equip/drop/give) has been
  done for longer -- see `InventoryReporter`/`InventoryActions`/
  `InventoryTracker`/`InventoryController` above.
- **None of the mining/digging work has been live-tested against a real
  server yet** -- same caveat every other mod-side feature in this file
  has needed at least once (inventory, respawn, eating all needed a live
  pass to catch real bugs the static port/compile alone couldn't). Two
  specific things worth confirming live before trusting this in front of
  other players: (1) whether a hotbar tool-swap genuinely never happens
  mid-break in practice, not just by the code's own timing (the
  `sameDestroyTarget` mid-break-invalidation risk noted in "Mining /
  digging" above was reasoned from decompiled source, not observed); (2)
  whether the real per-tick dig-cost formula in `Movements.safeOrBreak`
  produces sane A* routes in practice (e.g. does the bot ever prefer
  digging through stone over a two-block walking detour when it
  shouldn't) -- the cost model was ported carefully but has no unit tests
  (see the existing pathfinding-has-no-tests gap above, which now also
  covers the new dig-cost branches).
- `Movements.safeOrBreak`'s dig-cost formula derives a labor cost from
  real per-tick `getDestroyProgress` rather than mineflayer's own
  registry-estimated `digTime` (see "Mining / digging" above for why) --
  the two aren't guaranteed to produce comparable *magnitudes* even
  though the formula shape was kept the same, since one is a real
  measured value and the other was always an estimate; worth revisiting
  the relative weighting against walk-move costs (currently 1.0 per
  step) if live testing shows the bot digging through blocks it should
  obviously just walk around instead.
- `ModBridge` (Python, the WebSocket server side) does handle reconnects
  now -- confirmed live across multiple mod/game-client restarts -- but
  there's no test coverage yet for what happens if the *Python* process
  itself needs to restart mid-session beyond the regression test for the
  events()-busy-loop fix (see `test_mod_bridge.py`); the mod's own
  `ControlClient` retries every 2s regardless.
- The control channel has no authentication -- anything that can open a
  TCP connection to `127.0.0.1:47893` can drive the bot. Fine on a
  single-user machine; would need hardening before ever exposing this
  differently.
- `FoodEater` still triggers on health, not hunger -- it eats reactively
  once already low on health rather than proactively maintaining hunger
  before that happens, and doesn't prefer higher-nutrition food when
  multiple edible types are held. It has no test coverage on the mod
  side.
- **Unexplained: why does a direct `MultiPlayerGameMode.useItem()` call
  never complete an eat, when `Minecraft.startUseItem()` (real input's
  own entry point) calls the exact same method with no confirmed
  difference in setup?** See the full investigation writeup under
  `FoodEater.java` above -- six bytecode passes ruled out every vanilla
  mechanism that could explain it, and the actual fix (hold the real
  `keyUse` keybind instead of calling `useItem()`) works but doesn't
  explain *why* the direct call doesn't. Worth a fresh look if this
  version ever gets a Minecraft/mapping update, in case something
  changes that makes the cause obvious in hindsight -- or if the same
  "direct API call doesn't work, real input does" pattern shows up
  again elsewhere (e.g. if block-breaking/placing hits the same issue).
- Passive health regeneration (vanilla, gated by the
  `naturalRegeneration` game rule, confirmed `true` on this server) was
  separately reported as "not happening" during the same investigation
  session -- still unconfirmed whether that's real or just an artifact
  of a short observation window (the session/game-client ended shortly
  after the observation). Neither this mod nor the Python backend touch
  regen at all, so if real, it isn't a minebot bug -- worth a longer,
  deliberate test (full hunger, low health, just wait and watch) next
  time this comes up.
- **Confirmed working live overall**, as of the `keyUse`-hold fix: death
  detection, auto-respawn, low-health chat announcements, hunger-gating
  detection, and now real eating that actually completes (a human
  directly watched the bot hold right-click and consume food, health
  recovering) -- all confirmed in the same investigation session.
- `RespawnHandler` (death detection + auto-respawn + `death`/`respawn`
  events) has no test coverage on the mod side, but **is confirmed
  working live**: a real playtest showed the bot correctly detecting
  death (`ritebot was slain by riterite` -> `death` event logged) and
  auto-respawning with no manual click needed (`respawn` event logged,
  confirmed by the other player: "ok respawn worked").
- That same playtest caught a real bug in `FoodEater`'s hearts display:
  `Math.round(health / 2.0f)` rounded a genuine, nonzero 0.5 HP (a
  quarter heart) down to 0, so chat said "I have 0 hearts!" while the
  player was visibly still alive -- a player watching flagged it
  directly ("bug: 0 hearts?, but I see you have 0.5"). Fixed to report
  hearts to one decimal place instead of rounding to a whole number.
- `MinebotMod.handleMessage` (including the new `move_to_hotbar`/`equip`/
  `drop`/`give` cases) runs on the WebSocket's own network thread, not
  the client render/tick thread, and calls real client-internal APIs
  (`Inventory`, `containerMenu`, `MultiPlayerGameMode.handleContainerInput`)
  directly from there with no thread-hop -- most of Minecraft's client-
  side game logic isn't designed to be thread-safe. This isn't a new risk
  introduced by the inventory work specifically: the pre-existing `chat`
  case already calls `client.player.connection.sendChat(...)` the same
  way, so this matches established (if fragile) precedent rather than
  being a regression -- but it's still an outstanding gap worth fixing
  properly (e.g. hopping onto the client thread via
  `Minecraft.getInstance().execute(...)`) before it causes a live issue.
- None of the inventory work (`InventoryReporter`/`InventoryActions` on
  the mod side, `InventoryTracker`/`InventoryController`/the four new
  `!inventory`/`!equip`/`!drop`/`!give` commands on the Python side) has
  been live-tested against a real server yet -- both sides only compile/
  pass their existing test suites so far. In particular the
  `InventoryMenu` slot-index math (+0 for main storage, +36 for hotbar)
  was verified by disassembling `AbstractContainerMenu` bytecode, not by
  actually clicking a slot and watching it happen in-game -- worth
  confirming live before trusting `equip`/`drop`/`give` in front of other
  players.
- `equip` doesn't let the caller target a *specific* equipment slot --
  it relies on `InventoryMenu.quickMoveStack`'s automatic routing (same
  as vanilla shift-click), so it only works for items that clearly belong
  in exactly one slot (a chestplate, an elytra, a shield in the offhand).
  There's no way to e.g. force a non-armor item into the offhand.
- `give` has no handling for the recipient's inventory being full (the
  dropped item just lands on the ground near them, same as vanilla --
  not a bug, just not "guaranteed delivered"), and no timeout: if the
  recipient is (briefly) visible when `give` starts but then genuinely
  never comes within `stop_distance` (e.g. path is blocked, they keep
  moving away), the bot just keeps walking toward them indefinitely,
  same open-ended behavior `follow` already has.
- `Configs.botLanguage` (bot chat language, en/es) is only changeable via
  the ModMenu config screen, in-game -- there's no way to set it from
  Python/the control channel or from a config file, and it resets to
  `"en"` every client restart.
- Only two languages are bundled (`en`, `es`), and only `FoodEater` has
  any translated lines yet -- other mod-originated chat (if any gets
  added later) would need its own `Messages.get(...)` calls and message
  keys added to both JSON files.

## Key external paths referenced (outside these two repos)

- `/home/colaila/git/mindcraft` -- Node.js reference project (mineflayer-
  based bot with an LLM in the loop). Original architecture reference for
  the command-grammar/skills-layer pattern (`minebot/commands/`
  mirrors this), not for movement anymore.
- `/home/colaila/git/mods/VillagerHelper` -- source of the decompiled
  26.1.2 jar (via Fabric Loom's `genSources`), and the reference project
  for the exact Fabric/Loom/fabric-api version pin `minebot-mod` uses.
- `/home/colaila/git/mineflayer-pathfinder` -- cloned repo, pinned to the
  `2.4.5` tag, used as the real source for the A* port on
  `pure-protocol-backend` (not currently in use on `master`).
