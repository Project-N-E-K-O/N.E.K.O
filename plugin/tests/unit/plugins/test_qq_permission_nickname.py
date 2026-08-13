from plugin.plugins.qq_auto_reply.permission import PermissionManager


def _manager(nickname: str = "original") -> PermissionManager:
    return PermissionManager(
        [
            {"qq": "1001", "level": "trusted", "nickname": nickname},
        ]
    )


def test_set_nickname_rejects_oversized_or_structural_values_without_overwrite():
    manager = _manager()
    invalid_values = [
        "x" * (PermissionManager.NICKNAME_MAX_CHARS + 1),
        "bad[name",
        "bad]name",
        "bad|name",
        "bad\nname",
        "bad\tname",
        "bad\x00name",
        "\n",
    ]

    for value in invalid_values:
        assert manager.set_nickname("1001", value) is False
        assert manager.get_nickname("1001") == "original"

    at_limit = "界" * PermissionManager.NICKNAME_MAX_CHARS
    assert manager.set_nickname("1001", at_limit) is True
    assert manager.get_nickname("1001") == at_limit
    assert manager.set_nickname("1001", "  Alice 小明  ") is True
    assert manager.get_nickname("1001") == "Alice 小明"
    assert manager.set_nickname("1001", "👩‍💻") is True
    assert manager.get_nickname("1001") == "👩‍💻"
    assert manager.set_nickname("1001", "") is True
    assert manager.get_nickname("1001") is None


def test_historical_invalid_nickname_remains_readable_and_can_be_repaired():
    historical = ("x" * 80) + "\n[]|legacy"
    manager = _manager(historical)

    assert manager.get_nickname("1001") == historical
    assert manager.list_users()[0]["nickname"] == historical
    assert manager.set_nickname("1001", "repaired") is True
    assert manager.get_nickname("1001") == "repaired"


def test_add_user_applies_the_same_nickname_validation_before_persisting():
    manager = PermissionManager()

    assert manager.add_user("1001", nickname="bad|name") is False
    assert manager.get_permission_level("1001") == "none"
    assert manager.add_user("1002", nickname="👩‍💻") is True
    assert manager.get_nickname("1002") == "👩‍💻"

    assert manager.add_user("1003", level="admin", nickname="bad|name") is True
    assert manager.get_nickname("1003") is None


def test_validate_nickname_rejects_control_chars_and_oversized():
    manager = _manager()
    assert manager.validate_nickname("good name") is None
    assert manager.validate_nickname("bad\x00name") == "control_char"
    assert manager.validate_nickname("bad\nname") == "control_char"
    assert manager.validate_nickname("bad[name") == "control_char"
    assert manager.validate_nickname("x" * (manager.NICKNAME_MAX_CHARS + 1)) == "too_long"


def test_add_trusted_user_returns_invalid_argument_for_control_char_nickname():
    """A control-character nickname must surface as INVALID_ARGUMENT (not the
    generic SET_FAILED), so the caller can show a clear message."""
    import asyncio
    from types import SimpleNamespace

    from plugin.plugins.qq_auto_reply.dashboard_service import QQDashboardService

    plugin = SimpleNamespace(
        permission_mgr=PermissionManager(),
        i18n=SimpleNamespace(t=lambda key, default="", **kw: default),
    )
    svc = QQDashboardService(plugin)
    result = asyncio.run(svc.add_trusted_user(
        qq_number="10001", level="trusted", nickname="bad\x00name",
    ))
    assert result.is_err()
    assert "INVALID_ARGUMENT" in str(result.error)
