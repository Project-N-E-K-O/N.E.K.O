import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest.mock import patch

import pytest

from utils import cookies_login
from utils.cookies_login import CredentialManager
from utils.web_scraper import platform_helpers
from utils.web_scraper.personal_dynamics import _is_weibo_auth_failure


def test_concurrent_load_decrypts_once_and_returns_defensive_copies():
    manager = CredentialManager()

    def load_once(_platform):
        time.sleep(0.02)
        return {"SESSDATA": "secret"}

    with patch(
        "utils.cookies_login._load_cookies_from_file_uncached",
        side_effect=load_once,
    ) as loader:
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(manager.load, ["bilibili"] * 16))

        results[0]["SESSDATA"] = "mutated"
        fresh = manager.load("bilibili")

    assert loader.call_count == 1
    assert all(result for result in results)
    assert fresh == {"SESSDATA": "secret"}


def test_missing_and_invalid_credentials_are_negative_cached(tmp_path):
    manager = CredentialManager()
    missing_file = tmp_path / "missing.json"
    invalid_file = tmp_path / "invalid.json"
    invalid_file.write_text("broken", encoding="utf-8")

    with (
        patch.dict(
            "utils.cookies_login.COOKIE_FILES",
            {"missing": missing_file, "invalid": invalid_file},
        ),
        patch(
            "utils.cookies_login._load_cookies_from_file_uncached",
            return_value={},
        ) as loader,
    ):
        assert manager.load("missing") == {}
        assert manager.load("missing") == {}
        assert manager.load("invalid") == {}
        assert manager.load("invalid") == {}

        missing_status = manager.status("missing")
        invalid_status = manager.status("invalid")

    assert loader.call_count == 2
    assert missing_status["credential_state"] == CredentialManager.MISSING
    assert invalid_status["credential_state"] == CredentialManager.INVALID


def test_save_delete_and_auth_rejection_update_cache_without_reload():
    manager = CredentialManager()

    with patch(
        "utils.cookies_login._save_cookies_to_file_uncached",
        return_value=True,
    ) as saver:
        assert manager.save("weibo", {"SUB": "first"}) is True
        assert manager.save("weibo", {"SUB": "second"}) is True

    with patch("utils.cookies_login._load_cookies_from_file_uncached") as loader:
        assert manager.load("weibo") == {"SUB": "second"}
        assert manager.mark_auth_rejected("weibo", {"SUB": "second"}) is True
        assert manager.load("weibo") == {}
        rejected_status = manager.status("weibo")
        assert rejected_status["credential_state"] == CredentialManager.AUTH_REJECTED
        assert rejected_status["has_stored_credentials"] is True
        assert manager.delete_stored_credentials("weibo") == (False, True)
        assert manager.load("weibo") == {}
        assert manager.status("weibo")["credential_state"] == CredentialManager.MISSING

    assert saver.call_count == 2
    loader.assert_not_called()


def test_failed_save_preserves_previous_cached_credentials():
    manager = CredentialManager()

    with patch(
        "utils.cookies_login._save_cookies_to_file_uncached",
        side_effect=[True, False],
    ):
        assert manager.save("weibo", {"SUB": "existing"}) is True
        assert manager.save("weibo", {"SUB": "replacement"}) is False

    assert manager.load("weibo") == {"SUB": "existing"}


def test_changing_credential_path_invalidates_cached_result(tmp_path):
    manager = CredentialManager()
    first_file = tmp_path / "first.json"
    second_file = tmp_path / "second.json"

    with (
        patch.dict("utils.cookies_login.COOKIE_FILES", {"weibo": first_file}),
        patch(
            "utils.cookies_login._load_cookies_from_file_uncached",
            side_effect=[{"SUB": "first"}, {"SUB": "second"}],
        ) as loader,
    ):
        assert manager.load("weibo") == {"SUB": "first"}
        assert manager.load("weibo") == {"SUB": "first"}

        with patch.dict("utils.cookies_login.COOKIE_FILES", {"weibo": second_file}):
            assert manager.load("weibo") == {"SUB": "second"}
            assert manager.load("weibo") == {"SUB": "second"}

    assert loader.call_count == 2


def test_stale_auth_rejection_does_not_override_new_credentials():
    manager = CredentialManager()

    with patch("utils.cookies_login._save_cookies_to_file_uncached", return_value=True):
        assert manager.save("weibo", {"SUB": "old"}) is True
        old_credentials = manager.load("weibo")
        assert manager.save("weibo", {"SUB": "new"}) is True

    assert manager.mark_auth_rejected("weibo", old_credentials) is False
    assert manager.load("weibo") == {"SUB": "new"}
    assert manager.status("weibo")["credential_state"] == CredentialManager.READY


def test_partial_auth_match_does_not_reject_replacement_credentials():
    manager = CredentialManager()

    with patch("utils.cookies_login._save_cookies_to_file_uncached", return_value=True):
        assert manager.save("weibo", {"SUB": "shared", "token": "old"}) is True
        old_credentials = manager.load("weibo")
        assert manager.save("weibo", {"SUB": "shared", "token": "new"}) is True

    assert manager.mark_auth_rejected("weibo", {"SUB": "shared"}) is False
    assert manager.mark_auth_rejected("weibo", old_credentials) is False
    assert manager.load("weibo") == {"SUB": "shared", "token": "new"}


def test_external_file_changes_invalidate_positive_and_negative_cache(tmp_path):
    manager = CredentialManager()
    cookie_file = tmp_path / "weibo.json"

    with (
        patch.dict("utils.cookies_login.COOKIE_FILES", {"weibo": cookie_file}),
        patch.object(cookies_login, "CONFIG_DIR", tmp_path),
        patch(
            "utils.cookies_login._load_cookies_from_file_uncached",
            wraps=cookies_login._load_cookies_from_file_uncached,
        ) as loader,
    ):
        cookie_file.write_text('{"SUB":"first"}', encoding="utf-8")
        assert manager.load("weibo") == {"SUB": "first"}
        assert manager.load("weibo") == {"SUB": "first"}

        cookie_file.write_text('{"SUB":"second-and-longer"}', encoding="utf-8")
        assert manager.load("weibo") == {"SUB": "second-and-longer"}

        cookie_file.unlink()
        assert manager.load("weibo") == {}
        assert manager.load("weibo") == {}

        cookie_file.write_text('{"SUB":"restored"}', encoding="utf-8")
        assert manager.load("weibo") == {"SUB": "restored"}

    assert loader.call_count == 4


def test_file_change_during_load_retries_before_caching(tmp_path):
    manager = CredentialManager()
    cookie_file = tmp_path / "weibo.json"
    cookie_file.write_text('{"SUB":"first"}', encoding="utf-8")

    def load_and_replace(_platform):
        credentials = json.loads(cookie_file.read_text(encoding="utf-8"))
        if credentials["SUB"] == "first":
            cookie_file.write_text(
                '{"SUB":"second-and-longer"}',
                encoding="utf-8",
            )
        return credentials

    with (
        patch.dict("utils.cookies_login.COOKIE_FILES", {"weibo": cookie_file}),
        patch.object(cookies_login, "CONFIG_DIR", tmp_path),
        patch(
            "utils.cookies_login._load_cookies_from_file_uncached",
            side_effect=load_and_replace,
        ) as loader,
    ):
        assert manager.load("weibo") == {"SUB": "second-and-longer"}
        assert manager.load("weibo") == {"SUB": "second-and-longer"}

    assert loader.call_count == 2


def test_continuous_file_changes_have_bounded_uncached_retries(tmp_path):
    manager = CredentialManager()
    cookie_file = tmp_path / "weibo.json"
    cookie_file.write_text('{"SUB":"initial"}', encoding="utf-8")
    revisions = 0

    def load_and_replace(_platform):
        nonlocal revisions
        credentials = json.loads(cookie_file.read_text(encoding="utf-8"))
        revisions += 1
        cookie_file.write_text(
            json.dumps({"SUB": "x" * (20 + revisions)}),
            encoding="utf-8",
        )
        return credentials

    with (
        patch.dict("utils.cookies_login.COOKIE_FILES", {"weibo": cookie_file}),
        patch.object(cookies_login, "CONFIG_DIR", tmp_path),
        patch(
            "utils.cookies_login._load_cookies_from_file_uncached",
            side_effect=load_and_replace,
        ) as loader,
    ):
        assert manager.load("weibo") == {}
        assert loader.call_count == cookies_login._SOURCE_READ_ATTEMPTS
        assert manager.load("weibo") == {}
        assert loader.call_count == cookies_login._SOURCE_READ_ATTEMPTS * 2


def test_delete_and_save_share_the_same_platform_lock(tmp_path):
    manager = CredentialManager()
    cookie_file = tmp_path / "weibo.json"
    cookie_file.write_text('{"SUB":"old"}', encoding="utf-8")
    delete_unlinked = Event()
    allow_delete_to_finish = Event()
    save_started = Event()
    original_unlink = Path.unlink

    def slow_unlink(path, *args, **kwargs):
        original_unlink(path, *args, **kwargs)
        if path == cookie_file:
            delete_unlinked.set()
            assert allow_delete_to_finish.wait(timeout=2)

    def save_new(_platform, credentials, *, encrypt=True):
        assert encrypt is True
        save_started.set()
        cookie_file.write_text('{"SUB":"new"}', encoding="utf-8")
        return credentials == {"SUB": "new"}

    with (
        patch.dict("utils.cookies_login.COOKIE_FILES", {"weibo": cookie_file}),
        patch.object(cookies_login, "CONFIG_DIR", tmp_path),
        patch.object(Path, "unlink", slow_unlink),
        patch("utils.cookies_login._save_cookies_to_file_uncached", side_effect=save_new),
        ThreadPoolExecutor(max_workers=2) as executor,
    ):
        assert manager.load("weibo") == {"SUB": "old"}
        delete_future = executor.submit(manager.delete_stored_credentials, "weibo")
        assert delete_unlinked.wait(timeout=2)
        save_future = executor.submit(manager.save, "weibo", {"SUB": "new"})
        time.sleep(0.02)
        assert save_started.is_set() is False

        allow_delete_to_finish.set()
        assert delete_future.result(timeout=2) == (True, True)
        assert save_future.result(timeout=2) is True
        assert manager.load("weibo") == {"SUB": "new"}
        assert cookie_file.exists()


def test_delete_removes_orphaned_key_when_cookie_file_is_missing(tmp_path):
    manager = CredentialManager()
    cookie_file = tmp_path / "weibo.json"
    key_file = tmp_path / "weibo_key.key"
    key_file.write_bytes(b"orphaned-key")

    with (
        patch.dict("utils.cookies_login.COOKIE_FILES", {"weibo": cookie_file}),
        patch.object(cookies_login, "CONFIG_DIR", tmp_path),
    ):
        assert manager.delete_stored_credentials("weibo") == (True, True)

    assert not key_file.exists()


def test_failed_cookie_delete_preserves_encryption_key(tmp_path):
    manager = CredentialManager()
    cookie_file = tmp_path / "weibo.json"
    key_file = tmp_path / "weibo_key.key"
    cookie_file.write_text("encrypted", encoding="utf-8")
    key_file.write_bytes(b"required-key")
    original_unlink = Path.unlink

    def fail_cookie_unlink(path, *args, **kwargs):
        if path == cookie_file:
            raise PermissionError("busy")
        return original_unlink(path, *args, **kwargs)

    with (
        patch.dict("utils.cookies_login.COOKIE_FILES", {"weibo": cookie_file}),
        patch.object(cookies_login, "CONFIG_DIR", tmp_path),
        patch.object(Path, "unlink", fail_cookie_unlink),
        pytest.raises(PermissionError, match="busy"),
    ):
        manager.delete_stored_credentials("weibo")

    assert cookie_file.exists()
    assert key_file.exists()


def test_auth_rejected_state_skips_legacy_plaintext_fallback():
    with (
        patch.object(cookies_login.credential_manager, "load", return_value={}),
        patch.object(
            cookies_login.credential_manager,
            "state",
            return_value=CredentialManager.AUTH_REJECTED,
        ),
        patch.object(Path, "exists") as exists,
    ):
        assert platform_helpers._get_platform_cookies("weibo") == {}

    exists.assert_not_called()


def test_invalid_state_skips_legacy_plaintext_fallback():
    with (
        patch.object(cookies_login.credential_manager, "load", return_value={}),
        patch.object(
            cookies_login.credential_manager,
            "state",
            return_value=CredentialManager.INVALID,
        ),
        patch.object(Path, "exists") as exists,
    ):
        assert platform_helpers._get_platform_cookies("weibo") == {}

    exists.assert_not_called()


def test_missing_state_keeps_legacy_plaintext_fallback(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "weibo_cookies.json").write_text(
        '{"SUB":"legacy"}',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    with (
        patch.object(cookies_login.credential_manager, "load", return_value={}),
        patch.object(
            cookies_login.credential_manager,
            "state",
            return_value=CredentialManager.MISSING,
        ),
        patch.object(
            platform_helpers.os.path,
            "expanduser",
            return_value=str(tmp_path / "home"),
        ),
        patch.object(
            cookies_login.credential_manager,
            "cache_legacy_credentials",
            return_value=True,
        ),
    ):
        assert platform_helpers._get_platform_cookies("weibo") == {"SUB": "legacy"}


def test_legacy_fallback_can_be_rejected_and_deleted(tmp_path, monkeypatch):
    manager = CredentialManager()
    configured_file = tmp_path / "config" / "weibo_cookies.json"
    legacy_file = tmp_path / "weibo_cookies.json"
    legacy_file.write_text('{"SUB":"legacy"}', encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    with (
        patch.dict(
            "utils.cookies_login.COOKIE_FILES",
            {"weibo": configured_file},
        ),
        patch.object(cookies_login, "CONFIG_DIR", tmp_path / "config"),
        patch.object(cookies_login, "credential_manager", manager),
        patch.object(
            platform_helpers.os.path,
            "expanduser",
            return_value=str(tmp_path / "home"),
        ),
    ):
        credentials = platform_helpers._get_platform_cookies("weibo")
        assert credentials == {"SUB": "legacy"}
        assert manager.mark_auth_rejected("weibo", credentials) is True
        assert platform_helpers._get_platform_cookies("weibo") == {}
        assert manager.delete_stored_credentials("weibo") == (True, True)

    assert not legacy_file.exists()


def test_changed_legacy_source_is_not_cached(tmp_path):
    manager = CredentialManager()
    configured_file = tmp_path / "config" / "weibo_cookies.json"
    legacy_file = tmp_path / "weibo_cookies.json"
    legacy_file.write_text('{"SUB":"first"}', encoding="utf-8")
    source_signature = manager.legacy_source_signature(legacy_file)
    legacy_file.write_text('{"SUB":"second-and-longer"}', encoding="utf-8")

    with (
        patch.dict(
            "utils.cookies_login.COOKIE_FILES",
            {"weibo": configured_file},
        ),
        patch.object(cookies_login, "CONFIG_DIR", tmp_path / "config"),
    ):
        assert manager.cache_legacy_credentials(
            "weibo",
            {"SUB": "first"},
            source_signature,
        ) is False
        assert manager.state("weibo") == CredentialManager.MISSING


def test_weibo_auth_failure_detection_is_conservative():
    assert _is_weibo_auth_failure({"ok": 0, "msg": "请先登录"}) is True
    assert _is_weibo_auth_failure({"ok": 0, "msg": "登录已过期"}) is True
    assert _is_weibo_auth_failure({"ok": 0, "msg": "访问频次过高"}) is False
    assert _is_weibo_auth_failure({"ok": 0, "msg": "服务暂时不可用"}) is False
    assert _is_weibo_auth_failure({"ok": 1, "msg": "请先登录"}) is False


def test_credential_ui_keeps_rejected_entries_visible_and_removable():
    source = Path("static/js/cookies_login.js").read_text(encoding="utf-8")
    template = Path("templates/cookies_login.html").read_text(encoding="utf-8")

    assert "const hasStoredCredentials" in source
    assert "if (!stored) return;" in source
    assert "credentialState === 'auth_rejected'" in source
    assert "if (stored)" in source
    assert ".del-btn {\n            display: inline-grid;\n            width: 44px;\n            height: 44px;" in template

    for locale_path in Path("static/locales").glob("*.json"):
        status = json.loads(locale_path.read_text(encoding="utf-8"))["cookiesLogin"]["status"]
        assert status["expired"].strip(), locale_path
        assert status["invalid"].strip(), locale_path
