import stat

from minebot.auth import token_cache


def test_load_refresh_token_returns_none_when_missing(tmp_path):
    cache_path = tmp_path / "does_not_exist.json"
    assert token_cache.load_refresh_token(cache_path) is None


def test_save_and_load_roundtrip(tmp_path):
    cache_path = tmp_path / "nested" / "msa_token.json"
    token_cache.save_refresh_token("some-refresh-token", cache_path)

    assert token_cache.load_refresh_token(cache_path) == "some-refresh-token"


def test_save_creates_owner_only_permissions(tmp_path):
    cache_path = tmp_path / "msa_token.json"
    token_cache.save_refresh_token("secret", cache_path)

    mode = cache_path.stat().st_mode
    assert stat.S_IMODE(mode) == (stat.S_IRUSR | stat.S_IWUSR)


def test_load_refresh_token_returns_none_for_corrupt_json(tmp_path):
    cache_path = tmp_path / "msa_token.json"
    cache_path.write_text("not valid json {{{")
    assert token_cache.load_refresh_token(cache_path) is None


def test_clear_removes_the_file(tmp_path):
    cache_path = tmp_path / "msa_token.json"
    token_cache.save_refresh_token("secret", cache_path)
    assert cache_path.exists()

    token_cache.clear(cache_path)
    assert not cache_path.exists()


def test_clear_is_a_noop_when_file_does_not_exist(tmp_path):
    cache_path = tmp_path / "does_not_exist.json"
    token_cache.clear(cache_path)  # should not raise
