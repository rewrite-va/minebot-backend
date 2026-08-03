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

Events, mod -> Python:
- `{"type":"position","x":..,"y":..,"z":..,"yaw":..,"pitch":..,"on_ground":..}`
  (every client tick)
- `{"type":"chat","sender":"name-or-omitted","text":".."}`
- `{"type":"entity","action":"add"|"move"|"remove","id":..,"name":"...","x":..,"y":..,"z":..}`
  (name/position omitted on `remove`; name omitted on `move`, since it never
  changes)
- `{"type":"health","health":..}`

## Repo layout (Python side, `master`)

- `minebot/bridge/client.py` -- `ModBridge`: the WebSocket connection to
  the mod, `events()` async-iterates parsed `ModEvent`s, `send_goto`/
  `send_follow`/`send_stop`/`send_chat` for commands.
- `minebot/bridge/entities.py` -- `EntityTracker`: id/name -> position,
  fed purely from the mod's own `entity` events (no packet parsing).
- `minebot/bot/movement.py` -- `MovementController`: `!follow`/`!stop`
  chat-command handlers, translating to `ModBridge` goal calls. `!follow`
  with no name follows the chat sender; an explicit name resolves through
  `EntityTracker`.
- `minebot/bot/run_loop.py` -- the main event loop: iterates
  `bridge.events()`, feeds `entity` events to the tracker, dispatches
  `chat` text through `CommandRegistry`, logs `position`/`health`.
- `minebot/commands/parser.py`/`registry.py` -- unchanged from the old
  architecture; both were already protocol-agnostic (`!name(args)` chat
  grammar and name->handler dispatch), so they carried over as-is.
- `minebot/config.py` -- now just `MINEBOT_MOD_HOST`/`MINEBOT_MOD_PORT`
  (default `0.0.0.0:47893` -- Python binds as the server now; see the
  WSL2-networking note above).
- `minebot/main.py` -- wires it all together: connect the bridge, build
  the registry/tracker/movement controller, run the loop.

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
- `MovementIntent.java` -- the concrete per-tick forward/jump/yaw resolved
  from the current goal against live game state; separates goal
  resolution (needs live entity/player state) from input plumbing (doesn't).
- `MinebotInput.java` -- the `ClientInput` replacement described above
  (keyboard-override + `MovementIntent`-driven fallback).
- `FoodEater.java` -- autonomous eating: every client tick, if health is
  at or below 20% of max and the player isn't already mid-eating-animation
  (`isUsingItem()`, which also acts as the natural throttle -- no separate
  cooldown needed), eats via the real `MultiPlayerGameMode.useItem`
  interaction (offhand food first, else the first edible item found in the
  main inventory, selected into the hotbar first if necessary). "Edible"
  is a `DataComponents.FOOD` presence check -- the older
  `Item.getFoodProperties()` API is gone in 26.1.2. Also sends real chat
  lines via `player.connection.sendChat` (same path Python-originated
  `chat` commands use): once per low-health episode when it starts eating
  ("I have N hearts!, eating...", translated) and once if the inventory
  scan comes up empty ("oh, I have no food! aaaa"), each gated by its own
  per-episode boolean so it doesn't spam chat every tick while health
  stays low. Entirely autonomous on the mod side; no control-channel wire
  format changes, so Python has no visibility into any of this beyond the
  `health` events it already gets (and the chat lines showing up as
  regular `chat` events, same as any other player's chat).
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
- `StatusHud.java` -- a HUD text overlay showing whether the control
  channel is currently connected.
- `MinebotMod.java` -- entry point: starts the control client, registers
  the client-tick hook (resolves the goal via `PathTracker`, updates
  `MinebotInput`, sets yaw, calls `FoodEater`, broadcasts position/entity/
  health events), registers chat-event forwarding, registers the HUD.

`fabric.mod.json` declares `"environment": "client"` (no server-side
component -- this only makes sense running inside an actual client).

## Known gaps / next steps

- The Java pathfinding port (`Move`/`AStar`/`BlockInfo`/`Movements`/
  `GoalNear`/`PathTracker`) has no unit tests yet, unlike its Python
  equivalent on `pure-protocol-backend` -- so far only validated by live
  testing `!follow` across a floor transition, not by an automated suite.
- Mining/placing/combat/inventory are not implemented on either side yet,
  so pathfinding is walk/climb/parkour only (no dig/place moves).
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
- `FoodEater` triggers on health, not hunger (`FoodData`/the hunger bar
  isn't polled at all yet) -- it eats reactively once already low on
  health rather than proactively maintaining hunger before that happens.
  It also doesn't check saturation/hunger before eating (a real player
  eating at full hunger just wastes the item -- vanilla blocks this via
  `Player.canEat`, worth checking if this bites in practice), doesn't
  prefer higher-nutrition food when multiple edible types are held, and
  has no test coverage. Not yet confirmed by live testing (only compiled
  successfully so far).
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
