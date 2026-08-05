# minebot -- agent operating notes

Python brain/controller for a Minecraft bot. See `FINDINGS.md` for the
full architecture writeup (why this talks to a companion Fabric mod over
a local WebSocket instead of speaking the Minecraft protocol directly).

## Three repos, one bot

- **This repo** (`minebot`) -- the Python backend. Runs the control-channel
  WebSocket *server*; connects, dispatches chat commands/LLM tool calls,
  tracks entity/inventory/health state.
- **`/home/colaila/git/mods/minebot-mod`** -- the Fabric mod (separate
  repo). Runs inside a real Minecraft client logged into the bot's
  account. The only thing that actually speaks the Minecraft protocol;
  connects *out* to this backend as a WebSocket client.
- **`/home/colaila/git/minebot-frontend`** -- a React/TypeScript/Tailwind
  live viewer (separate repo) for the wire messages flowing between this
  backend and minebot-mod. Connects to this backend's `ObserverServer`
  (`minebot/bridge/observer.py`, a separate WebSocket port from the
  control channel itself -- `MINEBOT_OBSERVER_PORT`, default 47894), a
  read-only broadcast of every message sent/received. Purely observational
  -- has no way to send commands or affect the bot.

## Important folders

- **`/home/colaila/git/minebot`** -- this repo, the Python backend.
  `minebot/bot/` is where chat-command controllers live
  (`movement.py`/`inventory.py`/`mining.py`/`combat.py`), `minebot/bridge/` is the
  WebSocket client + state trackers, `logs/` is this process's own
  timestamped run logs (gitignored).
- **`/home/colaila/git/mods/minebot-mod`** -- the Fabric mod repo.
  `src/main/java/minebot/mod/` is the mod source;
  `src/main/java/minebot/mod/pathfinding/` is the A* port + block
  breaking. `build/libs/minebot-mod-0.1.0+26.1.2.jar` is the build
  output that needs copying out to actually take effect (see "Changing
  anything in the mod" below).
- **`/home/colaila/git/minebot-frontend`** -- the wire-message viewer
  repo. `src/App.tsx` is the main view; `src/useWireFeed.ts` connects to
  `ObserverServer` and buffers incoming messages (`MAX_BUFFERED_MESSAGES`
  bounds live DOM node count -- no virtualization); `src/wire.ts` has the
  message type definitions; `src/typeColors.ts` maps message type to a
  Tailwind color class; `src/useHiddenTypes.ts` +
  `src/components/TypeFilterMenu.tsx` back the per-type suppression UI
  (localStorage-backed). `pnpm dev` runs the Vite dev server --
  `vite.config.ts` sets `server: { host: '0.0.0.0' }` so it's reachable
  from Windows (WSL2 default of `127.0.0.1`-only isn't). Purely
  observational (see "Three repos, one bot" above) -- never needs a
  rebuild/redeploy dance the way `minebot-mod` does, just a normal Vite
  dev server restart if it's already running.
- **`/mnt/c/Users/colaila/AppData/Roaming/PrismLauncher/instances/26.1.2 - v1 ritebot/`**
  -- the PrismLauncher instance the bot's account actually runs in
  (Windows side, mounted into WSL2 under `/mnt/c`).
  `minecraft/mods/minebot-mod-0.1.0+26.1.2.jar` is the deployed jar
  (the redeploy target below). `minecraft/logs/latest.log` is the
  *client's* own log -- this is where chat lines, mod `LOGGER`
  output, and any Java exceptions from `minebot-mod` actually show up;
  check this (not just the Python backend's log) when something silently
  doesn't work, since a stale-jar or stale-client-process problem often
  leaves nothing informative in the Python log at all. Sibling instances
  (`26.1.2 - v1 riterite`, `26.1.2(1)`) are other accounts/test setups,
  not this bot's.
- **`~/.gradle/caches/fabric-loom/decompile/`** -- Loom's decompiled
  vanilla Minecraft source cache (populated by a Loom `genSources`-style
  task). The actual ground truth for "what does this vanilla API really
  do" when working on the mod -- e.g. confirming `MultiPlayerGameMode`'s
  real block-destroy sequence or `BlockBehaviour.getDestroyProgress`'s
  exact formula came from reading real decompiled source here, not
  guessing from method names. Check this before assuming behavior of any
  vanilla/Fabric API the mod calls.

Changing anything in the mod requires **both** a rebuild and a redeploy
to actually take effect in-game -- editing the mod's source alone does
nothing until both of these happen:

```bash
cd /home/colaila/git/mods/minebot-mod
./gradlew clean build -x test
cp build/libs/minebot-mod-0.1.0+26.1.2.jar \
   "/mnt/c/Users/colaila/AppData/Roaming/PrismLauncher/instances/26.1.2 - v1 ritebot/minecraft/mods/minebot-mod-0.1.0+26.1.2.jar"
```

**Use `clean build`, not a plain `build`.** Found live (twice): a plain
`./gradlew build -x test` right after a fresh commit silently deployed
a jar still baked from an *older* commit -- `generateBuildInfo` is
configured to always rerun (`outputs.upToDateWhen { false }`), but some
later task in the chain (`processResources`/`jar`) still incrementally
reused stale output in practice, with no error or warning of any kind.
Confirmed after the fact both times by unzipping the deployed jar's own
`minebot-mod-build-info.properties` and finding it named an older
commit than the one actually built. `clean build` forces every task to
genuinely rerun, eliminating this whole class of silent-stale-jar bug
rather than trying to spot it after the fact -- **verify the deployed
jar's baked-in commit directly** (`unzip -p build/libs/minebot-mod-0.1.0+26.1.2.jar
minebot-mod-build-info.properties`, compare against `git rev-parse HEAD`)
after every build, not just after something looks wrong live.

**Standing instruction: always run both of these commands yourself,
every time a mod-side change is made** -- don't leave the jar built but
undeployed for the user to copy over manually. Then **ask the user to
fully quit and relaunch the Minecraft client** (PrismLauncher instance
"26.1.2 - v1 ritebot") -- this part can't be automated (it's a real
desktop app on the Windows side of WSL2, not something reachable from
here), so request it explicitly rather than silently assuming it
happened. Fabric loads mod jars once at startup, so a running client
keeps running the old code even after the jar on disk changes -- a
stale-jar deploy is a common silent-failure mode: symptoms look like
"the bot isn't doing anything I just added" even though the source
change, build, and redeploy are all correct, just not yet loaded.

The backend now catches this automatically: the mod broadcasts its own
git commit (baked into the jar at build time) the moment it connects,
and the backend compares it against `minebot-mod`'s current `git
rev-parse HEAD` (see `minebot/mod_version.py`), logging a loud WARNING
on mismatch. Check the backend's log right after a relaunch if
something still doesn't seem to be working -- if it warns about a
commit mismatch, the client wasn't actually restarted (or is still
running a jar built from an older/different commit), not a logic bug.

**The Python backend has the same stale-process trap, with no automatic
detection.** Editing `minebot/*.py` does nothing to an already-running
`python -m minebot.main` process -- it has the old code loaded in memory
regardless of what's on disk. Found live: a new chat command
(`!dig`/`!collect`) silently didn't exist as far as `!help` was
concerned, even though the source was correct and the mod was
connected/reconnecting fine -- `ps aux | grep minebot.main` showed a
process that had been running since well before the new code was
written. Kill it and re-run `./start.sh` after any Python-side change
that's supposed to take effect; there's no equivalent of the mod's
commit-mismatch warning for this side yet.

## Running the backend

```bash
./start.sh
```

Thin wrapper around `uv run python -m minebot.main`. Blocks waiting for
the mod to connect in on `MINEBOT_MOD_HOST:MINEBOT_MOD_PORT` (default
`0.0.0.0:47893`). Copy `.env.example` to `.env` first and fill in real
values (`MINEBOT_BOT_NAME`, optionally `MINEBOT_TRIGGER_WORDS`).

Every run also writes its own timestamped file under `logs/`
(`logs/<datetime>.log`, gitignored) in addition to the usual console
output -- no need to manually `tee` to a file to capture a session for
debugging, it's captured by default (see `minebot/logging_setup.py`).

## Tests

```bash
uv run pytest -q
```

Python-side only -- the mod side has no test suite yet (see
FINDINGS.md's known gaps).
