"""The pre-split import surface of ``utils.connection.onebot`` must keep working.

The qq_auto_reply plugin (published separately, in the plugin market) looks the
connector up at runtime: it imports ``utils.connection.onebot`` and only uses the
host package when all of the names below are present, otherwise it silently
falls back to its own vendored copy. So a missing name is not an ImportError
anywhere; it is the plugin quietly running old code. These tests pin that
contract after the package was split into ``base`` / ``onebot`` / ``qq``.

Also pinned:

- the old names are the SAME objects as their new homes (not copies), so
  ``isinstance`` checks and class identity hold across both paths;
- ``qq_open_plat`` is the real implementation module, so ``monkeypatch.setattr``
  on its globals reaches the code (the plugin's tests do exactly that);
- ``OneBotConnector``'s public member set, which the plugin compares against its
  vendored copy and fails on any drift.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

import utils.connection.base as base
import utils.connection.onebot as onebot
import utils.connection.qq as qq
from utils.connection.qq import open_platform

_REPO_ROOT = Path(__file__).resolve().parents[2]

#: The plugin's ``connector_seam._REQUIRED_ATTRS`` (5 exports + 4 submodules).
PLUGIN_REQUIRED_ATTRS = (
    "OneBotClient",
    "OneBotConnectionBase",
    "OneBotConnector",
    "QQOpenPlatformConnection",
    "create_onebot_connection",
    "factory",
    "onebot_client",
    "onebot_connection",
    "qq_open_plat",
)

#: ``OneBotConnector``'s public members as of the split. The plugin's drift guard
#: compares this set against its vendored copy; changing it breaks the plugin's
#: tests until the plugin re-vendors.
CONNECTOR_PROTOCOL_MEMBERS = frozenset({
    "connect", "disconnect", "get_login_status", "is_connected", "is_group_muted",
    "needs_attention", "onebot_url", "receive_message", "record_sent_message_id",
    "self_id", "send_group_ark_card", "send_group_image", "send_group_message_segments",
    "send_group_poke", "send_group_record", "send_private_message_segments",
    "sent_message_ids", "set_inbound_sink", "supports_ark_cards", "supports_poke",
    "supports_voice",
})


@pytest.mark.parametrize("name", PLUGIN_REQUIRED_ATTRS)
def test_plugin_lookup_names_are_present(name):
    assert hasattr(onebot, name), f"utils.connection.onebot 缺 {name}：插件会静默退回内置副本"


def test_old_names_are_the_new_objects():
    assert onebot.OneBotConnectionBase is base.ConnectionBase
    assert onebot.OneBotConnector is base.ChatConnector
    assert onebot.QQOpenPlatformConnection is qq.QQOpenPlatformConnection
    assert onebot.create_onebot_connection is qq.create_qq_connection
    assert onebot.factory.create_onebot_connection is qq.create_qq_connection
    assert onebot.factory.OneBotConnector is base.ChatConnector
    assert onebot.onebot_connection.OneBotConnectionBase is base.ConnectionBase


def test_qq_open_plat_is_the_implementation_module():
    assert onebot.qq_open_plat is open_platform
    assert importlib.import_module("utils.connection.onebot.qq_open_plat") is open_platform


def test_monkeypatching_the_old_module_reaches_the_code(monkeypatch):
    """A re-exporting shim would pass the identity checks on classes but not this."""
    monkeypatch.setattr(onebot.qq_open_plat, "_IDENTITY_PROBE_MAX_LINES", 7)
    assert open_platform._IDENTITY_PROBE_MAX_LINES == 7


def test_connector_protocol_members_are_unchanged():
    members = {n for n in dir(onebot.OneBotConnector) if not n.startswith("_")}
    assert members == CONNECTOR_PROTOCOL_MEMBERS


def test_both_connections_still_share_the_old_base():
    assert issubclass(onebot.OneBotClient, onebot.OneBotConnectionBase)
    assert issubclass(onebot.QQOpenPlatformConnection, onebot.OneBotConnectionBase)


def test_extension_actions_are_still_on_the_client():
    """Moving the NapCat extensions into a mixin must not drop them from OneBotClient."""
    for name in ("get_file_by_id", "get_private_file_url", "get_group_file_url",
                 "set_msg_emoji_like", "get_group_msg_history", "nc_get_rkey"):
        assert callable(getattr(onebot.OneBotClient, name, None)), name


@pytest.mark.parametrize("first_import", [
    "import utils.connection.onebot",
    "import utils.connection.qq",
    "import utils.connection.onebot.qq_open_plat",
    "import utils.connection.onebot.factory",
    "from utils.connection.onebot import create_onebot_connection",
])
def test_every_entry_point_imports_cleanly_in_a_fresh_process(first_import):
    """``qq`` imports ``onebot`` and ``onebot`` exposes ``qq`` names; whichever
    is imported first, a fresh interpreter must not hit a circular import."""
    probe = (
        f"{first_import}\n"
        "import utils.connection.onebot as m\n"
        f"missing = [n for n in {PLUGIN_REQUIRED_ATTRS!r} if not hasattr(m, n)]\n"
        "assert not missing, missing\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=_REPO_ROOT, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr[-2000:]
