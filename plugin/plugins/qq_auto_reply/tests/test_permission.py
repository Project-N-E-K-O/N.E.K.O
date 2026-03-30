import pytest
from permission import PermissionManager


class TestPermissionManagerInit:
    def test_init_empty(self):
        pm = PermissionManager()
        assert pm.list_users() == []

    def test_init_with_users(self):
        pm = PermissionManager([
            {"qq": "123", "level": "admin", "nickname": "Alice"},
            {"qq": "456", "level": "trusted"},
        ])
        assert pm.get_permission_level("123") == "admin"
        assert pm.get_permission_level("456") == "trusted"
        assert pm.get_nickname("123") == "Alice"
        assert pm.get_nickname("456") is None

    def test_init_skips_empty_qq(self):
        pm = PermissionManager([{"qq": "", "level": "admin"}])
        assert pm.list_users() == []

    def test_init_defaults_missing_level_to_trusted(self):
        pm = PermissionManager([{"qq": "123"}])
        assert pm.get_permission_level("123") == "trusted"


class TestAddRemoveUser:
    def test_add_user(self):
        pm = PermissionManager()
        pm.add_user("123", "admin", "Bob")
        assert pm.get_permission_level("123") == "admin"
        assert pm.get_nickname("123") == "Bob"

    def test_add_user_invalid_level_defaults_to_trusted(self):
        pm = PermissionManager()
        pm.add_user("123", "superuser")
        assert pm.get_permission_level("123") == "trusted"

    def test_add_user_without_nickname(self):
        pm = PermissionManager()
        pm.add_user("123", "trusted")
        assert pm.get_nickname("123") is None

    def test_remove_user(self):
        pm = PermissionManager()
        pm.add_user("123", "trusted", "Charlie")
        pm.remove_user("123")
        assert pm.get_permission_level("123") == "none"
        assert pm.get_nickname("123") is None

    def test_remove_nonexistent_user_does_not_raise(self):
        pm = PermissionManager()
        pm.remove_user("999")  # should not raise


class TestPermissionLevel:
    def test_unknown_user_returns_none(self):
        pm = PermissionManager()
        assert pm.get_permission_level("999") == "none"

    def test_get_permission_level_converts_int_to_str(self):
        pm = PermissionManager()
        pm.add_user("123", "trusted")
        assert pm.get_permission_level(123) == "trusted"

    def test_is_admin_true(self):
        pm = PermissionManager()
        pm.add_user("123", "admin")
        assert pm.is_admin("123") is True

    def test_is_admin_false_for_trusted(self):
        pm = PermissionManager()
        pm.add_user("123", "trusted")
        assert pm.is_admin("123") is False

    def test_is_admin_false_for_unknown(self):
        pm = PermissionManager()
        assert pm.is_admin("999") is False

    def test_is_trusted_includes_admin(self):
        pm = PermissionManager()
        pm.add_user("1", "admin")
        pm.add_user("2", "trusted")
        pm.add_user("3", "normal")
        assert pm.is_trusted("1") is True
        assert pm.is_trusted("2") is True
        assert pm.is_trusted("3") is False
        assert pm.is_trusted("999") is False


class TestNickname:
    def test_set_nickname(self):
        pm = PermissionManager()
        pm.add_user("123", "trusted")
        result = pm.set_nickname("123", "Dave")
        assert result is True
        assert pm.get_nickname("123") == "Dave"

    def test_set_nickname_nonexistent_user_returns_false(self):
        pm = PermissionManager()
        result = pm.set_nickname("999", "Eve")
        assert result is False

    def test_set_empty_nickname_removes_it(self):
        pm = PermissionManager()
        pm.add_user("123", "trusted", "Frank")
        pm.set_nickname("123", "")
        assert pm.get_nickname("123") is None


class TestListUsers:
    def test_list_users(self):
        pm = PermissionManager()
        pm.add_user("123", "admin", "Alice")
        pm.add_user("456", "trusted")
        users = pm.list_users()
        assert len(users) == 2
        by_qq = {u["qq"]: u for u in users}
        assert by_qq["123"]["level"] == "admin"
        assert by_qq["123"]["nickname"] == "Alice"
        assert by_qq["456"]["level"] == "trusted"
        assert "nickname" not in by_qq["456"]
