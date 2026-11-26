"""Panel / privacy-page data: risk tiers, reconciliation, counts, last use,
privacy groups, the pending list, the grants-reset marker and the decision
routes."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from plugin.core import capability_analysis
from plugin.sdk.shared.permissions import catalog
from plugin.server.application.plugins import call_audit, permission_panel, permission_service
from plugin.server.application.plugins.permission_usage import UsageIndex
from plugin.server.infrastructure.plugin_permissions import (
    GRANTS_RESET,
    LEGACY_DENIED,
    PluginPermissionStore,
    PluginSource,
)

pytestmark = pytest.mark.plugin_unit

MEMORY = "plugin_sdk.main_agent.memory:read"
CONVERSATIONS = "plugin_sdk.main_agent.conversations:read"
PUSH = "plugin_sdk.main_agent.messages:push"
MODELS = "plugin_sdk.models:invoke"
CONNECT = "system.network:connect"
LISTEN = "system.network:listen"
SPAWN = "system.process:spawn"
ENV = "system.env:read"
FS_READ = "system.fs:read"
FS_WRITE = "system.fs:write"

MARKET = PluginSource("market", "abcdef0123456789")
MANUAL = PluginSource("manual")
# One clock for fixed-time indexes and for entries fed to the panel fixture
# (whose store and index use the real time): fixed when the module loads.
NOW = datetime.now(timezone.utc).replace(microsecond=0)


class ConfigManager:
    def __init__(self, root: Path):
        self.root = root

    def get_runtime_config_path(self, name):
        return self.root / name

    def save_json_config(self, name, data):
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / name).write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def store(tmp_path):
    return PluginPermissionStore(ConfigManager(tmp_path / "config"))


def _ts(delta: timedelta = timedelta()) -> str:
    return (NOW - delta).isoformat()


def _index(tmp_path, *, created=NOW) -> UsageIndex:
    directory = tmp_path / "audit"
    directory.mkdir(exist_ok=True)
    index = UsageIndex(now=lambda: created, directory=str(directory))
    return index


# ---------------------------------------------------------------------------
# 1. Risk tiers: one table
# ---------------------------------------------------------------------------


def test_every_permission_has_the_tier_of_the_requirement():
    assert dict(catalog.PERMISSION_TIERS) == {
        CONNECT: "high",
        SPAWN: "high",
        FS_WRITE: "high",
        ENV: "high",
        MEMORY: "high",
        CONVERSATIONS: "high",
        FS_READ: "medium",
        PUSH: "medium",
        LISTEN: "low",
        MODELS: "low",
    }
    assert catalog.RISK_TIERS == ("high", "medium", "low", "safe")


@pytest.mark.parametrize(
    ("tier", "count", "collapsed"),
    [("high", 9, False), ("medium", 3, False), ("medium", 4, True), ("low", 0, False), ("low", 4, True)],
)
def test_high_tier_never_folds_others_fold_above_three(tier, count, collapsed):
    assert catalog.tier_collapsed(tier, count) is collapsed


def test_native_loader_is_the_high_risk_blind_spot_in_both_places():
    assert dict(catalog.BLIND_SPOT_TIERS) == {"native_loader": "high"}
    assert capability_analysis.HIGH_RISK_BLIND_SPOT_TYPES == frozenset({"native_loader"})


def test_catalog_payload_is_what_the_ui_reads():
    payload = permission_panel.catalog()
    tiers = {row["tier"]: row for row in payload["tiers"]}
    assert tiers["high"]["always_expanded"] is True and tiers["high"]["collapse_over"] is None
    assert tiers["medium"]["collapse_over"] == 3
    assert tiers["safe"]["listed"] is False
    assert payload["permissions"][ENV] == {
        "tier": "high", "enforced": False, "lock": False, "privacy_group": "process_env",
    }
    groups = {row["group"]: row for row in payload["privacy_groups"]}
    assert groups["memory"]["partial_block"] and groups["conversations"]["partial_block"]
    assert not any(row["partial_block"] for key, row in groups.items() if key not in ("memory", "conversations"))
    assert groups["process_env"]["permissions"] == [SPAWN, ENV]
    assert groups["fs_read"]["tier"] == "medium" and groups["models"]["tier"] == "low"


# ---------------------------------------------------------------------------
# Grants-reset marker
# ---------------------------------------------------------------------------


def test_source_change_that_drops_decisions_marks_a_reset(store):
    store.decide("x", MEMORY, "granted", source=MARKET)

    record = store.sync_source("x", MANUAL)

    assert record[GRANTS_RESET]["reason"] == "source_changed"
    assert record[GRANTS_RESET]["at"] == record["source_changed_at"]


def test_source_change_without_decisions_is_no_reset(store):
    store.sync_source("x", MARKET)  # no record: nothing to bind
    store.decide("x", MEMORY, "granted", source=MARKET)
    store.clear("x", MEMORY)

    record = store.sync_source("x", MANUAL)

    assert GRANTS_RESET not in record


def test_view_shows_the_reset_before_it_is_persisted(store):
    store.decide("x", MEMORY, "granted", source=MARKET)

    assert GRANTS_RESET in store.view("x", MANUAL)
    assert GRANTS_RESET not in store.read("x")


@pytest.mark.parametrize("act", ["decide", "clear", "legacy"])
def test_next_decision_clears_the_reset(store, act):
    store.decide("x", MEMORY, "granted", source=MARKET)
    store.decide("x", CONNECT, "granted", source=MARKET)
    store.sync_source("x", MANUAL)
    store.decide("x", CONNECT, "granted", source=MANUAL)  # put one back to clear
    store.sync_source("x", MARKET)
    assert GRANTS_RESET in store.read("x")

    if act == "decide":
        store.decide("x", FS_READ, "denied", source=MARKET)
    elif act == "clear":
        store.decide("x", FS_READ, "denied", source=MARKET)
        store.sync_source("x", MANUAL)
        assert GRANTS_RESET in store.read("x")
        store.clear("x", MEMORY)  # nothing stored: no write, marker stays
        assert GRANTS_RESET in store.read("x")
        store.decide("x", MEMORY, "granted", source=MANUAL)
        store.clear("x", MEMORY)
    else:
        store.apply_legacy_decision("x", "approved", ["memory.read"], source=MARKET)

    assert GRANTS_RESET not in store.read("x")


def test_drop_grants_keeps_a_record_holding_only_the_reset(store):
    store.decide("x", MEMORY, "granted", source=MARKET)

    store.drop_grants("x")

    record = store.read("x")
    assert record["permissions"] == {}
    assert record[GRANTS_RESET]["reason"] == "source_changed"


def test_drop_grants_of_an_empty_record_still_removes_it(store):
    store.decide("x", MEMORY, "granted", source=MARKET)
    store.clear("x", MEMORY)

    store.drop_grants("x")

    assert store.read("x") == {}


# ---------------------------------------------------------------------------
# 4. Last use from the call audit
# ---------------------------------------------------------------------------


def _decision(plugin, capability, *, verdict="allow", enforcing=False, reason=None, ts=None, origin="ui_action",
              flags=(), blocked_by=None, call_id=None):
    if verdict == "would_deny" and blocked_by is None:
        blocked_by = [plugin]
    return {
        "ts": ts or _ts(), "kind": "decision", "plugin": plugin, "capability": capability,
        "verdict": verdict, "enforcing": enforcing, "reason": reason, "origin_kind": origin, "flags": list(flags),
        "blocked_by": blocked_by or [], "chain": [plugin], "call_id": call_id,
    }


def _system_use(plugin, capability, *, target="api.example.com", verdict="allow", enforcing=False, reason=None, ts=None):
    return {
        "ts": ts or _ts(), "kind": "system_use", "plugin": plugin, "capability": capability, "target": target,
        "verdict": verdict, "enforcing": enforcing, "reason": reason, "flags": ["plugin_reported"],
    }


def test_outcomes_tell_would_block_from_blocked(tmp_path):
    index = _index(tmp_path)
    index.observe(_decision("a", MEMORY, verdict="would_deny", reason="undecided", ts=_ts(timedelta(hours=2))))
    index.observe(_decision("a", CONNECT, verdict="would_deny", enforcing=True, reason="denied"))
    index.observe(_decision("a", MODELS))

    assert index.last_use("a", MEMORY).outcome == "would_block"
    assert index.last_use("a", CONNECT).outcome == "blocked"
    assert index.last_use("a", MODELS).outcome == "allowed"
    # A refused use is no use: it neither counts nor is the last effective one.
    assert index.count("a", CONNECT, 7) == 0
    assert index.last_effective_use("a", CONNECT) is None


def test_system_use_and_legacy_names_are_indexed(tmp_path):
    index = _index(tmp_path)
    index.observe(_system_use("a", CONNECT, ts=_ts(timedelta(minutes=5))))
    index.observe(_system_use("a", FS_READ, target="<data>/x"))
    index.observe(_decision("b", "memory.read"))

    assert index.last_use("a", CONNECT).target == "api.example.com"
    assert index.last_use("a", FS_READ).target is None  # only network targets are kept
    assert index.last_use("b", MEMORY) is not None


def test_allow_summary_counts_go_to_the_called_plugin_with_the_call_origin(tmp_path):
    index = _index(tmp_path)
    index.observe({"ts": _ts(), "kind": "forward", "call_id": "c1", "origin_kind": "chat_tool", "target": "a"})
    index.observe({"ts": _ts(), "kind": "allow_summary", "call_id": "c1", "target": "a", "counts": {MEMORY: 3, "bogus": 2}})

    use = index.last_use("a", MEMORY)
    assert use.origin_kind == "chat_tool"
    assert index.count("a", MEMORY, 7) == 3
    assert index.plugins_using([MEMORY]) == {"a"}


def test_unattributed_decision_has_no_origin(tmp_path):
    index = _index(tmp_path)
    index.observe(_decision("a", MEMORY, flags=["unattributed"]))
    assert index.last_use("a", MEMORY).origin_kind is None


def test_counts_cover_the_requested_days_only(tmp_path):
    index = _index(tmp_path)
    index.observe(_decision("a", MEMORY, ts=_ts(timedelta(days=1))))
    index.observe(_decision("a", MEMORY, ts=_ts(timedelta(days=6))))
    index.observe(_decision("a", MEMORY, ts=_ts(timedelta(days=8))))

    assert index.count("a", MEMORY, 7) == 2
    assert index.count("a", MEMORY, 30) == 3


def test_files_are_read_once_and_only_for_entries_before_the_index(tmp_path):
    index = _index(tmp_path)
    old = _decision("a", MEMORY, ts=_ts(timedelta(days=40)))
    # Written to the file after the index was created: it arrives through the
    # observer as well, so the file copy must not count twice.
    live = _decision("a", MEMORY, ts=_ts(timedelta(seconds=-1)))
    lines = [json.dumps(old), "not json", json.dumps(live)]
    (tmp_path / "audit" / "call_audit.jsonl.1").write_text(
        json.dumps({"ts": _ts(timedelta(days=50)), "kind": "usage_coverage_start"}) + "\n"
        + json.dumps(_decision("a", CONNECT, ts=_ts(timedelta(days=49)))) + "\n"
    )
    (tmp_path / "audit" / "call_audit.jsonl").write_text("\n".join(lines) + "\n")
    index.observe(live)

    assert index.history_since() == NOW - timedelta(days=50)
    assert index.count("a", MEMORY, 1) == 1
    assert index.covers_days(30) is True
    (tmp_path / "audit" / "call_audit.jsonl").write_text(json.dumps(_decision("z", MEMORY, ts=_ts(timedelta(days=2)))))
    assert index.plugins_using([MEMORY]) == {"a"}  # not read again


def test_refused_uses_are_collected(tmp_path):
    index = _index(tmp_path)
    index.observe(_system_use("a", SPAWN, verdict="would_deny", reason="undecided", ts=_ts(timedelta(hours=1))))
    index.observe(_system_use("a", SPAWN, verdict="would_deny", reason="undecided"))
    index.observe(_system_use("a", FS_WRITE))

    assert index.refused_uses() == {("a", SPAWN): (NOW, 2)}


def test_a_chain_refusal_counts_for_the_caller_that_lacked_it(tmp_path):
    index = _index(tmp_path)
    index.observe(_decision("b", MEMORY, verdict="would_deny", reason="caller_chain", blocked_by=["a"]))

    assert index.refused_uses() == {("a", MEMORY): (NOW, 1)}
    assert index.last_use("b", MEMORY).outcome == "would_block"


def test_allowed_decision_inside_a_call_is_not_counted_twice(tmp_path):
    index = _index(tmp_path)
    index.observe({"ts": _ts(), "kind": "forward", "call_id": "c1", "origin_kind": "chat_tool", "target": "a"})
    index.observe(_decision("a", MEMORY, flags=["consent_unavailable"], call_id="c1"))
    index.observe({"ts": _ts(), "kind": "allow_summary", "call_id": "c1", "target": "a", "counts": {MEMORY: 1}})
    index.observe(_decision("a", MEMORY, flags=["unattributed"]))

    assert index.count("a", MEMORY, 1) == 2


def test_recent_keeps_the_newest_and_is_sorted(tmp_path):
    index = _index(tmp_path)
    for minute in range(120):
        index.observe(_decision("a", MEMORY, ts=_ts(timedelta(minutes=minute))))
    recent = index.recent([MEMORY])
    assert len(recent) == 50
    assert recent[0].at == NOW and recent[-1].at == NOW - timedelta(minutes=49)


def test_call_audit_feeds_observers(monkeypatch):
    seen = []
    call_audit.add_observer(seen.append)
    monkeypatch.setattr(call_audit, "_audit_logger_locked", lambda: None)
    try:
        call_audit.record({"kind": "decision", "plugin": "a", "capability": MEMORY})
    finally:
        call_audit.remove_observer(seen.append)
    assert seen and seen[0]["plugin"] == "a" and "ts" in seen[0]


def test_a_failing_observer_does_not_break_recording(monkeypatch):
    def broken(_entry):
        raise RuntimeError("boom")

    call_audit.add_observer(broken)
    monkeypatch.setattr(call_audit, "_audit_logger_locked", lambda: None)
    try:
        call_audit.record({"kind": "decision", "plugin": "a", "capability": MEMORY})
    finally:
        call_audit.remove_observer(broken)


# ---------------------------------------------------------------------------
# Panel, pending list and privacy page
# ---------------------------------------------------------------------------


MANIFEST = """
[plugin]
id = "{pid}"

[permissions.require]
"system.network:connect" = {{ reason = "download", hosts = ["api.example.com"] }}
"system.fs:read" = {{ reason = {{ en = "import", zh-CN = "导入" }} }}

[permissions.ask]
"plugin_sdk.main_agent.memory:read" = {{ reason = "city", background = true, background_reason = "daily" }}
"plugin_sdk.main_agent.messages:push" = {{ reason = "send" }}
"""


def _finding(file, line, basis="resolved_call"):
    return {"file": file, "line": line, "method": "m", "basis": basis}


_KEYS = {perm.replace(".", "_").replace(":", "__"): perm for perm in catalog.PERMISSION_IDS}


def _report(**permissions):
    return {
        "status": "completed",
        "_analyzer_version": 8,
        "analyzed_at": _ts(),
        "permissions": {_KEYS[key]: value for key, value in permissions.items()},
        "blind_spots": [{"type": "native_loader", "file": "core/compat.py", "line": 7}],
        "blind_spot_files": [{"file": "core/compat.py", "count": 1, "types": {"native_loader": 1}, "high_risk": True}],
        "not_analyzed": [
            {"reason": "test", "file": "tests"},
            {"reason": "frontend", "file": "ui/a.vue"},
            {"reason": "vendor", "file": None, "omitted": 5},  # a list cut short
            {"reason": "budget_exceeded", "file": None},  # scan-wide, not a file
        ],
    }


class Env:
    def __init__(self, monkeypatch, tmp_path, store):
        self.tmp_path = tmp_path
        self.store = store
        self.metas: dict[str, dict] = {}
        self.reports: dict[str, dict] = {}
        self.index = _index(tmp_path, created=datetime.now(timezone.utc))
        monkeypatch.setattr(permission_service, "permission_store", store)
        monkeypatch.setattr(permission_service, "current_source", lambda pid: self.sources.get(pid))
        monkeypatch.setattr(permission_panel, "_plugin_metas", lambda: {k: dict(v) for k, v in self.metas.items()})
        monkeypatch.setattr(
            permission_panel, "read_report",
            lambda pid, root, version, verify_revision=True: self.reports.get(pid, {"status": "unavailable"}),
        )
        monkeypatch.setattr(permission_panel, "usage_index", self.index)
        monkeypatch.setattr(permission_panel, "_checks_off", lambda: False)
        self.sources: dict[str, PluginSource] = {}

    def plugin(self, pid, manifest=MANIFEST, report=None, name=None):
        root = self.tmp_path / "plugins" / pid
        root.mkdir(parents=True, exist_ok=True)
        if manifest is not None:
            (root / "plugin.toml").write_text(manifest.format(pid=pid), encoding="utf-8")
        self.metas[pid] = {"config_path": str(root / "plugin.toml"), "version": "1.0", "name": name or pid}
        if report is not None:
            self.reports[pid] = report


@pytest.fixture
def env(monkeypatch, tmp_path, store):
    return Env(monkeypatch, tmp_path, store)


def _weather(env):
    env.plugin(
        "weather",
        report=_report(
            system_network__connect={"declared": "require", "findings": [_finding("api.py", 18), _finding("geo.py", 9)], "basis": "resolved_call"},
            system_fs__read={"declared": "require", "findings": [], "basis": "declared_only"},
            plugin_sdk_main_agent_memory__read={"declared": "ask", "findings": [_finding("profile.py", 31)], "basis": "resolved_call"},
            system_env__read={"declared": None, "findings": [_finding("config.py", 12, "resolved_reference"), _finding("config.py", 13, "resolved_reference")], "basis": "resolved_reference"},
            system_fs__write={"declared": None, "findings": [_finding("cache.py", 3, "suffix_match")], "basis": "suffix_match"},
        ),
    )


def test_panel_reconciles_declaration_and_scan(env):
    _weather(env)
    env.sources["weather"] = MARKET
    env.store.decide("weather", CONNECT, "granted", source=MARKET)
    env.store.decide("weather", FS_READ, "granted", source=MARKET)
    env.store.decide("weather", MEMORY, "granted", condition="anytime", source=MARKET)

    panel = permission_panel.plugin_panel("weather", index=env.index)

    rows = {row["permission"]: row for row in panel["rows"]}
    assert rows[CONNECT]["reconcile"] == "matched"
    assert rows[ENV]["reconcile"] == "undeclared"
    assert rows[FS_READ]["reconcile"] == "unscanned"
    assert rows[PUSH]["reconcile"] == "unscanned"
    assert rows[CONNECT]["scan"] == {"files": 2, "sites": 2, "basis": "resolved_call", "possible_only": False}
    assert rows[FS_WRITE]["scan"]["possible_only"] is True
    assert rows[CONNECT]["declared"]["hosts"] == ["api.example.com"]
    assert rows[FS_READ]["declared"]["reason"] == {"en": "import", "zh-CN": "导入"}
    assert rows[MEMORY]["condition"] == "anytime" and rows[MEMORY]["condition_basis"] == "plugin_reported"
    assert rows[PUSH]["enforced"] is False
    # Ordered by tier, then by catalogue order.
    assert [row["tier"] for row in panel["rows"]] == sorted(
        (row["tier"] for row in panel["rows"]), key=catalog.tier_rank
    )
    assert panel["counts"] == {"total": 6, "granted": 3, "denied": 0, "undecided": 3, "undeclared_found": 2}
    assert {t["tier"]: (t["count"], t["collapsed"]) for t in panel["tiers"]} == {
        "high": (4, False), "medium": (2, False), "low": (0, False),
    }
    assert panel["source"] == {"channel": "market", "fingerprint8": "abcdef01", "confirmed": True}


def test_panel_without_finished_scan_says_so(env):
    env.plugin("p", report={"status": "running"})
    rows = {row["permission"]: row for row in permission_panel.plugin_panel("p", index=env.index)["rows"]}
    assert rows[CONNECT]["reconcile"] == "scan_pending"
    assert rows[CONNECT]["scan"] is None


def test_panel_user_view_has_blind_spots_by_file_without_lines(env):
    _weather(env)
    panel = permission_panel.plugin_panel("weather", index=env.index)

    assert panel["blind_spots"] == {
        "files": [{"file": "core/compat.py", "count": 1, "types": {"native_loader": 1}, "high_risk": True}],
        "counted": 1,
        "not_analyzed_files": 7,
    }
    assert "line" not in json.dumps(panel["blind_spots"])
    assert panel["developer"]["blind_spots"] == [{"type": "native_loader", "file": "core/compat.py", "line": 7}]
    assert panel["developer"]["findings"][ENV][0]["line"] == 12
    assert set(panel["developer"]["manifest_permissions"]) == {"require", "ask"}


def test_panel_marks_legacy_approvals_and_resets(env):
    env.plugin("old", manifest=None, report=_report(
        plugin_sdk_main_agent_memory__read={"declared": None, "findings": [_finding("a.py", 1)], "basis": "resolved_call"},
    ))
    env.store.apply_legacy_decision("old", "approved", ["memory.read"], source=MARKET)
    rows = {row["permission"]: row for row in permission_panel.plugin_panel("old", index=env.index)["rows"]}
    assert rows[MEMORY]["legacy_any"] is True
    assert permission_panel.plugin_panel("old", index=env.index)["grants_reset"] is None

    env.sources["old"] = MANUAL
    panel = permission_panel.plugin_panel("old", index=env.index)
    assert panel["grants_reset"]["reason"] == "source_changed"
    assert panel["counts"]["granted"] == 0


def test_panel_last_used_and_unused_30_days(env, monkeypatch):
    _weather(env)
    env.store.decide("weather", CONNECT, "granted", source=MARKET)
    env.store.decide("weather", FS_READ, "granted", source=MARKET)
    old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    record = env.store.read("weather")
    for entry in record["permissions"].values():
        entry["decided_at"] = old
    env.store.restore("weather", record)
    now = datetime.now(timezone.utc)
    env.index.observe({"ts": (now - timedelta(days=45)).isoformat(), "kind": "usage_coverage_start"})
    env.index.observe(_system_use("weather", CONNECT, ts=(now - timedelta(days=2)).isoformat()))

    rows = {row["permission"]: row for row in permission_panel.plugin_panel("weather", index=env.index)["rows"]}
    assert rows[CONNECT]["last_used"]["outcome"] == "allowed"
    assert rows[CONNECT]["unused_30d"] is False
    assert rows[FS_READ]["unused_30d"] is True
    assert rows[ENV]["unused_30d"] is None  # not granted


def test_unused_is_unknown_while_the_history_is_short(env):
    _weather(env)
    env.store.decide("weather", FS_READ, "granted", source=MARKET)
    rows = {row["permission"]: row for row in permission_panel.plugin_panel("weather", index=env.index)["rows"]}
    assert rows[FS_READ]["unused_30d"] is None


def test_pending_list(env):
    _weather(env)  # declared, nothing decided -> to confirm
    env.plugin("upgraded", report=_report())
    env.store.decide("upgraded", MEMORY, "granted", source=MARKET)
    env.plugin("legacy", manifest="[plugin]\nid='legacy'\n", report=_report(
        system_process__spawn={"declared": None, "findings": [_finding("a.py", 1)], "basis": "resolved_call"},
    ))
    env.index.observe(_system_use("legacy", SPAWN, verdict="would_deny", reason="undecided"))

    result = permission_panel.pending(index=env.index)

    assert result["to_confirm"] == [{"plugin_id": "weather", "items": [CONNECT, FS_WRITE, ENV, FS_READ]}]
    assert result["upgraded"] == [{"plugin_id": "upgraded", "items": [CONNECT, FS_READ]}]
    assert result["inferred"] == [{"plugin_id": "legacy", "permission": SPAWN}]
    assert [(row["plugin_id"], row["permission"], row["count"]) for row in result["first_use"]] == [("legacy", SPAWN, 1)]


def test_first_use_is_dropped_once_decided(env):
    env.plugin("legacy", manifest=None, report=_report(
        system_process__spawn={"declared": None, "findings": [_finding("a.py", 1)], "basis": "resolved_call"},
    ))
    env.index.observe(_system_use("legacy", SPAWN, verdict="would_deny", reason="undecided"))
    env.store.decide("legacy", SPAWN, "denied")
    assert permission_panel.pending(index=env.index)["first_use"] == []


def test_privacy_overview_groups_and_combinations(env):
    _weather(env)
    env.store.decide("weather", CONNECT, "granted", source=MARKET)
    env.store.decide("weather", MEMORY, "granted", source=MARKET)
    env.plugin("diary", report=_report())
    env.store.decide("diary", MEMORY, "granted", source=MARKET)
    env.index.observe(_decision("diary", MEMORY))

    overview = permission_panel.privacy_overview(index=env.index)

    groups = {row["group"]: row for row in overview["groups"]}
    assert groups["memory"]["plugins"] == 2
    assert groups["memory"]["latest"]["plugin_id"] == "diary"
    assert groups["memory"]["partial_block"] is True
    # Disclosure-only: declaring it is enough to be able to use it.
    assert groups["messages"]["plugins"] == 2
    assert groups["process_env"]["plugins"] == 1  # weather reads env (found), not enforced
    assert groups["network"]["plugins"] == 1
    assert overview["combos"] == [{"plugin_id": "weather", "reads": [MEMORY], "sends": [CONNECT]}]
    assert overview["pending"]["upgraded"] == 2


def test_privacy_group_detail_lists_plugins_and_recent_records(env):
    _weather(env)
    env.store.decide("weather", MEMORY, "granted", condition="anytime", source=MARKET)
    env.store.apply_legacy_decision("weather", "approved", ["memory.read"], source=MARKET)
    env.index.observe(_decision("weather", MEMORY))
    env.index.observe(_decision("translate", MEMORY, verdict="would_deny", reason="not_declared"))

    detail = permission_panel.privacy_group("memory", index=env.index)

    plugins = {row["plugin_id"]: row for row in detail["plugins"]}
    assert plugins["weather"]["has_permission"] is True
    assert plugins["weather"]["items"][0]["uses_7d"] == 1
    assert plugins["translate"]["has_permission"] is False and plugins["translate"]["installed"] is False
    assert [row["outcome"] for row in detail["recent"]] == ["would_block", "allowed"] or [
        row["outcome"] for row in detail["recent"]
    ] == ["allowed", "would_block"]
    assert detail["partial_block"] is True


def test_unknown_group_and_plugin_are_404(env):
    from plugin.server.domain.errors import ServerDomainError

    with pytest.raises(ServerDomainError) as group:
        permission_panel.privacy_group("nope", index=env.index)
    assert group.value.status_code == 404
    with pytest.raises(ServerDomainError) as plugin:
        permission_panel.plugin_panel("nope", index=env.index)
    assert plugin.value.code == "PLUGIN_NOT_FOUND"


# ---------------------------------------------------------------------------
# Decision routes
# ---------------------------------------------------------------------------


async def _call(method: str, path: str, body=None):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from plugin.server.infrastructure.mutation_auth import require_plugin_mutation_access
    from plugin.server.routes import plugin_permissions as routes

    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[require_plugin_mutation_access] = lambda: None
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        return await client.request(method, path, json=body)


@pytest.mark.asyncio
async def test_put_and_delete_decide_one_permission(env):
    _weather(env)

    put = await _call("PUT", f"/plugin-permissions/plugins/weather/permissions/{CONNECT}", {"state": "granted"})
    assert put.status_code == 200, put.text
    rows = {row["permission"]: row for row in put.json()["rows"]}
    assert rows[CONNECT]["state"] == "granted" and rows[CONNECT]["condition"] == "in_use"

    deleted = await _call("DELETE", f"/plugin-permissions/plugins/weather/permissions/{CONNECT}")
    assert deleted.status_code == 200
    assert {row["permission"]: row for row in deleted.json()["rows"]}[CONNECT]["state"] == "undecided"


@pytest.mark.asyncio
async def test_anytime_needs_a_declared_background(env):
    _weather(env)

    refused = await _call(
        "PUT", f"/plugin-permissions/plugins/weather/permissions/{CONNECT}", {"state": "granted", "condition": "anytime"}
    )
    assert refused.status_code == 422
    assert refused.headers["X-Error-Code"] == "PERMISSION_BACKGROUND_NOT_DECLARED"

    allowed = await _call(
        "PUT", f"/plugin-permissions/plugins/weather/permissions/{MEMORY}", {"state": "granted", "condition": "anytime"}
    )
    assert allowed.status_code == 200


@pytest.mark.asyncio
async def test_anytime_is_allowed_for_inferred_items(env):
    env.plugin("legacy", manifest=None, report=_report())
    response = await _call(
        "PUT", f"/plugin-permissions/plugins/legacy/permissions/{SPAWN}", {"state": "granted", "condition": "anytime"}
    )
    assert response.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "body", "status"),
    [
        (f"/plugin-permissions/plugins/nope/permissions/{CONNECT}", {"state": "granted"}, 404),
        ("/plugin-permissions/plugins/weather/permissions/bogus", {"state": "granted"}, 404),
        (f"/plugin-permissions/plugins/weather/permissions/{CONNECT}", {"state": "once"}, 422),
    ],
)
async def test_put_rejects_unknown_input(env, path, body, status):
    _weather(env)
    response = await _call("PUT", path, body)
    assert response.status_code == status
    assert env.store.read("weather") == {}


@pytest.mark.asyncio
async def test_read_routes_respond(env):
    _weather(env)
    for path in ("/plugin-permissions/catalog", "/plugin-permissions/pending", "/plugin-permissions/privacy",
                 "/plugin-permissions/privacy/memory", "/plugin-permissions/plugins/weather/panel"):
        response = await _call("GET", path)
        assert response.status_code == 200, (path, response.text)


def test_writes_require_mutation_access():
    from plugin.server.infrastructure.mutation_auth import require_plugin_mutation_access
    from plugin.server.routes import plugin_permissions as routes

    for route in routes.router.routes:
        if route.methods & {"PUT", "DELETE", "POST"}:
            assert any(dep.call is require_plugin_mutation_access for dep in route.dependant.dependencies), route.path


def test_legacy_denied_record_reads_as_denied_in_the_panel(env):
    env.plugin("d", manifest=None, report=_report(
        plugin_sdk_main_agent_memory__read={"declared": None, "findings": [_finding("a.py", 1)], "basis": "resolved_call"},
    ))
    env.store.apply_legacy_decision("d", "denied", source=MARKET)
    assert env.store.read("d")[LEGACY_DENIED]
    rows = {row["permission"]: row for row in permission_panel.plugin_panel("d", index=env.index)["rows"]}
    assert rows[MEMORY]["state"] == "denied"


def test_recent_memory_stays_bounded(tmp_path):
    index = _index(tmp_path)
    for minute in range(500):
        index.observe(_decision("a", MEMORY, ts=_ts(timedelta(minutes=minute))))
    assert len(index._recent[MEMORY]) <= 2 * 50


def test_undeclared_non_system_item_needs_confirmation(env):
    env.plugin("p", report=_report(
        plugin_sdk_models__invoke={"declared": None, "findings": [_finding("a.py", 1)], "basis": "resolved_call"},
    ))
    items = permission_panel.pending(index=env.index)["to_confirm"][0]["items"]
    assert MODELS in items
    # A declared ask item that is not a system one is asked on first use instead.
    assert MEMORY not in items


def test_recent_grant_is_not_unused(env):
    _weather(env)
    env.store.decide("weather", FS_READ, "granted", source=MARKET)
    env.index.observe({"ts": (datetime.now(timezone.utc) - timedelta(days=45)).isoformat(), "kind": "usage_coverage_start"})
    rows = {row["permission"]: row for row in permission_panel.plugin_panel("weather", index=env.index)["rows"]}
    assert rows[FS_READ]["unused_30d"] is False


def test_fs_findings_are_possible_only_even_when_resolved(env):
    env.plugin("p", report=_report(
        system_fs__write={"declared": None, "findings": [_finding("a.py", 1, "resolved_call")], "basis": "resolved_call"},
    ))
    rows = {row["permission"]: row for row in permission_panel.plugin_panel("p", index=env.index)["rows"]}
    assert rows[FS_WRITE]["scan"]["possible_only"] is True


def test_inferred_lists_only_undecided_items(env):
    env.plugin("legacy", manifest=None, report=_report(
        system_process__spawn={"declared": None, "findings": [_finding("a.py", 1)], "basis": "resolved_call"},
        system_network__connect={"declared": None, "findings": [_finding("a.py", 2)], "basis": "resolved_call"},
    ))
    env.store.decide("legacy", CONNECT, "granted")
    assert permission_panel.pending(index=env.index)["inferred"] == [{"plugin_id": "legacy", "permission": SPAWN}]


def test_host_side_first_use_reaches_the_pending_list(env):
    """A real gate entry: reason self_not_approved, never "undecided"."""
    from plugin.server.application.plugins import call_audit, capability_gate

    env.plugin("p", report=_report())
    observed = []
    call_audit.add_observer(observed.append)
    try:
        capability_gate._last_written.clear()
        capability_gate.check("p", None, MEMORY, "MEMORY_QUERY")
    finally:
        call_audit.remove_observer(observed.append)
    decisions = [entry for entry in observed if entry.get("kind") == "decision"]
    assert decisions and decisions[0]["reason"] == "self_not_approved"
    for entry in decisions:
        env.index.observe(entry)

    first_use = permission_panel.pending(index=env.index)["first_use"]
    assert [(row["plugin_id"], row["permission"]) for row in first_use] == [("p", MEMORY)]


def test_first_use_of_an_unseen_permission_is_listed_and_shown(env):
    """Neither declared nor found by the scan (e.g. behind ctypes)."""
    env.plugin("p", report=_report())
    env.index.observe(_system_use("p", SPAWN, verdict="would_deny", reason="undecided"))

    assert [row["permission"] for row in permission_panel.pending(index=env.index)["first_use"]] == [SPAWN]
    rows = {row["permission"]: row for row in permission_panel.plugin_panel("p", index=env.index)["rows"]}
    assert rows[SPAWN]["reconcile"] is None and rows[SPAWN]["last_used"]["outcome"] == "would_block"


def test_unattributed_allowed_use_is_audited(monkeypatch):
    from plugin.server.application.plugins import call_audit, capability_gate

    monkeypatch.setattr(capability_gate, "granted_capabilities", lambda _pid: (frozenset({MEMORY}), True))
    observed = []
    call_audit.add_observer(observed.append)
    try:
        capability_gate._last_written.clear()
        decision = capability_gate.check("timer_plugin", None, MEMORY, "MEMORY_QUERY")
        capability_gate.check("timer_plugin", None, MEMORY, "MEMORY_QUERY")  # deduplicated
    finally:
        call_audit.remove_observer(observed.append)
    assert decision.allowed
    entries = [entry for entry in observed if entry.get("kind") == "decision"]
    assert len(entries) == 1
    assert entries[0]["verdict"] == "allow" and "unattributed" in entries[0]["flags"]


def test_reset_only_stub_drops_the_old_source(store):
    store.decide("x", MEMORY, "granted", source=MARKET)
    store.drop_grants("x")
    assert store.read("x")["source"] is None
    # A decision while the new source is unknown survives its first binding.
    store.decide("x", CONNECT, "granted", source=None)
    record = store.sync_source("x", MANUAL)
    assert CONNECT in record["permissions"]


def test_explicit_undecided_entries_are_no_reset(store):
    store.apply_legacy_decision("x", "denied", source=MARKET)
    store.clear("x", MEMORY)  # explicit undecided entry on a legacy_denied record
    record = store.sync_source("x", MANUAL)
    assert GRANTS_RESET not in record


@pytest.mark.asyncio
async def test_refusing_with_anytime_is_not_checked(env):
    _weather(env)
    response = await _call(
        "PUT", f"/plugin-permissions/plugins/weather/permissions/{CONNECT}", {"state": "denied", "condition": "anytime"}
    )
    assert response.status_code == 200


def test_laundering_allowance_is_counted(tmp_path):
    index = _index(tmp_path)
    index.observe(_decision("a", MEMORY, flags=["possible_laundering"], call_id="c1"))
    assert index.count("a", MEMORY, 1) == 1


@pytest.mark.asyncio
async def test_refusals_before_the_users_choice_leave_the_pending_list(env):
    env.plugin("p", report=_report())
    env.index.observe(_system_use("p", SPAWN, verdict="would_deny", reason="undecided"))
    assert permission_panel.pending(index=env.index)["first_use"]

    await _call("PUT", f"/plugin-permissions/plugins/p/permissions/{SPAWN}", {"state": "denied"})
    await _call("DELETE", f"/plugin-permissions/plugins/p/permissions/{SPAWN}")
    assert permission_panel.pending(index=env.index)["first_use"] == []

    later = (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()
    env.index.observe(_system_use("p", SPAWN, verdict="would_deny", reason="undecided", ts=later))
    assert len(permission_panel.pending(index=env.index)["first_use"]) == 1


def test_refusals_older_than_the_stored_decision_are_ignored(env):
    """After a restart the index is rebuilt from the files: the stored
    decision time still hides older refusals."""
    env.plugin("p", report=_report())
    env.index.observe(_decision("p", MEMORY, verdict="would_deny", reason="self_not_approved",
                                ts=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()))
    env.store.apply_legacy_decision("p", "denied", source=MARKET)
    env.store.clear("p", MEMORY)  # explicit undecided entry with a time
    assert permission_panel.pending(index=env.index)["first_use"] == []


@pytest.mark.parametrize(
    ("target", "host"),
    [
        ("api.example.com(93.184.216.34):443", "api.example.com"),
        ("天气.example(1.2.3.4):80", "天气.example"),
        ("averyveryl…(1.2.3.4):443", "averyveryl…"),
        ("93.184.216.34:443", "93.184.216.34"),
        ("[2001:db8::1]:443", "[2001:db8::1]"),
        ("api.example.com", "api.example.com"),
        ("https://api.example.com/path?q=1", None),
        ("api.example.com/path", None),
        ("api.example.com:443/x", None),
        ("a b", None),
        ("<invalid>", None),
        ("", None),
    ],
)
def test_network_target_keeps_the_host_only(target, host):
    from plugin.server.application.plugins.permission_usage import network_host

    assert network_host(target) == host


def test_daily_counts_are_bounded(tmp_path):
    index = _index(tmp_path)
    for day in range(200):
        index.observe(_decision("a", MEMORY, ts=_ts(timedelta(days=day))))
    assert len(index._daily[("a", MEMORY)]) <= 32
    assert index.count("a", MEMORY, 30) == 30


def test_indexed_network_target_is_the_host_only(tmp_path):
    index = _index(tmp_path)
    index.observe(_system_use("a", CONNECT, target="api.example.com/secret?token=1"))
    index.observe(_system_use("b", CONNECT, target="api.example.com(1.2.3.4):443"))
    assert index.last_use("a", CONNECT).target is None
    assert [use.target for use in index.recent([CONNECT])] == ["api.example.com", None] or [
        use.target for use in index.recent([CONNECT])
    ] == [None, "api.example.com"]


def test_uses_older_than_the_kept_days_get_no_bucket(tmp_path):
    index = _index(tmp_path)
    index.observe(_decision("a", MEMORY, ts=_ts(timedelta(days=40))))
    assert ("a", MEMORY) not in index._daily
    assert index.last_use("a", MEMORY) is not None


def test_buckets_are_pruned_as_time_moves_on(tmp_path):
    clock = {"now": NOW}
    index = UsageIndex(now=lambda: clock["now"], directory=str(tmp_path))
    for day in range(31):
        index.observe(_decision("a", MEMORY, ts=(NOW - timedelta(days=day)).isoformat()))
    clock["now"] = NOW + timedelta(days=40)
    for day in range(3):
        index.observe(_decision("a", MEMORY, ts=(clock["now"] - timedelta(days=day)).isoformat()))
    assert len(index._daily[("a", MEMORY)]) <= 32



def test_history_counts_only_from_a_coverage_start(tmp_path):
    """Entries from before complete recording began do not prove 30 days."""
    index = _index(tmp_path)
    index.observe(_decision("a", MEMORY, ts=_ts(timedelta(days=60))))
    assert index.history_since() is None and index.covers_days(30) is False

    index.observe({"ts": _ts(timedelta(days=10)), "kind": "usage_coverage_start"})
    assert index.history_since() == NOW - timedelta(days=10)
    assert index.covers_days(30) is False


def test_coverage_start_is_recorded_in_the_audit(monkeypatch):
    from plugin.server.application.plugins import permission_usage

    seen = []
    call_audit.add_observer(seen.append)
    monkeypatch.setattr(call_audit, "_audit_logger_locked", lambda: None)
    try:
        permission_usage.record_coverage_start()
    finally:
        call_audit.remove_observer(seen.append)
    assert [entry["kind"] for entry in seen] == ["usage_coverage_start"]


def test_latest_is_the_newest_use_not_a_refused_attempt(tmp_path):
    index = _index(tmp_path)
    index.observe(_decision("a", MEMORY, ts=_ts(timedelta(hours=1))))
    index.observe(_decision("b", MEMORY, verdict="would_deny", enforcing=True, reason="self_not_approved"))
    assert index.latest([MEMORY]).plugin_id == "a"


def test_cleared_permission_keeps_old_refusals_out_after_a_restart(env):
    env.plugin("p", report=_report())
    before = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    env.store.decide("p", SPAWN, "granted")
    env.store.clear("p", SPAWN)
    # Rebuilt from the files after a restart: the refusal is seen again.
    env.index.observe(_system_use("p", SPAWN, verdict="would_deny", reason="undecided", ts=before))
    assert permission_panel.pending(index=env.index)["first_use"] == []


def test_master_switch_off_counts_undecided_items_as_usable(env, monkeypatch):
    _weather(env)
    monkeypatch.setattr(permission_panel, "_checks_off", lambda: True)
    overview = permission_panel.privacy_overview(index=env.index)
    groups = {row["group"]: row["plugins"] for row in overview["groups"]}
    assert groups["memory"] == 1 and groups["network"] == 1
    assert overview["combos"] == [{"plugin_id": "weather", "reads": [MEMORY], "sends": [CONNECT]}]


@pytest.mark.parametrize(
    ("status", "reconcile"),
    [("queued", "scan_pending"), ("running", "scan_pending"), ("failed", "scan_unavailable"),
     ("unavailable", "scan_unavailable")],
)
def test_declared_rows_without_a_finished_scan(env, status, reconcile):
    env.plugin("p", report={"status": status})
    rows = {row["permission"]: row for row in permission_panel.plugin_panel("p", index=env.index)["rows"]}
    assert rows[CONNECT]["reconcile"] == reconcile


def test_panel_verifies_the_scan_and_queues_a_stale_one(env, monkeypatch):
    env.plugin("p")
    calls, scheduled = [], []

    def read_report(pid, root, version, verify_revision=True):
        calls.append(verify_revision)
        return {"status": "queued"} if scheduled else {"status": "unavailable"}

    monkeypatch.setattr(permission_panel, "read_report", read_report)
    monkeypatch.setattr(permission_panel, "schedule_analysis", scheduled.append)

    panel = permission_panel.plugin_panel("p", index=env.index)

    assert calls == [True, False] and scheduled == ["p"]
    assert panel["analysis"]["status"] == "queued"
    calls.clear()
    permission_panel.privacy_overview(index=env.index)
    assert calls == [False]  # the all-plugin views do not walk every tree


@pytest.mark.asyncio
async def test_disclosure_only_permission_cannot_be_refused(env):
    _weather(env)
    refused = await _call("PUT", f"/plugin-permissions/plugins/weather/permissions/{PUSH}", {"state": "denied"})
    assert refused.status_code == 422
    assert refused.headers["X-Error-Code"] == "PERMISSION_NOT_REFUSABLE"
    assert env.store.read("weather") == {}
    granted = await _call("PUT", f"/plugin-permissions/plugins/weather/permissions/{PUSH}", {"state": "granted"})
    assert granted.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["PUT", "DELETE"])
async def test_busy_operation_lock_answers_in_the_module_error_shape(env, monkeypatch, method):
    from plugin.server.application.plugins.operation_lock import PluginOperationBusy
    from plugin.server.routes import plugin_permissions as routes

    async def busy(*_args, **_kwargs):
        raise PluginOperationBusy()

    monkeypatch.setattr(routes, "_decide" if method == "PUT" else "_clear", busy)
    _weather(env)
    response = await _call(method, f"/plugin-permissions/plugins/weather/permissions/{CONNECT}", {"state": "granted"})
    assert response.status_code == 409
    assert response.headers["X-Error-Code"] == "PLUGIN_OPERATION_BUSY"
    assert response.json()["detail"]["code"] == "PLUGIN_OPERATION_BUSY"
    assert response.json()["detail"]["details"] == {}
