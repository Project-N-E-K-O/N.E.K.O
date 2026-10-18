"""Smoke test: telemetry distribution / steam_user_id share the same source.

Regression for the race fixed by ``_get_telemetry_metadata()``: originally
``_get_telemetry_distribution()`` and ``_get_telemetry_steam_user_id()`` each called
``Users.GetSteamID()``. During asynchronous Steamworks init, those calls could straddle
the ready boundary, producing inconsistent ``distribution='release'`` with nonempty Steam64.
One shared observation now derives both fields, eliminating inconsistency at the source.

Core invariant (must never be violated):
  Nonempty steam_user_id ⟹ distribution == 'steam'
Equivalently, release/source with nonempty Steam64 is impossible.
"""
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import utils.token_tracker as tt
import utils.token_tracker.reporting as reporting
import utils.token_tracker.telemetry as telemetry


@contextmanager
def _patched_env(*, is_release, steam_id, workshop_subs, workshop_file_exists):
    """Construct a controlled Steamworks / config / build environment.

    steam_id=None means ``get_steamworks()`` returns None (SDK unavailable).
    Return GetSteamID's MagicMock to assert it is called only once.
    """
    get_steam_id = MagicMock(return_value=steam_id if steam_id is not None else 0)

    if steam_id is None:
        sw = None
    else:
        sw = SimpleNamespace(
            Users=SimpleNamespace(GetSteamID=get_steam_id),
            Workshop=SimpleNamespace(
                GetNumSubscribedItems=MagicMock(return_value=workshop_subs)
            ),
        )

    fake_config_dir = MagicMock()
    fake_config_dir.__truediv__ = lambda self, other: SimpleNamespace(
        exists=lambda: workshop_file_exists
    )
    cm = SimpleNamespace(config_dir=fake_config_dir)

    with patch.object(telemetry, "_is_release_build", return_value=is_release), \
         patch("utils.steam_state.get_steamworks", return_value=sw), \
         patch("utils.config_manager.get_config_manager", return_value=cm):
        yield get_steam_id


# (is_release, steam_id, workshop_subs, workshop_file_exists) -> (distribution, steam_user_id)
_CASES = [
    # Source mode: always source + empty, even when Steam runs.
    (False, 76561198000000000, 5, True, ("source", "")),
    (False, None, 0, False, ("source", "")),
    # Release + Steam64 available → steam + ID.
    (True, 76561198000000000, 0, False, ("steam", "76561198000000000")),
    # Release + SDK ready but no logged-in user (0), with Workshop subscriptions → steam + empty (valid trailer).
    (True, 0, 3, False, ("steam", "")),
    # Release + SDK ready but 0, no subscriptions, workshop_config.json fallback → steam + empty.
    (True, 0, 0, True, ("steam", "")),
    # Release + SDK unavailable (None), Workshop file fallback → steam + empty.
    (True, None, 0, True, ("steam", "")),
    # Release + no Steam signals → release + empty.
    (True, 0, 0, False, ("release", "")),
    (True, None, 0, False, ("release", "")),
]


@pytest.mark.parametrize("is_release,steam_id,subs,file_exists,expected", _CASES)
def test_metadata_mapping(is_release, steam_id, subs, file_exists, expected):
    with _patched_env(
        is_release=is_release,
        steam_id=steam_id,
        workshop_subs=subs,
        workshop_file_exists=file_exists,
    ):
        assert tt._get_telemetry_metadata() == expected


@pytest.mark.parametrize("is_release,steam_id,subs,file_exists,expected", _CASES)
def test_invariant_no_id_without_steam(is_release, steam_id, subs, file_exists, expected):
    """Core invariant: nonempty steam_user_id ⟹ distribution == 'steam'."""
    with _patched_env(
        is_release=is_release,
        steam_id=steam_id,
        workshop_subs=subs,
        workshop_file_exists=file_exists,
    ):
        distribution, steam_user_id = tt._get_telemetry_metadata()
    if steam_user_id != "":
        assert distribution == "steam", (
            f"矛盾态：distribution={distribution!r} 带非空 steam_user_id={steam_user_id!r}"
        )


def test_get_steam_id_called_at_most_once():
    """Prove the race is fixed: GetSteamID() is called at most once per report.

    The old bug called twice, potentially crossing the ready boundary and observing different results.
    distribution and steam_user_id must now derive from the same observation.
    """
    with _patched_env(
        is_release=True,
        steam_id=76561198000000000,
        workshop_subs=0,
        workshop_file_exists=False,
    ) as get_steam_id:
        tt._get_telemetry_metadata()
    assert get_steam_id.call_count <= 1, (
        f"GetSteamID() 被调用 {get_steam_id.call_count} 次，race 风险未消除"
    )


def test_exceptions_swallowed():
    """Telemetry must not raise: Steamworks failures safely degrade rather than propagate."""
    boom = MagicMock(side_effect=RuntimeError("steam boom"))
    sw = SimpleNamespace(
        Users=SimpleNamespace(GetSteamID=boom),
        Workshop=SimpleNamespace(GetNumSubscribedItems=boom),
    )
    fake_config_dir = MagicMock()
    fake_config_dir.__truediv__ = lambda self, other: SimpleNamespace(
        exists=MagicMock(side_effect=OSError("disk boom"))
    )
    cm = SimpleNamespace(config_dir=fake_config_dir)

    with patch.object(telemetry, "_is_release_build", return_value=True), \
         patch("utils.steam_state.get_steamworks", return_value=sw), \
         patch("utils.config_manager.get_config_manager", return_value=cm):
        # Even exceptions throughout must safely return release + empty, never raise.
        assert tt._get_telemetry_metadata() == ("release", "")


def test_facade_telemetry_url_remains_live(monkeypatch):
    """Facade writes and owner-module rebinds must observe the same URL state."""
    monkeypatch.setattr(tt, "_TELEMETRY_SERVER_URL", "http://127.0.0.1:18099")
    assert reporting._TELEMETRY_SERVER_URL == "http://127.0.0.1:18099"

    reporting._TELEMETRY_SERVER_URL = ""
    assert tt._TELEMETRY_SERVER_URL == ""
