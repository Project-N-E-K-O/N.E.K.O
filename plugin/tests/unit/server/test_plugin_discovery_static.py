"""Discovery reads plugin metadata off disk and never imports a plugin.

Reading a plugin's metadata used to mean importing it, once per plugin, in a
throwaway subprocess, on every registry refresh — so a plugin only had to sit
in the plugins directory to get its module-level code executed, and starting
one plugin executed every other one. The derivation now happens once on the
author's machine (``neko-plugin build``) and ships as ``plugin.meta.json``.

The load-bearing guard here is behavioural, not structural: a refresh must
spawn zero subprocesses. A structural check for "does registry_service mention
the scanner" would pass the moment someone reached the scanner through a
helper, which is exactly the shape of the regression worth catching.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from plugin.server.application.plugins import registry_service as module
from plugin.server.infrastructure import autostart_approvals, packaged_metadata
from plugin.settings import BUILTIN_PLUGIN_CONFIG_ROOT

pytestmark = pytest.mark.plugin_unit


class _PopenPoisoned(AssertionError):
    """Raised if anything tries to start a process during discovery."""


@pytest.fixture
def _no_subprocess(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    attempts: list[object] = []

    def _poisoned(*args, **kwargs):
        attempts.append(args[0] if args else kwargs.get("args"))
        raise _PopenPoisoned(
            "discovery started a subprocess; it must read packaged metadata "
            f"instead of importing plugins: {attempts[-1]!r}"
        )

    monkeypatch.setattr(subprocess, "Popen", _poisoned)
    return attempts


def test_a_full_discovery_never_starts_a_process(_no_subprocess: list[object]) -> None:
    """The whole point, checked against the real builtin plugin tree.

    Mutation: put ``scan_plugin_metadata_isolated`` back into
    ``_build_discovery_payload`` and this fails with ``_PopenPoisoned``.
    """
    root = Path(BUILTIN_PLUGIN_CONFIG_ROOT)
    if not root.is_dir():
        pytest.skip("builtin plugin root is not present in this checkout")

    snapshot = module._discover_registry_snapshot_sync((root,))

    assert _no_subprocess == [], (
        f"discovery spawned {len(_no_subprocess)} process(es): {_no_subprocess}"
    )
    assert snapshot.records, "没有发现任何插件，这条守卫就不知道自己在盯什么"


def test_discovery_recovers_the_real_entries_from_packaged_metadata(
    _no_subprocess: list[object],
) -> None:
    """Not importing must not mean not knowing.

    The builtin tree ships ``plugin.meta.json`` for every plugin, so a refresh
    that imports nothing still has to produce the same entries the old scan
    produced. Zero subprocesses with zero entries would satisfy the guard above
    while having thrown the metadata away.
    """
    root = Path(BUILTIN_PLUGIN_CONFIG_ROOT)
    if not root.is_dir():
        pytest.skip("builtin plugin root is not present in this checkout")

    snapshot = module._discover_registry_snapshot_sync((root,))
    entries = [
        entry
        for record in snapshot.records
        for entry in (record.meta_payload.get("entries_preview") or [])
    ]
    assert entries, (
        "一个入口都没读出来：不 import 是达成了，但元数据也一起丢了"
    )
    unnamed = [entry for entry in entries if not entry.get("id")]
    assert not unnamed, f"有入口没有 id：{unnamed[:3]}"


def test_the_placeholder_schema_has_no_properties_key() -> None:
    """An empty ``properties`` map is worse than none at all.

    The plugin manager decides whether to render a generated form with
    ``!!(schema?.properties && typeof schema.properties === 'object')``, and
    ``!!{}`` is true in JavaScript. A placeholder carrying ``properties: {}``
    therefore renders a form with zero fields, submits ``{}`` as the arguments,
    and takes away the raw-JSON box the user needs — strictly worse than
    admitting we do not know.

    Mutation: add ``"properties": {}`` to ``PLACEHOLDER_INPUT_SCHEMA``.
    """
    assert "properties" not in packaged_metadata.PLACEHOLDER_INPUT_SCHEMA
    assert packaged_metadata.PLACEHOLDER_INPUT_SCHEMA.get("additionalProperties") is True

    normalized = module._normalize_entry_input_schema({"id": "x"})
    assert "properties" not in normalized["input_schema"]


def test_a_known_but_empty_schema_is_left_alone() -> None:
    """A parameterless entry keeps its empty ``properties`` map.

    ``properties: {}`` from the packager means "we looked, it takes nothing":
    the UI renders an empty form and submits ``{}``, which is right. Replacing
    it with the placeholder would hand the user a raw JSON box for an entry
    that accepts no arguments.

    Mutation: normalise on truthiness (``if schema:``) instead of on the
    presence of the ``properties`` key.
    """
    normalized = module._normalize_entry_input_schema(
        {"id": "ping", "input_schema": {"type": "object", "properties": {}}}
    )
    assert normalized["input_schema"]["properties"] == {}


def _write_plugin(tmp_path: Path, *, entries: list[dict], sdk_version: str | None = None,
                  source_sha: str | None = None) -> Path:
    plugin_dir = tmp_path / "demo"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.toml").write_text("id = 'demo'\n", encoding="utf-8")
    (plugin_dir / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
    payload = {
        "schema_version": packaged_metadata.PACKAGED_METADATA_SCHEMA_VERSION,
        "sdk_version": sdk_version if sdk_version is not None else packaged_metadata.SDK_VERSION,
        "source_sha256": (
            source_sha
            if source_sha is not None
            else packaged_metadata.compute_source_sha256(plugin_dir)
        ),
        "entries": entries,
    }
    (plugin_dir / packaged_metadata.PACKAGED_METADATA_FILENAME).write_text(
        json.dumps(payload), encoding="utf-8"
    )
    return plugin_dir


def test_packaged_metadata_is_read_back(tmp_path: Path) -> None:
    plugin_dir = _write_plugin(tmp_path, entries=[{"id": "go", "input_schema": {"properties": {}}}])
    result = packaged_metadata.read_packaged_metadata(plugin_dir)
    assert result is not None
    assert [entry["id"] for entry in result.entries] == ["go"]


def test_a_newer_mtime_alone_does_not_reject_the_metadata(tmp_path: Path) -> None:
    """A fresh clone has arbitrary mtimes; content is what decides.

    git does not preserve modification times, so on any newly cloned checkout
    the sources can easily look newer than the generated file. Rejecting on
    mtime alone would silently degrade every builtin plugin to placeholders on
    every new machine.

    Mutation: drop the content-hash confirmation and reject on mtime alone.
    """
    plugin_dir = _write_plugin(tmp_path, entries=[{"id": "go"}])
    meta_path = plugin_dir / packaged_metadata.PACKAGED_METADATA_FILENAME
    future = time.time() + 60
    os.utime(plugin_dir / "main.py", (future, future))

    assert (plugin_dir / "main.py").stat().st_mtime_ns > meta_path.stat().st_mtime_ns, (
        "前提没成立：源文件并没有比生成物新"
    )
    assert packaged_metadata.read_packaged_metadata(plugin_dir) is not None, (
        "内容没变却因为时间戳被判过时，新 clone 上所有内置插件都会退化成占位"
    )


def test_changed_sources_reject_the_metadata(tmp_path: Path) -> None:
    plugin_dir = _write_plugin(tmp_path, entries=[{"id": "go"}])
    (plugin_dir / "main.py").write_text("VALUE = 2\n", encoding="utf-8")
    future = time.time() + 60
    os.utime(plugin_dir / "main.py", (future, future))

    assert packaged_metadata.read_packaged_metadata(plugin_dir) is None, (
        "插件代码改了却仍然用打包时的 schema，作者改完签名看不到任何变化"
    )


def test_a_foreign_sdk_major_rejects_the_metadata(tmp_path: Path) -> None:
    plugin_dir = _write_plugin(tmp_path, entries=[{"id": "go"}], sdk_version="99.0.0")
    assert packaged_metadata.read_packaged_metadata(plugin_dir) is None


def test_a_patch_level_sdk_difference_is_accepted(tmp_path: Path) -> None:
    """Otherwise every SDK release invalidates the whole ecosystem's metadata."""
    major = packaged_metadata.SDK_VERSION.split(".", 1)[0]
    plugin_dir = _write_plugin(tmp_path, entries=[{"id": "go"}], sdk_version=f"{major}.99.99")
    assert packaged_metadata.read_packaged_metadata(plugin_dir) is not None


def test_a_plugin_the_user_never_started_is_not_autostarted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Installing and running are different acts; only the second is the user's.

    ``plugin_runtime.auto_start`` defaults to true and is declared by the
    plugin itself, so without this a freshly installed plugin runs its own code
    at the next greeting without ever having been started.

    Mutation: drop the ``is_autostart_approved`` check from
    ``_get_autostart_plugin_ids_sync``.
    """
    pending = {"just_installed"}
    monkeypatch.setattr(
        module, "is_autostart_approved", lambda plugin_id: plugin_id not in pending
    )
    monkeypatch.setattr(
        module,
        "_build_ordered_plugin_ids_sync",
        lambda candidates: sorted(candidates),
    )
    monkeypatch.setattr(
        module.state,
        "plugins",
        {
            "old_timer": {"runtime_enabled": True, "runtime_auto_start": True},
            "just_installed": {"runtime_enabled": True, "runtime_auto_start": True},
        },
        raising=False,
    )

    assert module._get_autostart_plugin_ids_sync() == ["old_timer"]


def _isolated_store(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Keep the approval store out of the developer's real config directory.

    Without this the suite writes a real ``plugin_autostart_pending.json`` under
    the user's app config dir, and the entries one test leaves behind change how
    autostart behaves for every test after it.
    """
    store: dict[str, object] = {}

    class _FakeConfigManager:
        def load_json_config(self, name):
            if name not in store:
                raise FileNotFoundError(name)
            return store[name]

        def save_json_config(self, name, payload):
            store[name] = payload

    import utils.config_manager as config_manager_module

    monkeypatch.setattr(
        config_manager_module, "get_config_manager", lambda: _FakeConfigManager()
    )
    autostart_approvals._reset_cache_for_testing()
    return store


def test_a_plugin_with_no_record_is_allowed_to_autostart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Absence of a record means "not our business", never "denied".

    The record is a pending-list rather than an approved-list precisely so that
    every failure in this file errs towards a plugin autostarting the way it
    always did. An approved-list needs a baseline, and getting that baseline
    wrong silences the user's whole autostart set.

    Mutation: invert the store to an approved-list.
    """
    _isolated_store(monkeypatch)
    try:
        assert autostart_approvals.is_autostart_approved("never_seen_before")
    finally:
        autostart_approvals._reset_cache_for_testing()


def test_an_unreadable_store_still_allows_autostart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A corrupt or unreadable store must not take everyone's plugins away.

    Mutation: re-raise instead of falling back to an empty pending set.
    """
    class _BrokenConfigManager:
        def load_json_config(self, name):
            raise OSError("disk said no")

        def save_json_config(self, name, payload):
            raise OSError("disk said no")

    import utils.config_manager as config_manager_module

    monkeypatch.setattr(
        config_manager_module, "get_config_manager", lambda: _BrokenConfigManager()
    )
    autostart_approvals._reset_cache_for_testing()
    try:
        assert autostart_approvals.is_autostart_approved("anything")
    finally:
        autostart_approvals._reset_cache_for_testing()


def test_a_freshly_installed_plugin_waits_for_the_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Marked at install, cleared the first time the user starts it.

    Mutation: drop the ``clear_autostart_pending`` call from
    ``_persist_user_runtime_intent``.
    """
    _isolated_store(monkeypatch)
    try:
        autostart_approvals.mark_autostart_pending("just_installed")
        assert not autostart_approvals.is_autostart_approved("just_installed")
        assert autostart_approvals.is_autostart_approved("some_other_plugin"), (
            "一个插件的待批准记录不该影响别的插件"
        )
        autostart_approvals.clear_autostart_pending("just_installed")
        assert autostart_approvals.is_autostart_approved("just_installed")
    finally:
        autostart_approvals._reset_cache_for_testing()
