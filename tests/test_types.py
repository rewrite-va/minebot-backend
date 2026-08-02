import uuid

from minebot.net.types import ByteReader, ByteWriter, decode_varint, encode_varint


def test_varint_roundtrip_small_values():
    for value in [0, 1, 127, 128, 255, 300, 2097151, 2147483647, -1, -2147483648]:
        encoded = encode_varint(value)
        decoded, consumed = decode_varint(encoded)
        assert decoded == value
        assert consumed == len(encoded)


def test_varint_known_encodings():
    # From wiki.vg-style reference examples, unchanged wire format.
    assert encode_varint(0) == b"\x00"
    assert encode_varint(1) == b"\x01"
    assert encode_varint(127) == b"\x7f"
    assert encode_varint(128) == b"\x80\x01"
    assert encode_varint(255) == b"\xff\x01"
    assert encode_varint(2097151) == b"\xff\xff\x7f"
    assert encode_varint(-1) == b"\xff\xff\xff\xff\x0f"


def test_utf_roundtrip():
    writer = ByteWriter()
    writer.write_utf("hello world")
    reader = ByteReader(writer.getvalue())
    assert reader.read_utf() == "hello world"


def test_uuid_roundtrip():
    value = uuid.uuid4()
    writer = ByteWriter()
    writer.write_uuid(value)
    reader = ByteReader(writer.getvalue())
    assert reader.read_uuid() == value


def test_byte_array_roundtrip():
    writer = ByteWriter()
    writer.write_byte_array(b"\x01\x02\x03")
    reader = ByteReader(writer.getvalue())
    assert reader.read_byte_array() == b"\x01\x02\x03"
