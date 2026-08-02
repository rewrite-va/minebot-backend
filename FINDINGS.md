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
  connects to the Minecraft server. Exposes a local WebSocket server
  (`ControlServer`, `127.0.0.1:47893` by default) that Python connects to.

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
game state -- aim yaw at the target (`atan2(-dx, dz)`, vanilla's yaw
convention), hold forward while still farther than `stop_distance`, hold
jump whenever the target sits meaningfully above us. This deliberately
mirrors the same heuristic the earlier from-scratch physics port used (see
`pure-protocol-backend`'s FINDINGS.md history) for the same reason: it's a
reasonable substitute for a full jump-arc/pathfinding model without needing
one yet. No A* pathfinding exists on this side yet -- `follow`/`goto` walk
in a straight line toward the target and rely on vanilla's own step-height
(0.6 blocks) to handle small ledges; a real obstacle will just stall
forward progress against it, not route around it. Porting the old A*
pathfinding (`pure-protocol-backend`'s `minebot/pathfinding/`) to run
against real block data the mod could expose is the natural next step if
that's needed.

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
  (default `127.0.0.1:47893`, matching the mod's `ControlServer.DEFAULT_PORT`).
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

- `ControlServer.java` -- embeds Java-WebSocket (shaded via Loom's
  jar-in-jar `include`, since nothing else provides it), bound to
  `127.0.0.1` only (no auth of its own -- fine only as long as it's
  unreachable from outside the machine).
- `ControlState.java` -- the current goal (`IDLE`/`GOTO`/`FOLLOW` +
  target), set by incoming WebSocket commands.
- `MovementIntent.java` -- the concrete per-tick forward/jump/yaw resolved
  from the current goal against live game state; separates goal
  resolution (needs live entity/player state) from input plumbing (doesn't).
- `MinebotInput.java` -- the `ClientInput` replacement described above
  (keyboard-override + `MovementIntent`-driven fallback).
- `MinebotMod.java` -- entry point: starts the control server, registers
  the client-tick hook (resolves the goal, updates `MinebotInput`, sets
  yaw, broadcasts position/entity/health events), registers chat-event
  forwarding.

`fabric.mod.json` declares `"environment": "client"` (no server-side
component -- this only makes sense running inside an actual client).

## Known gaps / next steps

- No A* pathfinding wired up on the mod side yet -- `follow`/`goto` are
  straight-line-plus-step-height only. Real obstacles (walls, gaps wider
  than a single step) will just stall the bot rather than route around
  them. The old A*/movements cost-model port on `pure-protocol-backend`
  could be adapted to run against real block data the mod could expose
  (e.g. `ClientLevel.getBlockState(pos)`), if/when that's needed.
- Mining/placing/combat/inventory are not implemented on either side yet.
- Not yet load-tested for WebSocket reconnection -- if the mod restarts
  (e.g. game crash) while Python is running, `ModBridge` has no retry/
  reconnect logic yet; Python would need to be restarted too.
- The control channel has no authentication -- anything that can open a
  TCP connection to `127.0.0.1:47893` can drive the bot. Fine on a
  single-user machine; would need hardening before ever exposing this
  differently.

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
