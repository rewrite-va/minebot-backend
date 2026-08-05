import subprocess

from minebot.mod_version import _format_built_at, check_hello, expected_commit


def _init_repo(path) -> str:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=path, check=True)
    (path / "file.txt").write_text("hello")
    subprocess.run(["git", "add", "file.txt"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True)
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=path, capture_output=True, text=True, check=True).stdout.strip()


def test_expected_commit_reads_head_of_a_real_repo(tmp_path):
    commit = _init_repo(tmp_path)
    assert expected_commit(str(tmp_path)) == commit


def test_expected_commit_returns_none_when_path_is_none():
    assert expected_commit(None) is None


def test_expected_commit_returns_none_for_a_non_git_directory(tmp_path):
    assert expected_commit(str(tmp_path)) is None


def test_expected_commit_returns_none_for_a_nonexistent_path(tmp_path):
    assert expected_commit(str(tmp_path / "does-not-exist")) is None


def test_check_hello_does_not_raise_on_match(tmp_path):
    commit = _init_repo(tmp_path)
    check_hello(commit, "2026-01-01T00:00:00Z", str(tmp_path))  # should not raise


def test_check_hello_does_not_raise_on_mismatch(tmp_path):
    _init_repo(tmp_path)
    check_hello("0000000000000000000000000000000000000000", "2026-01-01T00:00:00Z", str(tmp_path))  # should not raise


def test_check_hello_strips_dirty_suffix_before_comparing(tmp_path, caplog):
    commit = _init_repo(tmp_path)

    with caplog.at_level("WARNING"):
        check_hello(f"{commit}-dirty", "2026-01-01T00:00:00Z", str(tmp_path))

    assert not any("stale" in record.message for record in caplog.records)


def test_check_hello_warns_on_real_mismatch(tmp_path, caplog):
    _init_repo(tmp_path)

    with caplog.at_level("WARNING"):
        check_hello("0000000000000000000000000000000000000000", "2026-01-01T00:00:00Z", str(tmp_path))

    assert any("stale" in record.message for record in caplog.records)


def test_check_hello_does_not_raise_when_no_repo_path_configured():
    check_hello("abc123", "2026-01-01T00:00:00Z", None)  # should not raise


def test_format_built_at_renders_a_readable_version_string():
    assert _format_built_at("2026-08-04T22:35:51.123Z") == "v20260804 22.35.51"


def test_format_built_at_falls_back_to_the_raw_string_on_bad_input():
    assert _format_built_at("not a real timestamp") == "not a real timestamp"


def test_check_hello_logs_a_readable_connected_line(caplog):
    with caplog.at_level("INFO"):
        check_hello("abc123", "2026-08-04T22:35:51.123Z", None)

    assert any("backend: connected (v20260804 22.35.51" in record.message for record in caplog.records)
