import pytest
from group_permission import GroupPermissionManager


class TestGroupPermissionManagerInit:
    def test_init_empty(self):
        gpm = GroupPermissionManager()
        assert gpm.list_groups() == []

    def test_init_with_groups(self):
        gpm = GroupPermissionManager([
            {"group_id": "111", "level": "trusted"},
            {"group_id": "222", "level": "normal"},
        ])
        assert gpm.get_group_level("111") == "trusted"
        assert gpm.get_group_level("222") == "normal"

    def test_init_skips_empty_group_id(self):
        gpm = GroupPermissionManager([{"group_id": "", "level": "trusted"}])
        assert gpm.list_groups() == []

    def test_init_defaults_missing_level_to_normal(self):
        gpm = GroupPermissionManager([{"group_id": "111"}])
        assert gpm.get_group_level("111") == "normal"


class TestAddRemoveGroup:
    def test_add_group_trusted(self):
        gpm = GroupPermissionManager()
        gpm.add_group("111", "trusted")
        assert gpm.get_group_level("111") == "trusted"

    def test_add_group_normal(self):
        gpm = GroupPermissionManager()
        gpm.add_group("111", "normal")
        assert gpm.get_group_level("111") == "normal"

    def test_add_group_invalid_level_defaults_to_normal(self):
        gpm = GroupPermissionManager()
        gpm.add_group("111", "supergroup")
        assert gpm.get_group_level("111") == "normal"

    def test_remove_group(self):
        gpm = GroupPermissionManager()
        gpm.add_group("111", "trusted")
        gpm.remove_group("111")
        assert gpm.get_group_level("111") == "none"

    def test_remove_nonexistent_group_does_not_raise(self):
        gpm = GroupPermissionManager()
        gpm.remove_group("999")


class TestGroupLevel:
    def test_unknown_group_returns_none(self):
        gpm = GroupPermissionManager()
        assert gpm.get_group_level("999") == "none"

    def test_get_group_level_converts_int_to_str(self):
        gpm = GroupPermissionManager()
        gpm.add_group("111", "trusted")
        assert gpm.get_group_level(111) == "trusted"

    def test_is_trusted_group_true(self):
        gpm = GroupPermissionManager()
        gpm.add_group("111", "trusted")
        assert gpm.is_trusted_group("111") is True

    def test_is_trusted_group_false_for_normal(self):
        gpm = GroupPermissionManager()
        gpm.add_group("111", "normal")
        assert gpm.is_trusted_group("111") is False

    def test_is_trusted_group_false_for_unknown(self):
        gpm = GroupPermissionManager()
        assert gpm.is_trusted_group("999") is False

    def test_is_allowed_group_includes_trusted_and_normal(self):
        gpm = GroupPermissionManager()
        gpm.add_group("1", "trusted")
        gpm.add_group("2", "normal")
        assert gpm.is_allowed_group("1") is True
        assert gpm.is_allowed_group("2") is True
        assert gpm.is_allowed_group("999") is False


class TestListGroups:
    def test_list_groups(self):
        gpm = GroupPermissionManager()
        gpm.add_group("111", "trusted")
        gpm.add_group("222", "normal")
        groups = gpm.list_groups()
        assert len(groups) == 2
        by_id = {g["group_id"]: g for g in groups}
        assert by_id["111"]["level"] == "trusted"
        assert by_id["222"]["level"] == "normal"
