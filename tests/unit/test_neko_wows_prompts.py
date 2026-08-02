"""Prompt revisions: whole-bundle validation, rollover, atomic swap, preview."""

from __future__ import annotations

import pytest

from plugin.plugins.neko_wows.detectors._base import GameEvent
from plugin.plugins.neko_wows.domain.catalog import DAMAGE_MILESTONE, LOW_HEALTH
from plugin.plugins.neko_wows.domain.contracts import (
    CHANNEL_DUAL,
    CHANNEL_SINGLE,
    WowsConfig,
)
from plugin.plugins.neko_wows.domain.facts import WowsFacts
from plugin.plugins.neko_wows.knowledge.store import KnowledgeStore
from plugin.plugins.neko_wows.policy.tactic_policy import WowsTacticPolicy
from plugin.plugins.neko_wows.presentation.instructions import (
    BASE_INSTRUCTIONS,
    BUILTIN_REVISION_ID,
    DEFAULT_BUNDLE,
    MAX_SECTION_CHARS,
    NORMAL_OVERLAY,
    URGENT_OVERLAY,
    PromptBundle,
    PromptRejected,
    bundle_from_revision,
    instructions_for,
    validate_sections,
)
from plugin.plugins.neko_wows.presentation.prompt_router import (
    PromptProfile,
    WowsPromptRouter,
)

CFG = WowsConfig()


@pytest.fixture
def store(tmp_path):
    instance = KnowledgeStore(tmp_path / "tactical.db")
    instance.open()
    yield instance
    instance.close()


def candidate(event_id=LOW_HEALTH):
    event = GameEvent(
        event_id=event_id, severity=80, at=100.0, seq=1, battle_id="b-1",
        detail={"hp_ratio": 0.12})
    facts = WowsFacts(seq=1, at=100.0, battle_id="b-1", own_hp_ratio=0.12)
    return WowsTacticPolicy(CFG).expand([event], facts)[0]


# --- bundle assembly -----------------------------------------------------

def test_the_builtin_bundle_is_the_default():
    assert DEFAULT_BUNDLE.revision_id == BUILTIN_REVISION_ID
    assert DEFAULT_BUNDLE.is_builtin is True
    assert DEFAULT_BUNDLE.base == BASE_INSTRUCTIONS


def test_dual_channel_appends_the_lane_overlay():
    bundle = DEFAULT_BUNDLE
    urgent = bundle.instructions_for("urgent", CHANNEL_DUAL)
    normal = bundle.instructions_for("normal", CHANNEL_DUAL)
    assert URGENT_OVERLAY.strip() in urgent
    assert NORMAL_OVERLAY.strip() in normal
    assert urgent != normal


def test_single_channel_uses_only_the_base():
    assert DEFAULT_BUNDLE.instructions_for("urgent", CHANNEL_SINGLE) == BASE_INSTRUCTIONS


def test_the_module_helper_still_uses_the_builtin_bundle():
    assert instructions_for("urgent", CHANNEL_DUAL) == (
        DEFAULT_BUNDLE.instructions_for("urgent", CHANNEL_DUAL))


def test_a_custom_bundle_replaces_the_text():
    bundle = PromptBundle(
        revision_id="rev-1", base="自定义 base", urgent="自定义 urgent",
        normal="自定义 normal")
    assembled = bundle.instructions_for("urgent", CHANNEL_DUAL)
    assert "自定义 base" in assembled
    assert "自定义 urgent" in assembled
    assert BASE_INSTRUCTIONS not in assembled


# --- validation ----------------------------------------------------------

def test_all_three_sections_are_required():
    for missing in ("base", "urgent", "normal"):
        sections = {"base": "甲", "urgent": "乙", "normal": "丙"}
        sections[missing] = "   "
        with pytest.raises(PromptRejected) as excinfo:
            validate_sections(**sections)
        assert missing in str(excinfo.value)


def test_a_non_string_section_is_rejected():
    with pytest.raises(PromptRejected):
        validate_sections("甲", 42, "丙")


def test_an_oversized_section_is_rejected():
    with pytest.raises(PromptRejected) as excinfo:
        validate_sections("甲" * (MAX_SECTION_CHARS + 1), "乙", "丙")
    assert str(MAX_SECTION_CHARS) in str(excinfo.value)


def test_validation_trims_but_keeps_content():
    base, urgent, normal = validate_sections("  甲  ", "乙\n", "\n丙")
    assert (base, urgent, normal) == ("甲", "乙", "丙")


def test_a_stored_revision_that_no_longer_validates_falls_back():
    """A bad row in the database must not brick the plugin."""
    bundle = bundle_from_revision(
        {"revision_id": "rev-x", "base": "", "urgent": "乙", "normal": "丙"})
    assert bundle.revision_id == BUILTIN_REVISION_ID


def test_no_revision_means_the_builtin_bundle():
    assert bundle_from_revision(None) is DEFAULT_BUNDLE


# --- persistence ---------------------------------------------------------

def test_saving_a_revision_makes_it_active(store):
    saved = store.save_revision(base="甲", urgent="乙", normal="丙", note="第一版")
    active = store.get_active_revision()
    assert active["revision_id"] == saved["revision_id"]
    assert active["base"] == "甲"
    assert active["note"] == "第一版"


def test_saving_again_deactivates_the_previous_revision(store):
    first = store.save_revision(base="甲1", urgent="乙", normal="丙")
    second = store.save_revision(base="甲2", urgent="乙", normal="丙")
    assert store.get_active_revision()["revision_id"] == second["revision_id"]
    assert store.get_revision(first["revision_id"])["active"] is False


def test_only_the_newest_revisions_are_kept(store):
    keep = 5
    ids = [
        store.save_revision(base=f"甲{index}", urgent="乙", normal="丙", keep=keep)[
            "revision_id"]
        for index in range(keep + 4)
    ]
    revisions = store.list_revisions()
    assert len(revisions) == keep
    kept = {row["revision_id"] for row in revisions}
    assert kept == set(ids[-keep:])


def test_the_default_keeps_twenty_revisions():
    assert WowsConfig().prompt_revisions_kept == 20


def test_rolling_back_reactivates_an_older_revision(store):
    first = store.save_revision(base="甲1", urgent="乙", normal="丙")
    store.save_revision(base="甲2", urgent="乙", normal="丙")

    restored = store.activate_revision(first["revision_id"])
    assert restored["revision_id"] == first["revision_id"]
    assert store.get_active_revision()["base"] == "甲1"


def test_activating_an_unknown_revision_reports_nothing(store):
    assert store.activate_revision("rev-missing") is None


def test_resetting_drops_every_revision(store):
    store.save_revision(base="甲", urgent="乙", normal="丙")
    store.reset_revisions()
    assert store.list_revisions() == []
    assert store.get_active_revision() is None
    assert bundle_from_revision(store.get_active_revision()) is DEFAULT_BUNDLE


def test_revision_summaries_report_section_lengths(store):
    store.save_revision(base="甲" * 10, urgent="乙" * 5, normal="丙" * 3)
    summary = store.list_revisions()[0]
    assert summary["lengths"] == {"base": 10, "urgent": 5, "normal": 3}


# --- atomic swap ---------------------------------------------------------

def test_a_request_carries_the_revision_that_built_it():
    bundle = PromptBundle(revision_id="rev-7", base="甲", urgent="乙", normal="丙")
    request = WowsPromptRouter(CFG).build(
        candidate(),
        PromptProfile(channel_mode=CHANNEL_DUAL, dry_run=True, bundle=bundle))
    assert request.metadata["prompt_revision"] == "rev-7"


def test_the_builtin_revision_is_reported_when_nothing_is_customized():
    request = WowsPromptRouter(CFG).build(
        candidate(), PromptProfile(channel_mode=CHANNEL_DUAL, dry_run=True))
    assert request.metadata["prompt_revision"] == BUILTIN_REVISION_ID


def test_swapping_the_bundle_cannot_change_a_request_already_built():
    """The profile captures the bundle, so one request uses exactly one revision."""
    old = PromptBundle(revision_id="rev-old", base="旧 base", urgent="旧 u",
                       normal="旧 n")
    new = PromptBundle(revision_id="rev-new", base="新 base", urgent="新 u",
                       normal="新 n")
    profile = PromptProfile(channel_mode=CHANNEL_DUAL, dry_run=True, bundle=old)

    router = WowsPromptRouter(CFG)
    first = router.build(candidate(), profile)
    # A swap happens; the already-captured profile is unaffected.
    second = router.build(
        candidate(),
        PromptProfile(channel_mode=CHANNEL_DUAL, dry_run=True, bundle=new))
    third = router.build(candidate(), profile)

    assert "旧 base" in first.text
    assert "新 base" in second.text
    assert "旧 base" in third.text
    assert third.metadata["prompt_revision"] == "rev-old"


def test_one_frame_never_mixes_two_revisions():
    """Both lanes in a single frame come from the same captured bundle."""
    bundle = PromptBundle(revision_id="rev-1", base="共同 base",
                          urgent="紧急段", normal="常规段")
    profile = PromptProfile(channel_mode=CHANNEL_DUAL, dry_run=True, bundle=bundle)
    router = WowsPromptRouter(CFG)
    urgent = router.build(candidate(LOW_HEALTH), profile)
    normal = router.build(candidate(DAMAGE_MILESTONE), profile)
    assert "共同 base" in urgent.text and "共同 base" in normal.text
    assert urgent.metadata["prompt_revision"] == normal.metadata["prompt_revision"]


def test_channel_mode_does_not_change_timing():
    dual = WowsConfig.from_mapping({"channel_mode": CHANNEL_DUAL})
    single = WowsConfig.from_mapping({"channel_mode": CHANNEL_SINGLE})
    for lane in ("urgent", "normal"):
        assert dual.ttl_for(lane) == single.ttl_for(lane)
        assert dual.min_gap_for(lane) == single.min_gap_for(lane)


# --- preview -------------------------------------------------------------

def test_building_a_preview_never_touches_the_dispatcher():
    """The lab goes straight to the router, so a preview cannot become a message."""
    from plugin.plugins.neko_wows.adapters.neko_dispatcher import NekoDispatcher

    class CountingHost:
        def __init__(self):
            self.calls = []

        def push_message(self, **kwargs):
            self.calls.append(kwargs)

    host = CountingHost()
    cfg = WowsConfig()
    cfg.dry_run = False  # even with real output enabled
    dispatcher = NekoDispatcher(host, cfg)

    request = WowsPromptRouter(cfg).build(
        candidate(),
        PromptProfile(channel_mode=CHANNEL_DUAL, dry_run=True,
                      bundle=PromptBundle(revision_id="draft", base="甲",
                                          urgent="乙", normal="丙")))

    assert request.text
    assert host.calls == []
    assert dispatcher.stats()["host_calls"] == 0
