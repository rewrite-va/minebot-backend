import uuid

from minebot.net.types import ByteWriter
from minebot.protocol.chat import (
    PlayerChatMessage,
    SystemChatMessage,
    parse_play_chat,
    parse_player_chat,
    parse_system_chat,
)
from minebot.protocol.registry import REGISTRY

STATE = "play"


def _encode_named_compound_entry(name: str, tag_type: int, payload: bytes) -> bytes:
    encoded_name = name.encode("utf-8")
    return bytes([tag_type]) + len(encoded_name).to_bytes(2, "big") + encoded_name + payload


def _encode_string(value: str) -> bytes:
    encoded = value.encode("utf-8")
    return len(encoded).to_bytes(2, "big") + encoded


def _encode_plain_text_component_tag(text: str) -> bytes:
    TAG_STRING = 8
    TAG_COMPOUND = 10
    TAG_END = 0
    body = _encode_named_compound_entry("text", TAG_STRING, _encode_string(text))
    body += bytes([TAG_END])
    return bytes([TAG_COMPOUND]) + body


def _encode_string_list(values: list[str]) -> bytes:
    TAG_STRING = 8
    payload = bytes([TAG_STRING]) + len(values).to_bytes(4, "big", signed=True)
    for v in values:
        payload += _encode_string(v)
    return payload


def _encode_translatable_component_tag(translate: str, fallback: str | None, with_args: list[str]) -> bytes:
    TAG_STRING = 8
    TAG_LIST = 9
    TAG_COMPOUND = 10
    TAG_END = 0

    body = _encode_named_compound_entry("translate", TAG_STRING, _encode_string(translate))
    if fallback is not None:
        body += _encode_named_compound_entry("fallback", TAG_STRING, _encode_string(fallback))
    if with_args:
        body += _encode_named_compound_entry("with", TAG_LIST, _encode_string_list(with_args))
    body += bytes([TAG_END])
    return bytes([TAG_COMPOUND]) + body


def test_parse_player_chat_without_signature():
    sender = uuid.uuid4()
    writer = ByteWriter()
    writer.write_varint(0)  # globalIndex
    writer.write_uuid(sender)
    writer.write_varint(0)  # index
    writer.write_bool(False)  # no signature
    writer.write_utf("!forward(2)")  # SignedMessageBody.Packed.content

    result = parse_player_chat(writer.getvalue())
    assert result == PlayerChatMessage(sender=sender, content="!forward(2)")


def test_parse_player_chat_with_signature_skips_it_correctly():
    sender = uuid.uuid4()
    writer = ByteWriter()
    writer.write_varint(0)
    writer.write_uuid(sender)
    writer.write_varint(0)
    writer.write_bool(True)
    writer.write(bytes(256))  # fixed-size signature blob
    writer.write_utf("hello")

    result = parse_player_chat(writer.getvalue())
    assert result.content == "hello"


def test_parse_system_chat():
    writer = ByteWriter()
    writer.write(_encode_plain_text_component_tag("server message"))
    writer.write_bool(True)

    result = parse_system_chat(writer.getvalue())
    assert result == SystemChatMessage(content="server message", overlay=True)


def test_parse_play_chat_dispatches_by_packet_id():
    packet_id = REGISTRY.id_for(STATE, "clientbound", "CLIENTBOUND_SYSTEM_CHAT")
    writer = ByteWriter()
    writer.write(_encode_plain_text_component_tag("hi"))
    writer.write_bool(False)

    result = parse_play_chat(packet_id, writer.getvalue())
    assert isinstance(result, SystemChatMessage)
    assert result.content == "hi"


def test_parse_play_chat_returns_none_for_unhandled_packet():
    packet_id = REGISTRY.id_for(STATE, "clientbound", "CLIENTBOUND_KEEP_ALIVE")
    assert parse_play_chat(packet_id, b"\x00" * 8) is None


def test_parse_system_chat_translatable_with_fallback_and_args():
    # Regression test: our first real online-mode run showed empty
    # "[system] " log lines for join/leave-style messages, which use
    # translate+fallback+with rather than plain text -- previously
    # silently dropped to "".
    writer = ByteWriter()
    writer.write(
        _encode_translatable_component_tag(
            "multiplayer.player.joined", "%s joined the game", ["Steve"]
        )
    )
    writer.write_bool(False)

    result = parse_system_chat(writer.getvalue())
    assert result.content == "Steve joined the game"


def test_parse_system_chat_translatable_without_fallback_uses_key():
    writer = ByteWriter()
    writer.write(_encode_translatable_component_tag("some.untranslated.key", None, []))
    writer.write_bool(False)

    result = parse_system_chat(writer.getvalue())
    assert result.content == "some.untranslated.key"


def test_parse_system_chat_translatable_multiple_args():
    writer = ByteWriter()
    writer.write(
        _encode_translatable_component_tag(
            "chat.type.text", "<%s> %s", ["Alex", "hello world"]
        )
    )
    writer.write_bool(False)

    result = parse_system_chat(writer.getvalue())
    assert result.content == "<Alex> hello world"
