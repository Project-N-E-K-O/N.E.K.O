"""Every install path withholds autostart until the user starts the plugin.

The gate exists because ``plugin_runtime.auto_start`` defaults to true and is
declared by the plugin itself: without it, a freshly installed plugin runs its
own module-level code at the next greeting without ever having been started.

The guard here is on ``install()`` rather than on any one source-recording
helper. Hanging it off ``_record_requested_install_source`` looked equivalent
and was not: ``upload_and_install`` records its source separately and never
calls that helper, so uploaded plugins — the ones most worth gating — skipped
the check entirely.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from plugin.neko_plugin_cli.core.metadata_probe import write_packaged_metadata
from plugin.server.application.plugin_cli import service as cli_service
from plugin.server.infrastructure import packaged_metadata

pytestmark = pytest.mark.plugin_unit


@pytest.mark.asyncio
async def test_a_plain_install_marks_the_plugin_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fresh-install exit of ``install()`` records the plugin.

    Mutation: move the call back into ``_record_requested_install_source``.
    """
    marked: list[dict] = []
    monkeypatch.setattr(
        cli_service, "_mark_new_install_awaiting_autostart", marked.append
    )
    monkeypatch.setattr(cli_service, "get_install_source_manager", lambda: None)

    install_result = {"installed_plugins": [{"plugin_id": "brand_new"}]}
    service = cli_service.PluginCliService()

    async def _plan_install(**_kwargs):
        return {"action": "install"}

    monkeypatch.setattr(service, "plan_install", _plan_install)
    monkeypatch.setattr(service, "_install_sync", lambda **_kwargs: install_result)

    async def _record(*, install_result, package, source):
        return install_result

    monkeypatch.setattr(service, "_record_requested_install_source", _record)

    await service.install(package="whatever.neko-plugin")

    assert marked == [install_result], (
        "全新安装没有登记待批准，插件会在下一次开机自己跑起来"
    )


@pytest.mark.asyncio
async def test_the_mark_lands_before_any_source_recording(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The gate must not depend on which source-recording path an install takes.

    ``upload_and_install`` records its own install source and never calls
    ``_record_requested_install_source``; the first version of this gate lived
    inside that helper and so did nothing for uploaded plugins. Rather than
    driving the whole upload stack, this pins the invariant that made it break:
    the plugin is marked *before* source recording, so a caller that records
    its source elsewhere is still covered.

    Mutation: move the call back into ``_record_requested_install_source`` —
    this test fails while the plain-install one above still passes.
    """
    marked: list[dict] = []
    monkeypatch.setattr(
        cli_service, "_mark_new_install_awaiting_autostart", marked.append
    )
    monkeypatch.setattr(cli_service, "get_install_source_manager", lambda: None)

    install_result = {"installed_plugins": [{"plugin_id": "uploaded_one"}]}
    service = cli_service.PluginCliService()

    async def _plan_install(**_kwargs):
        return {"action": "install"}

    monkeypatch.setattr(service, "plan_install", _plan_install)
    monkeypatch.setattr(service, "_install_sync", lambda **_kwargs: install_result)

    # 上传路径自己登记来源，走的是 _record_install_source_best_effort，
    # 完全不经过 _record_requested_install_source。
    async def _record(*, install_result, package, source):
        raise AssertionError(
            "前提没成立：这条路本不该经过 _record_requested_install_source"
        )

    monkeypatch.setattr(service, "_record_requested_install_source", _record)

    with pytest.raises(AssertionError):
        await service.install(package="uploaded.neko-plugin", install_source=None)

    assert marked == [install_result], (
        "登记发生在来源登记之后：上传安装的插件因此完全绕过了这道闸"
    )


def _installed(tmp_path: Path, directory_name: str, manifest_id: str) -> dict:
    """One ``installed_plugins`` row, with a real manifest on disk behind it."""
    target = tmp_path / directory_name
    target.mkdir(parents=True, exist_ok=True)
    (target / "plugin.toml").write_text(
        "\n".join(["[plugin]", f"id = '{manifest_id}'", ""]), encoding="utf-8"
    )
    # 安装结果里没有 plugin_id 字段：InstalledPlugin 带的是 target_plugin_id，
    # 而那个值就是目录名。
    return {"target_plugin_id": directory_name, "target_dir": str(target)}


def test_the_gate_does_not_consult_the_registry_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """"Is this plugin new?" is the install plan's answer, not the registry's.

    A refresh that overlaps the install can register the freshly written
    directory before the gate runs; if the gate asked ``state.plugins`` it would
    see the plugin as pre-existing and skip it, so a race would silently grant
    autostart to a plugin the user never started (greptile). A stale registry
    produces the mirror error (codex). Upgrades are excluded at the call site
    instead — the replace exit does not call this at all.

    Mutation: re-add an ``already_known`` check against ``state.plugins``.
    """
    calls: list[str] = []
    monkeypatch.setattr(
        "plugin.server.infrastructure.autostart_approvals.mark_autostart_pending",
        calls.append,
    )
    from plugin.core.state import state

    # 并发刷新已经把它登记进注册表了——这不该让它逃过批准闸。
    monkeypatch.setattr(
        state, "plugins", {"already_seen": {"id": "already_seen"}}, raising=False
    )

    cli_service._mark_new_install_awaiting_autostart(
        {"installed_plugins": [_installed(tmp_path, "already_seen", "already_seen")]}
    )

    assert calls == ["already_seen"], (
        f"并发刷新抢先登记之后，这个插件就绕过了批准闸：{calls}"
    )


def test_an_upgrade_never_reaches_the_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """The replace exit must not re-gate a plugin the user already runs.

    Mutation: call ``_mark_new_install_awaiting_autostart`` from the replace
    exit as well.
    """
    import inspect

    source = inspect.getsource(cli_service.PluginCliService.install)
    marks = source.count("_mark_new_install_awaiting_autostart")
    assert marks == 1, (
        f"install() 里登记待批准的调用点有 {marks} 个；升级路径也登记的话，"
        "用户早就在用的插件会因为一次升级失去自启动资格"
    )


def test_the_gate_records_the_manifest_id_not_the_directory_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The registry keys plugins by their manifest id, so the gate must too.

    ``InstalledPlugin`` only carries ``target_plugin_id``, which is the
    directory name; this repo supports a directory whose name differs from
    ``[plugin].id``. Recording the directory name means the autostart filter
    looks up an id that was never registered and the gate silently does nothing
    (coderabbit).

    Mutation: drop ``_installed_manifest_plugin_id`` and fall back to the
    directory name.
    """
    calls: list[str] = []
    monkeypatch.setattr(
        "plugin.server.infrastructure.autostart_approvals.mark_autostart_pending",
        calls.append,
    )
    from plugin.core.state import state

    monkeypatch.setattr(state, "plugins", {}, raising=False)

    cli_service._mark_new_install_awaiting_autostart(
        {"installed_plugins": [_installed(tmp_path, "some_folder_2", "real_plugin_id")]}
    )

    assert calls == ["real_plugin_id"], (
        f"登记的是目录名而不是 manifest 里的 id，这道闸对该插件完全不生效：{calls}"
    )


def test_write_packaged_metadata_stamps_the_staged_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end through the packaging entry point, not just the helper."""
    source_dir = tmp_path / "src"
    staged_dir = tmp_path / "staged"
    source_dir.mkdir()
    staged_dir.mkdir()
    for directory in (source_dir, staged_dir):
        (directory / "plugin.toml").write_text("id = 'demo'\n", encoding="utf-8")
        (directory / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
    (source_dir / "dev_only.py").write_text("SECRET = 2\n", encoding="utf-8")

    monkeypatch.setattr(
        "plugin.neko_plugin_cli.core.metadata_probe.derive_plugin_metadata",
        lambda plugin_dir, *, hash_dir=None: {
            "schema_version": packaged_metadata.PACKAGED_METADATA_SCHEMA_VERSION,
            "sdk_version": packaged_metadata.SDK_VERSION,
            "source_sha256": packaged_metadata.compute_source_sha256(
                hash_dir or plugin_dir
            ),
            "entries": [],
        },
    )

    written = write_packaged_metadata(source_dir=source_dir, target_dir=staged_dir)
    assert written is not None
    payload = json.loads(written.read_text(encoding="utf-8"))
    assert payload["source_sha256"] == packaged_metadata.compute_source_sha256(
        staged_dir
    ), "打包期没有把 hash_dir 指向暂存目录"


def test_derive_uses_the_hash_dir_it_was_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``derive_plugin_metadata`` must hash ``hash_dir``, not the source tree.

    The sibling test above pins that ``write_packaged_metadata`` *passes*
    ``hash_dir``; this one pins that the value is actually used. Passing an
    argument that the callee ignores looks identical from the caller's side,
    and a guard that only watches the call site survives exactly that mutation.

    Mutation: hash ``plugin_dir`` instead of ``hash_dir or plugin_dir``.
    """
    from plugin.neko_plugin_cli.core import metadata_probe

    source_dir = tmp_path / "src"
    staged_dir = tmp_path / "staged"
    source_dir.mkdir()
    staged_dir.mkdir()
    for directory in (source_dir, staged_dir):
        (directory / "plugin.toml").write_text("id = 'demo'\n", encoding="utf-8")
        (directory / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
    # 只在源目录里、被构建规则排除在包外的文件。
    (source_dir / "dev_only.py").write_text("SECRET = 2\n", encoding="utf-8")
    assert packaged_metadata.compute_source_sha256(
        source_dir
    ) != packaged_metadata.compute_source_sha256(staged_dir), (
        "前提没成立：两棵树内容一样，这条守卫证明不了任何事"
    )

    class _Ctx:
        pid = "demo"
        entry = "demo.main:Plugin"
        conf: dict = {}
        pdata: dict = {}
        python_requirement_paths: list = []

    class _Isolated:
        entries_preview: list = []
        handlers: dict = {}
        entry_methods: dict = {}

    monkeypatch.setattr(
        "plugin.core.registry._parse_single_plugin_config",
        lambda config_path, processed, logger: _Ctx(),
    )
    monkeypatch.setattr(
        "plugin.server.application.plugins.metadata_scanner"
        ".scan_plugin_metadata_isolated",
        lambda **_kwargs: _Isolated(),
    )

    payload = metadata_probe.derive_plugin_metadata(source_dir, hash_dir=staged_dir)

    assert payload["source_sha256"] == packaged_metadata.compute_source_sha256(
        staged_dir
    ), "摘要算的是作者的源目录，用户机器上哈希的却是装出来的那份，两边永远对不上"


def test_reload_counts_as_the_user_starting_a_plugin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reload is a button the user presses, and it works on a stopped plugin.

    ``reload_plugin`` stops then starts; the frontend offers Reload even while a
    plugin is stopped. Starting a pending plugin that way is the same act as
    pressing Start, so it has to clear the pending record — otherwise the plugin
    can be run by hand forever and still never autostart (codex).

    Mutation: drop ``persist_user_intent=True`` from ``reload_plugin``.
    """
    import inspect

    from plugin.server.application.plugins import lifecycle_service

    source = inspect.getsource(lifecycle_service.PluginLifecycleService.reload_plugin)
    assert "persist_user_intent=True" in source, (
        "reload 启动插件时没有带用户意图，待批准记录不会被清掉"
    )


def test_renaming_clears_the_pending_record_under_the_old_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Approval must follow the plugin across an id-conflict rename.

    Installation records the manifest id, but a plugin can register under a
    conflict-resolved runtime id. Clearing only the runtime id leaves the old
    entry behind, and once the conflict goes away that stale record blocks
    autostart forever (coderabbit).

    Mutation: drop the ``previous_plugin_ids`` loop.
    """
    from plugin.server.application.plugins import lifecycle_service

    cleared: list[str] = []
    monkeypatch.setattr(
        lifecycle_service, "clear_autostart_pending", cleared.append
    )
    monkeypatch.setattr(
        lifecycle_service, "migrate_runtime_override", lambda *a, **k: None
    )
    monkeypatch.setattr(lifecycle_service, "set_runtime_override", lambda *a, **k: None)

    lifecycle_service._persist_user_runtime_intent(
        "demo_1", True, previous_plugin_ids=("demo",)
    )

    assert cleared == ["demo_1", "demo"], (
        f"改名前的 id 没被一起清掉，冲突消失后它会继续挡着自启：{cleared}"
    )


def test_metadata_is_obtained_before_the_host_process_starts(tmp_path: Path) -> None:
    """The metadata import must not run concurrently with the plugin's own.

    ``start_plugin`` starts the real process, which imports the plugin. Doing
    the metadata import after that means two concurrent imports of the same
    module: a plugin that takes a file lock, binds a port or starts a singleton
    at import time fails the second one, lifecycle cleanup kills the healthy
    host, and the start is reported as failed (codex). Before this PR the scan
    happened inside ``refresh_plugin``, ahead of the host; refresh no longer
    scans, so the ordering has to be restored here.

    Mutation: move the metadata block back below ``_start_host_with_timeout``.
    """
    import inspect

    from plugin.server.application.plugins import lifecycle_service

    source = inspect.getsource(lifecycle_service.PluginLifecycleService.start_plugin)
    metadata_at = source.find("_read_packaged_isolated_metadata")
    host_start_at = source.find("_start_host_with_timeout(")
    assert metadata_at != -1 and host_start_at != -1, "前提没成立：两个调用点都要在"
    assert metadata_at < host_start_at, (
        "元数据 import 排在 host 启动之后，会和插件进程自己的 import 并发"
    )


def test_the_packaged_metadata_file_is_staged_only_once(tmp_path: Path) -> None:
    """Repo plugins already ship the file, so copying then writing double-counts.

    ``PayloadBuildResult`` sorts its file list but does not de-duplicate, so a
    path recorded twice inflates ``staged_file_count`` and makes
    ``--keep-staging`` list the same file twice (coderabbit).

    Mutation: append unconditionally instead of going through
    ``_record_staged_file``.
    """
    from plugin.neko_plugin_cli.core import build as build_module

    already = tmp_path / "plugin.meta.json"
    already.write_text("{}", encoding="utf-8")
    staged = [already]

    build_module._record_staged_file(staged, already)
    assert staged == [already], f"同一个文件被记了两次：{staged}"

    another = tmp_path / "main.py"
    another.write_text("", encoding="utf-8")
    build_module._record_staged_file(staged, another)
    assert staged == [already, another], "新文件反而没被记上"
