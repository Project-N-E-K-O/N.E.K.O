from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from plugin.core import capability_analysis
from plugin.core.capability_analysis import analyze_capabilities
from plugin.server.application.plugins import (
    capability_analysis_service as analysis,
)
from plugin.server.application.plugins import consent_service as service
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure.plugin_consent import PluginConsentStore
from plugin.server.infrastructure.capability_reports import CapabilityReportStore


class ConfigManager:
    def __init__(self, root):
        self.root = root
        self.fail = False

    def get_runtime_config_path(self, name):
        return self.root / name

    def save_json_config(self, name, data):
        if self.fail:
            raise OSError("disk full")
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / name).write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def store(tmp_path):
    return PluginConsentStore(ConfigManager(tmp_path))


@pytest.fixture
def reports(tmp_path):
    return CapabilityReportStore(tmp_path / "reports")


def manifest(root, version="1"):
    root.mkdir(parents=True, exist_ok=True)
    (root / "plugin.toml").write_text(
        f'[plugin]\nid="demo"\nname="Demo"\nentry="demo:Plugin"\nversion="{version}"\n',
        encoding="utf-8",
    )


def test_analysis_finds_aliases_references_and_both_memory_sources(tmp_path):
    (tmp_path / "main.py").write_text(
        """
from plugin.sdk.plugin import MemoryClient as Memories
class Feature:
    def init(self):
        self.reader = Memories(self.ctx)
        send = self.push_message
        helper(send)
        turns = self.ctx.bus.conversations_v2
        turns.watch()
        self.ctx.bus.memory.get(bucket_id="default")
        self.reader.search(target=target, query="x")
        self.reader.get_persona(target=target)
        self.ctx.reply_tail.subscribe("completed")
        self.ctx.reply_tail.register(context)
""",
        encoding="utf-8",
    )
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    (vendor / "broken.py").write_text("broken Python !!!")
    result = analyze_capabilities(tmp_path)
    assert result["status"] == "completed"
    assert result["capabilities"] == [
        "conversations.read",
        "memory.read",
        "messages.push",
    ]
    assert result["files_analyzed"] == 1
    assert any("MemoryClient().search" in row["method"] for row in result["evidence"])
    memory_get = [
        row for row in result["evidence"] if row["method"].endswith("bus.memory.get")
    ]
    assert memory_get and all(
        row["capability"] == "memory.read" for row in memory_get
    )
    assert all(
        row["file"] == "main.py" and row["line"] > 0 for row in result["evidence"]
    )


def test_analysis_never_imports_code_and_distinguishes_failure(tmp_path):
    (tmp_path / "feature.py").write_text(
        "raise RuntimeError('must not execute')\nself.ctx.push_message()"
    )
    (tmp_path / "bad.py").write_text("def broken(")
    report = analyze_capabilities(tmp_path)
    assert report["status"] == "partial"
    assert report["capabilities"] == ["messages.push"]
    assert report["warnings"] == [{"code": "source_unreadable", "file": "bad.py"}]


def test_analysis_limits_and_no_sources_are_reported(tmp_path, monkeypatch):
    assert analyze_capabilities(tmp_path)["status"] == "unavailable"
    (tmp_path / "feature.py").write_text("unrelated.search()\nunrelated.get()")
    assert analyze_capabilities(tmp_path)["capabilities"] == []
    monkeypatch.setattr(capability_analysis, "MAX_NODES", 5)
    report = analyze_capabilities(tmp_path)
    assert report["status"] == "partial"
    assert {"code": "budget_exceeded"} in report["warnings"]


@pytest.mark.parametrize(
    "module", ["..context.situation_context", "context.situation_context"]
)
def test_imported_helpers_are_not_flagged(tmp_path, module):
    (tmp_path / "main.py").write_text(
        f"from {module} import compile_situation\n"
        "def bind():\n"
        "    context = compile_situation()\n"
        "    context.push_message()\n"
        "    compile_situation().create_card()\n",
        encoding="utf-8",
    )
    report = analyze_capabilities(tmp_path)
    assert report["status"] == "completed"
    assert report["capabilities"] == []
    assert report["evidence"] == []


@pytest.mark.parametrize(
    "source",
    [
        # Third-party/stdlib imports are never plugin SDK use.
        "import sqlalchemy_utils\nsqlalchemy_utils.create_view()",
        "from sqlalchemy_utils import create_view\ncreate_view()",
        # Relative imports resolve inside the plugin's own modules.
        "from .fsrs_bridge import create_card\ncreate_card()",
        "from . import helpers\nhelpers.push_message()",
        # A class or function defined in the plugin's own source is not an SDK call.
        "class Queue:\n    def push_message(self): ...\nq = Queue()\nq.push_message()",
        "def create_card(): ...\ncreate_card()",
    ],
)
def test_plugin_internal_and_third_party_origins_are_not_flagged(tmp_path, source):
    (tmp_path / "main.py").write_text(source, encoding="utf-8")
    report = analyze_capabilities(tmp_path)
    assert report["capabilities"] == []
    assert report["evidence"] == []


@pytest.mark.parametrize(
    "name",
    ["tests/test_feature.py", "test/test_feature.py", "test_feature.py", "feature_test.py", "conftest.py"],
)
def test_test_sources_are_excluded(tmp_path, name):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("self.ctx.push_message()", encoding="utf-8")
    assert analyze_capabilities(tmp_path)["status"] == "unavailable"


def test_getattr_with_constant_attribute_is_flagged(tmp_path):
    (tmp_path / "main.py").write_text(
        'getattr(self.ctx, "push_message")()\n'
        'getattr(self.ctx, "push_message")\n'
        'getattr(other, name)()\n'
        'getattr(other, "unrelated")()\n',
        encoding="utf-8",
    )
    report = analyze_capabilities(tmp_path)
    assert report["capabilities"] == ["messages.push"]
    assert [row["method"] for row in report["evidence"]] == [
        "self.ctx.push_message",
        "self.ctx.push_message",
    ]


@pytest.mark.parametrize(
    "source,capability",
    [
        ("self.push_message()", "messages.push"),
        ("self.ctx.push_message()", "messages.push"),
        ("plugin.bus.conversations.get()", "conversations.read"),
        ("from plugin.sdk.plugin import MemoryClient\nMemoryClient().search()", "memory.read"),
        ("from plugin.sdk import create_card\ncreate_card()", "messages.push"),
    ],
)
def test_sdk_origins_are_still_flagged(tmp_path, source, capability):
    (tmp_path / "main.py").write_text(source, encoding="utf-8")
    report = analyze_capabilities(tmp_path)
    assert report["capabilities"] == [capability]


def test_evidence_is_deduplicated_per_capability_and_line(tmp_path):
    (tmp_path / "main.py").write_text(
        "self.ctx.bus.conversations.get()\nx = self.ctx.bus.conversations.get\n",
        encoding="utf-8",
    )
    report = analyze_capabilities(tmp_path)
    keys = [(row["capability"], row["file"], row["line"]) for row in report["evidence"]]
    assert len(keys) == len(set(keys))


def test_analysis_stops_at_the_node_budget(tmp_path, monkeypatch):
    (tmp_path / "main.py").write_text(
        "api.push_message()\n"
        + "\n".join(f"value_{index} = build()" for index in range(50)),
        encoding="utf-8",
    )
    monkeypatch.setattr(capability_analysis, "MAX_NODES", 1)
    first = analyze_capabilities(tmp_path)
    second = analyze_capabilities(tmp_path)
    assert first["status"] == second["status"] == "partial"
    assert first["warnings"] == second["warnings"] == [{"code": "budget_exceeded"}]
    assert first["evidence"] == second["evidence"]


def test_analysis_caps_expanded_references(tmp_path):
    first = "a" * 2500
    second = "b" * 2500
    (tmp_path / "main.py").write_text(
        f"client = api.{first}\nclient.{second}.push_message()\n",
        encoding="utf-8",
    )
    report = analyze_capabilities(tmp_path)
    assert report["status"] == "partial"
    assert report["warnings"] == [{"code": "budget_exceeded"}]
    assert report["evidence"] == []


def test_scanner_keeps_function_locals_and_parameters_in_their_scope(tmp_path):
    (tmp_path / "main.py").write_text("""
from plugin.sdk.plugin import MemoryClient
client = MemoryClient(ctx)
async def read_memory(ctx):
    client = MemoryClient(ctx)
    return await client.search(target=target, query="q")
async def other():
    client = OtherClient()
    return client.get()
async def parameter(client):
    return client.get_persona(target=target)
""")
    result = analyze_capabilities(tmp_path)
    assert result["capabilities"] == ["memory.read"]
    assert any(row["line"] == 6 for row in result["evidence"])
    assert not any(row["line"] == 11 for row in result["evidence"])


def test_reassignments_do_not_hide_capabilities(tmp_path):
    (tmp_path / "main.py").write_text("""
class Plugin:
    def setup(self, text):
        self.client = first
        self.client = second
        self.client = self.client.next
        client = first
        client = second
        text = text.strip()
        self.ctx.push_message(text)
""")
    report = analyze_capabilities(tmp_path)
    assert report["status"] == "completed"
    assert report["capabilities"] == ["messages.push"]


def test_analysis_visits_lambda_and_definition_expressions(tmp_path):
    (tmp_path / "main.py").write_text("""
class Plugin:
    def setup(self):
        callback = lambda: self.ctx.push_message("hi")
        @self.ctx.reply_tail.register("x")
        def handler(arg=self.ctx.bus.memory.get("a")):
            ...
        class Nested(self.ctx.bus.conversations.get()):
            ...
""")
    report = analyze_capabilities(tmp_path)
    assert report["status"] == "completed"
    assert {row["line"] for row in report["evidence"]} == {4, 5, 6, 8}


def test_source_revision_changes_with_python_source_without_manifest_changes(tmp_path):
    manifest(tmp_path)
    original = analysis.source_revision(tmp_path, "1")
    file = tmp_path / "main.py"
    file.write_text("pass", encoding="utf-8")
    added = analysis.source_revision(tmp_path, "1")
    assert added != original
    file.write_text("self.ctx.push_message()", encoding="utf-8")
    assert analysis.source_revision(tmp_path, "1") != added
    file.unlink()
    assert analysis.source_revision(tmp_path, "1") == original


def test_source_revision_bounds_the_files_it_stats(tmp_path, monkeypatch):
    manifest(tmp_path)
    monkeypatch.setattr(analysis, "MAX_FILES", 1)
    first = tmp_path / "a.py"
    second = tmp_path / "b.py"
    second.write_text("pass")
    first.write_text("pass")

    initial = analysis.source_revision(tmp_path, "1")
    assert initial["truncated"]
    second.write_text("self.ctx.push_message()")
    assert analysis.source_revision(tmp_path, "1") == initial
    first.write_text("self.ctx.push_message()")
    assert analysis.source_revision(tmp_path, "1") != initial


def test_decisions_are_durable_and_delete_removes_the_record(store):
    saved = store.decide("demo", "approved", ["memory.read", "conversations.read"], "1")
    assert saved["decision"] == "approved"
    assert saved["approved_capabilities"] == [
        "conversations.read",
        "memory.read",
    ]
    assert saved["version"] == "1"
    assert saved["decided_at"]
    restored = PluginConsentStore(store._manager_override)
    assert restored.read("demo")["decision"] == "approved"
    store.decide("demo", "denied", [], "1")
    reread = PluginConsentStore(store._manager_override)
    assert reread.read("demo")["decision"] == "denied"
    assert reread.read("demo")["approved_capabilities"] == []
    store.delete("demo")
    assert PluginConsentStore(store._manager_override).read("demo") == {}
    store.delete("demo")


def test_failed_save_does_not_overwrite_invalid_store(store):
    store._manager_override.fail = True
    with pytest.raises(ServerDomainError) as error:
        store.decide("demo", "approved", [], "1")
    assert error.value.code == "PLUGIN_CONSENT_WRITE_FAILED"
    assert store.read("demo") == {}
    path = store._manager_override.get_runtime_config_path("plugin_consents.json")
    path.write_text("not json")
    restored = PluginConsentStore(store._manager_override)
    with pytest.raises(ServerDomainError) as error:
        restored.decide("demo", "approved", [], "1")
    assert error.value.code == "PLUGIN_CONSENT_READ_FAILED"
    assert path.read_text() == "not json"


def test_report_storage_failure_does_not_change_the_decision(store, reports, monkeypatch):
    store.decide("demo", "approved", ["memory.read"], "1")
    monkeypatch.setattr(
        reports, "save", lambda *_: (_ for _ in ()).throw(OSError("disk error"))
    )
    with pytest.raises(OSError):
        reports.save("demo", {"status": "completed"})
    assert store.read("demo")["decision"] == "approved"


@pytest.mark.parametrize(
    ("status", "capabilities", "record", "expected"),
    [
        ("queued", [], {}, {"state": "analyzing", "unconfirmed": [], "upgraded": False}),
        ("running", ["memory.read"], {}, {"state": "analyzing", "unconfirmed": ["memory.read"], "upgraded": False}),
        ("unavailable", [], {}, {"state": "analyzing", "unconfirmed": [], "upgraded": False}),
        ("completed", [], {}, {"state": "clear", "unconfirmed": [], "upgraded": False}),
        ("completed", ["memory.read"], {}, {"state": "confirm", "unconfirmed": ["memory.read"], "upgraded": False}),
        (
            "completed",
            ["memory.read"],
            {"decision": "approved", "approved_capabilities": ["memory.read"]},
            {"state": "clear", "unconfirmed": [], "upgraded": False},
        ),
        (
            "completed",
            ["memory.read", "conversations.read"],
            {"decision": "approved", "approved_capabilities": ["memory.read"]},
            {"state": "confirm", "unconfirmed": ["conversations.read"], "upgraded": True},
        ),
        ("partial", [], {}, {"state": "confirm", "unconfirmed": [], "upgraded": False}),
        ("failed", [], {}, {"state": "confirm", "unconfirmed": [], "upgraded": False}),
        (
            "partial",
            ["memory.read"],
            {"decision": "approved", "approved_capabilities": ["memory.read"]},
            {"state": "clear", "unconfirmed": [], "upgraded": False},
        ),
        (
            "completed",
            ["memory.read"],
            {"decision": "denied", "approved_capabilities": []},
            {"state": "confirm", "unconfirmed": ["memory.read"], "upgraded": False},
        ),
    ],
)
def test_derived_consent_state(status, capabilities, record, expected):
    from plugin.server.domain.consent_policy import derive_consent

    report = {"status": status, "capabilities": capabilities}
    view = derive_consent(record, report)
    assert {
        "state": view.state,
        "unconfirmed": view.unconfirmed,
        "upgraded": view.upgraded,
    } == expected
    assert view.decision == record.get("decision")


@pytest.mark.asyncio
async def test_background_scan_never_blocks_decisions_and_deduplicates(
    monkeypatch, store, reports, tmp_path
):
    entered, release = threading.Event(), threading.Event()
    calls = []

    def scan(root, **kwargs):
        calls.append(root)
        entered.set()
        assert release.wait(3)
        return {"status": "failed", "capabilities": [], "evidence": [], "warnings": []}

    monkeypatch.setattr(analysis, "analyze_capabilities", scan)
    runner = analysis.CapabilityAnalysisService(reports)
    manifest(tmp_path)
    try:
        runner.ensure_analyzed("demo", tmp_path, "1")
        runner.ensure_analyzed("demo", tmp_path, "1")
        assert await asyncio.to_thread(entered.wait, 2)
        store.decide("demo", "approved", [], "1")
        assert runner.report("demo", tmp_path, "1")["status"] == "running"
        release.set()
        with runner._lock:
            futures = [job[1] for job in runner._jobs.values()]
        await asyncio.gather(*(asyncio.wrap_future(future) for future in futures))
        assert len(calls) == 1
        assert reports.read("demo")["status"] == "failed"
        runner.ensure_analyzed("demo", tmp_path, "1")
        assert len(calls) == 1
    finally:
        release.set()
        await runner.close()


@pytest.mark.asyncio
async def test_pending_analysis_id_follows_the_current_job(tmp_path, reports, monkeypatch):
    manifest(tmp_path)
    (tmp_path / "main.py").write_text("self.ctx.push_message()")
    queued, running, release = threading.Event(), threading.Event(), threading.Event()
    original = analysis.analyze_capabilities

    def scan(*args, **kwargs):
        running.set()
        release.wait()
        return original(*args, **kwargs)

    monkeypatch.setattr(analysis, "analyze_capabilities", scan)
    runner = analysis.CapabilityAnalysisService(reports)
    runner._worker().submit(queued.wait)
    try:
        runner.ensure_analyzed("demo", tmp_path, "1")
        initial = runner.report("demo", tmp_path, "1")["analysis_id"]
        runner.ensure_analyzed("demo", tmp_path, "1")
        assert runner.report("demo", tmp_path, "1")["analysis_id"] == initial

        runner.reanalyze("demo", tmp_path, "1")
        replacement = runner.report("demo", tmp_path, "1")
        assert replacement["status"] == "queued"
        assert replacement["analysis_id"] != initial
        with runner._lock:
            job = runner._jobs["demo"][1]
        queued.set()
        assert await asyncio.to_thread(running.wait, 2)
        current = runner.report("demo", tmp_path, "1")
        assert current["status"] == "running"
        assert current["analysis_id"] == replacement["analysis_id"]
        release.set()
        await asyncio.wrap_future(job)
        final = runner.report("demo", tmp_path, "1")
        assert final["status"] == "completed"
        assert "analysis_id" not in final
    finally:
        queued.set()
        release.set()
        await runner.close()


@pytest.mark.asyncio
async def test_revision_checks_stay_in_the_worker_until_a_detail_requires_one(
    tmp_path, reports, monkeypatch
):
    root = tmp_path / "demo"
    manifest(root)
    (root / "main.py").write_text("self.ctx.push_message()")
    runner = analysis.CapabilityAnalysisService(reports)
    revision_calls = []
    original = analysis.source_revision

    def record_thread(*args, **kwargs):
        revision_calls.append(threading.get_ident())
        return original(*args, **kwargs)

    monkeypatch.setattr(analysis, "source_revision", record_thread)
    try:
        runner.ensure_analyzed("demo", root, "1")
        with runner._lock:
            job = runner._jobs.get("demo", (None, None))[1]
        if job is not None:
            await asyncio.wrap_future(job)
        assert revision_calls and threading.get_ident() not in revision_calls
        call_count = len(revision_calls)
        assert runner.report("demo", root, "1", verify_revision=False)["status"] == "completed"
        assert len(revision_calls) == call_count
        assert runner.report("demo", root, "1")["status"] == "completed"
        assert revision_calls[-1] == threading.get_ident()
    finally:
        await runner.close()


@pytest.mark.asyncio
async def test_running_plugin_summary_exposes_a_queued_same_version_scan(
    tmp_path, reports, monkeypatch
):
    from plugin.core.state import state

    root = tmp_path / "demo"
    manifest(root)
    source = root / "main.py"
    source.write_text("pass")
    meta = {"config_path": str(root / "plugin.toml"), "version": "1"}
    reports.save(
        "demo",
        {
            **analyze_capabilities(root),
            "_analyzer_version": analysis.ANALYZER_VERSION,
            "_source": analysis.source_revision(root, "1"),
            "_source_root": str(root),
            "version": "1",
        },
    )
    monkeypatch.setitem(state.plugins, "demo", meta)
    monkeypatch.setitem(state.plugin_hosts, "demo", object())
    runner = analysis.CapabilityAnalysisService(reports)
    monkeypatch.setattr(analysis, "analysis_service", runner)
    release = threading.Event()
    runner._worker().submit(release.wait)
    try:
        assert service.consent_summary("demo", meta)["state"] == "clear"
        runner.ensure_analyzed("demo", root, "1")
        current = service.consent_details("demo")["analysis"]
        assert current["status"] == "completed"
        assert current["analysis_id"]
        source.write_text("self.ctx.push_message()")
        assert service.consent_summary("demo", meta)["state"] == "analyzing"
        assert service.consent_notices() == []
        stale = runner.report("demo", root, "1")
        assert stale["status"] == "unavailable"
        assert stale["analysis_id"] == current["analysis_id"]
        with runner._lock:
            job = runner._jobs["demo"][1]
        release.set()
        await asyncio.wrap_future(job)
        assert service.consent_summary("demo", meta)["state"] == "confirm"
        assert "analysis_id" not in service.consent_details("demo")["analysis"]
        assert [notice["unconfirmed"] for notice in service.consent_notices()] == [["messages.push"]]
    finally:
        release.set()
        await runner.close()


@pytest.mark.asyncio
async def test_analysis_replaces_legacy_reports_and_reuses_current_reports(
    tmp_path, reports, monkeypatch
):
    root = tmp_path / "demo"
    manifest(root)
    (root / "main.py").write_text("self.push_message()\n", encoding="utf-8")
    reports.save(
        "demo",
        {
            "status": "partial",
            "version": "1",
            "_source_root": str(root),
            "_source": analysis.source_revision(root, "1"),
            "capabilities": [],
            "evidence": [],
            "warnings": [{"code": "budget_exceeded"}],
        },
    )
    runner = analysis.CapabilityAnalysisService(reports)

    async def run():
        runner.ensure_analyzed("demo", root, "1")
        with runner._lock:
            job = runner._jobs.get("demo", (None, None))[1]
        if job is not None:
            await asyncio.wrap_future(job)
        return job

    try:
        assert runner.report("demo", root, "1")["status"] == "unavailable"
        await run()
        fresh = reports.read("demo")
        assert fresh["status"] == "completed"
        assert fresh["capabilities"] == ["messages.push"]
        await runner.close()

        def unexpected_rescan(*args, **kwargs):
            pytest.fail("a current report with the same source revision was rescanned")

        monkeypatch.setattr(analysis, "analyze_capabilities", unexpected_rescan)
        assert runner.report("demo", root, "1") == fresh
        assert await run() is None
        assert reports.read("demo") == fresh
    finally:
        await runner.close()


@pytest.mark.asyncio
async def test_reanalysis_repairs_corrupt_cache_after_service_restart(tmp_path, reports):
    manifest(tmp_path)
    (tmp_path / "main.py").write_text("self.ctx.push_message()", encoding="utf-8")
    reports.save("demo", {"invalid": True})
    runner = analysis.CapabilityAnalysisService(reports)
    await runner.close()
    runner.reanalyze("demo", tmp_path, "1")
    assert not runner._jobs
    runner.start()
    try:
        runner.reanalyze("demo", tmp_path, "1")
        with runner._lock:
            job = runner._jobs.get("demo", (None, None))[1]
        if job is not None:
            await asyncio.wrap_future(job)
        assert reports.read("demo")["capabilities"] == ["messages.push"]
    finally:
        await runner.close()


@pytest.mark.asyncio
async def test_source_reads_wait_for_plugin_directory_mutation(monkeypatch, tmp_path, reports):
    from plugin.server.application.plugins.operation_lock import plugin_operation_lock

    root = tmp_path / "demo"
    manifest(root)
    source = root / "main.py"
    source.write_text("self.ctx.push_message()", encoding="utf-8")
    pending, reading = threading.Event(), threading.Event()
    original_lock = analysis.hold_plugin_operation_file_lock
    original_read = analysis.read_capability_sources

    def wait_for_source_lock(stop):
        pending.set()
        return original_lock(stop)

    def read_sources(*args, **kwargs):
        reading.set()
        return original_read(*args, **kwargs)

    monkeypatch.setattr(analysis, "hold_plugin_operation_file_lock", wait_for_source_lock)
    monkeypatch.setattr(analysis, "read_capability_sources", read_sources)
    runner = analysis.CapabilityAnalysisService(reports)
    try:
        async with plugin_operation_lock.hold():
            runner.reanalyze("demo", root, "1")
            assert await asyncio.to_thread(pending.wait, 2)
            assert not reading.is_set()
            moved = root.rename(tmp_path / "moved")
            moved.rename(root)
        with runner._lock:
            job = runner._jobs.get("demo", (None, None))[1]
        if job is not None:
            await asyncio.wrap_future(job)
        assert reading.is_set()
        assert reports.read("demo")["capabilities"] == ["messages.push"]
    finally:
        await runner.close()


@pytest.mark.asyncio
async def test_stale_scan_cannot_replace_new_install_report(
    monkeypatch, store, reports, tmp_path
):
    entered, release = threading.Event(), threading.Event()

    def scan(root, **kwargs):
        if root.name == "old":
            entered.set()
            assert release.wait(3)
        return {
            "status": "completed",
            "capabilities": [],
            "evidence": [],
            "warnings": [],
        }

    monkeypatch.setattr(analysis, "analyze_capabilities", scan)
    runner = analysis.CapabilityAnalysisService(reports)
    manifest(tmp_path / "old")
    manifest(tmp_path / "new", "2")
    try:
        runner.ensure_analyzed("demo", tmp_path / "old", "1")
        assert await asyncio.to_thread(entered.wait, 2)
        runner.reanalyze("demo", tmp_path / "new", "2")
        release.set()
        with runner._lock:
            latest = runner._jobs["demo"][1]
        await asyncio.wrap_future(latest)
        assert reports.read("demo")["version"] == "2"
    finally:
        release.set()
        await runner.close()


@pytest.mark.asyncio
async def test_install_committed_runs_one_analysis_job(
    monkeypatch, reports, tmp_path
):
    root = tmp_path / "demo"
    manifest(root)
    (root / "main.py").write_text("self.push_message()\n", encoding="utf-8")
    runner = analysis.CapabilityAnalysisService(reports)
    try:
        runner.installed(root)
        report = None
        for _ in range(100):
            report = reports.read("demo")
            if report is not None:
                break
            await asyncio.sleep(0.05)
        assert report is not None
        assert report["status"] == "completed"
        assert report["capabilities"] == ["messages.push"]
    finally:
        await runner.close()


@pytest.mark.asyncio
async def test_start_plugin_never_consults_consent(
    monkeypatch, store, tmp_path
):
    from plugin.server.application.plugins import lifecycle_service as lifecycle
    from plugin.core.state import state

    class Host:
        async def start(self, **kwargs):
            return None

        async def shutdown(self, timeout=None):
            return None

        def is_alive(self):
            return True

    monkeypatch.setattr(service, "consent_store", store)
    monkeypatch.setitem(state.plugin_hosts, "demo", Host())
    result = await lifecycle.PluginLifecycleService().start_plugin(
        "demo", refresh_registry=False
    )
    assert result["success"] is True
    assert result["already_running"] is True
    assert store.read("demo") == {}


@pytest.mark.asyncio
async def test_batch_autostart_skips_denied_and_starts_undecided(
    monkeypatch, store
):
    from plugin.server.application.plugins import lifecycle_service as lifecycle

    monkeypatch.setattr(service, "consent_store", store)
    store.decide("denied_plugin", "denied", [], "1")
    started = []

    async def fake_start(plugin_id, **kwargs):
        started.append(plugin_id)
        return {"success": True, "plugin_id": plugin_id}

    lifecycle_service = lifecycle.PluginLifecycleService()
    monkeypatch.setattr(lifecycle_service, "_start_plugin_under_lock", fake_start)
    result = await lifecycle_service.start_plugins_batch(
        ["undecided_plugin", "denied_plugin"], concurrency=1
    )
    assert started == ["undecided_plugin"]
    assert result["started"] == ["undecided_plugin"]
    assert result["skipped_denied"] == ["denied_plugin"]


def _consent_app(monkeypatch, store, report, tmp_path):
    from plugin.server.routes import plugin_consent as routes

    manifest(tmp_path)
    monkeypatch.setattr(service, "consent_store", store)
    monkeypatch.setattr(service, "plugin_source", lambda _: (tmp_path, "1"))
    monkeypatch.setattr(
        service,
        "read_report",
        lambda *_, **__: dict(report),
    )
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[routes.require_plugin_mutation_access] = lambda: None
    return app


@pytest.mark.asyncio
async def test_first_details_request_schedules_analysis(monkeypatch, tmp_path):
    from plugin.core.state import state
    from plugin.server.routes import plugin_consent as routes

    manifest(tmp_path)
    (tmp_path / "main.py").write_text("self.ctx.push_message()", encoding="utf-8")
    monkeypatch.setitem(
        state.plugins, "demo", {"config_path": str(tmp_path / "plugin.toml"), "version": "1"}
    )
    app = FastAPI()
    app.include_router(routes.router)
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        response = await client.get("/plugin-consent/plugins/demo")
        assert response.status_code == 200
        runner = analysis.analysis_service
        with runner._lock:
            job = runner._jobs.get("demo", (None, None))[1]
        if job is not None:
            await asyncio.wrap_future(job)
        assert runner.report("demo", tmp_path, "1")["capabilities"] == ["messages.push"]


@pytest.mark.asyncio
async def test_consent_api_records_approval_as_a_union_of_capabilities(
    monkeypatch, store, tmp_path
):
    report = {
        "status": "completed",
        "capabilities": ["memory.read"],
        "evidence": [],
        "warnings": [],
    }
    app = _consent_app(monkeypatch, store, report, tmp_path)
    store.decide("demo", "approved", ["messages.push"], "1")
    async with AsyncClient(
        transport=ASGITransport(app), base_url="http://test"
    ) as client:
        details = (await client.get("/plugin-consent/plugins/demo")).json()
        assert details["state"] == "confirm"
        assert details["upgraded"] is True
        assert details["unconfirmed"] == ["memory.read"]
        snapshot = {
            "version": details["version"],
            "unconfirmed": details["unconfirmed"],
            "analysis_status": details["analysis"]["status"],
        }
        report["capabilities"].append("conversations.read")
        stale = await client.post(
            "/plugin-consent/plugins/demo", json={"approved": True, "snapshot": snapshot}
        )
        assert stale.status_code == 409
        assert store.read("demo")["approved_capabilities"] == ["messages.push"]
        fresh = (await client.get("/plugin-consent/plugins/demo")).json()
        snapshot["unconfirmed"] = fresh["unconfirmed"]
        response = await client.post(
            "/plugin-consent/plugins/demo", json={"approved": True, "snapshot": snapshot}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["decision"] == "approved"
        assert body["state"] == "clear"
        assert body["approved_capabilities"] == [
            "conversations.read",
            "memory.read",
            "messages.push",
        ]


@pytest.mark.asyncio
async def test_reject_records_denied_and_stops_a_running_plugin(
    monkeypatch, store, tmp_path
):
    from plugin.server.application.plugins import lifecycle_service as lifecycle

    report = {
        "status": "completed",
        "capabilities": ["memory.read"],
        "evidence": [],
        "warnings": [],
    }
    app = _consent_app(monkeypatch, store, report, tmp_path)
    stopped = []

    async def fake_stop(self, plugin_id, **kwargs):
        stopped.append(plugin_id)
        return {"success": True}

    monkeypatch.setattr(lifecycle.PluginLifecycleService, "stop_plugin", fake_stop)
    async with AsyncClient(
        transport=ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/plugin-consent/plugins/demo", json={"approved": False}
        )
        assert response.status_code == 200
        assert response.json()["decision"] == "denied"
        assert stopped == ["demo"]
        assert store.read("demo")["decision"] == "denied"


@pytest.mark.asyncio
async def test_reject_tolerates_a_plugin_that_is_not_running(
    monkeypatch, store, tmp_path
):
    from plugin.server.application.plugins import lifecycle_service as lifecycle

    app = _consent_app(
        monkeypatch,
        store,
        {"status": "completed", "capabilities": [], "evidence": [], "warnings": []},
        tmp_path,
    )

    async def not_running(self, plugin_id, **kwargs):
        raise ServerDomainError("PLUGIN_NOT_RUNNING", "not running", 409)

    monkeypatch.setattr(
        lifecycle.PluginLifecycleService, "stop_plugin", not_running
    )
    async with AsyncClient(
        transport=ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/plugin-consent/plugins/demo", json={"approved": False}
        )
        assert response.status_code == 200
        assert response.json()["decision"] == "denied"


@pytest.mark.asyncio
async def test_delete_consent_revokes_the_record(monkeypatch, store, tmp_path):
    app = _consent_app(
        monkeypatch,
        store,
        {"status": "completed", "capabilities": [], "evidence": [], "warnings": []},
        tmp_path,
    )
    store.decide("demo", "approved", ["memory.read"], "1")
    async with AsyncClient(
        transport=ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.delete("/plugin-consent/plugins/demo")
        assert response.status_code == 200
        assert store.read("demo") == {}
        assert response.json()["decision"] is None


@pytest.mark.asyncio
async def test_stale_notice_approval_schedules_a_new_report(tmp_path, reports, monkeypatch):
    from plugin.core.state import state
    from plugin.server.routes import plugin_consent as routes

    manifest(tmp_path)
    source = tmp_path / "main.py"
    source.write_text("self.ctx.push_message()", encoding="utf-8")
    reports.save(
        "demo",
        {
            **analyze_capabilities(tmp_path),
            "_analyzer_version": analysis.ANALYZER_VERSION,
            "_source": analysis.source_revision(tmp_path, "1"),
            "_source_root": str(tmp_path),
            "version": "1",
        },
    )
    meta = {"config_path": str(tmp_path / "plugin.toml"), "version": "1"}
    monkeypatch.setitem(state.plugins, "demo", meta)
    monkeypatch.setitem(state.plugin_hosts, "demo", object())
    runner = analysis.CapabilityAnalysisService(reports)
    monkeypatch.setattr(analysis, "analysis_service", runner)
    release = threading.Event()
    runner._worker().submit(release.wait)
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[routes.require_plugin_mutation_access] = lambda: None
    try:
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
            notice = (await client.get("/plugin-consent/notices")).json()[0]
            assert notice["unconfirmed"] == ["messages.push"]
            source.write_text("self.ctx.bus.memory.get('a')", encoding="utf-8")
            response = await client.post(
                "/plugin-consent/plugins/demo",
                json={"approved": True, "snapshot": notice},
            )
            assert response.status_code == 409
            assert service.consent_store.read("demo") == {}
            assert (await client.get("/plugin-consent/notices")).json() == []
            with runner._lock:
                job = runner._jobs["demo"][1]
            release.set()
            await asyncio.wrap_future(job)
            updated = (await client.get("/plugin-consent/notices")).json()[0]
            assert updated["unconfirmed"] == ["memory.read"]
    finally:
        release.set()
        await runner.close()


@pytest.mark.asyncio
async def test_notices_list_only_running_plugins_still_needing_confirmation(
    monkeypatch, store, tmp_path
):
    from plugin.core.state import state

    report = {
        "status": "completed",
        "capabilities": ["memory.read"],
        "evidence": [],
        "warnings": [],
    }
    app = _consent_app(monkeypatch, store, report, tmp_path)
    meta = {"config_path": str(tmp_path / "plugin.toml"), "name": "Demo", "version": "1"}
    for plugin_id in ("demo", "denied", "stopped"):
        monkeypatch.setitem(state.plugins, plugin_id, dict(meta))
    monkeypatch.setitem(state.plugin_hosts, "demo", object())
    monkeypatch.setitem(state.plugin_hosts, "denied", object())
    store.decide("denied", "denied", [], "1")
    async with AsyncClient(
        transport=ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.get("/plugin-consent/notices")
        assert response.status_code == 200
        assert response.json() == [
            {
                "plugin_id": "demo",
                "name": "Demo",
                "version": "1",
                "unconfirmed": ["memory.read"],
                "upgraded": False,
                "analysis_status": "completed",
            }
        ]


@pytest.mark.asyncio
async def test_decision_waits_for_uninstall_and_revalidates(monkeypatch, store):
    from plugin.server.application.plugins.operation_lock import plugin_operation_lock

    present = True

    def source(_):
        if not present:
            raise ServerDomainError("PLUGIN_NOT_FOUND", "Plugin not found", 404)
        return Path("demo"), "1"

    monkeypatch.setattr(service, "plugin_source", source)
    monkeypatch.setattr(service, "consent_store", store)
    monkeypatch.setattr(
        service,
        "read_report",
        lambda *_, **__: {
            "status": "completed",
            "capabilities": [],
            "evidence": [],
            "warnings": [],
        },
    )
    async with plugin_operation_lock.hold():
        request = asyncio.create_task(
            service.approve_plugin("demo", version="1", unconfirmed=[], analysis_status="completed")
        )
        await asyncio.sleep(0)
        assert not request.done()
        # The real uninstall operation clears consent under this same lock.
        present = False
        store.delete("demo")
    try:
        await request
    except ServerDomainError as error:
        assert error.code == "PLUGIN_NOT_FOUND"
    else:
        pytest.fail("approved a plugin after uninstall")
    assert store.read("demo") == {}


@pytest.mark.asyncio
async def test_uninstall_waits_for_a_decision_then_forgets_it(monkeypatch, store):
    from plugin.server.application.plugins.operation_lock import (
        serialized_plugin_operation,
    )

    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(service, "consent_store", store)
    monkeypatch.setattr(service, "plugin_source", lambda _: (Path("demo"), "1"))
    monkeypatch.setattr(
        service,
        "read_report",
        lambda *_, **__: {
            "status": "completed",
            "capabilities": [],
            "evidence": [],
            "warnings": [],
        },
    )
    original = store.decide

    def slow_decide(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "decide", slow_decide)

    @serialized_plugin_operation
    async def uninstall():
        store.delete("demo")

    approval = asyncio.create_task(
        service.approve_plugin("demo", version="1", unconfirmed=[], analysis_status="completed")
    )
    assert await asyncio.to_thread(entered.wait, 2)
    deletion = asyncio.create_task(uninstall())
    try:
        await asyncio.sleep(0)
        assert not deletion.done()
    finally:
        release.set()
    assert (await approval)["decision"] == "approved"
    await deletion
    assert store.read("demo") == {}


@pytest.mark.asyncio
async def test_forget_plugin_removes_report_and_consent_record(
    monkeypatch, store, reports, tmp_path
):
    reports.save("demo", {"status": "completed"})
    store.decide("demo", "approved", ["memory.read"], "1")
    runner = analysis.CapabilityAnalysisService(reports)
    monkeypatch.setattr(analysis, "analysis_service", runner)
    monkeypatch.setattr(service, "consent_store", store)
    try:
        await asyncio.to_thread(service.forget_plugin, "demo")
        assert reports.read("demo") is None
        assert store.read("demo") == {}
    finally:
        await runner.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_validation", [False, True])
async def test_market_replacement_notifies_only_after_real_transaction_commit(
    monkeypatch, tmp_path, fail_validation
):
    from plugin.server.routes import market_bridge as market
    from plugin.server.application.plugins.installation_transactions import replace
    from plugin.core.plugin_layout import resolve_plugin_layout

    root = tmp_path / "demo"
    manifest(root)
    notifications = []
    monkeypatch.setattr(
        analysis, "analysis_service", SimpleNamespace(installed=notifications.append)
    )
    monkeypatch.setattr(replace, "ensure_plugin_layout_runtime_config", lambda *_: None)

    async def not_running(_):
        return False

    monkeypatch.setattr(replace, "_plugin_is_running", not_running)
    entry = SimpleNamespace(
        plugin_id="demo",
        directory_name="demo",
        package_id="demo",
        channel="market",
        removed=False,
        root_id="user",
    )
    manager = SimpleNamespace(find_active_user_entry=lambda _: entry)

    async def install_new():
        manifest(root, "2")
        assert notifications == []
        return {"installed_plugins": [{"target_dir": str(root)}]}

    async def validate():
        assert notifications == []
        if fail_validation:
            raise RuntimeError("post-install validation failed")

    operation = market._replace_market_plugin_transaction(
        manager=manager,
        expected_plugin_id="demo",
        original_entry=entry,
        original_entry_fingerprint=market._market_entry_fingerprint(entry),
        installed_package_id="demo",
        plugin_dir=root,
        layout=resolve_plugin_layout("demo", root, storage_root=tmp_path / "state"),
        install_new=install_new,
        validate_channel_specific=validate,
    )
    if fail_validation:
        with pytest.raises(replace.ReplacePluginError):
            await operation
        assert notifications == []
        assert 'version="1"' in (root / "plugin.toml").read_text()
    else:
        await operation
        assert notifications == [root]


def test_v1_consent_document_is_rejected(store):
    path = store._manager_override.get_runtime_config_path("plugin_consents.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "plugins": {
                    "demo": {
                        "decision": "approved",
                        "approved_capabilities": [],
                        "version": "1",
                        "decided_at": "2026-01-01T00:00:00+00:00",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    restored = PluginConsentStore(store._manager_override)
    with pytest.raises(ServerDomainError) as error:
        restored.read("demo")
    assert error.value.code == "PLUGIN_CONSENT_READ_FAILED"


def test_fixed_point_resolves_a_four_step_alias_chain(tmp_path):
    # Reversed assignment order: each pass resolves only one hop, so a bounded
    # three-pass propagation would leave ``d`` unexpanded.
    (tmp_path / "main.py").write_text(
        "from plugin.sdk.plugin import MemoryClient\n"
        "client = MemoryClient(ctx)\n"
        "d = c\n"
        "c = b\n"
        "b = a\n"
        "a = client\n"
        "d.search(target=t, query='q')\n",
        encoding="utf-8",
    )
    report = analyze_capabilities(tmp_path)
    assert report["capabilities"] == ["memory.read"]
    assert any(
        row["method"] == "plugin.sdk.plugin.MemoryClient().search"
        for row in report["evidence"]
    )


def test_capability_rule_table_covers_declared_capabilities():
    from plugin.core.capability_analysis import (
        CAPABILITIES,
        _RULES,
        _capability_for,
    )

    assert {rule["capability"] for rule in _RULES} <= set(CAPABILITIES)
    assert _capability_for("plugin.bus.conversations.get") == "conversations.read"
    assert _capability_for("self.ctx.reply_tail.subscribe") == "conversations.read"
    assert _capability_for("self.ctx.bus.memory.get") == "memory.read"
    assert _capability_for("helpers.query_memory") == "memory.read"
    assert (
        _capability_for("plugin.sdk.plugin.MemoryClient().search") == "memory.read"
    )
    assert _capability_for("other.MemoryClient().search") is None
    assert _capability_for("self.ctx.push_message") == "messages.push"
    assert _capability_for("self.ctx.create_card") == "messages.push"
    assert _capability_for("self.ctx.reply_tail.register") == "messages.push"
    assert _capability_for("unrelated.search") is None


@pytest.mark.asyncio
async def test_freshness_is_uniform_across_report_statuses(
    reports, tmp_path, monkeypatch
):
    root = tmp_path / "demo"
    manifest(root)
    revision = analysis.source_revision(root, "1")
    runner = analysis.CapabilityAnalysisService(reports)
    try:
        for status in ("completed", "partial", "failed"):
            reports.save(
                "demo",
                {
                    "status": status,
                    "version": "1",
                    "_source_root": str(root),
                    "_source": revision,
                    "_analyzer_version": analysis.ANALYZER_VERSION,
                    "capabilities": [],
                    "evidence": [],
                    "warnings": [],
                },
            )
            assert runner.report("demo", root, "1")["status"] == status
            # A changed source revision stales any non-in-flight status.
            reports.save(
                "demo",
                {
                    "status": status,
                    "version": "1",
                    "_source_root": str(root),
                    "_source": {**revision, "size": revision["size"] + 1},
                    "_analyzer_version": analysis.ANALYZER_VERSION,
                    "capabilities": [],
                    "evidence": [],
                    "warnings": [],
                },
            )
            assert runner.report("demo", root, "1")["status"] == "unavailable"
    finally:
        await runner.close()


@pytest.mark.asyncio
async def test_revoke_is_serialized_with_other_plugin_operations(
    monkeypatch, store, tmp_path
):
    from plugin.server.application.plugins.operation_lock import plugin_operation_lock

    monkeypatch.setattr(service, "consent_store", store)
    monkeypatch.setattr(service, "plugin_source", lambda _: (tmp_path, "1"))
    monkeypatch.setattr(
        service,
        "read_report",
        lambda *_, **__: {
            "status": "completed",
            "capabilities": [],
            "evidence": [],
            "warnings": [],
        },
    )
    store.decide("demo", "approved", ["memory.read"], "1")
    async with plugin_operation_lock.hold():
        request = asyncio.create_task(service.revoke_plugin("demo"))
        await asyncio.sleep(0)
        assert not request.done()
    await request
    assert store.read("demo") == {}


@pytest.mark.asyncio
async def test_deny_stops_a_running_plugin_without_deadlock(
    monkeypatch, store, tmp_path
):
    from plugin.server.application.plugins import lifecycle_service as lifecycle

    monkeypatch.setattr(service, "consent_store", store)
    monkeypatch.setattr(service, "plugin_source", lambda _: (tmp_path, "1"))
    monkeypatch.setattr(
        service,
        "read_report",
        lambda *_, **__: {
            "status": "completed",
            "capabilities": ["memory.read"],
            "evidence": [],
            "warnings": [],
        },
    )
    stopped = []

    async def fake_stop(self, plugin_id, **kwargs):
        stopped.append(plugin_id)
        return {"success": True}

    monkeypatch.setattr(lifecycle.PluginLifecycleService, "stop_plugin", fake_stop)
    details = await asyncio.wait_for(service.deny_plugin("demo"), timeout=10)
    assert details["decision"] == "denied"
    assert stopped == ["demo"]
    assert store.read("demo")["decision"] == "denied"
