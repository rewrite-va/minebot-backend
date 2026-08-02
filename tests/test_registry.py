from minebot.protocol.registry import REGISTRY


def test_protocol_version():
    assert REGISTRY.protocol_version == 775


def test_handshake_client_intention_is_zero():
    assert REGISTRY.id_for("handshake", "serverbound", "CLIENT_INTENTION") == 0


def test_login_packet_ids_match_declaration_order():
    assert REGISTRY.id_for("login", "serverbound", "SERVERBOUND_HELLO") == 0
    assert REGISTRY.id_for("login", "serverbound", "SERVERBOUND_KEY") == 1
    assert REGISTRY.id_for("login", "clientbound", "CLIENTBOUND_LOGIN_FINISHED") == 2

    assert REGISTRY.name_for("login", "serverbound", 0) == "SERVERBOUND_HELLO"


def test_play_state_has_expected_packet_counts():
    assert len(REGISTRY.packet_names("play", "serverbound")) == 69
    assert len(REGISTRY.packet_names("play", "clientbound")) == 141


def test_play_spot_checks():
    assert REGISTRY.id_for("play", "serverbound", "SERVERBOUND_CHAT") == 9
    assert REGISTRY.id_for("play", "clientbound", "CLIENTBOUND_KEEP_ALIVE") == 44
    assert REGISTRY.id_for("play", "clientbound", "CLIENTBOUND_SYSTEM_CHAT") == 121


def test_unknown_packet_raises_keyerror():
    import pytest

    with pytest.raises(KeyError):
        REGISTRY.id_for("play", "serverbound", "NOT_A_REAL_PACKET")
