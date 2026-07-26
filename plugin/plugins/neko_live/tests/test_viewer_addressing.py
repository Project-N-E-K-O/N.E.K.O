from __future__ import annotations

from plugin.plugins.neko_live.core.contracts import ViewerProfile
from plugin.plugins.neko_live.core.viewer_addressing import viewer_address_name


def test_regular_viewer_poetic_nickname_uses_short_address():
    profile = ViewerProfile(uid="42", nickname="\u4e0a\u4e5d\u5929\u63fd\u661f\u8fb0", danmaku_count=8)

    assert viewer_address_name("\u4e0a\u4e5d\u5929\u63fd\u661f\u8fb0", profile) == "\u661f\u8fb0"


def test_regular_viewer_cjk_nickname_uses_natural_alias_not_initials():
    profile = ViewerProfile(uid="42", nickname="\u6d45\u971c\u6e05\u97f5", danmaku_count=8)

    assert viewer_address_name("\u6d45\u971c\u6e05\u97f5", profile) == "\u6e05\u97f5"


def test_regular_viewer_separator_initials_are_not_used_as_alias():
    profile = ViewerProfile(uid="42", nickname="\u6d45\u971c\u6e05\u97f5-WF", danmaku_count=8)

    assert viewer_address_name("\u6d45\u971c\u6e05\u97f5-WF", profile) == "\u6e05\u97f5"


def test_regular_viewer_keeps_full_name_when_short_piece_is_not_natural():
    profile = ViewerProfile(uid="42", nickname="\u6d4b\u8bd5\u7528\u6237", danmaku_count=8)

    assert viewer_address_name("\u6d4b\u8bd5\u7528\u6237", profile) == "\u6d4b\u8bd5\u7528\u6237"


def test_regular_viewer_all_latin_nickname_keeps_readable_name():
    profile = ViewerProfile(uid="42", nickname="LittleStar", danmaku_count=8)

    assert viewer_address_name("LittleStar", profile) == "LittleStar"


def test_regular_viewer_separated_latin_nickname_uses_natural_token():
    profile = ViewerProfile(uid="42", nickname="Little-Star", danmaku_count=8)

    assert viewer_address_name("Little-Star", profile) == "Star"


def test_regular_viewer_all_latin_initials_stay_original_when_no_better_name():
    profile = ViewerProfile(uid="42", nickname="WF", danmaku_count=8)

    assert viewer_address_name("WF", profile) == "WF"


def test_new_viewer_keeps_full_nickname_until_memory_is_stable():
    profile = ViewerProfile(uid="42", nickname="\u4e0a\u4e5d\u5929\u63fd\u661f\u8fb0", danmaku_count=1)

    assert viewer_address_name("\u4e0a\u4e5d\u5929\u63fd\u661f\u8fb0", profile) == "\u4e0a\u4e5d\u5929\u63fd\u661f\u8fb0"
