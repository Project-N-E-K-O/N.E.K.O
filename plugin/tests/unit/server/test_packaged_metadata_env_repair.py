"""Foreign-environment metadata is cached outside installed plugin files.

The host cache is isolated by installation, package contents and environment.
Root plugin.meta.local.json remains plugin-owned data, including during builds.
Cache failures and unfingerprintable trees must be rejected before source
hashing. Existing schema upgrades and all metadata validation remain intact.
"""

from __future__ import annotations

import ast
import inspect
import json
import os
import sys
from pathlib import Path

import pytest

from plugin.server.infrastructure import packaged_metadata

pytestmark = pytest.mark.plugin_unit

_META = packaged_metadata.PACKAGED_METADATA_FILENAME
_LOCAL = "plugin.meta.local.json"
_SCHEMA = packaged_metadata.PACKAGED_METADATA_SCHEMA_VERSION


def _write_plugin(
    tmp_path: Path,
    *,
    name: str = "demo",
    build_env: dict | None = None,
    schema: int | None = None,
    with_meta: bool = True,
) -> Path:
    """一个带合法 ``plugin.meta.json`` 的最小插件目录。

    指纹一律按当前树真算，好让读取方除了被测的那个维度之外全部满意。
    """
    plugin_dir = tmp_path / name
    plugin_dir.mkdir(parents=True)
    # 必须是 [plugin] 段：_refresh_scanned_packaged_metadata 会校验 manifest 里的 id
    # 与运行时 id 一致（handler 键里嵌着 id，写错归属就再也对不上）。
    (plugin_dir / "plugin.toml").write_text(f"[plugin]\nid = '{name}'\n", encoding="utf-8")
    (plugin_dir / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
    if not with_meta:
        return plugin_dir
    payload = {
        "schema_version": _SCHEMA if schema is None else schema,
        "sdk_version": packaged_metadata.SDK_VERSION,
        "source_sha256": packaged_metadata.compute_source_sha256(plugin_dir),
        "source_files": packaged_metadata.source_file_names(plugin_dir)[0],
        "source_bytes": packaged_metadata.source_stat_summary(plugin_dir).total_bytes,
        "build_env": (
            packaged_metadata.build_environment() if build_env is None else build_env
        ),
        "entries": [{"id": "go", "name": "Go"}],
        "handlers": {"demo.go": {"event_type": "plugin_entry", "id": "go", "name": "Old"}},
        "entry_methods": {"go": "go"},
        "entries_config_sha256": packaged_metadata.entries_config_digest({}, {}),
    }
    (plugin_dir / _META).write_text(json.dumps(payload), encoding="utf-8")
    return plugin_dir


def _foreign_env(**overrides) -> dict:
    env = dict(packaged_metadata.build_environment())
    env.update(overrides)
    return env


@pytest.mark.parametrize("relative_path", [
    ".vendor.staging-deadbeef", ".vendor.backup-deadbeef",
    ".vendor.backup-deadbeef.pending", "vendor/.vendor.staging-deadbeef",
])
def test_sync_work_paths_do_not_enter_metadata_fingerprint(tmp_path, monkeypatch, relative_path):
    from plugin.neko_plugin_cli.core.build_rules import BuildRuleSet, should_skip_path

    plugin_dir = _write_plugin(tmp_path)
    (plugin_dir / "vendor").mkdir()
    (plugin_dir / "vendor/dependency.py").write_text("VALUE = 1\n", encoding="utf-8")
    before = packaged_metadata.compute_source_sha256(plugin_dir)
    names = packaged_metadata.source_file_names(plugin_dir)[0]
    work_path = plugin_dir / relative_path
    if relative_path.endswith(".pending"):
        work_path.write_text("pending\n", encoding="utf-8")
    else:
        work_path.mkdir()
        (work_path / "large_dependency.py").write_text("VALUE = 2\n", encoding="utf-8")
    assert should_skip_path(Path(relative_path), is_dir=work_path.is_dir(), rules=BuildRuleSet())

    scandir = packaged_metadata.os.scandir

    def guarded_scandir(path):
        assert Path(path) != work_path, "fingerprint descended into a dependency work tree"
        return scandir(path)

    monkeypatch.setattr(packaged_metadata.os, "scandir", guarded_scandir)
    assert packaged_metadata.source_file_names(plugin_dir)[0] == names
    assert packaged_metadata.compute_source_sha256(plugin_dir) == before


@pytest.mark.parametrize("relative_path", [
    ".vendor.backup-notes", ".vendor.staging-deadbeef.pending",
    "data/.vendor.staging-deadbeef",
])
def test_similar_plugin_owned_paths_remain_fingerprinted(tmp_path, relative_path):
    plugin_dir = _write_plugin(tmp_path)
    before = packaged_metadata.compute_source_sha256(plugin_dir)
    source = plugin_dir / relative_path / "owned.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 2\n", encoding="utf-8")
    assert f"{relative_path}/owned.py" in packaged_metadata.source_file_names(plugin_dir)[0]
    assert packaged_metadata.compute_source_sha256(plugin_dir) != before


_SCAN_KWARGS = dict(
    entries=[{"id": "go", "name": "Go"}],
    handlers={"demo.go": {"event_type": "plugin_entry", "id": "go", "name": "Scanned", "timeout": 7}},
    entry_methods={"go": "go"},
    conf={},
    pdata={},
)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_bytes().decode("utf-8"))


def _cache_path(plugin_dir: Path) -> Path:
    path = packaged_metadata.local_packaged_metadata_path(plugin_dir)
    assert path is not None
    return path


def test_package_updates_bound_the_host_cache_and_preserve_current_metadata(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(packaged_metadata, "_LOCAL_METADATA_CACHE_MAX_FILES", 2)
    plugin_dir = _write_plugin(tmp_path, build_env=_foreign_env(python="3.9"))
    paths = []
    for release in range(5):
        package = _read_json(plugin_dir / _META)
        package["release"] = release
        packaged_bytes = json.dumps(package).encode("utf-8")
        (plugin_dir / _META).write_bytes(packaged_bytes)
        assert packaged_metadata.write_local_packaged_metadata(
            plugin_dir,
            before_scan=packaged_metadata.snapshot_source_tree(plugin_dir),
            **_SCAN_KWARGS,
        )
        paths.append(_cache_path(plugin_dir))
        timestamp = (release + 1) * 1_000_000_000
        os.utime(paths[-1], ns=(timestamp, timestamp))
        assert (plugin_dir / _META).read_bytes() == packaged_bytes

    assert set(paths[-1].parent.glob("*.json")) == set(paths[-2:])
    assert packaged_metadata.read_packaged_metadata(
        plugin_dir
    ).built_in_this_environment


def test_cache_retention_preserves_business_files_and_directories(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(packaged_metadata, "_LOCAL_METADATA_CACHE_MAX_FILES", 1)
    plugin_dir = _write_plugin(tmp_path, build_env=_foreign_env(python="3.9"))
    current = _cache_path(plugin_dir)
    current.parent.mkdir(parents=True)
    business = current.parent / "notes.json"
    business.write_text("business data", encoding="utf-8")
    directory = current.parent / ("a" * 64 + ".json")
    directory.mkdir()
    (directory / "keep.txt").write_text("nested data", encoding="utf-8")
    obsolete = current.parent / ("b" * 64 + ".json")
    obsolete.write_text("old generated cache", encoding="utf-8")

    assert packaged_metadata.write_local_packaged_metadata(
        plugin_dir,
        before_scan=packaged_metadata.snapshot_source_tree(plugin_dir),
        **_SCAN_KWARGS,
    )
    assert current.is_file()
    assert not obsolete.exists()
    assert business.read_text(encoding="utf-8") == "business data"
    assert (directory / "keep.txt").read_text(encoding="utf-8") == "nested data"


def test_cache_prune_failure_does_not_fail_a_successful_write(tmp_path, monkeypatch):
    monkeypatch.setattr(packaged_metadata, "_LOCAL_METADATA_CACHE_MAX_FILES", 1)
    plugin_dir = _write_plugin(tmp_path, build_env=_foreign_env(python="3.9"))
    current = _cache_path(plugin_dir)
    current.parent.mkdir(parents=True)
    obsolete = current.parent / ("c" * 64 + ".json")
    obsolete.write_text("old generated cache", encoding="utf-8")
    original_unlink = Path.unlink
    attempted = []

    def deny_obsolete(path, *args, **kwargs):
        if path == obsolete:
            attempted.append(path)
            raise PermissionError("cache is temporarily held open")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", deny_obsolete)
    assert packaged_metadata.write_local_packaged_metadata(
        plugin_dir,
        before_scan=packaged_metadata.snapshot_source_tree(plugin_dir),
        **_SCAN_KWARGS,
    )
    assert attempted == [obsolete]
    assert obsolete.exists()
    assert packaged_metadata.read_packaged_metadata(
        plugin_dir
    ).built_in_this_environment


def test_cache_retention_does_not_delete_a_concurrently_refreshed_file(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(packaged_metadata, "_LOCAL_METADATA_CACHE_MAX_FILES", 1)
    plugin_dir = _write_plugin(tmp_path, build_env=_foreign_env(python="3.9"))
    current = _cache_path(plugin_dir)
    current.parent.mkdir(parents=True)
    obsolete = current.parent / ("d" * 64 + ".json")
    obsolete.write_text("old", encoding="utf-8")
    os.utime(obsolete, ns=(1, 1))
    original_stat = Path.stat

    def refresh_before_recheck(path, *args, **kwargs):
        if path == obsolete:
            path.write_text("refreshed by another writer", encoding="utf-8")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", refresh_before_recheck)
    assert packaged_metadata.write_local_packaged_metadata(
        plugin_dir,
        before_scan=packaged_metadata.snapshot_source_tree(plugin_dir),
        **_SCAN_KWARGS,
    )
    assert obsolete.read_text(encoding="utf-8") == "refreshed by another writer"


# ── 1. 核心：写 sidecar，发行产物不动 ────────────────────────────────────


def test_an_env_mismatched_package_gets_a_sidecar_and_the_package_is_untouched(tmp_path) -> None:
    """变异：让 write_local_packaged_metadata 改写 plugin.meta.json 本身。"""
    plugin_dir = _write_plugin(tmp_path, build_env=_foreign_env(python="3.9"))
    shipped_before = (plugin_dir / _META).read_bytes()

    # 前提：这不是 schema 路径。
    assert packaged_metadata.stale_packaged_schema_version(plugin_dir) is None
    assert packaged_metadata.packaged_metadata_env_mismatched(plugin_dir) is True
    assert packaged_metadata.packaged_metadata_needs_rebuild(plugin_dir) is True

    packaged = packaged_metadata.read_packaged_metadata(plugin_dir)
    assert packaged is not None and packaged.built_in_this_environment is False, (
        "前提没成立：读取方应该拒绝这份异环境元数据（但只打标记，不报错）"
    )
    assert not (plugin_dir / _LOCAL).exists()

    before_scan = packaged_metadata.snapshot_source_tree(plugin_dir)
    assert packaged_metadata.write_local_packaged_metadata(
        plugin_dir, before_scan=before_scan, **_SCAN_KWARGS
    ) is True

    # 1) 发行产物一个字节都没动
    assert (plugin_dir / _META).read_bytes() == shipped_before, (
        "发行产物被改写了——sidecar 方案的全部意义就在于不动它"
    )
    # 2) 缓存落在宿主运行时目录，内容是**本机**的答案
    assert _cache_path(plugin_dir).exists()
    assert not (plugin_dir / _LOCAL).exists()
    assert not _cache_path(plugin_dir).is_relative_to(plugin_dir)
    local = _read_json(_cache_path(plugin_dir))
    assert local["build_env"] == packaged_metadata.build_environment()
    assert local["schema_version"] == _SCHEMA
    assert local["handlers"]["demo.go"]["name"] == "Scanned", "写进去的不是这次扫描的结果"
    assert local["handlers"]["demo.go"]["timeout"] == 7
    assert local["source_sha256"] == packaged_metadata.compute_source_sha256(plugin_dir)
    # 3) 读取方现在优先命中 sidecar → 下次启动走快路径
    after = packaged_metadata.read_packaged_metadata(plugin_dir)
    assert after is not None
    assert after.built_in_this_environment is True, "sidecar 写好了读取方却仍拒绝它"
    assert after.handlers["demo.go"]["name"] == "Scanned"


def test_host_cache_does_not_change_the_installed_source_fingerprint(tmp_path) -> None:
    """Cache writes leave both the original metadata and source tree intact."""
    plugin_dir = _write_plugin(tmp_path, build_env=_foreign_env(python="3.9"))
    sha_before = packaged_metadata.compute_source_sha256(plugin_dir)
    names_before = packaged_metadata.source_file_names(plugin_dir)[0]
    assert _META not in names_before, "前提没成立：包内那份本来就该被排除"

    packaged_metadata.write_local_packaged_metadata(
        plugin_dir,
        before_scan=packaged_metadata.snapshot_source_tree(plugin_dir),
        **_SCAN_KWARGS,
    )
    assert _cache_path(plugin_dir).exists(), "前提没成立：本机缓存没写出来"
    assert not (plugin_dir / _LOCAL).exists()

    assert _LOCAL not in packaged_metadata.source_file_names(plugin_dir)[0], (
        "sidecar 进了源文件清单 —— 它会让包内那份 plugin.meta.json 判定失配而失效"
    )
    assert packaged_metadata.compute_source_sha256(plugin_dir) == sha_before, (
        "写出 sidecar 改变了源树摘要 —— 同上，会自我失效"
    )
    assert packaged_metadata.source_stat_summary(plugin_dir).names == names_before

    # 结果：包内那份**仍然**有效（只是 build_env 不是本机的），sidecar 覆盖它。
    shipped = packaged_metadata._read_packaged_metadata_from(plugin_dir / _META, plugin_dir)
    assert shipped is not None, "包内那份被 sidecar 的存在弄失效了"
    assert shipped.built_in_this_environment is False


def test_an_unusable_sidecar_falls_back_to_the_package_file(tmp_path) -> None:
    """sidecar 通不过校验时必须**静默回落**到包内那份，而不是报错、也不是返回 None。

    故意用"手写一份坏 sidecar"而不是"改源码让它过时"来构造：后者依赖 mtime 判据，
    而 mtime 在 Windows 上有量化，写入与打戳落在同一刻度时快路径会放过它——源码里
    ``_read_packaged_metadata_from`` 自己的注释也说过清单才是确定性判据、时间戳会
    "本机过、CI 挂"。这里要验的是**回落逻辑**，不该顺带赌一个时序。
    """
    plugin_dir = _write_plugin(tmp_path, build_env=_foreign_env(python="3.9"))
    shipped = _read_json(plugin_dir / _META)

    # 一份清单对不上的 sidecar（模拟插件升级后残留的旧 sidecar）。
    #
    # 用"文件清单不匹配"而不是"摘要不匹配"来构造失效，因为清单判据是**确定性**的、
    # 且排在 mtime 快路径**之前**；而摘要只在 ``newest_source_ns > meta.mtime`` 时才
    # 重算——一份刚写出来的 sidecar mtime 比源码新，快路径会直接放行，摘要写成什么
    # 都不检查（实测：source_sha256 填 64 个 0 照样被接受）。这里要验的是回落逻辑，
    # 不该依赖一个只在特定 mtime 关系下才生效的判据。
    stale = dict(shipped)
    stale["build_env"] = packaged_metadata.build_environment()
    stale["source_files"] = list(shipped["source_files"]) + ["no_longer_here.py"]
    stale["handlers"] = {"demo.go": {"event_type": "plugin_entry", "id": "go", "name": "StaleSidecar"}}
    cache_path = _cache_path(plugin_dir)
    cache_path.parent.mkdir(parents=True)
    cache_path.write_text(json.dumps(stale), encoding="utf-8")

    got = packaged_metadata.read_packaged_metadata(plugin_dir)
    assert got is not None, "sidecar 失效后返回了 None —— 应该回落到包内那份"
    assert got.built_in_this_environment is False, "回落到的不是包内那份（异环境）"
    assert got.handlers["demo.go"]["name"] == "Old", f"用到的还是坏 sidecar：{got.handlers}"


def test_a_sidecar_from_another_environment_is_not_used(tmp_path) -> None:
    """变异：``read_packaged_metadata`` 里去掉 ``local.built_in_this_environment`` 这一条。

    sidecar 的价值全在于它是**本机**的答案。用户装完 sidecar 之后又升级了 Python，
    那份 sidecar 就变成了和包内文件一样的"异环境答案"——必须同样被拒绝，退回去付
    一次扫描（然后重新写一份新的 sidecar，自愈）。少了这个判断，一份过期的 sidecar
    会被当成权威，插件于是暴露一批它在本机不会注册的入口。
    """
    plugin_dir = _write_plugin(tmp_path, build_env=_foreign_env(python="3.9"))
    packaged_metadata.write_local_packaged_metadata(
        plugin_dir,
        before_scan=packaged_metadata.snapshot_source_tree(plugin_dir),
        **_SCAN_KWARGS,
    )
    local_path = _cache_path(plugin_dir)
    assert packaged_metadata.read_packaged_metadata(plugin_dir).built_in_this_environment is True

    # 模拟"写完 sidecar 之后又换了 Python"：把 sidecar 的 build_env 也改成异环境
    raw = _read_json(local_path)
    raw["build_env"] = _foreign_env(python="3.8")
    local_path.write_text(json.dumps(raw), encoding="utf-8")

    got = packaged_metadata.read_packaged_metadata(plugin_dir)
    assert got is not None
    assert got.built_in_this_environment is False, "用了异环境的 sidecar —— 它和包内那份一样不可信"
    assert got.handlers["demo.go"]["name"] == "Old", "拿到的是 sidecar 的表，不是包内那份"
    # 而且它重新变成"需要修"的状态 → 下次启动会扫描并重写 sidecar（自愈）
    assert packaged_metadata.packaged_metadata_needs_rebuild(plugin_dir) is True


# ── 2. 不越权 ───────────────────────────────────────────────────────────


def test_a_matching_environment_never_writes_a_sidecar(tmp_path) -> None:
    """本机打的包不需要 sidecar，也不该每次启动白拍一次指纹。"""
    plugin_dir = _write_plugin(tmp_path)

    assert packaged_metadata.packaged_metadata_env_mismatched(plugin_dir) is False
    assert packaged_metadata.packaged_metadata_needs_rebuild(plugin_dir) is False
    assert not (plugin_dir / _LOCAL).exists()

    assert packaged_metadata.write_local_packaged_metadata(
        plugin_dir,
        before_scan=packaged_metadata.snapshot_source_tree(plugin_dir),
        **_SCAN_KWARGS,
    ) is False, "本机包被写了 sidecar"
    assert not (plugin_dir / _LOCAL).exists()


def test_a_plugin_without_packaged_metadata_never_grows_one(tmp_path) -> None:
    """手工放入 / dev 模式的插件从来没有过 plugin.meta.json，不该因为启动一次就长出一份。

    这与既有测试 ``test_a_scan_does_not_write_metadata_it_has_no_business_writing[no_file]``
    是同一条不变量，只是从 sidecar 这一侧再钉一次。
    """
    plugin_dir = _write_plugin(tmp_path, with_meta=False)

    assert packaged_metadata.packaged_metadata_env_mismatched(plugin_dir) is False, (
        "缺文件被当成了 env 不匹配"
    )
    assert packaged_metadata.packaged_metadata_needs_rebuild(plugin_dir) is False
    assert packaged_metadata.write_local_packaged_metadata(
        plugin_dir,
        before_scan=packaged_metadata.snapshot_source_tree(plugin_dir),
        **_SCAN_KWARGS,
    ) is False
    assert not (plugin_dir / _LOCAL).exists()
    assert not (plugin_dir / _META).exists()


def test_a_newer_schema_is_never_downgraded_into_a_sidecar(tmp_path) -> None:
    """变异：把 ``packaged_metadata_env_mismatched`` 里的 schema 相等判断去掉。

    一份比本机更新的包（用户降级了 N.E.K.O）里的表可能用到本机读不懂的字段；用本机
    的 schema 号给它写一份 sidecar，读取方会优先命中它 —— 那就是**降级**，而且降完
    就再也回不去了（sidecar 会一直盖住那份更新的包内文件）。
    """
    newer = _SCHEMA + 1
    plugin_dir = _write_plugin(tmp_path, build_env=_foreign_env(python="3.9"), schema=newer)

    assert packaged_metadata.packaged_metadata_env_mismatched(plugin_dir) is False
    assert packaged_metadata.write_local_packaged_metadata(
        plugin_dir,
        before_scan=packaged_metadata.snapshot_source_tree(plugin_dir),
        **_SCAN_KWARGS,
    ) is False
    assert not (plugin_dir / _LOCAL).exists()
    assert _read_json(plugin_dir / _META)["schema_version"] == newer


def test_no_sidecar_without_a_fresh_scan(tmp_path, monkeypatch) -> None:
    """变异：让 ``before_scan is None`` 时也写。

    没有扫描结果就没有"本机 import 这棵树学到了什么"，写出来的表只能来自那份异环境
    的旧文件——那正好是安全属性禁止的事。

    这条属性有**两道**独立的门（``before_scan is None`` 的早退，以及后面的
    ``after_scan != before_scan``），只看返回值区分不出是哪道挡住的，所以断言的是
    第一道**真的早退了**：它后面的全树 stat/哈希一次都不该跑。
    """
    plugin_dir = _write_plugin(tmp_path, build_env=_foreign_env(python="3.9"))
    touched: list[str] = []

    def _tripwire(name):
        def _wrapped(*args, **kwargs):
            touched.append(name)
            raise AssertionError(f"{name} 在 before_scan is None 时仍被执行——早退门失效")

        return _wrapped

    monkeypatch.setattr(packaged_metadata, "source_stat_summary", _tripwire("source_stat_summary"))
    monkeypatch.setattr(packaged_metadata, "snapshot_source_tree", _tripwire("snapshot_source_tree"))

    assert packaged_metadata.write_local_packaged_metadata(
        plugin_dir, before_scan=None, **_SCAN_KWARGS
    ) is False
    assert not (plugin_dir / _LOCAL).exists()
    assert touched == []


def test_python_minor_is_part_of_the_environment_identity() -> None:
    """变异：把 ``build_environment`` 的 python 改成只取 major。

    ``build_environment`` 的 docstring 自己写了理由：插件可以按 ``sys.version_info``
    决定注册哪些 entry，而小版本之间 C 扩展 ABI 也不兼容。放宽到 major 会让一份在
    3.11 上 import 出来的表被当成 3.13 的权威答案——插件于是暴露一批它在本机根本不会
    注册的入口，模型会去调一个不存在的 entry。sidecar 方案消除的是"永远修不好"，
    **不是**"判得严"。
    """
    env = packaged_metadata.build_environment()
    assert set(env) == {"os", "python", "arch"}, f"build_env 的维度变了：{sorted(env)}"
    assert env["python"] == f"{sys.version_info.major}.{sys.version_info.minor}", (
        f"python 不再是 major.minor 精度：{env['python']}"
    )
    for key in ("os", "python", "arch"):
        foreign = dict(env)
        foreign[key] = "definitely-not-this-machine"
        assert packaged_metadata._environment_matches(foreign) is False, f"{key} 不同却判成匹配"
    assert packaged_metadata._environment_matches(env) is True
    assert packaged_metadata._environment_matches(None) is False
    assert packaged_metadata._environment_matches("not-a-mapping") is False


# ── 3. 接线 ─────────────────────────────────────────────────────────────


def test_the_start_path_snapshots_the_tree_for_an_env_mismatched_package(tmp_path) -> None:
    """变异：把 ``_snapshot_package_tree_for_rebuild`` 的判据换回 schema-only。

    判据一换回去，异环境的包就拿不到 ``before_scan``，而两个写入函数在
    ``before_scan is None`` 时都直接返回 False —— 修复静默失效，测试还全绿。
    """
    from plugin.server.application.plugins import lifecycle_service

    mismatched = _write_plugin(tmp_path / "a", name="demo", build_env=_foreign_env(python="3.9"))
    matching = _write_plugin(tmp_path / "b", name="demo")
    no_meta = _write_plugin(tmp_path / "c", name="demo", with_meta=False)

    assert lifecycle_service._snapshot_package_tree_for_rebuild(mismatched / "plugin.toml") is not None, (
        "env 不匹配的包没有拍指纹 → 扫描结果无处可写 → 永久走慢路径"
    )
    assert lifecycle_service._snapshot_package_tree_for_rebuild(matching / "plugin.toml") is None, (
        "本机包不该付这次全树哈希"
    )
    assert lifecycle_service._snapshot_package_tree_for_rebuild(no_meta / "plugin.toml") is None, (
        "没有 plugin.meta.json 的插件不该拍指纹（也不该长出一份来）"
    )

    source = inspect.getsource(lifecycle_service._snapshot_package_tree_for_rebuild)
    assert "packaged_metadata_needs_rebuild" in source
    assert "stale_packaged_schema_version" not in source, (
        "又只看 schema 过期了 —— env 不匹配那条路会静默失效"
    )


def test_the_dispatch_keeps_the_existing_schema_path_and_only_adds_the_sidecar(tmp_path) -> None:
    """变异：把分派改成"先判 env、后判 schema"，或者干脆只留一条。

    schema 过期时**必须**仍然就地改写包内那份（既有行为，有测试钉着）；只有
    "schema 当前 + env 异环境"才写 sidecar。两者同时成立时走 schema 路径，这样这次
    改动不改变任何已有行为。
    """
    from plugin.server.application.plugins import lifecycle_service

    source = inspect.getsource(lifecycle_service._refresh_scanned_packaged_metadata)
    schema_at = source.index("refresh_stale_packaged_metadata(")
    local_at = source.index("write_local_packaged_metadata(")
    gate_at = source.index("stale_packaged_schema_version(plugin_dir) is not None")
    assert gate_at < schema_at < local_at, (
        "分派顺序变了：schema 过期这条路必须仍然优先走就地改写，否则会改变既有行为"
    )

    # schema 过期 → 就地改写，不写 sidecar
    stale_dir = _write_plugin(tmp_path / "stale", name="demo", schema=_SCHEMA - 1)
    scanned = _fake_scanned()
    _upgrade(stale_dir, scanned)
    assert _read_json(stale_dir / _META)["schema_version"] == _SCHEMA, "schema 过期没有就地升级"
    assert not (stale_dir / _LOCAL).exists(), "schema 路径不该写 sidecar"

    # schema 当前 + env 异环境 → 只写 sidecar
    env_dir = _write_plugin(tmp_path / "env", name="demo", build_env=_foreign_env(python="3.9"))
    shipped_before = (env_dir / _META).read_bytes()
    _upgrade(env_dir, scanned)
    assert (env_dir / _META).read_bytes() == shipped_before, "env 路径改写了发行产物"
    assert _cache_path(env_dir).exists(), "env 路径没有写宿主缓存"
    assert not (env_dir / _LOCAL).exists()


def _upgrade(plugin_dir: Path, scanned) -> None:
    """走真实的分派函数，并把 manifest 原样当作生效配置传进去。

    传 manifest 本身是为了让 ``_refresh_scanned_packaged_metadata`` 的两道前置守卫都通过
    （manifest id 与运行时 id 一致、生效 entries 表就是 manifest 自己那份，摘要相等）。
    本测试要验的是"写到哪"，不是那两道守卫——它们由 test_plugins_lifecycle_service 里
    既有的用例覆盖。
    """
    import tomllib

    from plugin.server.application.plugins import lifecycle_service

    config_path = plugin_dir / "plugin.toml"
    manifest = tomllib.loads(config_path.read_text(encoding="utf-8"))
    pdata = manifest.get("plugin") if isinstance(manifest.get("plugin"), dict) else {}
    lifecycle_service._refresh_scanned_packaged_metadata(
        config_path,
        "demo",
        scanned,
        before_scan=packaged_metadata.snapshot_source_tree(plugin_dir),
        conf=manifest,
        pdata=pdata,
    )


def _fake_scanned():
    from plugin.server.application.plugins.metadata_scanner import IsolatedPluginMetadata

    return IsolatedPluginMetadata(
        entries_preview=_SCAN_KWARGS["entries"],
        handlers=_SCAN_KWARGS["handlers"],
        entry_methods=_SCAN_KWARGS["entry_methods"],
    )


def test_existing_root_local_json_is_preserved_and_remains_source_data(tmp_path):
    from plugin.server.application.plugins.metadata_scanner import scan_plugin_metadata_isolated

    plugin_dir = _write_plugin(tmp_path, build_env=_foreign_env(python="3.9"))
    business_file = plugin_dir / _LOCAL
    business_file.write_text('{"business_data":"Original entry"}', encoding="utf-8")
    (plugin_dir / "__init__.py").write_text(
        "import json\nfrom pathlib import Path\n"
        "from plugin.sdk.plugin.decorators import plugin_entry\n"
        "LABEL = json.loads((Path(__file__).parent / 'plugin.meta.local.json').read_text())['business_data']\n"
        "class Plugin:\n"
        "    @plugin_entry(id='go', name=LABEL)\n"
        "    def go(self): return LABEL\n", encoding="utf-8",
    )
    # Model an existing package produced before the host cache was introduced.
    payload = _read_json(plugin_dir / _META)
    payload["source_sha256"] = packaged_metadata.compute_source_sha256(plugin_dir)
    summary = packaged_metadata.source_stat_summary(plugin_dir)
    payload["source_files"] = summary.names
    payload["source_bytes"] = summary.total_bytes
    (plugin_dir / _META).write_text(json.dumps(payload), encoding="utf-8")
    saved_business = business_file.read_bytes()
    saved_sources = packaged_metadata.snapshot_source_tree(plugin_dir)
    from plugin.server.application.plugins.installation_transactions.manual_takeover import _replaceable_content_sha256
    saved_content = _replaceable_content_sha256(plugin_dir)
    before = packaged_metadata.snapshot_packaged_metadata_rebuild_tree(plugin_dir)
    scan_kwargs = dict(
        plugin_id="demo", module_path="plugins.demo", class_name="Plugin",
        config_path=plugin_dir / "plugin.toml", conf={}, pdata={}, source_only=True,
    )
    scanned = scan_plugin_metadata_isolated(**scan_kwargs)
    assert packaged_metadata.write_local_packaged_metadata(
        plugin_dir, before_scan=before, entries=scanned.entries_preview,
        handlers=scanned.handlers, entry_methods=scanned.entry_methods, conf={}, pdata={},
    )
    assert business_file.read_bytes() == saved_business
    assert packaged_metadata.snapshot_source_tree(plugin_dir) == saved_sources
    assert _replaceable_content_sha256(plugin_dir) == saved_content
    assert _LOCAL in packaged_metadata.source_file_names(plugin_dir)[0]
    assert scan_plugin_metadata_isolated(**scan_kwargs).handlers["demo.go"]["name"] == "Original entry"
    assert packaged_metadata.read_packaged_metadata(plugin_dir).built_in_this_environment


def test_unwritable_cache_skips_hashing_and_recovers_after_write_access_returns(tmp_path, monkeypatch):
    from plugin.server.application.plugins import lifecycle_service

    plugin_dir = _write_plugin(tmp_path, build_env=_foreign_env(python="3.9"))
    real_mkstemp = packaged_metadata.tempfile.mkstemp
    real_hash = packaged_metadata.compute_source_sha256
    hashes = []

    def count_hash(path):
        hashes.append(path)
        return real_hash(path)

    def unavailable_cache(*args, **kwargs):
        raise PermissionError("runtime cache is read-only")

    monkeypatch.setattr(packaged_metadata, "compute_source_sha256", count_hash)
    monkeypatch.setattr(packaged_metadata.tempfile, "mkstemp", unavailable_cache)
    for _ in range(2):
        assert lifecycle_service._snapshot_package_tree_for_rebuild(plugin_dir / "plugin.toml") is None
    assert hashes == []
    monkeypatch.setattr(packaged_metadata.tempfile, "mkstemp", real_mkstemp)
    before = lifecycle_service._snapshot_package_tree_for_rebuild(plugin_dir / "plugin.toml")
    assert before is not None
    assert packaged_metadata.write_local_packaged_metadata(plugin_dir, before_scan=before, **_SCAN_KWARGS)


def test_read_only_installed_code_uses_writable_runtime_cache(tmp_path, monkeypatch):
    plugin_dir = _write_plugin(tmp_path, build_env=_foreign_env(python="3.9"))
    real_mkstemp = packaged_metadata.tempfile.mkstemp
    attempts = []

    def reject_installed_writes(*args, **kwargs):
        destination = Path(kwargs["dir"])
        attempts.append(destination)
        if destination.is_relative_to(plugin_dir):
            raise PermissionError("installed code is read-only")
        return real_mkstemp(*args, **kwargs)

    monkeypatch.setattr(packaged_metadata.tempfile, "mkstemp", reject_installed_writes)
    before = packaged_metadata.snapshot_packaged_metadata_rebuild_tree(plugin_dir)
    assert before is not None
    assert packaged_metadata.write_local_packaged_metadata(plugin_dir, before_scan=before, **_SCAN_KWARGS)
    assert attempts and all(not path.is_relative_to(plugin_dir) for path in attempts)
    assert packaged_metadata.read_packaged_metadata(plugin_dir).built_in_this_environment
    assert not (plugin_dir / _LOCAL).exists()


@pytest.mark.parametrize("refusal", ["untrustworthy", "empty", "unicode"])
def test_uncacheable_tree_is_rejected_before_hashing(tmp_path, monkeypatch, refusal):
    plugin_dir = _write_plugin(tmp_path, build_env=_foreign_env(python="3.9"))
    if refusal == "untrustworthy":
        monkeypatch.setattr(packaged_metadata, "source_stat_summary", lambda _: packaged_metadata.SourceStatSummary(untrustworthy=True))
    elif refusal == "empty":
        monkeypatch.setattr(packaged_metadata, "empty_source_directories", lambda _: ["empty"])
    else:
        monkeypatch.setattr(packaged_metadata, "unicode_renamed_source_files", lambda _: ["renamed"])
    monkeypatch.setattr(packaged_metadata, "compute_source_sha256", lambda _: pytest.fail("Rejected tree must not be hashed"))
    assert packaged_metadata.snapshot_packaged_metadata_rebuild_tree(plugin_dir) is None


def test_final_write_failure_does_not_repeat_hashes_and_new_cache_root_recovers(tmp_path, monkeypatch):
    plugin_dir = _write_plugin(tmp_path, build_env=_foreign_env(python="3.9"))
    real_hash = packaged_metadata.compute_source_sha256
    real_write = packaged_metadata.atomic_write_bytes
    hashes = []

    def count_hash(path):
        hashes.append(path)
        return real_hash(path)

    def fail_write(*args, **kwargs):
        raise PermissionError("cache target cannot be replaced")

    monkeypatch.setattr(packaged_metadata, "compute_source_sha256", count_hash)
    before = packaged_metadata.snapshot_packaged_metadata_rebuild_tree(plugin_dir)
    monkeypatch.setattr(packaged_metadata, "atomic_write_bytes", fail_write)
    assert not packaged_metadata.write_local_packaged_metadata(plugin_dir, before_scan=before, **_SCAN_KWARGS)
    assert len(hashes) == 2
    assert packaged_metadata.snapshot_packaged_metadata_rebuild_tree(plugin_dir) is None
    assert len(hashes) == 2
    monkeypatch.setattr(packaged_metadata, "atomic_write_bytes", real_write)
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(tmp_path / "other_runtime"))
    before = packaged_metadata.snapshot_packaged_metadata_rebuild_tree(plugin_dir)
    assert before is not None
    assert packaged_metadata.write_local_packaged_metadata(plugin_dir, before_scan=before, **_SCAN_KWARGS)


def test_cache_identity_changes_with_installation_environment_and_package(tmp_path, monkeypatch):
    first = _write_plugin(tmp_path / "a", build_env=_foreign_env(python="3.9"))
    second = _write_plugin(tmp_path / "b", build_env=_foreign_env(python="3.9"))
    original = _cache_path(first)
    assert _cache_path(second) != original
    real_environment = packaged_metadata.build_environment
    monkeypatch.setattr(packaged_metadata, "build_environment", lambda: {**real_environment(), "python": "other"})
    assert _cache_path(first) != original
    monkeypatch.setattr(packaged_metadata, "build_environment", real_environment)
    payload = _read_json(first / _META)
    payload["handlers"]["demo.go"]["name"] = "New package"
    (first / _META).write_text(json.dumps(payload), encoding="utf-8")
    assert _cache_path(first) != original


def test_long_runtime_cache_path_can_be_written(tmp_path, monkeypatch):
    plugin_dir = _write_plugin(tmp_path, build_env=_foreign_env(python="3.9"))
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(tmp_path / ("runtime_" + "r" * 100)))
    before = packaged_metadata.snapshot_packaged_metadata_rebuild_tree(plugin_dir)
    assert before is not None
    assert packaged_metadata.write_local_packaged_metadata(plugin_dir, before_scan=before, **_SCAN_KWARGS)
    assert packaged_metadata.read_packaged_metadata(plugin_dir).built_in_this_environment


def test_the_two_write_paths_share_one_set_of_refusals() -> None:
    """变异：把共用体复制一份到 write_local_packaged_metadata 里，然后只改一边。

    读取方对两份文件跑的是同一套校验；一边写得出去、另一边读不进来，就等于白写。
    所以拒绝理由必须共用同一个函数，而不是各写一份。
    """
    shared = inspect.getsource(packaged_metadata._write_scanned_packaged_metadata)
    for guard in ("untrustworthy", "empty_source_directories", "unicode_renamed_source_files",
                  "after_scan != before_scan", "MAX_PACKAGED_METADATA_BYTES"):
        assert guard in shared, f"共用体里少了 {guard}"

    for fn in (packaged_metadata.refresh_stale_packaged_metadata,
               packaged_metadata.write_local_packaged_metadata):
        src = inspect.getsource(fn)
        assert "_write_scanned_packaged_metadata(" in src, (
            f"{fn.__name__} 没有走共用体——两条写路径的拒绝理由会各自漂移"
        )
        tree = ast.parse(src)
        called = {
            n.func.id for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        for duplicated in ("source_stat_summary", "snapshot_source_tree", "atomic_write_bytes"):
            assert duplicated not in called, (
                f"{fn.__name__} 自己又做了一遍 {duplicated}——应该只在共用体里做一次"
            )


@pytest.mark.parametrize("invalid", ["oversized", "deep_json", "invalid_utf8", "directory"])
def test_every_metadata_read_path_rejects_unsafe_input(tmp_path, monkeypatch, invalid):
    meta_path = tmp_path / _META
    if invalid == "directory":
        meta_path.mkdir()
    elif invalid == "oversized":
        meta_path.write_bytes(b" " * (packaged_metadata.MAX_PACKAGED_METADATA_BYTES + 1))
    elif invalid == "deep_json":
        meta_path.write_bytes(b'{"schema_version":4,"nested":' + b"[" * 1500 + b"0" + b"]" * 1500 + b"}")
    else:
        meta_path.write_bytes(b"\xff")
    if invalid in {"directory", "oversized"}:
        original_open = Path.open

        def unexpected_open(path, *args, **kwargs):
            if path == meta_path:
                pytest.fail("Rejected metadata must not be opened")
            return original_open(path, *args, **kwargs)

        monkeypatch.setattr(Path, "open", unexpected_open)
    assert packaged_metadata.read_packaged_metadata(tmp_path) is None
    assert packaged_metadata.stale_packaged_schema_version(tmp_path) is None
    assert packaged_metadata.packaged_metadata_env_mismatched(tmp_path) is False
    assert packaged_metadata.packaged_metadata_needs_rebuild(tmp_path) is False


def test_metadata_size_is_checked_after_open_as_well(tmp_path, monkeypatch):
    meta_path = tmp_path / _META
    meta_path.write_text('{"schema_version":4}', encoding="utf-8")
    original_open = packaged_metadata.os.open

    def grow_before_open(path, flags, *args, **kwargs):
        meta_path.write_bytes(b" " * (packaged_metadata.MAX_PACKAGED_METADATA_BYTES + 1))
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(packaged_metadata.os, "open", grow_before_open)
    assert packaged_metadata._read_metadata_json(meta_path) is None


def test_metadata_read_is_bounded_even_if_stat_reports_a_smaller_file(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import stat

    meta_path = tmp_path / _META
    meta_path.write_bytes(b" " * (packaged_metadata.MAX_PACKAGED_METADATA_BYTES + 100))
    original_stat = Path.stat
    original_fdopen = packaged_metadata.os.fdopen
    small_stat = SimpleNamespace(st_mode=stat.S_IFREG, st_size=1)
    monkeypatch.setattr(Path, "stat", lambda path, **kw: small_stat if path == meta_path else original_stat(path, **kw))
    monkeypatch.setattr(packaged_metadata.os, "fstat", lambda _fd: small_stat)
    read_limits = []

    class BoundedFile:
        def __init__(self, fd, mode):
            self.handle = original_fdopen(fd, mode)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.handle.__exit__(*args)

        def fileno(self):
            return self.handle.fileno()

        def read(self, limit):
            read_limits.append(limit)
            assert limit == packaged_metadata.MAX_PACKAGED_METADATA_BYTES + 1
            return self.handle.read(limit)

    monkeypatch.setattr(packaged_metadata.os, "fdopen", BoundedFile)
    assert packaged_metadata._read_metadata_json(meta_path) is None
    assert read_limits == [packaged_metadata.MAX_PACKAGED_METADATA_BYTES + 1]


@pytest.mark.parametrize("ineligible", ["runtime_id", "entries_override"])
def test_ineligible_rebuilds_do_not_hash_the_source_tree(tmp_path, monkeypatch, ineligible):
    import tomllib
    from plugin.server.application.plugins import lifecycle_service

    plugin_dir = _write_plugin(tmp_path, build_env=_foreign_env(python="3.9"))
    manifest = tomllib.loads((plugin_dir / "plugin.toml").read_text(encoding="utf-8"))
    conf = dict(manifest)
    if ineligible == "entries_override":
        conf["entries"] = [{"id": "profile-only"}]

    def unexpected_snapshot(*_args):
        pytest.fail("An ineligible rebuild must not hash the source tree")

    monkeypatch.setattr(lifecycle_service, "snapshot_packaged_metadata_rebuild_tree", unexpected_snapshot)
    assert lifecycle_service._snapshot_package_tree_for_rebuild(
        plugin_dir / "plugin.toml",
        plugin_id="renamed" if ineligible == "runtime_id" else "demo",
        conf=conf,
        pdata=manifest["plugin"],
    ) is None


@pytest.mark.parametrize("probe_succeeds", [False, True])
def test_direct_probe_preserves_local_metadata_named_business_data(tmp_path, monkeypatch, probe_succeeds):
    from plugin.neko_plugin_cli.core import metadata_probe

    plugin_dir = _write_plugin(tmp_path)
    local = plugin_dir / _LOCAL
    local.write_bytes((plugin_dir / _META).read_bytes())
    saved_data = local.read_bytes()
    payload = _read_json(plugin_dir / _META)

    def probe(*_args, **_kwargs):
        if not probe_succeeds:
            raise metadata_probe.MetadataProbeError("optional dependency missing")
        return payload

    monkeypatch.setattr(metadata_probe, "derive_plugin_metadata", probe)
    written = metadata_probe.write_packaged_metadata(source_dir=tmp_path, target_dir=plugin_dir)
    assert local.read_bytes() == saved_data
    assert (written is not None) == probe_succeeds
    assert (plugin_dir / _META).exists() == probe_succeeds
