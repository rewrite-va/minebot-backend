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
- `{"type":"position","x":..,"y":..,"z":..,"yaw":..,"pitch":..,"on_ground":..}`
  (every client tick)
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
- `{"type":"inventory","selected_slot":..,"slots":[{"slot":..,"item":"minecraft:...","count":..,"damage":..,"max_damage":..}, ...]}`
  -- a full snapshot (only non-empty slots listed), broadcast whenever it
  differs from the last one sent (change-only, same shape as `health`).
  `damage`/`max_damage` are only present for damaged (not full-durability)
  items.

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
  (doesn't). `resolveMovementIntent` (in `MinebotMod.java`) picks between
  two look-direction behaviors: while a pathfinding waypoint is still
  queued, yaw keeps aiming at the next waypoint (needed to actually walk
  the route); once the waypoint queue empties (arrived, or pathfinding
  failed and it's falling back to the raw target) and there's a live
  `FOLLOW`/`GIVE` entity, yaw+pitch instead aim directly at that entity's
  eye level (`Entity.getEyeY()` on both sides) -- so the bot looks at
  whoever it's following once close, rather than continuing to face
  wherever the last waypoint was. Pitch sign (positive = looking down)
  confirmed via decompiled `Entity.calculateViewVector`.
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
- Mining/placing/combat are not implemented on either side yet, so
  pathfinding is walk/climb/parkour only (no dig/place moves). Inventory
  (visibility + move-to-hotbar/equip/drop/give) now is -- see
  `InventoryReporter`/`InventoryActions`/`InventoryTracker`/
  `InventoryController` above.
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
