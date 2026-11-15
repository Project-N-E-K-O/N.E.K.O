"""Avatar type telemetry: how many users run Live2D / VRM / MMD / PNGTuber."""
import types

import pytest


def _cat(model_type=None, sub_type=None, mmd_path=None, vrm_path=None):
    avatar = {}
    if model_type is not None:
        avatar["model_type"] = model_type
    if sub_type is not None:
        avatar["live3d_sub_type"] = sub_type
    if mmd_path is not None:
        avatar["mmd"] = {"model_path": mmd_path}
    if vrm_path is not None:
        avatar["vrm"] = {"model_path": vrm_path}
    return {"_reserved": {"avatar": avatar}}


@pytest.mark.parametrize(
    "cfg, expected",
    [
        (_cat(), "live2d"),  # Missing model_type renders as Live2D.
        (_cat("live2d"), "live2d"),
        (_cat("pngtuber"), "pngtuber"),
        (_cat("live3d", "vrm"), "vrm"),
        (_cat("live3d", "mmd"), "mmd"),
        (_cat("vrm", vrm_path="/user_vrm/a.vrm"), "vrm"),  # Legacy 'vrm' model_type.
        (_cat("live3d", mmd_path="/user_mmd/a.pmx", vrm_path="/user_vrm/a.vrm"), "mmd"),
        (_cat("live3d"), "unknown"),
        (_cat("weird"), "unknown"),
        ({"model_type": "pngtuber"}, "pngtuber"),  # Legacy flat field.
        (None, "unknown"),
    ],
)
def test_resolve_avatar_telemetry_type(cfg, expected):
    from utils.token_tracker import resolve_avatar_telemetry_type

    assert resolve_avatar_telemetry_type(cfg) == expected


def _patch_characters(monkeypatch, characters, *, do_not_track=False):
    import utils.config_manager as cm_mod
    import utils.token_tracker.reporting as reporting

    fake_cm = types.SimpleNamespace(load_characters=lambda: characters)
    monkeypatch.setattr(cm_mod, "get_config_manager", lambda: fake_cm)
    # Pin the opt-out flag so a developer's DO_NOT_TRACK=1 cannot change the outcome.
    monkeypatch.setattr(reporting, "_DO_NOT_TRACK", do_not_track)


def test_record_avatar_state_counts_active_and_owned(monkeypatch):
    import utils.instrument as inst
    from utils.token_tracker import record_avatar_state

    _patch_characters(monkeypatch, {
        "当前猫娘": "B",
        "猫娘": {
            "A": _cat("live2d"),
            "B": _cat("live3d", "vrm"),
            "C": _cat("live3d", "vrm"),
            "D": _cat("pngtuber"),
        },
    })
    inst.snapshot()  # Drain leftovers.
    record_avatar_state()
    counters = inst.snapshot().get("counters", {})

    assert counters.get("avatar_type_active|type=vrm") == 1
    assert counters.get("avatar_type_owned|type=vrm") == 2
    assert counters.get("avatar_type_owned|type=live2d") == 1
    assert counters.get("avatar_type_owned|type=pngtuber") == 1
    assert "avatar_type_owned|type=mmd" not in counters


def test_record_avatar_state_invalid_current_falls_back_to_first(monkeypatch):
    import utils.instrument as inst
    from utils.token_tracker import record_avatar_state

    _patch_characters(monkeypatch, {
        "当前猫娘": "gone",
        "猫娘": {"A": _cat("live3d", "mmd"), "B": _cat("live2d")},
    })
    inst.snapshot()
    record_avatar_state()
    counters = inst.snapshot().get("counters", {})

    assert counters.get("avatar_type_active|type=mmd") == 1


def test_record_avatar_state_respects_do_not_track(monkeypatch):
    import utils.instrument as inst
    from utils.token_tracker import record_avatar_state

    _patch_characters(
        monkeypatch,
        {"当前猫娘": "A", "猫娘": {"A": _cat("live2d")}},
        do_not_track=True,
    )
    inst.snapshot()
    record_avatar_state()
    counters = inst.snapshot().get("counters", {})
    assert not any(k.startswith("avatar_type_") for k in counters)


def test_record_avatar_state_never_raises(monkeypatch):
    import utils.instrument as inst
    import utils.config_manager as cm_mod
    import utils.token_tracker.reporting as reporting
    from utils.token_tracker import record_avatar_state

    def _boom():
        raise RuntimeError("config unavailable")

    monkeypatch.setattr(reporting, "_DO_NOT_TRACK", False)
    monkeypatch.setattr(cm_mod, "get_config_manager", _boom)
    inst.snapshot()
    record_avatar_state()  # Must not raise.
    counters = inst.snapshot().get("counters", {})
    assert not any(k.startswith("avatar_type_") for k in counters)
