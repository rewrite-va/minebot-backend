# minebot -- agent operating notes

Python brain/controller for a Minecraft bot. See `FINDINGS.md` for the
full architecture writeup (why this talks to a companion Fabric mod over
a local WebSocket instead of speaking the Minecraft protocol directly).

## Two repos, one bot

- **This repo** (`minebot`) -- the Python backend. Runs the control-channel
  WebSocket *server*; connects, dispatches chat commands/LLM tool calls,
  tracks entity/inventory/health state.
- **`/home/colaila/git/mods/minebot-mod`** -- the Fabric mod (separate
  repo). Runs inside a real Minecraft client logged into the bot's
  account. The only thing that actually speaks the Minecraft protocol;
  connects *out* to this backend as a WebSocket client.

Changing anything in the mod requires **both** a rebuild and a redeploy
to actually take effect in-game -- editing the mod's source alone does
nothing until both of these happen:

```bash
cd /home/colaila/git/mods/minebot-mod
./gradlew build -x test
cp build/libs/minebot-mod-0.1.0+26.1.2.jar \
   "/mnt/c/Users/colaila/AppData/Roaming/PrismLauncher/instances/26.1.2 - v1 ritebot/minecraft/mods/minebot-mod-0.1.0+26.1.2.jar"
```

Then **fully quit and relaunch** the Minecraft client (PrismLauncher
instance "26.1.2 - v1 ritebot") -- Fabric loads mod jars once at
startup, so a running client keeps running the old code even after the
jar on disk changes. A stale-jar deploy is a common silent-failure mode:
symptoms look like "the bot isn't doing anything I just added" even
though the source change and backend are both correct.

## Running the backend

```bash
./start.sh
```

Thin wrapper around `uv run python -m minebot.main`. Blocks waiting for
the mod to connect in on `MINEBOT_MOD_HOST:MINEBOT_MOD_PORT` (default
`0.0.0.0:47893`). Copy `.env.example` to `.env` first and fill in real
values (`MINEBOT_BOT_NAME`, optionally `MINEBOT_TRIGGER_WORDS`).

## Tests

```bash
uv run pytest -q
```

Python-side only -- the mod side has no test suite yet (see
FINDINGS.md's known gaps).
