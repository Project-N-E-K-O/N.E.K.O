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
        sorted,
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
        config_manager_module, "get_config_manager", _FakeConfigManager
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
        config_manager_module, "get_config_manager", _BrokenConfigManager
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


@pytest.mark.asyncio
async def test_the_refresh_lock_covers_reading_disk_not_just_publishing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reading and publishing must happen under the same lock acquisition.

    Locking only the publish step is not enough: two overlapping refreshes can
    each read the same stale ``existing_snapshot`` outside the lock, then enter
    it one after the other, and the second one reconciles additions and
    removals against a registry that the first has already changed — deleting
    records it should have kept (codex).

    Behavioural rather than structural: discovery itself asserts the lock is
    held while it runs, so moving either read back outside fails here.

    Mutation: hoist ``_discover_registry_snapshot_sync`` or
    ``_get_registered_plugin_snapshot_sync`` above the ``with`` statement.
    """
    held: list[bool] = []

    def _discover(roots):
        held.append(module._REGISTRY_REFRESH_LOCK._is_owned())
        return module.PluginDiscoverySnapshot(
            records=[], failures=[], config_paths=set(), shadowed=[]
        )

    def _snapshot():
        held.append(module._REGISTRY_REFRESH_LOCK._is_owned())
        return {}

    monkeypatch.setattr(module, "_discover_registry_snapshot_sync", _discover)
    monkeypatch.setattr(module, "_get_registered_plugin_snapshot_sync", _snapshot)
    monkeypatch.setattr(module, "_list_running_plugin_ids_sync", set)
    monkeypatch.setattr(module, "_collect_missing_plugin_ids_sync", lambda snapshot: set())
    monkeypatch.setattr(
        module, "_remove_stale_plugin_metadata_sync", lambda ids, running_ids: ([], [])
    )

    await module.PluginRegistryService().refresh_registry()

    assert held and all(held), (
        f"读盘发生在锁外，两次重叠刷新会拿着过时快照互相覆盖：{held}"
    )


def test_a_packaged_plugin_does_not_need_a_second_import_to_start(
    tmp_path: Path,
) -> None:
    """``start_plugin`` reuses packaged handlers instead of re-importing.

    The plugin process imports the plugin; the metadata worker used to import
    it a second time for a result the package already carries. Packages built
    before this field existed have no ``handlers`` and must still fall back to
    the worker, or their entries would silently vanish.

    Mutation: return the packaged object even when ``handlers`` is empty — the
    "no handlers" case below then wrongly reports metadata.
    """
    from plugin.server.application.plugins import lifecycle_service

    plugin_dir = _write_plugin(tmp_path, entries=[{"id": "go"}])
    assert (
        lifecycle_service._read_packaged_isolated_metadata(
            plugin_dir / "plugin.toml", "demo"
        )
        is None
    ), "没有 handlers 的旧包必须回落到扫描，否则它的入口会凭空消失"

    meta_path = plugin_dir / packaged_metadata.PACKAGED_METADATA_FILENAME
    payload = json.loads(meta_path.read_text(encoding="utf-8"))
    payload["handlers"] = {"demo.go": {"event_type": "plugin_entry", "id": "go"}}
    payload["entry_methods"] = {"go": "go"}
    meta_path.write_text(json.dumps(payload), encoding="utf-8")

    recovered = lifecycle_service._read_packaged_isolated_metadata(
        plugin_dir / "plugin.toml", "demo"
    )
    assert recovered is not None
    assert recovered.entry_methods == {"go": "go"}
    assert list(recovered.handlers) == ["demo.go"]


def test_packaged_handlers_minted_under_another_id_are_not_reused(
    tmp_path: Path,
) -> None:
    """A conflict-renamed plugin must rescan, not register nothing.

    Handler keys embed the plugin id, and an id conflict renames a plugin at
    registration time (``demo`` becomes ``demo_1``).
    ``install_isolated_plugin_metadata`` silently drops every key that does not
    belong to the runtime id, so reusing packaged keys minted under the
    manifest id would register zero handlers — the plugin starts, reports
    success, and exposes no entries at all (coderabbit).

    Mutation: drop the ownership check and return the packaged object anyway.
    """
    from plugin.server.application.plugins import lifecycle_service

    plugin_dir = _write_plugin(tmp_path, entries=[{"id": "go"}])
    meta_path = plugin_dir / packaged_metadata.PACKAGED_METADATA_FILENAME
    payload = json.loads(meta_path.read_text(encoding="utf-8"))
    payload["handlers"] = {"demo.go": {"event_type": "plugin_entry", "id": "go"}}
    payload["entry_methods"] = {"go": "go"}
    meta_path.write_text(json.dumps(payload), encoding="utf-8")

    assert (
        lifecycle_service._read_packaged_isolated_metadata(
            plugin_dir / "plugin.toml", "demo_1"
        )
        is None
    ), "改名后的插件复用了按原 id 铸的 handler key，会一个 handler 都注册不上"


def test_config_declared_entries_force_a_real_scan(tmp_path: Path) -> None:
    """Packaged handlers are derived from the author's manifest, not this machine's.

    A runtime config or an active profile can carry its own ``entries`` table.
    The packager never saw it, so its handlers are not the set this machine
    should register (codex). Those plugins have to scan.

    Mutation: drop the ``_config_declares_entries`` check.
    """
    from plugin.server.application.plugins import lifecycle_service

    plugin_dir = _write_plugin(tmp_path, entries=[{"id": "go"}])
    meta_path = plugin_dir / packaged_metadata.PACKAGED_METADATA_FILENAME
    payload = json.loads(meta_path.read_text(encoding="utf-8"))
    payload["handlers"] = {"demo.go": {"event_type": "plugin_entry", "id": "go"}}
    payload["entry_methods"] = {"go": "go"}
    meta_path.write_text(json.dumps(payload), encoding="utf-8")

    assert (
        lifecycle_service._read_packaged_isolated_metadata(
            plugin_dir / "plugin.toml", "demo"
        )
        is not None
    ), "前提没成立：没有配置覆盖时本来就该用打包元数据"

    assert (
        lifecycle_service._read_packaged_isolated_metadata(
            plugin_dir / "plugin.toml",
            "demo",
            conf={"entries": [{"id": "from_profile"}]},
        )
        is None
    ), "生效配置自带 entries 时仍然用了包里的 handler，注册的会是另一套"


def test_the_freshness_fingerprint_watches_every_file(tmp_path: Path) -> None:
    """Entries can be derived from data files, not just code.

    A plugin whose module-level code builds entries from a YAML, CSV or
    template invalidates nothing if the fingerprint only looks at
    ``.py``/``.toml``/``.json`` — the host keeps serving a schema derived from
    data that has since changed (codex).

    Mutation: put a suffix filter back into ``_iter_source_files``.
    """
    plugin_dir = _write_plugin(tmp_path, entries=[{"id": "go"}])
    before = packaged_metadata.compute_source_sha256(plugin_dir)

    (plugin_dir / "entries.yaml").write_text("go: {}\n", encoding="utf-8")
    after = packaged_metadata.compute_source_sha256(plugin_dir)

    assert before != after, (
        "加了一个数据文件而指纹没变，从它派生条目的插件会一直用旧 schema"
    )


def _store_that_fails_to_write(monkeypatch: pytest.MonkeyPatch, seed: list[str]) -> None:
    """A config manager that reads fine but cannot write."""
    state = {"pending": list(seed)}

    class _ReadOnlyConfigManager:
        def load_json_config(self, name):
            return dict(state)

        def save_json_config(self, name, payload):
            raise OSError("no space left on device")

    import utils.config_manager as config_manager_module

    monkeypatch.setattr(
        config_manager_module, "get_config_manager", _ReadOnlyConfigManager
    )
    autostart_approvals._reset_cache_for_testing()


def test_a_failed_mark_does_not_pretend_the_plugin_is_gated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In-memory state must not claim more than what reached disk.

    If the pending record cannot be written, this process would believe the
    plugin is held back while the file says nothing — and after a restart it
    autostarts, unapproved, with nothing left to retry the write. Rolling the
    mutation back keeps memory and disk telling the same story: this plugin was
    not gated, and the log says why.

    Mutation: ignore ``_save_locked``'s return value in ``mark_autostart_pending``.
    """
    _store_that_fails_to_write(monkeypatch, [])
    try:
        autostart_approvals.mark_autostart_pending("newcomer")
        assert autostart_approvals.is_autostart_approved("newcomer"), (
            "写盘失败却在内存里当成已拦下：重启后它会未经批准自启，而没人重试"
        )
    finally:
        autostart_approvals._reset_cache_for_testing()


def test_a_failed_clear_keeps_the_plugin_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The mirror direction: a lost approval must be retried, not assumed.

    Mutation: ignore ``_save_locked``'s return value in
    ``clear_autostart_pending``.
    """
    _store_that_fails_to_write(monkeypatch, ["waiting"])
    try:
        assert not autostart_approvals.is_autostart_approved("waiting"), (
            "前提没成立：这个插件本来就该是待批准的"
        )
        autostart_approvals.clear_autostart_pending("waiting")
        assert not autostart_approvals.is_autostart_approved("waiting"), (
            "批准没写成却在内存里当成已完成：重启后旧文件又把它拦下来，没人知道为什么"
        )
    finally:
        autostart_approvals._reset_cache_for_testing()


class _FakeFifoEntry:
    """A directory entry shaped like a FIFO: not a dir, not a symlink, not a file."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.name = Path(path).name

    def is_symlink(self) -> bool:
        return False

    def is_dir(self, follow_symlinks: bool = True) -> bool:
        return False

    def is_file(self, follow_symlinks: bool = True) -> bool:
        return False

    def stat(self, follow_symlinks: bool = True):
        raise AssertionError("stat() reached a non-regular entry")


def test_a_named_pipe_never_reaches_the_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hashing must not read anything that can block.

    ``entry.stat()`` succeeds on a FIFO, socket or device node, so without an
    explicit regular-file check they land in the file list and the digest step
    calls ``Path.read_bytes()`` on them. A FIFO with no writer blocks there
    forever — and registry refresh now holds ``_REGISTRY_REFRESH_LOCK`` across
    the whole operation, so one named pipe in a plugin directory would wedge the
    entire plugin registry (coderabbit).

    Driven through a fake dir entry rather than ``os.mkfifo`` so the guard also
    runs on Windows, where there is no mkfifo.

    Mutation: drop the ``entry.is_file(follow_symlinks=False)`` check — the fake
    entry's ``stat()`` then raises and this fails.
    """
    plugin_dir = _write_plugin(tmp_path, entries=[{"id": "go"}])
    fifo_path = str(plugin_dir / "control.pipe")

    real_scandir = os.scandir

    class _Scan:
        def __init__(self, path):
            self._path = path

        def __enter__(self):
            entries = list(real_scandir(self._path))
            if Path(self._path).resolve() == plugin_dir.resolve():
                entries.append(_FakeFifoEntry(fifo_path))
            return iter(entries)

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(
        packaged_metadata.os, "scandir", lambda path: _Scan(path)
    )

    files, untrustworthy, _dirs = packaged_metadata._iter_source_files(plugin_dir)

    assert fifo_path not in [str(plugin_dir / rel) for rel, _ in files], (
        "命名管道进了摘要列表，read_bytes() 会在没有写端时永久阻塞"
    )
    assert untrustworthy, (
        "非普通文件没有把这棵树标成不可信——摘要覆盖不到它，就不该拿包里的元数据当真"
    )


def test_an_oversized_metadata_file_is_refused_before_parsing(tmp_path: Path) -> None:
    """``plugin.meta.json`` comes from a third-party package; cap it.

    ``json.loads`` materialises the whole document, and registry refresh now
    holds ``_REGISTRY_REFRESH_LOCK`` across the operation, so an enormous
    metadata file in one installed package can exhaust memory while everything
    else waits on the lock (codex).

    Mutation: drop the ``MAX_PACKAGED_METADATA_BYTES`` check.
    """
    plugin_dir = _write_plugin(tmp_path, entries=[{"id": "go"}])
    meta_path = plugin_dir / packaged_metadata.PACKAGED_METADATA_FILENAME

    # 除了体积，这份元数据其它方面完全合法——否则"被拒"可能是缺字段导致的，
    # 去掉大小闸门测试照样通过，守卫等于没守（本轮变异验证抓到过这一点）。
    payload = json.loads(meta_path.read_text(encoding="utf-8"))
    assert packaged_metadata.read_packaged_metadata(plugin_dir) is not None, (
        "前提没成立：这份元数据在放大之前就该是可用的"
    )
    payload["padding"] = "x" * packaged_metadata.MAX_PACKAGED_METADATA_BYTES
    meta_path.write_text(json.dumps(payload), encoding="utf-8")

    assert meta_path.stat().st_size > packaged_metadata.MAX_PACKAGED_METADATA_BYTES, (
        "前提没成立：文件没有超过上限"
    )
    assert packaged_metadata.read_packaged_metadata(plugin_dir) is None, (
        "超大的第三方元数据被原样解析：刷新整段持锁，一份够大的文件能把进程撑爆"
    )


def test_binary_files_are_hashed_byte_for_byte(tmp_path: Path) -> None:
    """CR is a meaningful byte in a binary asset, not a line ending.

    Line-ending normalisation exists so a package built on Windows still
    verifies on Linux — a text-only problem. Applying it to binary assets makes
    two different files hash the same (codex).

    Mutation: normalise every file regardless of suffix.
    """
    plugin_dir = _write_plugin(tmp_path, entries=[{"id": "go"}])
    asset = plugin_dir / "model.bin"

    asset.write_bytes(bytes([0, 13, 10, 1]))
    with_crlf = packaged_metadata.compute_source_sha256(plugin_dir)
    asset.write_bytes(bytes([0, 10, 1]))
    with_lf = packaged_metadata.compute_source_sha256(plugin_dir)

    assert with_crlf != with_lf, (
        "两份不同的二进制资源算出了同一个摘要，改动它不会让元数据失效"
    )


def test_deleting_a_source_file_invalidates_the_metadata(tmp_path: Path) -> None:
    """A deletion leaves every surviving file untouched.

    The mtime fast path only looked at files, so removing one that fed the
    packaged entries was invisible and the host kept serving the pre-deletion
    schema (codex). Directory mtimes move when entries are added or removed.

    Mutation: stop folding directory mtimes into ``newest_source_mtime_ns``.
    """
    plugin_dir = _write_plugin(tmp_path, entries=[{"id": "go"}])
    extra = plugin_dir / "helper.py"
    extra.write_text("HELPER = 1\n", encoding="utf-8")
    meta_path = plugin_dir / packaged_metadata.PACKAGED_METADATA_FILENAME
    payload = json.loads(meta_path.read_text(encoding="utf-8"))
    payload["source_sha256"] = packaged_metadata.compute_source_sha256(plugin_dir)
    meta_path.write_text(json.dumps(payload), encoding="utf-8")
    assert packaged_metadata.read_packaged_metadata(plugin_dir) is not None, (
        "前提没成立：这份元数据本来就该是可用的"
    )

    extra.unlink()

    assert packaged_metadata.read_packaged_metadata(plugin_dir) is None, (
        "删掉一个源文件之后元数据仍被当成新鲜的，宿主会继续用删除前推出来的 schema"
    )


def test_mark_reports_whether_the_gate_is_durable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Callers that promote new code need to know the gate actually landed.

    ``install_builtin_override`` marks before promoting the third-party source.
    If that write is lost, the promotion would go ahead with no pending record
    and the new code autostarts unapproved at the next boot, so the mark has to
    report failure rather than only logging it (coderabbit).

    Mutation: return ``None``/``True`` unconditionally from
    ``mark_autostart_pending``.
    """
    _store_that_fails_to_write(monkeypatch, [])
    try:
        assert autostart_approvals.mark_autostart_pending("newcomer") is False
    finally:
        autostart_approvals._reset_cache_for_testing()

    store: dict[str, object] = {}

    class _WorkingConfigManager:
        def load_json_config(self, name):
            if name not in store:
                raise FileNotFoundError(name)
            return store[name]

        def save_json_config(self, name, payload):
            store[name] = payload

    import utils.config_manager as config_manager_module

    monkeypatch.setattr(
        config_manager_module, "get_config_manager", _WorkingConfigManager
    )
    autostart_approvals._reset_cache_for_testing()
    try:
        assert autostart_approvals.mark_autostart_pending("newcomer") is True
    finally:
        autostart_approvals._reset_cache_for_testing()


def test_a_non_regular_metadata_file_is_never_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``plugin.meta.json`` itself can be a named pipe.

    The regular-file check added for the source walk deliberately skips this
    file — a generated artefact does not take part in its own freshness check —
    so nothing was checking the metadata file itself. ``stat()`` succeeds on a
    FIFO and ``read_text()`` then blocks forever with no writer, while registry
    refresh holds the lock (coderabbit).

    Mutation: drop the ``stat.S_ISREG`` check.
    """
    plugin_dir = _write_plugin(tmp_path, entries=[{"id": "go"}])
    meta_path = plugin_dir / packaged_metadata.PACKAGED_METADATA_FILENAME
    real_stat = Path.stat

    class _FifoStat:
        st_mode = 0o010600  # S_IFIFO
        st_size = 128
        st_mtime_ns = 1

    def _fake_stat(self, *args, **kwargs):
        if Path(self) == meta_path:
            return _FifoStat()
        return real_stat(self, *args, **kwargs)

    def _boom(*_args, **_kwargs):
        raise AssertionError("read_text() reached a non-regular metadata file")

    monkeypatch.setattr(Path, "stat", _fake_stat)
    monkeypatch.setattr(Path, "read_text", _boom)

    assert packaged_metadata.read_packaged_metadata(plugin_dir) is None
