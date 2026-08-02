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

## Movement + follow-player

Implemented `!forward`/`!backward`/`!left`/`!right(distance=1.0)`,
`!follow` (or `!follow("name")`), and `!stop` (cancels an active follow).

Every command handler's signature is `(conn, sender, *args)` --
`run_play_loop` now calls `commands.dispatch(text, conn, sender)` where
`sender` is the speaking player's UUID (from `PlayerChatMessage.sender`) for
player chat, or `None` for system chat. `!follow` uses this: with no
argument it targets whoever typed the command (via `EntityTracker.find_by_uuid`
on the sender directly, no name lookup needed at all); `!follow("name")`
still works by resolving the name through the tab-list mapping
(`EntityTracker.name_to_uuid`) first. Following by UUID directly (rather
than requiring a name) is strictly more robust, since it works even for
players not yet seen in a `ClientboundPlayerInfoUpdatePacket`.

### Verified live, with two real bugs found and one fixed

Tested against the real target server: sign-in (using the cached refresh
token -- see below), login, `!follow` typed by another player, and the bot
visibly followed. Two issues surfaced:

1. **Movement looked jerky** -- `FOLLOW_STEP_INTERVAL_SECONDS` was 0.5s with
   a flat 1.0-block step, i.e. ~2 blocks/sec in visibly discrete jumps.
   Fixed: tick interval down to 0.15s, step distance scaled to match
   vanilla's real walk speed (`4.317 blocks/sec * interval`) instead of a
   fixed distance, so it now moves in smaller, more frequent increments that
   read as continuous walking rather than teleport-stepping.
2. **Y-axis / no jumping** -- our follow logic only ever moves in the (x, z)
   plane; `self.y` is never touched after the initial position sync. When
   the user walked to a lower Y level, the bot kept following in x/z at its
   old Y and visibly floated in the air; walking to a higher Y (a 1-block
   step-up) produced no jump attempt at all, since we have no concept of
   ground height or block collision. **Not fixed this session** -- see
   "Why this needs real pathfinding" below for why it's not a quick patch.

### Why this needs real pathfinding (mineflayer-pathfinder), not a quick fix

Checked how mindcraft actually handles this: it doesn't implement
follow/movement logic itself at all. `skills.followPlayer()` /
`skills.goToPlayer()` (`src/agent/library/skills.js`) are thin wrappers
around `bot.pathfinder.setGoal(new pf.goals.GoalFollow(player, distance), true)`
-- the entire pathfinding/physics subsystem is `mineflayer-pathfinder`, a
separately-maintained library, not something mindcraft wrote.

Pulled `mineflayer-pathfinder@2.4.5`'s actual source to gauge real porting
effort: core logic is ~2300 lines across `astar.js` (125, generic A* --
portable as-is, no block data needed), `heap.js` (81, binary heap for the
open set), `goals.js` (492, goal definitions like `GoalFollow`/`GoalNear`),
`movements.js` (663, **the actual blocker** -- computes neighbor
moves/costs by querying real block state: is this solid, diggable, a
liquid, dangerous (lava/cobweb), climbable, etc., which requires both (a)
parsed chunk/block data, which we don't have -- `ClientboundLevelChunkWithLightPacket`
is entirely unparsed right now -- and (b) a block-registry data source
(block properties by ID/state) equivalent to Node's `minecraft-data`
package, which doesn't exist for `26.1.2` either, though the raw registry
IDs are in `CLIENTBOUND_REGISTRY_DATA`, which we currently only drain, not
decode), and `physics.js`/`move.js` (~140, jump/fall/step timing based on
real per-tick velocity simulation).

`astar.js` and `goals.js` are genuinely portable now with no new
dependencies. The real prerequisite work, in order, is:
1. Parse `ClientboundLevelChunkWithLightPacket`'s paletted-container block
   format (a bit-packed per-section block-state array with a small local
   palette -- a well-documented but nontrivial encoding) -- OR, as a
   cheaper first step, just decode the packet's **heightmaps**
   (`Map<Heightmap.Types, long[]>`, already precomputed server-side per
   (x,z) column) to answer "what's the ground height here" without needing
   full block-type data at all. Good enough for basic walk/step-up/fall
   movement; not enough for real A* around obstacles (needs full block
   solidity, not just height).
2. A minimal block-registry lookup (from `CLIENTBOUND_REGISTRY_DATA`) if/when
   full `movements.js`-equivalent cost modeling is wanted.
3. Port `physics.js`/`move.js`'s jump-timing logic against our own tick loop.
4. Port `movements.js`'s neighbor/cost model once (1)+(2) exist.
5. Port `astar.js`+`heap.js`+`goals.js` (already portable, just needs (4)
   to call into).

Decided to land the tick-rate fix now (self-contained, already done above)
and treat the chunk-parsing foundation as its own dedicated follow-up
rather than half-wiring jump physics on top of no block data.

### Step (1) done: heightmap-based ground-height tracking (minebot/protocol/chunks.py)

Implemented the "cheaper first step" from the plan above: parses
`ClientboundLevelChunkWithLightPacket`'s **heightmaps** only (not the raw
per-section paletted block buffer that follows them in the packet --
deliberately not parsed, and not needed for this). This answers "what's
the ground height at (x, z)" without needing full block-type data.

Key implementation details, all cross-checked against the decompiled source
rather than assumed:
- Heightmap bit-packing (`net.minecraft.util.SimpleBitStorage`): 256
  entries (16x16 columns), `bits`-wide fields, `valuesPerLong = 64 // bits`
  values per `long`, entry `index` at `data[index // valuesPerLong]`, bit
  offset `(index % valuesPerLong) * bits`. The class's actual division
  logic uses a lookup-table "magic number" multiply-shift optimization
  (for fast non-power-of-2 division), which is mathematically identical to
  plain integer floor division -- didn't need to replicate the optimization
  itself, just its semantics.
- `bits = ceil(log2(dimension_height + 1))`, where `dimension_height` is
  part of the dimension-type registry data we don't parse (part of
  `CLIENTBOUND_REGISTRY_DATA`). Rather than hardcode a specific dimension's
  height, `bits` is reverse-derived from the observed heightmap `long[]`
  array's length (`_infer_bits_from_long_count`), which is unambiguous for
  any realistic Minecraft world height (bits 1-10, i.e. heights up to 1023
  blocks -- verified this holds; it stops being unique somewhere past
  `bits=10`, but no real dimension is anywhere near that tall).
- The stored raw value means `getFirstAvailable` (first empty Y above the
  ground) when added to `min_y`, **not** the topmost solid block itself --
  confirmed via `Heightmap.getFirstAvailable`/`getHighestTaken` in the
  decompiled source (`getHighestTaken = getFirstAvailable - 1`). Our
  `ground_height_at()` returns the highest solid/matching block's Y
  (`getHighestTaken` equivalent); a player's feet rest one block above that.
- `min_y` itself is also dimension-registry data we don't parse; defaults
  to `-64` (the standard modern overworld since the 1.18 height expansion).
  Wrong for the Nether (`min_y=0`), a custom dimension, or pre-1.18 world
  format -- acceptable for now since we're only using this to follow a
  player who's presumably also in the overworld.

`ChunkHeightmapCache` holds `(chunk_x, chunk_z) -> ChunkHeightmap` and
answers `ground_height_at(world_x, world_z)`, handling negative-coordinate
chunk/local-index math correctly (Python's `>>`/`&` on negative ints already
give the right floor-division/modulo semantics here, verified explicitly
rather than assumed). Wired into `run_play_loop` (feeds
`ClientboundLevelChunkWithLightPacket`s in); still built and fed every run
as useful groundwork for future pathfinding work, but **not currently used
by `MovementController`** -- see the correction below for why.

**Correction from live testing**: the first version of the follow fix
snapped `self.y` to `ChunkHeightmapCache.ground_height_at()` every tick.
Live testing surfaced a real bug this approach can't solve: standing
indoors under a roof, the bot teleported *up to the roof* instead of
matching the player's actual (lower, indoor) Y. The reason is structural,
not a parsing bug: `MOTION_BLOCKING` heightmaps only ever store the single
highest solid/liquid block in an entire (x,z) column -- they have no way
to represent "the floor under whatever's above it." Any player standing
under a roof, overhang, or upper floor will always resolve to the
roof/ceiling's height, never the floor they're actually standing on.

Fixed by switching the follow loop's Y-source from the heightmap to the
**target's own tracked Y** (`EntityTracker`'s `TrackedEntity.y`, fed by
`AddEntity`/`TeleportEntity`/`EntityPositionSync`/`MoveEntity` -- see the
entity-tracking section above). This has no such ambiguity: it's simply
wherever the server says the target actually is, indoors or out.
`MovementController._step_toward_target_height()` ramps `self.y` toward
`target.y` by at most `FOLLOW_MAX_VERTICAL_STEP` (1.2 blocks) per tick,
rather than snapping instantly -- also fixes a related complaint from live
testing ("it's just teleporting to the target Y").

One more bug caught while fixing this: the follow loop's original
structure gated *all* per-tick updates (both x/z movement and the Y step)
behind "are we still further than `FOLLOW_STOP_DISTANCE` away
horizontally?" -- meaning once the bot was standing right next to the
target, it would stop reacting entirely, including to a pure vertical
difference (e.g. the target hopping onto a ledge right beside the bot).
Fixed by decoupling the two: horizontal movement is still gated on
distance, but the Y step now runs independently whenever `target.y !=
self.y`, even with zero horizontal distance left to close.

Net effect: `ChunkHeightmapCache`/heightmap parsing remains implemented,
tested, and wired into the PLAY loop (it's real, correct groundwork for
when full pathfinding is eventually built and needs terrain-height
estimation for *unvisited* columns where no live entity position exists)
but is not currently consulted for following a tracked player, since that
player's own reported position is always the better signal. Steps (2)-(5)
from the plan above (block-registry lookup, real jump timing, the
movements cost model, and A*) remain unimplemented. This still isn't real
physics: no jump animation, no "too high to climb" detection, and the
server may reject/correct a position that isn't a plausible single step
from where it last placed us -- not yet observed/handled.

### Our own position: ClientboundPlayerPositionPacket

Field layout: `id: varint` (a teleport id, echoed back), then
`PositionMoveRotation` = `position: Vec3(f64,f64,f64)`,
`deltaMovement: Vec3(f64,f64,f64)` (present on the wire but unused by us),
`yRot/xRot: f32`, then `relatives: Set<Relative>` as a **raw i32 bitmask**
(not a varint -- `Relative.SET_STREAM_CODEC` uses `ByteBufCodecs.INT`), bit N
= `Relative` enum ordinal N (`X=0, Y=1, Z=2, Y_ROT=3, X_ROT=4, ...`). Each
axis is either an absolute value or an offset added to our last known
value, per whether its bit is set -- in practice vanilla servers send an
all-absolute sync on spawn/teleport. We must reply with
`ServerboundAcceptTeleportationPacket(id: varint)`, echoing the same id, or
the server disconnects us for not acknowledging the teleport (same
"unacknowledged packet" pattern as the CONFIGURATION-phase keepalive bug
above -- always check whether a clientbound sync/state-change packet
expects an ack).

Movement is sent via `ServerboundMovePlayerPacket.PosRot`
(`x,y,z: f64, yRot,xRot: f32, flags: u8` where bit0=onGround,
bit1=horizontalCollision) with our tracked position updated locally first
-- the server trusts client-reported positions within reason (anti-cheat
notwithstanding) rather than us waiting for a round-trip confirmation per
step.

Yaw-to-direction math is standard vanilla convention: yaw 0 faces +Z, and
walking "forward" moves along `(-sin(yaw), cos(yaw))` in the (x, z) plane;
this hasn't changed across versions and is the same math every Minecraft
bot library uses.

### Tracking other entities/players: minebot/protocol/entities.py

`!follow` needs another player's live position. Building this required
more than one packet:

- `ClientboundAddEntityPacket`: gives `(entity_id, uuid, x, y, z)`. We
  deliberately do **not** decode the `type` field (a registry-ID varint) --
  matching purely on `uuid` avoids needing to parse
  `CLIENTBOUND_REGISTRY_DATA` (currently just drained, not interpreted) to
  know which registry ID corresponds to "player."
- `ClientboundRemoveEntitiesPacket`: varint-prefixed list of entity ids to
  drop from tracking.
- `ClientboundTeleportEntityPacket` / `ClientboundEntityPositionSyncPacket`:
  both share an `(id: varint, PositionMoveRotation, ...)` prefix giving an
  absolute position -- simpler than the delta-based move packets below.
- `ClientboundMoveEntityPacket.Pos`/`.PosRot`: relative position updates
  using the classic **fixed-point delta encoding** unchanged since ~1.8:
  `xa/ya/za: i16`, each unit = 1/4096 of a block. Must be accumulated onto
  the entity's last known absolute position (from AddEntity or a
  teleport/sync packet), not treated as absolute.
- `ClientboundPlayerInfoUpdatePacket`: the trickiest one -- gives
  username<->UUID (needed to resolve a name typed in `!follow("name")` to
  an entity). Its wire format is `actions: EnumSet<Action>` as a
  **fixed-size bitset** (`ceil(8 actions / 8) = 1 byte`, LSB-first per
  `BitSet.valueOf`), then a varint-prefixed entry list where **each entry's
  field layout depends on which actions are active** (every active action
  contributes one field, in the action enum's declared order, not
  necessarily UUID-then-name-then-whatever). We parse `ADD_PLAYER`'s
  `(name: string, GameProfileProperties)` pair for the name, but must still
  correctly consume every other active action's bytes (latency varint,
  listed bool, a nullable chat-session record with a nested nullable
  `ProfilePublicKey.Data`, a nullable NBT Component for display name, etc.)
  or the next entry's fields desync. Two bugs caught during this: (1) a
  `ProfilePublicKey.Data`'s `expiresAt` is a **plain i64 epoch-millis**
  (`FriendlyByteBuf.readInstant` = `Instant.ofEpochMilli(readLong())`), not
  i64-seconds+i32-nanos as commonly assumed from other serialization
  formats; (2) `readNullable`'s wire shape is a plain bool prefix (true =
  value follows), which is easy to get right but easy to forget to apply
  consistently across every nullable sub-field.

`EntityTracker` (in `minebot/protocol/entities.py`) holds `by_id: {entity_id
-> position}` and `name_to_uuid`, fed by `apply_entity_packet()` from the
PLAY loop; `MovementController.follow()` (in `minebot/bot/movement.py`)
runs a background `asyncio.Task` that polls the tracker every
`FOLLOW_STEP_INTERVAL_SECONDS` (0.15s, see the tick-rate fix above) and
steps toward the target's last known position, stopping within ~2 blocks;
`!stop` cancels that task.

## Death and respawn (minebot/protocol/health.py)

Found via live testing: a baby zombie killed the bot, and it never
recovered -- died server-side and just sat there indefinitely. Even a
subsequent `/tp` from another player didn't make it visibly reappear,
which makes sense in retrospect: a dead player that never requests respawn
isn't a normal tickable/visible entity to other clients, so nothing we did
afterward (including moving our internally-tracked x/y/z, which we kept
right on updating) would show up in-game. This is why the bot could
"report it's with you" while being invisible: our process-local state
(`MovementController.x/y/z`) never knew anything was wrong, only the
server-side entity did.

Implemented in `minebot/protocol/health.py`:
- `CLIENTBOUND_LOGIN` (the PLAY-phase spawn packet -- distinct from the
  LOGIN-state's `CLIENTBOUND_LOGIN_FINISHED`, and previously entirely
  unparsed by us): its first field is `playerId: i32`, our own entity ID.
  We now capture this in `run_play_loop` as `own_entity_id`.
- `CLIENTBOUND_PLAYER_COMBAT_KILL`: `playerId: varint, message: Component`
  (message not parsed -- we don't need the death message text). This
  packet fires for **any** player's death broadcast visible to us, not
  just our own -- confirmed via the decompiled client's own
  `handlePlayerCombatKill`, which only reacts
  `if (packet.playerId() == this.minecraft.player's entity id)`. We do the
  same comparison against our tracked `own_entity_id` before reacting;
  regression-tested in `tests/test_play_loop.py` (someone else's death is
  a no-op, ours triggers a respawn request).
- On our own death: send `SERVERBOUND_CLIENT_COMMAND` with
  `action=PERFORM_RESPAWN` (0) -- mirrors the real client's
  `shouldShowDeathScreen()`-false branch (we have no UI, so we always
  respawn immediately rather than waiting on user input we can't receive).
- `CLIENTBOUND_RESPAWN` arrives once the respawn completes; not parsed
  (it carries spawn info but no position -- the fresh position always
  arrives separately via the `ClientboundPlayerPositionPacket` handling we
  already had), just used as a signal.
- Both the death and the respawn events call
  `MovementController.mark_position_stale()`, which clears `has_position`
  (our tracked x/y/z is from wherever we died, not the new spawn point --
  every movement command already guards on `has_position` and raises if
  it's false) and cancels any in-progress `!follow` task (continuing to
  chase someone using stale pre-death coordinates would be actively wrong).

**Correction from a second round of live testing**: the first version only
listened for `CLIENTBOUND_PLAYER_COMBAT_KILL`. A real death was still
missed entirely (no respawn log line at all) -- turns out `COMBAT_KILL` is
not sent for every death path; the decompiled client's actual death
detection lives in `Minecraft.java`'s own per-tick game loop, which polls
`player.isDeadOrDying()` (`health <= 0`, set from
`ClientboundSetHealthPacket` via `hurtTo()`) every frame, entirely separate
from any death-message packet. Fixed by triggering respawn from
`CLIENTBOUND_SET_HEALTH` reaching `health <= 0` as well (matching what the
real client actually does), keeping `COMBAT_KILL` as a second, harmless
trigger for the same death. Guarded with an `awaiting_respawn` flag in
`run_play_loop` so receiving both signals for one death (a real
possibility) sends exactly one `PERFORM_RESPAWN`, not two -- regression
tested in `tests/test_play_loop.py` alongside a health-only-death test.

## Robustness gaps found from "the bot went silent after !follow" (third live-testing round)

After the health-based respawn fix, the bot respawned correctly and became
visible, but then went silent: `!follow` was typed three times across the
session, no movement was ever observed, and the log simply stopped
producing lines after the last `!follow` (no traceback, no further
activity logged). Two real bugs, found by inspection since the exact
runtime cause couldn't be reproduced/confirmed live in the moment:

1. **`MovementController._follow_loop` had two silent-forever-idle paths**:
   if `has_position` was `False` (e.g. right after a respawn that hasn't
   yet gotten a fresh `ClientboundPlayerPositionPacket`) or the target
   wasn't in `EntityTracker` (e.g. out of range, or a lookup gap), the loop
   just `continue`d forever with zero logging -- indistinguishable from
   "working but nothing to do" versus "stuck". Fixed: both conditions now
   log a one-time `logging.warning` (re-armed once the condition clears),
   so a stuck follow is now visible in the log instead of silent.
2. **`CommandRegistry.dispatch()` had no exception handling around the
   handler call at all.** Since `run_play_loop` awaits `dispatch()` inline
   inside its main `while True: read_packet()` loop (the same loop that
   answers every keepalive), an uncaught exception from *any* command
   handler -- not just movement -- would propagate out of `dispatch`,
   out of `run_play_loop`, and kill the entire read loop silently (no
   traceback would even reach the log if something upstream swallowed it,
   e.g. depending on how the process is being supervised). This is a
   plausible explanation for total unresponsiveness after a single bad
   command, though it could not be confirmed as *the* cause of this
   specific incident. Fixed regardless, since it's a real gap either way:
   `dispatch()` now catches and logs (`log.exception`) any handler
   exception rather than propagating it, so a bug in one command can never
   take down chat responsiveness or keepalive handling.

Also added: a `position sync: (x, y, z) yaw=...` log line for every
`ClientboundPlayerPositionPacket` (previously silent), so a future
"is our position actually updating" question can be answered directly from
the log rather than inferred.

Net effect: even if the exact trigger for this incident is never fully
pinned down, the system can no longer fail silently in either of these two
ways again.

**Root cause found (first incident)**: with the new logging in place,
`!follow` consistently logged
`follow(...) idling: target not found in entity tracker` right after
`!follow` was typed. Added `MINEBOT_LOG_LEVEL=DEBUG` (env var, read in
`main.py` -- see `.env.example`) to log every
`CLIENTBOUND_ADD_ENTITY`/`CLIENTBOUND_REMOVE_ENTITIES`/
`CLIENTBOUND_PLAYER_INFO_UPDATE` seen.

**Follow-up round with debug logging on**: `!follow` worked, but the user
reported it "freezes for some seconds, then works again," and noticed it
correlated with losing line of sight. The debug log explains this
precisely: `CLIENTBOUND_REMOVE_ENTITIES` fired **161 times** in a single
session (alongside 308 `ADD_ENTITY`s), i.e. the server is actively
removing and re-adding the target's entity from our view repeatedly as
they move (out of render distance, or possibly server-side occlusion/
visibility culling -- both plausible, not distinguished). This is expected
protocol behavior, not a bug: `EntityTracker.handle_remove_entities` and
`MovementController._follow_loop`'s "target not found" idle-and-retry
already handle this exactly as intended -- the "freeze" the user saw *is*
the idle period, and it self-recovers via the warning-then-clear logic
already in place once a fresh `AddEntity` arrives, matching what was
observed live. No further fix needed for this specific behavior; it's an
inherent limitation of following-by-last-known-position without real
pathfinding/prediction (see "Why this needs real pathfinding" above) --
during a visibility gap we simply don't know where the target is, so
idling is the only honest option available at this level of implementation.

The original hypothesis (from the very first symptom report, before debug
logging existed) that `AddEntity` "genuinely never arrived at all" for the
target turned out to be specific to that earlier session/moment, not a
structural gap -- once observed with proper logging, `AddEntity` for the
target does arrive reliably; it also gets removed and re-added
intermittently as a normal consequence of the server's own entity
visibility bookkeeping. `MINEBOT_LOG_LEVEL=DEBUG` remains available for
any future "is entity X being tracked" troubleshooting.

## Why the bot got stuck on stairs: movement authority is client-side (fourth live-testing round)

User reported the bot gets stuck descending stairs while following, and
correctly guessed the underlying cause before we'd even looked at it: "the
physics happens on clients and then the Y position resolved is reported to
the server" -- confirmed exactly right by reading
`ServerGamePacketListenerImpl.handleMovePlayer` (server) in the decompiled
source. The server does **not** run its own independent gravity
simulation and reject movement based on a hardcoded speed limit; instead
it tracks its own *expectation* of the player's velocity
(`this.player.getDeltaMovement()`, itself built up over time from the
player's own prior reported position deltas) and only corrects a reported
position when it deviates too far from that expectation
(`movedDist - expectedDist > metersPerTick * deltaPackets`, with a fairly
generous tolerance -- `metersPerTick=100.0`, i.e. squared distance, so
~10 blocks/tick of slack). Real clients never trip this because their
gravity/falling velocity was already being incrementally built up tick by
tick; our old flat-rate Y ramp (`FOLLOW_MAX_VERTICAL_STEP = 1.2`, snapping
straight toward the target's Y with zero prior velocity) looked, from the
server's perspective, like an instantaneous unexplained jump every single
tick, and got silently corrected back to the old position every time --
confirmed directly in the debug log: `self=` (our tracked position) was
frozen at the exact same value across dozens of consecutive follow ticks,
while `follow sending move:` showed we *were* computing and sending a
different, lower Y each time -- the server was simply overwriting it back
via `ClientboundPlayerPositionPacket` before our next tick ran.

Fixed in `MovementController._step_toward_target_height`
(`minebot/bot/movement.py`) by giving falling real physics instead of a
flat ramp:
- **Falling** (target below us): accumulate a real vertical velocity that
  accelerates by vanilla's actual gravity constant
  (`LivingEntity.DEFAULT_BASE_GRAVITY = 0.08` blocks per 20Hz game tick,
  confirmed in the decompiled source) every real game tick our
  slower follow-loop tick (`FOLLOW_STEP_INTERVAL_SECONDS = 0.15s`, i.e.
  ~3 real game ticks per follow tick) spans, then apply the resulting
  displacement. This produces a small, accelerating drop each report --
  exactly what a real client's own physics would produce -- rather than
  an arbitrary large jump. Lands exactly on the target Y (no overshoot)
  and resets velocity to 0 once reached.
- **Climbing** (target above us): vanilla doesn't need gravity/jump
  physics for a normal single-step rise -- the player's own step-up height
  (`LivingEntity.maxUpStep()` / `Attributes.STEP_HEIGHT`, `0.6` blocks) is
  handled as ordinary walking collision response. So climbing stays a flat
  per-tick cap (`FOLLOW_MAX_UPWARD_STEP = 0.6`), just renamed/re-scoped
  from the old single vertical-step constant to make clear it only applies
  to the upward case now.
- Switching direction (e.g. landing then needing to climb again) resets
  `_vertical_velocity` to 0 -- leftover fall speed must not carry into a
  climb.

**Cross-checked against mineflayer's own physics engine.** At the user's
suggestion, pulled mineflayer's actual physics dependency
(`prismarine-physics`, not something mindcraft wrote itself) to verify the
constants independently rather than relying solely on the decompiled
source. Its `index.js` confirms, exactly: `gravity: 0.08` (identical to
`LivingEntity.DEFAULT_BASE_GRAVITY`) and `stepHeight: 0.6` (identical to
what we used for `FOLLOW_MAX_UPWARD_STEP`) -- both values independently
corroborated by a mature, production-tested implementation of this exact
problem. It also revealed a gap in our first pass: prismarine-physics
applies `airdrag: 1 - 0.02` multiplicatively to vertical velocity every
tick, immediately after subtracting gravity (`vel.y -= gravity;
vel.y *= airdrag`) -- meaning falling approaches a **terminal velocity**
(`gravity / (1 - airdrag) = 0.08 / 0.02 = 4.0` blocks/tick at the limit),
rather than accelerating without bound. Our first implementation had no
drag term at all, which is harmless for short stair-height drops (the
difference is negligible over 1-2 blocks) but would make longer falls
report implausibly fast velocities. Added `_AIR_DRAG_PER_GAME_TICK = 0.98`,
applied in the same order (gravity subtraction, then drag) each simulated
game tick; a dedicated test drives the simulation for 2000 ticks and
confirms velocity converges to within `0.1` of the theoretical `-4.0`
blocks/tick terminal value rather than growing linearly.

This is still not full physics: no real jump impulse (can't gain upward
velocity the way pressing space does -- climbing is still just a flat
per-tick cap, not a jump arc), no collision/terrain awareness beyond the
target's own reported Y (we don't know if there's a wall or gap between us
and them), and the server may still reject movement in scenarios not yet
observed. It should, however, correctly handle ordinary descents (stairs,
ledges, drops) without getting stuck, which was the actual reported bug.

**Confirmed live**: the gravity fix works when we're already at (or very
near) the target's (x, z) column -- the debug log showed our reported Y
correctly descending in small accelerating steps (`102.00 -> 101.54 ->
100.87 -> ... -> 96.50`, matching the target exactly) and the server
accepting each step (no reset). But the user found the actual limiting
case immediately: **if the bot is still some blocks behind the target
horizontally when the target drops a level, the bot tries to fall to the
target's new (lower) Y while still positioned over ground/floor that's
still solid beneath the bot's own (older) (x, z)** -- i.e. we compute
"fall to Y=96.5" using only the *target's* Y, with zero awareness of
whether there's actually empty space to fall through at *our own* current
column. The server (correctly) rejects moving through a solid block, and
we get stuck oscillating between the fall attempt and the server's
correction back to solid ground.

This is exactly the boundary already predicted in "Why this needs real
pathfinding" above: matching a target's raw Y works fine as a *very* naive
substitute for real navigation as long as the space between wherever we
currently are and that Y is uniformly open air (or ground, for climbing) --
which stairs/ledges directly behind the target usually aren't. Fixing this
properly needs actual per-column, per-block awareness of what's below the
*bot itself* (not just the target), which is precisely what real chunk
block parsing (paletted containers, not just the heightmap summary we have
in `chunks.py`) plus `mineflayer-pathfinder`-equivalent A* pathfinding
would provide. No further band-aid was attempted here -- see "Why this
needs real pathfinding" above for the concrete phased plan (chunk block
parsing -> block-registry lookup -> movements/cost model -> A*), which
remains the right next step rather than another special-case fix layered
on top of raw-Y-following.

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
    movement.py                 # our own position sync (ClientboundPlayerPositionPacket +
                                 # ServerboundAcceptTeleportationPacket ack) and
                                 # ServerboundMovePlayerPacket.PosRot sends — done, tested
    entities.py                  # other-entity/player position tracking (AddEntity/RemoveEntities/
                                  # TeleportEntity/EntityPositionSync/MoveEntity + PlayerInfoUpdate's
                                  # name<->uuid mapping) — done, tested. See "Movement + follow-player"
                                  # section above for the two Instant/nullable-parsing bugs caught here.
    chunks.py                     # ClientboundLevelChunkWithLightPacket's heightmaps only (not the
                                   # raw per-section block buffer) — done, tested. ChunkHeightmapCache
                                   # answers ground_height_at(world_x, world_z); fed by the PLAY loop
                                   # as groundwork for future pathfinding, but not currently consulted
                                   # by MovementController -- see "Correction from live testing" in the
                                   # movement section above for why (heightmaps can't tell a floor
                                   # under a roof from the roof itself; the target's own tracked Y is
                                   # used instead).
    health.py                     # death/respawn — done, tested. Tracks our own entity id (from
                                   # CLIENTBOUND_LOGIN), requests respawn on our own death (ignores
                                   # other players' deaths), and tells MovementController to distrust
                                   # its tracked position until a fresh sync arrives. See "Death and
                                   # respawn" section above for the live-testing bug this fixes.
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
    movement.py               # MovementController — done, tested: forward/backward/left/right
                               # (yaw-relative walk, tracked position updated locally then sent via
                               # ServerboundMovePlayerPacket.PosRot), follow(name) (background
                               # asyncio.Task polling EntityTracker every 0.5s, walks toward the
                               # target and stops within ~2 blocks), stop() (cancels an active follow).
    play_loop.py                # PLAY-phase main loop — done: reads packets forever, auto-answers
                                 # keepalive + ping, syncs our position from ClientboundPlayerPosition
                                 # (acking with ServerboundAcceptTeleportation), feeds player/system
                                 # chat text through the command registry (dispatch(text, conn)), and
                                 # feeds entity/player-list packets into the EntityTracker. Proven live:
                                 # received and logged real chat messages from another player on the
                                 # target server. Chunk/inventory packets still ignored for now.
  commands/
    parser.py                 # !name(args) regex grammar, mirrors mindcraft — done, tested
    registry.py                # name -> async handler dispatch — done, tested
  main.py                      # wires config -> auth -> connection -> login_flow -> configuration
                                # -> play_loop, constructing one EntityTracker + MovementController per
                                # run. This is the full MVP path from prompt.txt (connect, listen to
                                # chat, parse commands, move) plus follow-player, proven end-to-end
                                # against the real online-mode target server (movement/follow verified
                                # via unit tests with a recording fake connection, not yet re-run live
                                # end-to-end after this pass -- worth doing before considering this
                                # fully proven in production).
  config.py                    # BotConfig.from_env(), loads .env via python-dotenv first —
                                # MINEBOT_HOST/PORT/USERNAME/ONLINE_MODE (see ".env" section above)
tools/
  extract_packet_ids.py      # regenerate packets_775.json from a sources jar — done
tests/                        # 68 tests, all passing (uv run pytest -q): varint/uuid/utf roundtrips,
                               # registry lookups + spot-checks against manually-read source, offline-UUID
                               # vs. known "Notch" reference value, command parser/registry, NBT reader
                               # (plain text + nested "extra" siblings), chat packet parsing (player chat
                               # with/without signature, system chat incl. translatable components),
                               # keepalive/ping echo (both states), server-hash vs. real java.math.BigInteger
                               # output, RSA/AES roundtrips, full LOGIN flow for both offline and online mode
                               # (the latter with a real generated RSA keypair and a full AES/CFB8 cipher
                               # switch mid-connection), CONFIGURATION phase incl. keepalive/ping, the PLAY
                               # loop, movement packet (de)serialization, entity/player-info tracking (incl.
                               # a regression test for correct positional field-skipping across multiple
                               # active PlayerInfoUpdate actions), and MovementController's yaw math +
                               # follow-loop behavior (via a RecordingConnection fake, no real socket needed)
                               # — all against fake in-process servers except where noted above
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

**Not started:** mining/placing/combat/inventory PLAY packets (chat,
keepalive/ping, and basic movement/follow are wired up now), and
Docker/compose packaging.

### Auth token caching (minebot/auth/token_cache.py)

The MSA refresh token is cached on disk at `~/.cache/minebot/msa_token.json`
(0600 permissions) so repeat runs skip the interactive device-code sign-in
-- matching how real launchers behave. `MicrosoftAuthenticator.get_profile()`
tries `msa.refresh_msa_tokens()` against the cached refresh token first;
if that fails (expired/revoked), it falls back to the normal device-code
flow and re-caches the new refresh token afterward. Only the MSA refresh
token is persisted, not the downstream Xbox/XSTS/Minecraft tokens -- those
are cheap to re-derive each run (a handful of HTTP calls) and caching them
too would mean tracking several more independent expiry clocks for little
benefit. `MicrosoftAuthenticator(cache_path=...)` accepts a custom path,
mainly so tests don't touch the real `~/.cache`.

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
