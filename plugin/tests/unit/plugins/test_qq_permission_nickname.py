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
    assert manager.set_nickname("1001", "") is True
    assert manager.get_nickname("1001") is None


def test_historical_invalid_nickname_remains_readable_and_can_be_repaired():
    historical = ("x" * 80) + "\n[]|legacy"
    manager = _manager(historical)

    assert manager.get_nickname("1001") == historical
    assert manager.list_users()[0]["nickname"] == historical
    assert manager.set_nickname("1001", "repaired") is True
    assert manager.get_nickname("1001") == "repaired"
