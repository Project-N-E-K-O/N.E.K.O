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


def test_an_already_registered_plugin_is_not_re_gated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Upgrading a plugin must not take away autostart it already had.

    Mutation: drop the ``already_known`` check.
    """
    calls: list[str] = []
    monkeypatch.setattr(
        "plugin.server.infrastructure.autostart_approvals.mark_autostart_pending",
        calls.append,
    )
    from plugin.core.state import state

    monkeypatch.setattr(state, "plugins", {"veteran": {"id": "veteran"}}, raising=False)

    cli_service._mark_new_install_awaiting_autostart(
        {
            "installed_plugins": [
                _installed(tmp_path, "veteran", "veteran"),
                _installed(tmp_path, "rookie", "rookie"),
            ]
        }
    )

    assert calls == ["rookie"], f"升级把老插件的自启动资格也收走了：{calls}"


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
