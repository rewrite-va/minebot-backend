# Project findings / handoff notes

Context for a future session picking this up cold. See `prompt.txt` for the
original ask: a Python Minecraft bot (mindcraft-alike, but no LLM in the loop
for basic actions) that connects via the raw protocol, parses chat commands,
and does movement/mining/placing/combat/inventory.

## The core problem: target version is brand new

Target server: `51.81.166.195:60433`, **online-mode** (real Microsoft/Mojang
auth required, not cracked).

Minecraft moved to year-based versioning; our target is **26.1.2**
(protocol version **775**, confirmed from `SharedConstants.java` ->
`RELEASE_NETWORK_PROTOCOL_VERSION = 775`). This is past Claude's training
cutoff (Jan 2026) and past every Python Minecraft library's support window:

- `pycraft` (PyPI as `pycraft`/`minecraft-protocol`): dead since ~2019, caps
  out at protocol 340 (MC 1.12.2). Not usable as-is.
- `quarry` (PyPI, actively maintained, asyncio): current release 1.9.6 only
  ships packet tables through MC 1.19.1. Still useful as a **networking
  primitives** reference/dependency (VarInt, frame splitting, zlib
  compression, AES/CFB8 encryption, zone Login flow) but its packet ID
  tables are useless for us and were not used.
- No Yarn mapping branch exists for `26.1.2` (yarn's most recent branch at
  time of writing is `1.21.11`) — irrelevant, see below.

## The unlock: we already have ground-truth source for 26.1.2

`/home/colaila/git/mods/VillagerHelper` is a real, working Fabric mod pinned
to `minecraft_version=26.1.2`, `loader_version=0.19.3`. Its Gradle setup has
**no Yarn mapping dependency at all** — modern Loom defaults to Mojang's own
official mappings, so class/method/field names in the decompiled jar are the
same human-readable names Mojang's own source uses
(`net.minecraft.client.Minecraft`, `net.minecraft.network.chat.Component`,
`net.minecraft.core.BlockPos`, etc.) — no obfuscation to reverse and no
mapping-file hunting needed.

Fabric Loom caches the actual Minecraft jar for a project's pinned version
locally once built. It was already present at:

```
/home/colaila/.gradle/caches/fabric-loom/26.1.2/minecraft-merged.jar
```

Running Loom's decompile task against VillagerHelper produced full, readable
Java source for the entire game, client and server merged, for exactly
`26.1.2`:

```bash
cd /home/colaila/git/mods/VillagerHelper
./gradlew genSources      # needs network access once, to fetch the Vineflower
                           # decompiler jar itself — NOT to fetch Minecraft,
                           # that part is already cached and works --offline
```

Output lands at (note: gets regenerated / may move on subsequent runs, so
treat this path as "wherever VillagerHelper's Loom cache put it" and re-find
if stale):

```
mods/VillagerHelper/.gradle/loom-cache/minecraftMaven/net/minecraft/
  minecraft-merged-043a8b3edf/26.1.2/minecraft-merged-043a8b3edf-26.1.2-sources.jar
```

**This means the original plan (write a Fabric packet-sniffing mod with
Mixins into the client's Connection/PacketEncoder/PacketDecoder, join the
live server, capture packets) was unnecessary and was abandoned.** We have
better-than-packet-capture data: the actual source that defines every
packet's field layout and ID.

If this sources jar ever goes missing, regenerate it with the command above
against any mod repo under `mods/` pinned to `26.1.2` (VillagerHelper,
jade-trades also qualify — check each mod's `gradle.properties` for
`minecraft_version` before assuming).

## What we learned from reading the decompiled source

Connection state machine is **unchanged** from the 1.20.2+ protocol rework:
`HANDSHAKING -> {STATUS | LOGIN} -> CONFIGURATION -> PLAY`
(`net.minecraft.network.ConnectionProtocol`).

Packet IDs are **not** written anywhere as literal numbers in modern source.
Each phase/direction registers its packets via a fluent builder:

```java
// net/minecraft/network/protocol/game/GameProtocols.java (abridged)
builder -> builder.addPacket(GamePacketTypes.SERVERBOUND_ACCEPT_TELEPORTATION, ...)
    .addPacket(GamePacketTypes.SERVERBOUND_ATTACK, ...)
    .addPacket(GamePacketTypes.SERVERBOUND_BLOCK_ENTITY_TAG_QUERY, ...)
    // ...
```

and `ProtocolInfoBuilder.buildDetails()` assigns IDs by **enumeration order**:

```java
for (int i = 0; i < codecs.size(); i++) {
    output.accept(((CodecEntry) codecs.get(i)).type, i);
}
```

So a packet's ID = its zero-based position in that `.addPacket(...)` chain.
The five registration files are:

- `net/minecraft/network/protocol/handshake/HandshakeProtocols.java`
- `net/minecraft/network/protocol/status/StatusProtocols.java`
- `net/minecraft/network/protocol/login/LoginProtocols.java`
- `net/minecraft/network/protocol/configuration/ConfigurationProtocols.java`
- `net/minecraft/network/protocol/game/GameProtocols.java` (PLAY state)

`tools/extract_packet_ids.py` parses these five files directly out of the
sources jar with a regex (matches the `VAR = ProtocolInfoBuilder.xProtocol(...)`
assignment, then every `.addPacket(TYPE, ...)` / `.withBundlePacket(TYPE, ...)`
call inside it, in source order) and writes the resulting id<->name table to
`minebot/protocol/packets_775.json`. Re-run it any time the sources jar is
regenerated (e.g. after bumping to a newer game version):

```bash
python3 tools/extract_packet_ids.py
```

Result: 256 packets across 5 states (handshake: 1, status: 2+2, login: 5+6,
configuration: 10+19, play: 69 serverbound + 141 clientbound). Spot-checked
against manual reading of the source — matches exactly (e.g.
`SERVERBOUND_CHAT` = id 9, `CLIENTBOUND_KEEP_ALIVE` = id 44,
`CLIENTBOUND_SYSTEM_CHAT` = id 121 in PLAY).

### New-to-us protocol element found in 26.1.2

A "Code of Conduct" acceptance step was added to the CONFIGURATION phase,
not present in any protocol version predating this session's knowledge:

- `ClientboundCodeOfConductPacket(String codeOfConduct)` (clientbound)
- `ServerboundAcceptCodeOfConductPacket` (serverbound, no fields — singleton)

Our client will need to handle this packet (likely: receive it, immediately
reply with the accept packet) somewhere during the configuration phase or
the server will presumably stall/kick us. Not yet implemented.

Everything else in the packet inventory (bundle packets, cookies, known-packs
negotiation, registry data, feature flags, tags, `ServerboundClientTickEndPacket`,
`ServerboundPlayerLoadedPacket`, chat session/signing packets, etc.) matches
the shape already established in the 1.20.5-1.21.x line — no other surprises
found so far.

### Handshake / login wire format (unchanged from vanilla-since-1.7 shape)

- `ClientIntentionPacket(protocolVersion: varint, hostName: string, port: u16, intention: varint)`
  — `intention` values: STATUS=1, LOGIN=2, TRANSFER=3.
- `ServerboundHelloPacket(name: string, profileId: uuid)`.
- `ClientboundLoginCompressionPacket(compressionThreshold: varint)`.
- Online-mode encryption handshake is byte-for-byte the same shape used since
  1.7: `ClientboundHelloPacket(serverId: string, publicKey: bytes, challenge: bytes, shouldAuthenticate: bool)`
  -> client generates AES secret, RSA-encrypts secret+challenge with the
  server's public key, replies `ServerboundKeyPacket(keybytes, encryptedChallenge)`
  -> both sides switch to AES/CFB8 (`Connection.setEncryptionKey`, netty
  `CipherEncoder`/`CipherDecoder` inserted into the pipeline). Nothing new
  here; quarry/pycraft-era knowledge of this flow still applies directly.
- Netty pipeline shape in `net.minecraft.network.Connection`: `splitter`
  (frame-by-length) / `prepender` (length-prefix) with `decompress`/`compress`
  and `decrypt`/`encrypt` handlers insertable around them — same architecture
  as every MC version since compression was introduced.

## Auth requirement (blocking real connection, currently stubbed)

Server is online-mode, so before PLAY we need, in order:
1. Microsoft OAuth device-code flow (user visits a URL, enters a code).
2. Xbox Live authentication, then XSTS token.
3. Minecraft Services (`api.minecraftservices.com`) token exchange -> game
   profile (UUID + username).
4. On receiving `ClientboundHelloPacket`, compute the server hash and call
   Mojang's session server `joinServer` endpoint with the MC access token
   before replying with `ServerboundKeyPacket`.

None of this is protocol-version-specific — it's the same flow every modern
MC client/library uses — but it is real scope (OAuth flow + three HTTP APIs)
and is **not implemented yet**. Current code has a stub interface only
(see `minebot/auth/`), per explicit instruction to defer it.

## Tooling: uv, not raw venv/pip

This project uses `uv` (Astral) for dependency management and running things
— it's the current modern standard, replaces pip+venv+pip-tools with one
fast, lockfile-backed tool. `pyenv` is a different concern (manages multiple
*interpreter* versions side by side) and isn't in use here since one Python
version is enough for this project.

```bash
uv sync --extra dev   # creates .venv/, installs minebot + pytest + pytest-asyncio
uv run pytest -q      # run the test suite
uv run python -m minebot.main   # run the bot (once main.py goes past the
                                 # NotImplementedError for CONFIGURATION/PLAY)
```

`uv.lock` is checked in for reproducibility; regenerate with `uv lock` after
changing dependencies in `pyproject.toml`.

## Repo layout / implementation status as of this session

```
minebot/
  protocol/
    packets_775.json      # generated, see tools/extract_packet_ids.py
    registry.py            # PacketRegistry: id<->name lookups per state/direction — done, tested
    handshake.py            # handshake + LOGIN-phase packet (de)serialization — done for offline path
    login_flow.py            # drives socket -> LOGIN -> start of CONFIGURATION — done for offline path;
                              # raises NotImplementedError on ClientboundHello (online-mode encryption)
  net/
    types.py                # VarInt/UTF/UUID/byte-array encode-decode — done, tested
    connection.py            # framing + zlib compression, encryption hooks present but unwired — done
  auth/
    base.py                  # Authenticator protocol + OfflineAuthenticator (done, tested against
                              # vanilla's offline-UUID algorithm) + MicrosoftAuthenticator (stub, raises
                              # NotImplementedError — real target server needs this, see above)
  bot/
    movement.py               # forward/backward/left/right command handlers — registered but each
                               # raises NotImplementedError; blocked on PLAY-phase movement packets
  commands/
    parser.py                 # !name(args) regex grammar, mirrors mindcraft — done, tested
    registry.py                # name -> async handler dispatch — done, tested
  main.py                      # wires config -> auth -> connection -> login_flow; stops (deliberately,
                                # via NotImplementedError) right after login succeeds — CONFIGURATION
                                # phase (registry data, known-packs, the new code-of-conduct accept
                                # step) and PLAY phase (chat listen loop, movement sends) not built yet
  config.py                    # BotConfig.from_env() — MINEBOT_HOST/PORT/USERNAME/ONLINE_MODE
tools/
  extract_packet_ids.py      # regenerate packets_775.json from a sources jar — done
tests/                        # 22 tests, all passing (uv run pytest -q):
                               # varint/uuid/utf roundtrips, registry lookups + spot-checks against
                               # manually-read source, offline-UUID vs. known "Notch" reference value,
                               # command parser/registry, full LOGIN-phase flow against a fake
                               # in-process offline-mode server
```

**Not started:** CONFIGURATION-phase packet handling (registry data,
known-packs negotiation, feature flags, tags, and the new code-of-conduct
accept step — see above), PLAY-phase packets entirely (chat receive/send,
movement, mining, block placement, inventory, keepalive loop), the real
Microsoft OAuth + Mojang session-join flow needed for our actual target
server (online-mode), and Docker/compose packaging.

Check `git log` / current file state for what's actually been built since —
this doc captures research findings, not a live progress tracker.

## Key external paths referenced (outside this repo)

- `/home/colaila/git/mindcraft` — Node.js reference project (mineflayer-based
  bot with an LLM in the loop). We're porting the non-LLM action/command
  layer conceptually, not the code. See prior conversation turns for the
  full architecture writeup (command regex grammar, skills.js action
  implementations, action-manager/modes pattern) if that detail is needed
  again — not duplicated here since it's orthogonal to the protocol problem.
- `/home/colaila/git/mods/VillagerHelper` — source of the decompiled 26.1.2
  jar; also a good reference for modern Fabric networking API shape
  (`PayloadTypeRegistry`, `CustomPacketPayload`, `StreamCodec`) if we ever
  need a companion Fabric mod again.
- `/home/colaila/git/fabric-loom`, `/home/colaila/git/yarn`,
  `/home/colaila/git/fabric-loader`, `/home/colaila/git/fabric-example-mod` —
  cloned but only fabric-loom's own source and fabric-example-mod's
  `origin/26.1.2` branch ended up being relevant; yarn was a dead end for
  this version (no matching branch, and turned out to be unnecessary).
