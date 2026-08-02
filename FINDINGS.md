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

## Auth: real Microsoft online-mode login (working, proven against the live server)

Server is online-mode, so before PLAY we need real Microsoft/Xbox/Minecraft
auth. **This is fully implemented and has successfully logged into the real
target server** (`51.81.166.195:60433`, account `ritebot`) end-to-end,
including the encrypted LOGIN handshake, CONFIGURATION, and receiving real
chat messages in PLAY.

### The client-ID saga (read this before touching minebot/auth/msa.py)

The obvious approach — reuse `prismarine-auth`'s (mineflayer's underlying
auth library, and therefore mindcraft's) hardcoded Azure AD client id
`389b1b32-b5d5-43b2-bddc-84ce938d6737` (originally from a third-party tool,
Office365APIEditor) — **is dead**: Microsoft returns
`AADSTS700016: Application ... was not found in the directory`, meaning
that app registration has been deleted/deregistered since prismarine-auth's
code was written. Verified live, not assumed.

Investigated what mindcraft/mineflayer *actually* use: their default (no
`flow` specified to prismarine-auth's `Authflow`) is the `msal` flow with
that same dead client id — so mindcraft's own default auth is currently
broken too, for the identical reason, unless a user supplies their own
`authTitle`.

The fix: prismarine-auth also documents a `live` flow
(`docs/API.md`/`LiveTokenManager.js`) that authenticates against the legacy
`login.live.com` device-code endpoints using one of Microsoft's own
**official product Title IDs** (`prismarine-auth`'s `Titles` export) rather
than a self-registered Azure app — these can't go stale the way a
third-party registration can, since Microsoft controls them directly.
Critically, **`Titles.MinecraftJava` (`00000000402b5328`) does NOT work
with the `live` flow's device+title-token dance** (prismarine-auth's own
docs are explicit that `MinecraftJava` only works with the separate `sisu`
flow) — we hit this directly: using the Java title ID here made
`title.auth.xboxlive.com/title/authenticate` reject our device token with a
bare `400 {}`. Switching to **`Titles.MinecraftNintendoSwitch`
(`00000000441cc96b`) + `DeviceType: "Nintendo"`** (prismarine-auth's own
documented example for the `live` flow) fixed it immediately. This is *not*
what mindcraft's default does, but it's a legitimate, documented
prismarine-auth flow that actually works today with zero user setup
(no Azure app registration needed).

### The actual working flow (see minebot/auth/msa.py, xbox.py,
minecraft_services.py, encryption.py, base.py)

1. **MSA device-code**: `POST login.live.com/oauth20_connect.srf`
   (`client_id=00000000441cc96b`, `scope=service::user.auth.xboxlive.com::MBI_SSL`,
   `response_type=device_code`) -> `{user_code, device_code, verification_uri, interval, expires_in}`.
   Show the user `verification_uri` + `user_code`. Poll
   `POST login.live.com/oauth20_token.srf?client_id=...` with
   `grant_type=urn:ietf:params:oauth:grant-type:device_code` every
   `interval` seconds. **Gotcha**: the "still waiting" response
   (`authorization_pending`) comes back as **HTTP 400**, not 200 — check the
   JSON `error` field regardless of status code, don't treat 400 as fatal.
   Also carry the devicecode response's `Set-Cookie` cookies through to the
   polling requests (login.live.com ties the device-code session to them).
2. **Xbox device token**: `POST device.auth.xboxlive.com/device/authenticate`,
   `DeviceType: "Nintendo"`, `Version: "0.0.0"`, random `Id`/`SerialNumber`
   UUIDs, signed (see below) -> device token.
3. **Xbox title token**: `POST title.auth.xboxlive.com/title/authenticate`,
   `RpsTicket: "t=" + msa_access_token`, `DeviceToken: <device token>`,
   signed -> title token. (This step is what rejected the wrong title ID.)
4. **Xbox user token**: `POST user.auth.xboxlive.com/user/authenticate`,
   `RpsTicket: "t=" + msa_access_token` (note: `"t="` preamble for a Title-ID
   /`live`-flow token; a real Azure-app/`msal`-flow token would use `"d="`
   instead — mixing these up produces a cryptic auth failure), signed ->
   user token.
5. **XSTS**: `POST xsts.auth.xboxlive.com/xsts/authorize` with
   `UserTokens: [user_token]`, `DeviceToken`, `TitleToken`,
   `RelyingParty: "rp://api.minecraftservices.com/"`, signed -> XSTS token +
   `uhs` (user hash).
6. **Request signing** (steps 2-5 all need this): every Xbox Live call must
   include an `ES256` (P-256 EC) keypair-derived JWK as `"ProofKey"` in the
   payload, and a base64 `"Signature"` header computed over
   `policy_version(i32 BE) + \x00 + windows_epoch_timestamp(u64 BE) + \x00 +
   "POST\x00" + url_path_and_query + "\x00" + "\x00" (empty auth token) +
   body + "\x00"`, signed with ECDSA/SHA-256 over that byte string, with the
   raw (r || s, 32 bytes each) signature — not DER — appended after a
   4-byte policy version + 8-byte Windows-epoch timestamp header. Windows
   epoch = Unix epoch + 11644473600 seconds, in 100ns ticks
   (`* 10_000_000`). Skipping this makes Xbox Live reject every request.
7. **Minecraft Services login**: `POST api.minecraftservices.com/authentication/login_with_xbox`,
   `identityToken: "XBL3.0 x=" + uhs + ";" + xsts_token` -> Minecraft access
   token.
8. **Profile fetch**: `GET api.minecraftservices.com/minecraft/profile`,
   `Authorization: Bearer <mc access token>` -> `{id, name}` (UUID +
   username). A 404 here can mean several different things (no
   entitlement, entitlement present but no Java username set yet at
   minecraft.net, or — rarely — a very fresh purchase/rename not yet
   propagated); **always surface the actual response body** rather than
   assuming which, see "debugging notes" below.
9. **Session join**: on receiving `ClientboundHelloPacket` (empty
   `serverId` string in this protocol version — that's normal, not a bug),
   compute the Mojang server hash (`sha1(serverId_latin1_bytes + shared_secret
   + server_public_key_der)`, then Java `BigInteger(digest).toString(16)`
   semantics: two's-complement signed interpretation of the 20 hash bytes,
   formatted as hex with a leading `-` for negative values — cross-checked
   against real `java.math.BigInteger` output, see `tests/test_encryption.py`),
   then `POST sessionserver.mojang.com/session/minecraft/join` with
   `{accessToken, selectedProfile: <dashless uuid>, serverId: <hash>}`
   before replying with `ServerboundKeyPacket`.
10. **Encryption**: RSA (PKCS#1 v1.5, Java's default `Cipher.getInstance("RSA")`)
    -encrypt a fresh 16-byte (AES-128) secret and the server's challenge with
    its public key, send `ServerboundKeyPacket(encrypted_secret,
    encrypted_challenge)`, then switch the connection to AES/CFB8 (IV = the
    secret itself) both directions.

None of steps 1-10 are protocol-version-specific — this is the same flow
every modern MC client/library uses — but it took real investigation to get
right (see the client-ID saga above and the debugging notes below).

### A real bug we hit and fixed: CONFIGURATION-phase keepalive/ping

First full live run got all the way through LOGIN and well into
CONFIGURATION (client info sent, custom payload/feature-flags/known-packs/~25
registry-data chunks/tags all received correctly), then died with a clean
EOF a few seconds later. Turned out: the server sends `CLIENTBOUND_KEEP_ALIVE`
and `CLIENTBOUND_PING` during CONFIGURATION too, not just PLAY, and our
keepalive/ping handling only existed in the PLAY loop
(`minebot/bot/play_loop.py`) — CONFIGURATION-phase keepalives were silently
ignored, and the server disconnected us for not responding. Fixed by making
`minebot/protocol/keepalive.py`'s functions take an explicit `state`
parameter (packet IDs differ between CONFIGURATION and PLAY) and wiring
keepalive+ping handling into `run_configuration_phase` too. Regression-tested
in `tests/test_configuration_flow.py` and `tests/test_keepalive.py`.

### Debugging notes / how we found all this

- Ran the bot for real with `2>&1 | tee /tmp/some.log` so output could be
  read back after the fact — much more effective than relaying terminal
  output by hand.
- Added (then, once things worked, trimmed back to `logging.debug`) explicit
  logging at every step of LOGIN/CONFIGURATION — this is what let us see
  exactly which packet type preceded a disconnect, rather than guessing from
  a bare `IncompleteReadError`.
- `IncompleteReadError: 0 bytes read on N expected` = the peer closed the
  TCP connection cleanly (EOF), not a decode/decryption bug (which would
  instead produce garbage bytes or a length-prefix that doesn't make sense).
  Useful signal for distinguishing "our crypto is wrong" from "something else
  made the server hang up on us."
- When something looks like our code's fault, check the *server's* logs too
  if available — its Netty encoder error
  (`Sending unknown packet 'clientbound/minecraft:disconnect'`,
  `IdDispatchCodec.encode`) looked like a server-side crash at first, but was
  actually the server trying (and failing, due to being mid-teardown) to
  send a disconnect reason after the client side was already closed — a
  process-killed-by-user artifact, not a real bug.
- Cross-check crypto/algorithm implementations against real reference
  output when possible instead of trusting memorized "known" values: we
  initially had a wrong set of "known" server-hash test vectors (fabricated
  from memory) that made a *correct* implementation look broken. Recomputed
  the same test cases with actual `javac`/`java` (`java.math.BigInteger`)
  locally and confirmed the Python implementation matched exactly.
- Mojang's public, no-auth profile lookup
  (`https://api.mojang.com/users/profiles/minecraft/<name>`) is a useful,
  independent way to check whether a username has actually propagated on
  Mojang's backend, decoupled from our own OAuth chain.

## Tooling: uv, not raw venv/pip

This project uses `uv` (Astral) for dependency management and running things
— it's the current modern standard, replaces pip+venv+pip-tools with one
fast, lockfile-backed tool. `pyenv` is a different concern (manages multiple
*interpreter* versions side by side) and isn't in use here since one Python
version is enough for this project.

```bash
uv sync --extra dev   # creates .venv/, installs all deps incl. pytest/pytest-asyncio
uv run pytest -q      # run the test suite
uv run python -m minebot.main   # run the bot, reading config from .env (see below)
```

`uv.lock` is checked in for reproducibility; regenerate with `uv lock` after
changing dependencies in `pyproject.toml`.

### Config: .env (gitignored) + .env.example (committed template)

`minebot/config.py`'s `BotConfig.from_env()` calls `python-dotenv`'s
`load_dotenv()` first, so a `.env` file in the repo root is picked up
automatically — no need to prefix every invocation with env vars by hand.
`.env` is gitignored (it holds the real target server address, and will
hold cached auth tokens if/when that gets added); `.env.example` is the
committed, documented template to copy from. Vars:
`MINEBOT_HOST`, `MINEBOT_PORT`, `MINEBOT_USERNAME` (only used in offline
mode — see below), `MINEBOT_ONLINE_MODE`.

Note on `MINEBOT_USERNAME`: it's **only consumed by `OfflineAuthenticator`**
(`minebot/main.py`). In online mode it's completely unused — the real
username/UUID always comes from whichever Microsoft account you sign into
via the device-code flow at runtime, not this variable. This caused real
confusion mid-session (tried setting it to an email at one point) — worth
remembering if it comes up again.

## Repo layout / implementation status as of this session

```
minebot/
  protocol/
    packets_775.json      # generated, see tools/extract_packet_ids.py
    registry.py            # PacketRegistry: id<->name lookups per state/direction — done, tested
    handshake.py            # handshake + LOGIN-phase packet (de)serialization — done, incl. ServerboundKeyPacket
    login_flow.py            # drives socket -> LOGIN -> start of CONFIGURATION, incl. the full
                              # online-mode encryption handshake — done, proven against the real
                              # target server (see "Auth" section above)
    configuration.py         # CONFIGURATION-phase driver — done: sends client info, answers
                              # keepalive/ping (see "real bug" note above), replies to known-packs
                              # negotiation with an empty list, auto-accepts the code-of-conduct
                              # prompt, acks finish-configuration. Drains/ignores everything else
                              # (cookies, resource packs, registry data, feature flags, tags,
                              # dialogs, server links) — safe, not required to proceed.
    chat.py                  # PLAY-phase chat — done: parses ClientboundPlayerChatPacket (reads
                              # straight to SignedMessageBody.Packed.content, ignores trailing
                              # fields) and ClientboundSystemChatPacket (NBT component -> text,
                              # including translatable/"translate"+"fallback"+"with" components,
                              # not just plain "text" -- see below). Sends outbound chat as
                              # `/say <msg>` via ServerboundChatCommandPacket (see "Outbound chat"
                              # section below for why, not real player chat).
    keepalive.py              # keepalive + ping/pong, parameterized by state (CONFIGURATION and
                                # PLAY have separate packet-id tables) — done, tested
    nbt.py                    # minimal network-NBT reader (type byte + payload, no root name,
                                # per NbtIo.readAnyTag) — only compound/string/list + numeric types,
                                # enough to pull chat-component fields out. Extend this (don't write
                                # a second parser) if a later packet needs full NBT (e.g. item
                                # components, block entity data).
  net/
    types.py                # VarInt/UTF/UUID/byte-array encode-decode — done, tested
    connection.py            # framing + zlib compression + AES/CFB8 encryption — done, proven live
  auth/
    base.py                  # Authenticator protocol + OfflineAuthenticator (done, tested against
                              # vanilla's offline-UUID algorithm) + MicrosoftAuthenticator (done,
                              # proven against the real target server — see "Auth" section above)
    msa.py                    # MSA device-code flow against login.live.com w/ the Nintendo Switch
                               # title id — done
    xbox.py                   # Xbox Live device/title/user/XSTS token exchange + request signing — done
    minecraft_services.py      # Minecraft Services login + profile fetch — done
    encryption.py               # server-hash computation (Java BigInteger-compatible, cross-checked),
                                 # RSA/AES crypto helpers, sessionserver joinServer call — done
  bot/
    movement.py               # forward/backward/left/right command handlers — registered but each
                               # raises NotImplementedError; blocked on PLAY-phase movement packets.
                               # Handler signature is (conn, *args) to match play_loop's dispatch.
    play_loop.py                # PLAY-phase main loop — done: reads packets forever, auto-answers
                                 # keepalive + ping, feeds player/system chat text through the command
                                 # registry (dispatch(text, conn)). Proven live: received and logged
                                 # real chat messages from another player on the target server.
                                 # Everything else (entity/chunk/inventory packets) ignored for now.
  commands/
    parser.py                 # !name(args) regex grammar, mirrors mindcraft — done, tested
    registry.py                # name -> async handler dispatch — done, tested
  main.py                      # wires config -> auth -> connection -> login_flow -> configuration
                                # -> play_loop. This is the full MVP path from prompt.txt (connect,
                                # listen to chat, parse commands), proven end-to-end against the real
                                # online-mode target server.
  config.py                    # BotConfig.from_env(), loads .env via python-dotenv first —
                                # MINEBOT_HOST/PORT/USERNAME/ONLINE_MODE (see ".env" section above)
tools/
  extract_packet_ids.py      # regenerate packets_775.json from a sources jar — done
tests/                        # 48 tests, all passing (uv run pytest -q): varint/uuid/utf roundtrips,
                               # registry lookups + spot-checks against manually-read source, offline-UUID
                               # vs. known "Notch" reference value, command parser/registry, NBT reader
                               # (plain text + nested "extra" siblings), chat packet parsing (player chat
                               # with/without signature, system chat incl. translatable components),
                               # keepalive/ping echo (both states), server-hash vs. real java.math.BigInteger
                               # output, RSA/AES roundtrips, full LOGIN flow for both offline and online mode
                               # (the latter with a real generated RSA keypair and a full AES/CFB8 cipher
                               # switch mid-connection), CONFIGURATION phase incl. keepalive/ping, and the
                               # PLAY loop — all against fake in-process servers except where noted above
```

### Outbound chat: why /say instead of real ServerboundChatPacket

Real player chat (`ServerboundChatPacket`) requires the 1.19.1+ secure chat
signing system: a running `LastSeenMessages` acknowledgement tracker (offset,
BitSet, checksum) built from every `ClientboundPlayerChatPacket` seen, plus
optionally a cryptographic signature. That's real, version-independent scope
unrelated to the 26.1.2-specific work. Decided with the user to instead send
outbound bot messages as `/say <message>` via `ServerboundChatCommandPacket`
(just a single string field, no signing) — the trade-off is messages show up
as a command/announcement rather than a normal player chat bubble under the
bot's name. Revisit if a deployment needs real signed chat.

**Not started:** movement/mining/placing/combat/inventory PLAY packets (only
chat + keepalive/ping are wired up), and Docker/compose packaging. Auth
token caching to disk (every run currently redoes the full device-code
sign-in, which is correct but a bit disruptive) is a possible future
convenience improvement, not a blocker.

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
