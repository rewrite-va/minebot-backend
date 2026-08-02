"""PLAY-phase chat packets: receiving player chat / system chat, and
sending outbound chat as a /say command (see FINDINGS.md and the
conversation record for why: real ServerboundChatPacket needs the
1.19.1+ secure chat signing + LastSeenMessages acknowledgement machinery,
which is out of scope for the MVP; /say via ServerboundChatCommandPacket
sidesteps that entirely).

ClientboundPlayerChatPacket field layout (from the decompiled source):
    globalIndex: varint
    sender: uuid
    index: varint
    signature: nullable MessageSignature (256 raw bytes when present)
    body: SignedMessageBody.Packed = { content: string, timeStamp: instant,
          salt: i64, lastSeen: LastSeenMessages.Packed }
    unsignedContent: nullable Component (NBT) -- present when the message
          was alterred by chat filtering/formatting; not needed by us
    filterMask: FilterMask
    chatType: ChatType.Bound (registry holder + Components)

We only need `sender` and `body.content` (the actual typed text), both of
which occur before the fields we don't understand/need, so parsing simply
stops once they're extracted.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from minebot.net.connection import Connection, STATE_PLAY
from minebot.net.types import ByteReader, ByteWriter
from minebot.protocol.nbt import read_network_tag
from minebot.protocol.registry import REGISTRY

STATE = STATE_PLAY


@dataclass
class PlayerChatMessage:
    sender: uuid.UUID
    content: str


@dataclass
class SystemChatMessage:
    content: str
    overlay: bool


def _extract_component_text(value) -> str:
    """Walks a decoded NBT chat-component tree (dict/list/str, from
    read_network_tag) and concatenates plain text. Handles:

    - a bare string
    - a list of sibling components
    - literal text: `{"text": ..., "extra": [...]}`
    - translatable text: `{"translate": ..., "fallback": ..., "with": [...]}`
      -- e.g. "multiplayer.player.joined" style system messages, which is
      what most join/leave/server announcements actually use instead of
      plain "text". We don't do real locale-file translation (that needs
      en_us.json and %n$s-style format-string parsing matching Java's
      String.format quirks); instead:
        * if `fallback` is present (servers commonly include it precisely
          so non-full clients can show *something* useful), do simple
          positional %s substitution with the (stringified) `with` args
        * otherwise fall back to just the raw translation key, which is
          still far more useful for a chat-command bot than silence

    Does not resolve ScoreContents/SelectorContents/KeybindContents/
    NbtContents/ObjectContents, which are rare in ordinary chat/system
    messages and can be added if a real server turns out to need them.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(_extract_component_text(item) for item in value)
    if isinstance(value, dict):
        if "translate" in value:
            return _extract_translatable_text(value)
        text = value.get("text", "")
        extra = value.get("extra", [])
        return text + _extract_component_text(extra)
    return ""


def _extract_translatable_text(value: dict) -> str:
    import re

    key = value["translate"]
    fallback = value.get("fallback")
    if fallback is None:
        return key

    args = [_extract_component_text(arg) for arg in value.get("with", [])]
    arg_iter = iter(args)

    def substitute(match: "re.Match") -> str:
        try:
            return next(arg_iter)
        except StopIteration:
            return match.group(0)

    # Good-enough %s substitution: Java's actual format spec (%1$s,
    # width/precision modifiers, %%) isn't replicated -- vanilla fallback
    # strings for chat/system messages are consistently plain sequential
    # %s placeholders in practice.
    return re.sub(r"%s", substitute, fallback)


def _read_message_signature_or_none(reader: ByteReader) -> None:
    present = reader.read_bool()
    if present:
        reader.read(256)  # MessageSignature is a fixed 256-byte blob


def parse_player_chat(data: bytes) -> PlayerChatMessage:
    reader = ByteReader(data)
    reader.read_varint()  # globalIndex, unused
    sender = reader.read_uuid()
    reader.read_varint()  # index, unused
    _read_message_signature_or_none(reader)
    content = reader.read_utf()  # SignedMessageBody.Packed.content
    return PlayerChatMessage(sender=sender, content=content)


def parse_system_chat(data: bytes) -> SystemChatMessage:
    reader = ByteReader(data)
    component, new_pos = read_network_tag(data, reader.pos)
    reader.pos = new_pos
    overlay = reader.read_bool()
    return SystemChatMessage(content=_extract_component_text(component), overlay=overlay)


def parse_play_chat(packet_id: int, data: bytes):
    name = REGISTRY.name_for(STATE, "clientbound", packet_id)
    if name == "CLIENTBOUND_PLAYER_CHAT":
        return parse_player_chat(data)
    if name == "CLIENTBOUND_SYSTEM_CHAT":
        return parse_system_chat(data)
    return None


async def send_say(conn: Connection, message: str) -> None:
    """Sends `message` to the server as `/say <message>` via
    ServerboundChatCommandPacket, avoiding the secure chat signing path
    (see module docstring).
    """
    writer = ByteWriter()
    writer.write_utf(f"say {message}")
    packet_id = REGISTRY.id_for(STATE, "serverbound", "SERVERBOUND_CHAT_COMMAND")
    await conn.send_packet(packet_id, writer.getvalue())
