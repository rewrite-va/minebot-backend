# minebot -- agent operating notes

Python brain/controller for a Minecraft bot. See `FINDINGS.md` for the
full architecture writeup and investigation history.

## Three repos, one bot

- **This repo** (`minebot`) -- Python backend. Runs the control-channel
  WebSocket *server*; dispatches chat commands/LLM tool calls, tracks
  entity/inventory/health state. `minebot/bot/` = chat-command
  controllers (`movement.py`/`inventory.py`/`mining.py`/`combat.py`);
  `minebot/bridge/` = WebSocket client + state trackers; `logs/` =
  timestamped run logs (gitignored, auto-written, no manual `tee` needed).
- **`/home/colaila/git/mods/minebot-mod`** -- Fabric mod, separate repo.
  Runs inside a real Minecraft client on the bot's account; the only
  thing that speaks the Minecraft protocol; connects *out* to this
  backend as a WebSocket client. `src/main/java/minebot/mod/` = mod
  source; `.../pathfinding/` = A* port + block breaking.
- **`/home/colaila/git/minebot-frontend`** -- React/TS/Tailwind live
  wire-message viewer, separate repo, read-only (connects to this
  backend's `ObserverServer`, a separate port from the control channel --
  `MINEBOT_OBSERVER_PORT`, default 47894). `src/App.tsx` = main view;
  `src/useWireFeed.ts` = feed connection; `src/wire.ts` = message types.
  `pnpm dev` (Vite, `host: '0.0.0.0'` for WSL2 reachability) -- no
  rebuild/redeploy dance, just restart the dev server.

## Key external paths

- `/mnt/c/Users/colaila/AppData/Roaming/PrismLauncher/instances/26.1.2 - v1 ritebot/`
  -- the bot's actual Minecraft client (Windows side, mounted via WSL2).
  `minecraft/mods/minebot-mod-0.1.0+26.1.2.jar` = deployed jar (redeploy
  target below). `minecraft/logs/latest.log` = the *client's* own log --
  chat lines, mod `LOGGER` output, Java exceptions all show up here, not
  in the Python log; check this first when something silently doesn't
  work. Sibling instances (`26.1.2 - v1 riterite`, `26.1.2(1)`) are other
  accounts, not this bot's.
- `~/.gradle/caches/fabric-loom/decompile/` -- Loom's decompiled vanilla
  source cache. Ground truth for real vanilla/Fabric API behavior --
  check here before guessing.

## Mod changes need rebuild + redeploy + relaunch (all three, every time)

```bash
cd /home/colaila/git/mods/minebot-mod
./gradlew clean build -x test
cp build/libs/minebot-mod-0.1.0+26.1.2.jar \
   "/mnt/c/Users/colaila/AppData/Roaming/PrismLauncher/instances/26.1.2 - v1 ritebot/minecraft/mods/minebot-mod-0.1.0+26.1.2.jar"
```

**Standing instruction: run both commands yourself, every time, without
asking.** Then ask the user to fully quit and relaunch the client
(can't be automated -- real Windows desktop app).

- **Always `clean build`, never plain `build`.** A plain build has
  silently deployed a stale-commit jar before (`processResources`/`jar`
  incrementally reusing old output despite `generateBuildInfo` claiming
  to always rerun) -- `clean build` eliminates the whole bug class.
  Verify after every build: `unzip -p build/libs/minebot-mod-0.1.0+26.1.2.jar
  minebot-mod-build-info.properties` and compare its commit against
  `git rev-parse HEAD`.
- **Fabric loads jars once at startup** -- a running client keeps the old
  code even after the jar on disk changes. The backend auto-detects this:
  the mod broadcasts its own build commit on connect, and
  `minebot/mod_version.py` logs a loud WARNING on mismatch against
  `minebot-mod`'s current HEAD. Check the backend log right after a
  relaunch if something still seems off.

## Python backend also needs a restart after every change

Editing `minebot/*.py` does nothing to an already-running process --
no equivalent of the mod's mismatch warning exists for this side yet.

**Standing instruction: kill and restart yourself, every time, without
asking.** `ps aux | grep minebot.main` (kill both the `uv run` wrapper
and the real `python3 -m minebot.main` process), then `./start.sh` in
the background.

A "feature does nothing" report can be **both** staleness traps stacked
at once -- check the client log's commit AND the backend process's start
time before assuming a logic bug.

## Chat rate limiting

`ModBridge.CHAT_RATE_PER_SECOND` (1.0) caps outgoing chat, but only
smooths sends -- it doesn't stop a flood of *distinct* messages being
generated in the first place (e.g. a naive gained-item diff on first
connect queuing one line per already-carried item). Prefer fixing the
event count at the source over leaning on the rate cap to survive it.

## Running the backend

```bash
./start.sh
```

Wraps `uv run python -m minebot.main`. Blocks waiting for the mod to
connect on `MINEBOT_MOD_HOST:MINEBOT_MOD_PORT` (default `0.0.0.0:47893`).
Copy `.env.example` to `.env` first (`MINEBOT_BOT_NAME`, optionally
`MINEBOT_TRIGGER_WORDS`).

## Tests

```bash
uv run pytest -q
```

Python-side only -- the mod has no test suite yet.
