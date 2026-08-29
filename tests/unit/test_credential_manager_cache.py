import time
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

import pytest

from utils import cookies_login
from utils.cookies_login import CredentialManager


def test_1061_reads_and_status_checks_decrypt_once(tmp_path):
    manager = CredentialManager()
    cookie_file = tmp_path / "bilibili.json"
    cookie_file.write_text("encrypted", encoding="utf-8")

    with (
        patch.dict(
            "utils.cookies_login.COOKIE_FILES",
            {"bilibili": cookie_file},
        ),
        patch("utils.cookies_login.get_legacy_cookie_files", return_value=[]),
        patch(
            "utils.cookies_login._load_cookies_from_file_uncached",
            return_value={"SESSDATA": "secret"},
        ) as decrypt,
    ):
        for _ in range(1061):
            assert manager.load("bilibili") == {"SESSDATA": "secret"}
            assert manager.status("bilibili")["credential_state"] == manager.READY

    decrypt.assert_called_once_with("bilibili")


def test_concurrent_first_load_happens_once_and_returns_defensive_copies():
    manager = CredentialManager()

    def load_once(_platform):
        time.sleep(0.02)
        return {"SESSDATA": "secret"}, Path("bilibili.json"), True

    with patch(
        "utils.cookies_login._load_credential_sources_uncached",
        side_effect=load_once,
    ) as loader:
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(manager.load, ["bilibili"] * 16))

        results[0]["SESSDATA"] = "mutated"
        fresh = manager.load("bilibili")

    loader.assert_called_once_with("bilibili")
    assert fresh == {"SESSDATA": "secret"}


def test_different_platforms_do_not_share_a_load_lock():
    manager = CredentialManager()
    barrier = Barrier(2)

    def load_platform(platform):
        barrier.wait(timeout=1)
        return {"token": platform}, Path(f"{platform}.json"), True

    with patch(
        "utils.cookies_login._load_credential_sources_uncached",
        side_effect=load_platform,
    ):
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(manager.load, "first")
            second = executor.submit(manager.load, "second")
            assert first.result() == {"token": "first"}
            assert second.result() == {"token": "second"}


def test_missing_and_invalid_results_are_negative_cached(tmp_path):
    manager = CredentialManager()
    missing_file = tmp_path / "missing.json"
    invalid_file = tmp_path / "invalid.json"
    invalid_file.write_text("broken", encoding="utf-8")

    with (
        patch.dict(
            "utils.cookies_login.COOKIE_FILES",
            {"missing": missing_file, "invalid": invalid_file},
        ),
        patch("utils.cookies_login.get_legacy_cookie_files", return_value=[]),
        patch(
            "utils.cookies_login._load_cookies_from_file_uncached",
            return_value={},
        ) as loader,
    ):
        assert manager.load("missing") == manager.load("missing") == {}
        assert manager.load("invalid") == manager.load("invalid") == {}
        assert manager.snapshot("missing").state == manager.MISSING
        assert manager.snapshot("invalid").state == manager.INVALID

    loader.assert_called_once_with("invalid")


def test_manual_file_changes_require_a_new_manager(tmp_path, monkeypatch):
    cookie_file = tmp_path / "config" / "weibo_cookies.json"
    cookie_file.parent.mkdir()
    cookie_file.write_text('{"SUB":"first"}', encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    with _patched_weibo_paths(tmp_path, cookie_file):
        manager = CredentialManager()
        assert manager.load("weibo") == {"SUB": "first"}
        cookie_file.write_text('{"SUB":"second"}', encoding="utf-8")
        assert manager.load("weibo") == {"SUB": "first"}
        assert CredentialManager().load("weibo") == {"SUB": "second"}


def test_configured_source_precedes_and_invalid_source_falls_back_to_legacy(
    tmp_path,
    monkeypatch,
):
    configured = tmp_path / "config" / "weibo_cookies.json"
    legacy = tmp_path / "home" / "weibo_cookies.json"
    configured.parent.mkdir()
    legacy.parent.mkdir()
    configured.write_text('{"SUB":"configured"}', encoding="utf-8")
    legacy.write_text('{"SUB":"legacy"}', encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    with _patched_weibo_paths(tmp_path, configured):
        assert CredentialManager().load("weibo") == {"SUB": "configured"}
        configured.write_text("broken", encoding="utf-8")
        manager = CredentialManager()
        assert manager.load("weibo") == {"SUB": "legacy"}
        legacy.write_text('{"SUB":"changed"}', encoding="utf-8")
        assert manager.load("weibo") == {"SUB": "legacy"}


def test_save_updates_cache_without_reading_file(tmp_path):
    manager = CredentialManager()
    cookie_file = tmp_path / "weibo.json"

    with (
        patch.dict("utils.cookies_login.COOKIE_FILES", {"weibo": cookie_file}),
        patch(
            "utils.cookies_login._save_cookies_to_file_uncached",
            return_value=True,
        ),
        patch("utils.cookies_login._load_credential_sources_uncached") as loader,
    ):
        assert manager.save("weibo", {"SUB": "saved"}) is True
        assert manager.save(
            "weibo",
            {"SUB": "refreshed"},
            expected_credentials={"SUB": "saved"},
        ) is True
        assert manager.load("weibo") == {"SUB": "refreshed"}

    loader.assert_not_called()


def test_failed_save_preserves_previous_cached_credentials(tmp_path):
    manager = CredentialManager()
    cookie_file = tmp_path / "weibo.json"

    with (
        patch.dict("utils.cookies_login.COOKIE_FILES", {"weibo": cookie_file}),
        patch(
            "utils.cookies_login._save_cookies_to_file_uncached",
            side_effect=[True, False],
        ),
    ):
        assert manager.save("weibo", {"SUB": "existing"}) is True
        assert manager.save("weibo", {"SUB": "replacement"}) is False
        assert manager.load("weibo") == {"SUB": "existing"}


def test_stale_auth_rejection_does_not_override_new_credentials(tmp_path):
    manager = CredentialManager()
    cookie_file = tmp_path / "weibo.json"

    with (
        patch.dict("utils.cookies_login.COOKIE_FILES", {"weibo": cookie_file}),
        patch(
            "utils.cookies_login._save_cookies_to_file_uncached",
            return_value=True,
        ),
    ):
        assert manager.save("weibo", {"SUB": "old"}) is True
        assert manager.save("weibo", {"SUB": "new"}) is True
        assert manager.save(
            "weibo",
            {"SUB": "refreshed-old"},
            expected_credentials={"SUB": "old"},
        ) is False
        assert manager.load("weibo") == {"SUB": "new"}
        assert manager.mark_auth_rejected("weibo", {"SUB": "old"}) is False
        assert manager.mark_auth_rejected("weibo", {"SUB": "new"}) is True
        assert manager.load("weibo") == {}
        assert manager.status("weibo")["credential_state"] == manager.AUTH_REJECTED


def test_delete_retains_and_reuses_stable_key(tmp_path, monkeypatch):
    cookie_file = tmp_path / "config" / "weibo_cookies.json"
    monkeypatch.chdir(tmp_path)

    with _patched_weibo_paths(tmp_path, cookie_file):
        manager = CredentialManager()
        assert manager.save("weibo", {"SUB": "first"}) is True
        key_file = cookie_file.parent / "weibo_key.key"
        original_key = key_file.read_bytes()

        assert manager.delete("weibo") is True
        assert not cookie_file.exists()
        assert key_file.read_bytes() == original_key
        assert manager.snapshot("weibo").state == manager.MISSING

        assert manager.save("weibo", {"SUB": "second"}) is True
        assert key_file.read_bytes() == original_key
        assert CredentialManager().load("weibo") == {"SUB": "second"}


def test_save_rotates_only_a_malformed_retained_key(tmp_path, monkeypatch):
    cookie_file = tmp_path / "config" / "weibo_cookies.json"
    cookie_file.parent.mkdir()
    cookie_file.write_text("broken", encoding="utf-8")
    key_file = cookie_file.parent / "weibo_key.key"
    key_file.write_bytes(b"broken")
    monkeypatch.chdir(tmp_path)

    with _patched_weibo_paths(tmp_path, cookie_file):
        manager = CredentialManager()
        assert manager.snapshot("weibo").state == manager.INVALID
        assert manager.delete("weibo") is True
        assert manager.save("weibo", {"SUB": "recovered"}) is True
        assert key_file.read_bytes() != b"broken"
        assert CredentialManager().load("weibo") == {"SUB": "recovered"}


def test_delete_removes_legacy_payloads(tmp_path, monkeypatch):
    configured = tmp_path / "config" / "weibo_cookies.json"
    legacy = tmp_path / "home" / "weibo_cookies.json"
    unrelated = tmp_path / "weibo_cookies.json"
    configured.parent.mkdir()
    legacy.parent.mkdir()
    configured.write_text('{"SUB":"configured"}', encoding="utf-8")
    legacy.write_text('{"SUB":"legacy"}', encoding="utf-8")
    unrelated.write_text('{"notes":"keep"}', encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    with _patched_weibo_paths(tmp_path, configured):
        manager = CredentialManager()
        assert manager.delete("weibo") is True
        assert manager.delete("weibo") is False

    assert not configured.exists()
    assert not legacy.exists()
    assert unrelated.read_text(encoding="utf-8") == '{"notes":"keep"}'


def test_delete_unlinks_symlink_without_deleting_target(tmp_path, monkeypatch):
    configured = tmp_path / "config" / "weibo_cookies.json"
    target = tmp_path / "outside.json"
    configured.parent.mkdir()
    target.write_text('{"SUB":"outside"}', encoding="utf-8")
    try:
        configured.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    monkeypatch.chdir(tmp_path)

    with _patched_weibo_paths(tmp_path, configured):
        manager = CredentialManager()
        assert manager.snapshot("weibo").state == manager.INVALID
        assert manager.delete("weibo") is True

    assert not configured.exists()
    assert target.exists()


def test_delete_failure_drops_cached_entry(tmp_path):
    manager = CredentialManager()
    cookie_file = tmp_path / "weibo.json"
    cookie_file.write_text('{"SUB":"saved"}', encoding="utf-8")

    with (
        patch.dict("utils.cookies_login.COOKIE_FILES", {"weibo": cookie_file}),
        patch("utils.cookies_login.get_legacy_cookie_files", return_value=[]),
    ):
        assert manager.load("weibo") == {"SUB": "saved"}
        with patch.object(Path, "unlink", side_effect=PermissionError("busy")):
            with pytest.raises(PermissionError):
                manager.delete("weibo")
        cookie_file.write_text('{"SUB":"replacement"}', encoding="utf-8")
        assert manager.load("weibo") == {"SUB": "replacement"}


@contextmanager
def _patched_weibo_paths(tmp_path: Path, cookie_file: Path):
    with (
        patch.dict("utils.cookies_login.COOKIE_FILES", {"weibo": cookie_file}),
        patch.object(cookies_login, "CONFIG_DIR", cookie_file.parent),
        patch.object(
            cookies_login.os.path,
            "expanduser",
            return_value=str(tmp_path / "home"),
        ),
    ):
        yield
