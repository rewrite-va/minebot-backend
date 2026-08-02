from minebot.protocol.nbt import read_network_tag


def _encode_named_compound_entry(name: str, tag_type: int, payload: bytes) -> bytes:
    encoded_name = name.encode("utf-8")
    return bytes([tag_type]) + len(encoded_name).to_bytes(2, "big") + encoded_name + payload


def _encode_string(value: str) -> bytes:
    encoded = value.encode("utf-8")
    return len(encoded).to_bytes(2, "big") + encoded


def test_read_end_tag_returns_none():
    value, pos = read_network_tag(bytes([0]))
    assert value is None
    assert pos == 1


def test_read_plain_string_component():
    # {"text": "hello"} as a compound: byte(10) + [byte(8) name="text" string]
    # then TAG_End
    TAG_STRING = 8
    TAG_COMPOUND = 10
    TAG_END = 0

    body = _encode_named_compound_entry("text", TAG_STRING, _encode_string("hello"))
    body += bytes([TAG_END])
    data = bytes([TAG_COMPOUND]) + body

    value, pos = read_network_tag(data)
    assert value == {"text": "hello"}
    assert pos == len(data)


def test_read_component_with_extra_siblings():
    TAG_STRING = 8
    TAG_COMPOUND = 10
    TAG_LIST = 9
    TAG_END = 0

    # inner sibling compound payload (no leading type byte -- list elements
    # don't repeat the type, it's already declared by the list header): {"text": "world"}
    sibling = _encode_named_compound_entry("text", TAG_STRING, _encode_string("world")) + bytes([TAG_END])

    # extra: TAG_List of TAG_Compound, length 1
    extra_payload = bytes([TAG_COMPOUND]) + (1).to_bytes(4, "big", signed=True) + sibling

    body = _encode_named_compound_entry("text", TAG_STRING, _encode_string("hello "))
    body += _encode_named_compound_entry("extra", TAG_LIST, extra_payload)
    body += bytes([TAG_END])
    data = bytes([TAG_COMPOUND]) + body

    value, pos = read_network_tag(data)
    assert value == {"text": "hello ", "extra": [{"text": "world"}]}
    assert pos == len(data)


def test_trailing_bytes_after_tag_are_not_consumed():
    data = bytes([0]) + b"\x01\x02\x03"
    value, pos = read_network_tag(data)
    assert value is None
    assert pos == 1
