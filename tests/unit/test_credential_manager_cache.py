import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from utils.cookies_login import CredentialManager


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
        manager.mark_auth_rejected("weibo")
        assert manager.load("weibo") == {}
        assert manager.status("weibo")["credential_state"] == CredentialManager.AUTH_REJECTED
        manager.mark_deleted("weibo")
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
