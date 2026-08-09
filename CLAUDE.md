# minebot -- agent operating notes

Python brain/controller for a Minecraft bot. See `FINDINGS.md` for the
full architecture writeup and investigation history.

## Standing instructions -- do these yourself, without asking

- **After ANY edit under `minebot/*.py`**: kill and restart the backend
  before reporting the task done. No mismatch warning exists on this
  side (unlike the mod), so a stale process fails silently. Confirmed
  live this gets skipped even on substantial changes when the restart
  isn't the very next action after the last edit -- treat it as part of
  the edit, not a followup to remember separately.
  ```bash
  ps aux | grep minebot.main   # kill both the `uv run` wrapper and the
                                # real `python3 -m minebot.main` process
  ./start.sh                   # in the background
  ```
- **After ANY edit in `minebot-mod`**: rebuild + redeploy, every time.
  ```bash
  cd /home/colaila/git/mods/minebot-mod
  ./gradlew clean build -x test   # always clean -- a plain build has
                                   # silently deployed a stale-commit jar
                                   # before (processResources/jar reusing
                                   # old output despite generateBuildInfo
                                   # claiming to always rerun)
  cp build/libs/minebot-mod-0.1.0+26.1.2.jar \
     "/mnt/c/Users/colaila/AppData/Roaming/PrismLauncher/instances/26.1.2 - v1 ritebot/minecraft/mods/minebot-mod-0.1.0+26.1.2.jar"
  ```
  Verify: `unzip -p build/libs/minebot-mod-0.1.0+26.1.2.jar minebot-mod-build-info.properties`
  and compare its commit against `git rev-parse HEAD`. Then ask the user
  to fully quit and relaunch the client -- can't be automated (real
  Windows desktop app), and Fabric loads jars once at startup, so a
  running client keeps stale code even after the jar on disk changes.

A "feature does nothing" report can be **both** staleness traps stacked
at once. The backend auto-detects a client still on an old build (mod
broadcasts its commit on connect, `minebot/mod_version.py` logs a loud
WARNING on mismatch) -- check that log line, and the backend process's
own start time, before assuming a logic bug.

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
  source; `.../pathfinding/` = A* port + block breaking; `.../mixin/` =
  Fabric Mixin accessors/invokers for reaching vanilla `protected`/
  `private` members with no public equivalent (first added for
  `AbstractArrowAccessor`'s `isInGround()` -- see its own docstring).
  Config is `src/main/resources/minebot-mod.mixins.json`, referenced from
  `fabric.mod.json`'s `mixins` array -- add new mixin classes to both the
  `client` list in the JSON and `minebot.mod.mixin`, or Loom won't apply
  them (silent no-op at runtime, not a build error).
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
  target above). `minecraft/logs/latest.log` = the *client's* own log --
  chat lines, mod `LOGGER` output, Java exceptions all show up here, not
  in the Python log; check this first when something silently doesn't
  work. Sibling instances (`26.1.2 - v1 riterite`, `26.1.2(1)`) are other
  accounts, not this bot's.
- `~/.gradle/caches/fabric-loom/decompile/v1.zip` -- Loom's decompiled
  vanilla source cache. Ground truth for real vanilla/Fabric API
  behavior -- check here before guessing. **Not a browsable-by-filename
  tree**: it's a content-addressed blob store (every entry named by
  hash, not by class name). Extract it once (`unzip -q
  ~/.gradle/caches/fabric-loom/decompile/v1.zip -d /some/tmp/dir`), then
  grep blob *contents* for the class you want (`grep -rl "class Wolf "
  /some/tmp/dir`) -- filename search finds nothing. Modern MC classes
  also live in less-obvious subpackages than you'd guess from older MC
  versions/mineflayer/mindcraft docs -- confirmed live examples: `Bee`
  is `net.minecraft.world.entity.animal.bee.Bee` (not `.animal.Bee`),
  `Wolf` is `.animal.wolf.Wolf`, `PolarBear` is `.animal.polarbear.
  PolarBear`, `Breeze` is `.monster.breeze.Breeze` -- when the compiler
  says "cannot find symbol" on an import that looks right, check the
  actual package via `unzip -l
  ~/.gradle/caches/fabric-loom/26.1.2/minecraft-client.jar | grep -i
  ClassName` rather than guessing subpackage variations.

## Chat rate limiting

`ModBridge.CHAT_RATE_PER_SECOND` (1.0) caps outgoing chat, but only
smooths sends -- it doesn't stop a flood of *distinct* messages being
generated in the first place (e.g. a naive gained-item diff on first
connect queuing one line per already-carried item). Prefer fixing the
event count at the source over leaning on the rate cap to survive it.

## Running the backend

```bash
./start.sh   # wraps `uv run python -m minebot.main`
```

Blocks waiting for the mod to connect on `MINEBOT_MOD_HOST:MINEBOT_MOD_PORT`
(default `0.0.0.0:47893`). Copy `.env.example` to `.env` first
(`MINEBOT_BOT_NAME`, optionally `MINEBOT_TRIGGER_WORDS`).

## Tests

```bash
uv run pytest -q
```

Python-side only -- the mod has no test suite yet.
