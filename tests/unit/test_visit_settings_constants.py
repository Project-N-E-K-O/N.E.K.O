"""Contract tests for the visit constants (docs/design/visit-infrastructure.md PR-03).

The ``_USER_OWNED_FIELDS`` sets live in a router and in a plugin mirror; both
are read with ``ast`` instead of being imported so this test stays free of the
router / plugin import side effects.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

import config
from utils.conversation_settings_constants import ALLOWED_CONVERSATION_SETTINGS

vs = config.visit_settings   # config 包导入时已加载该子模块

REPO_ROOT = Path(__file__).resolve().parents[2]
VISIT_KEYS = {"visitEnabled", "visitMemoryEnabled", "visitVoiceEnabled"}


def _frozenset_literal(path: Path, name: str) -> frozenset[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == name
        ):
            call = node.value
            assert isinstance(call, ast.Call) and call.func.id == "frozenset"
            return frozenset(ast.literal_eval(call.args[0]))
    raise AssertionError(f"{name} not found in {path}")


def test_visit_keys_are_allowed_conversation_settings():
    assert VISIT_KEYS <= ALLOWED_CONVERSATION_SETTINGS


def test_visit_setting_defaults():
    # visitMemoryEnabled is a hidden, default-on setting (OD-09 v3).
    assert vs.VISIT_MEMORY_DEFAULT is True
    assert vs.VISIT_VOICE_DEFAULT is True
    assert vs.VISIT_ENABLED_DEFAULT is False


def test_user_owned_fields_mirror_is_identical_and_covers_visit_keys():
    server = _frozenset_literal(
        REPO_ROOT / "main_routers" / "proactive_router.py", "_USER_OWNED_FIELDS",
    )
    plugin = _frozenset_literal(
        REPO_ROOT / "plugin" / "plugins" / "proactive_controller" / "__init__.py",
        "_USER_OWNED_FIELDS",
    )
    assert server == plugin
    assert {"visitEnabled", "visitMemoryEnabled"} <= server
    assert "visitVoiceEnabled" not in server


def test_lifecycle_clock_ordering():
    assert sum(vs.VISIT_OUTBOX_RETRY_S) < vs.VISIT_PEER_LOST_S
    # Self reconnect gives up exactly one heartbeat before the peer declares us dead.
    assert vs.VISIT_PEER_LOST_S - vs.VISIT_SELF_RECONNECT_S >= vs.VISIT_HEARTBEAT_S
    assert vs.VISIT_SELF_RECONNECT_S - vs.VISIT_LOCAL_PAGE_GRACE_S >= vs.VISIT_HEARTBEAT_S
    assert vs.VISIT_PEER_REJOIN_GRACE_S >= vs.VISIT_LOCAL_PAGE_GRACE_S + 15
    assert vs.VISIT_INVITE_WAIT_S == vs.VISIT_INVITE_CODE_TTL_S


def test_guest_ready_wait_outlasts_latest_host_send():
    guest_wait = (
        vs.VISIT_ACCEPT_TIMEOUT_S
        + vs.VISIT_ACTIVATION_ALLOWANCE_S
        + vs.VISIT_READY_DELIVERY_MARGIN_S
    )
    assert guest_wait == 85
    latest_host_send = vs.VISIT_ACCEPT_TIMEOUT_S + vs.VISIT_ACTIVATION_ALLOWANCE_S
    assert guest_wait > latest_host_send + sum(vs.VISIT_OUTBOX_RETRY_S[:3])


def test_reliable_layer_budgets():
    assert vs.VISIT_OUTBOX_PENDING_MAX_BYTES == (
        vs.VISIT_DATA_BUCKET_BPS * (vs.VISIT_LEAVE_GAP_GRACE_S - 1)
    )
    assert vs.VISIT_DATA_BUCKET_BURST_BYTES >= vs.VISIT_PIECES_MAX * 1024
    assert vs.VISIT_INBOUND_TEXT_BURST == 20 + 2 * vs.VISIT_PEER_REJOIN_GRACE_S
    assert vs.VISIT_INBOUND_TEXT_MAX == vs.VISIT_UPLOAD_MAX_LINES // 2
    assert vs.VISIT_REORDER_BUFFER_MAX >= vs.VISIT_INBOUND_TEXT_BURST
    assert vs.VISIT_LINE_DELTA_PAYLOAD_MAX_BYTES < vs.VISIT_PIECE_MAX_BYTES


def test_ticket_lifetimes_cover_the_visit():
    assert vs.VISIT_HOST_CREDENTIAL_TTL_S == (
        vs.VISIT_INVITE_WAIT_S + vs.VISIT_MAX_DURATION_S + 600
    )
    assert vs.VISIT_CREDENTIAL_TTL_S > vs.VISIT_MAX_DURATION_S


def test_only_sd600_tier_is_enabled():
    assert {name for name, tier in vs.VISIT_TIERS.items() if tier["enabled"]} == {"sd600"}
    assert vs.VISIT_VIDEO_TIER_DEFAULT == "sd600"


@pytest.mark.parametrize("crop", ["upper", "full"])
def test_congestion_ladder_stays_in_the_standard_band(crop: str):
    ladder = vs.VISIT_CONGESTION_LADDER[crop]
    for width, height, kbps in ladder:
        assert width % 16 == 0 and height % 16 == 0, (width, height)
        # Packed canvas stacks alpha under colour: W × 2H.
        assert width * 2 * height < vs.VISIT_PACK_AREA_MAX, (width, height)
        assert kbps >= vs.VISIT_CONGESTION_MIN_KBPS == 300
    sd600 = vs.VISIT_TIERS["sd600"]
    assert ladder[0][:2] == sd600[f"crop_{crop}"]
    assert ladder[0][2] == sd600["video_kbps"]
    assert sd600[f"pack_{crop}"] == (ladder[0][0], 2 * ladder[0][1])


def test_invariant_checker_rejects_a_broken_relation(monkeypatch):
    monkeypatch.setattr(vs, "VISIT_SELF_RECONNECT_S", 28)
    with pytest.raises(ValueError, match="self reconnect"):
        vs._check_invariants()


def test_page_reload_deadline_must_outlast_the_socket_grace(monkeypatch):
    # 调小重入宽限或调大安全余量，绝对期限会悄悄短于 transport WS 的 20 s
    monkeypatch.setattr(vs, "VISIT_PAGE_REJOIN_SAFETY_S", 15)
    with pytest.raises(ValueError, match="absolute page reload deadline"):
        vs._check_invariants()


def test_debrief_commit_backoff_caps_at_one_hour():
    # 暂时性写入失败的退避：30 s / 2 min / 10 min / 1 h，之后一直取最后一项（owner 2026-10-02）
    assert vs.VISIT_DEBRIEF_COMMIT_BACKOFF_S == (30, 120, 600, 3600)


def test_every_visit_constant_is_re_exported_from_config():
    # 后续 PR 按惯例 from config import VISIT_*：漏了再导出就是 ImportError
    names = {n for n in dir(vs) if n.startswith("VISIT_")}
    assert names <= set(config.__all__)
    assert all(getattr(config, n) == getattr(vs, n) for n in names)


def test_page_reload_safety_margin_must_be_positive(monkeypatch):
    # 本侧要比对端的重入宽限早收口，靠的就是这个余量大于 0
    monkeypatch.setattr(vs, "VISIT_PAGE_REJOIN_SAFETY_S", 0)
    with pytest.raises(ValueError, match="positive safety margin"):
        vs._check_invariants()
