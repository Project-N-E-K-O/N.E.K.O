"""异环境打包的元数据：写一份本机 sidecar，**发行产物一个字节都不动**。

背景（实测）：``neko-plugin build`` 把**作者机器**的 ``build_env``（os / python 小版本 /
arch）写进 ``plugin.meta.json``；市场安装只是把那个文件逐字节解出来
（``neko_plugin_cli/core/install.py`` 的 ``extract_member`` = ``shutil.copyfileobj``），
既不校验也不重写。实测过：build 产出 ``build_env={'os':'win32','python':'3.11',
'arch':'AMD64'}``，``install_package`` 之后盘上那份一模一样。

于是用户换了 Python 小版本之后：``read_packaged_metadata`` 仍返回对象、只是
``built_in_this_environment=False`` → ``_read_packaged_isolated_metadata`` 拒绝它 →
每次启动付一个 2.5–4.2s 的隔离 metadata worker；而重写路径
``refresh_stale_packaged_metadata`` 只认 **schema 过期**，schema 是当前版就什么都不做
→ **永久**，且 INFO 以上什么都不记（注册表*发现*还接受同一份文件做 UI 预览，所以
插件在列表里看起来完全正常）。

**为什么是 sidecar 而不是就地改写**：``plugin.meta.json`` 是发行产物。改写它会动到
已安装包的字节，进而动到 manual takeover 的树哈希复核
（``manual_takeover._replaceable_content_sha256`` 无排除清单、全树逐文件哈希），以及
"盘上这份就是市场发布的那份"这个可核对性。既有测试
``test_plugins_lifecycle_service.py::test_a_scan_does_not_write_metadata_it_has_no_business_writing[current_schema]``
正是钉这一条的（它的 docstring 写着 "a foreign build environment … is not fixed by a
rewrite"，断言 ``plugin.meta.json`` 字节不变）。写在旁边、读取时优先，那条测试**一行
不改就仍然通过**。

这里钉住的不变量：

1. **发行产物字节不变**，sidecar 单独落在 ``plugin.meta.local.json``。
2. **sidecar 必须被排除在源树指纹之外**——否则写出它就改变了 ``source_files`` 与
   ``source_sha256``，**反过来让包内那份 plugin.meta.json 判定失配而失效**：修好一条
   慢路径，同时弄坏另一条快路径。这是本方案最容易踩的坑。
3. **自愈**：sidecar 写成功后 ``read_packaged_metadata`` 直接命中它 → 不再扫描 →
   也就不会再走到写这一步。
4. **不越权**：schema 比本机新的包不写（那是降级）；``plugin.meta.json`` 根本不存在的
   插件不写（手工放入/dev 模式的插件从来没有过这份文件，不该因为启动一次就长出一份）；
   没有新扫描结果不写；schema 过期仍然走**既有的**就地改写路径。
5. **不放宽环境比对**：``build_environment`` 的 docstring 说明插件可以按
   ``sys.version_info`` 决定注册哪些 entry，小版本之间 C 扩展 ABI 也不兼容。要消除的
   是"永远修不好"，不是"判得严"。

变异清单（每条都应有测试变红）：
* 把 ``_GENERATED_METADATA_NAMES`` 改回只含 ``PACKAGED_METADATA_FILENAME`` → 2 红
* ``read_packaged_metadata`` 里去掉 sidecar 优先 → 1、3 红
* ``packaged_metadata_needs_rebuild`` 里删掉 env 分支 → 6 红
* ``_snapshot_package_tree_for_rebuild`` 换回 schema-only 判据 → 7 红
* 分派改成"env 优先于 schema" → 8 红（改变了既有 schema 路径的行为）
* ``build_environment`` 的 python 放宽到 major → 5 红
"""

from __future__ import annotations

import ast
import inspect
import json
import sys
from pathlib import Path

import pytest

from plugin.server.infrastructure import packaged_metadata

pytestmark = pytest.mark.plugin_unit

_META = packaged_metadata.PACKAGED_METADATA_FILENAME
_LOCAL = packaged_metadata.LOCAL_PACKAGED_METADATA_FILENAME
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
    # 必须是 [plugin] 段：_upgrade_stale_packaged_metadata 会校验 manifest 里的 id
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


_SCAN_KWARGS = dict(
    entries=[{"id": "go", "name": "Go"}],
    handlers={"demo.go": {"event_type": "plugin_entry", "id": "go", "name": "Scanned", "timeout": 7}},
    entry_methods={"go": "go"},
    conf={},
    pdata={},
)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_bytes().decode("utf-8"))


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
    # 2) sidecar 落在旁边，内容是**本机**的答案
    assert (plugin_dir / _LOCAL).exists()
    local = _read_json(plugin_dir / _LOCAL)
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


def test_the_sidecar_is_excluded_from_the_source_fingerprint(tmp_path) -> None:
    """变异：把 ``_GENERATED_METADATA_NAMES`` 改回只含 ``PACKAGED_METADATA_FILENAME``。

    这是本方案最容易踩的坑，也是最静默的：sidecar 一旦参与指纹，写出它本身就改变了
    ``source_files`` 清单与 ``source_sha256`` —— 包内那份 ``plugin.meta.json`` 会立刻
    判定失配而失效。修好一条慢路径，同时弄坏了另一条快路径，而且没有任何东西会红。
    """
    plugin_dir = _write_plugin(tmp_path, build_env=_foreign_env(python="3.9"))
    sha_before = packaged_metadata.compute_source_sha256(plugin_dir)
    names_before = packaged_metadata.source_file_names(plugin_dir)[0]
    assert _META not in names_before, "前提没成立：包内那份本来就该被排除"

    packaged_metadata.write_local_packaged_metadata(
        plugin_dir,
        before_scan=packaged_metadata.snapshot_source_tree(plugin_dir),
        **_SCAN_KWARGS,
    )
    assert (plugin_dir / _LOCAL).exists(), "前提没成立：sidecar 没写出来"

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
    (plugin_dir / _LOCAL).write_text(json.dumps(stale), encoding="utf-8")

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
    local_path = plugin_dir / _LOCAL
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

    source = inspect.getsource(lifecycle_service._upgrade_stale_packaged_metadata)
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
    assert (env_dir / _LOCAL).exists(), "env 路径没有写 sidecar"


def _upgrade(plugin_dir: Path, scanned) -> None:
    """走真实的分派函数，并把 manifest 原样当作生效配置传进去。

    传 manifest 本身是为了让 ``_upgrade_stale_packaged_metadata`` 的两道前置守卫都通过
    （manifest id 与运行时 id 一致、生效 entries 表就是 manifest 自己那份，摘要相等）。
    本测试要验的是"写到哪"，不是那两道守卫——它们由 test_plugins_lifecycle_service 里
    既有的用例覆盖。
    """
    import tomllib

    from plugin.server.application.plugins import lifecycle_service

    config_path = plugin_dir / "plugin.toml"
    manifest = tomllib.loads(config_path.read_text(encoding="utf-8"))
    pdata = manifest.get("plugin") if isinstance(manifest.get("plugin"), dict) else {}
    lifecycle_service._upgrade_stale_packaged_metadata(
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
