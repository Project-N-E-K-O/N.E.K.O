"""覆盖 _find_project_root 对「装在仓库树外」的用户插件的兜底。

用户插件装在 `我的文档/{APP_NAME}/plugins/<id>/`，向上探测找不到仓库根。
旧兜底按内置布局 plugin/plugins/<id>/plugin.toml 往上数四层，对用户插件
会数到「我的文档」本身，而这个返回值会被插入插件子进程的 sys.path[0]。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from plugin.core import host as host_module

_HOST_REPO_ROOT = Path(host_module.__file__).resolve().parents[2]

# 向上探测有 10 步上限，嵌得比上限深就一定走不到任何真实仓库根，
# 于是无论 tmp_path 落在哪（系统临时目录 / 仓库内的 basetemp）都必然
# 命中兜底分支。
_DEEPER_THAN_WALK_LIMIT = tuple(f"lvl{index}" for index in range(12))


def _make_config(root: Path, *parts: str) -> Path:
    config_path = root.joinpath(*parts)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("[plugin]\n", encoding="utf-8")
    return config_path


def test_builtin_layout_resolves_by_walking_up(tmp_path: Path) -> None:
    """仓库内布局仍然靠向上探测命中，不受兜底改动影响。"""
    fake_repo = tmp_path / "checkout"
    (fake_repo / "utils").mkdir(parents=True)
    config_path = _make_config(fake_repo, "plugin", "plugins", "demo", "plugin.toml")

    assert host_module._find_project_root(config_path) == fake_repo.resolve()


def test_fallback_returns_host_repo_root_not_the_user_directory(tmp_path: Path) -> None:
    """兜底必须落在宿主自己所在的仓库根，而不是插件目录往上数四层。"""
    documents = tmp_path.joinpath(*_DEEPER_THAN_WALK_LIMIT, "Documents")
    config_path = _make_config(documents, "N.E.K.O", "plugins", "demo", "plugin.toml")

    resolved = host_module._find_project_root(config_path)

    assert resolved == _HOST_REPO_ROOT
    assert resolved != documents.resolve()


def test_resolved_root_always_looks_like_a_neko_root(tmp_path: Path) -> None:
    """返回值会被插到子进程 sys.path[0]，绝不能是一个普通用户目录。"""
    config_path = _make_config(
        tmp_path.joinpath(*_DEEPER_THAN_WALK_LIMIT, "Documents"),
        "N.E.K.O", "plugins", "demo", "plugin.toml",
    )

    resolved = host_module._find_project_root(config_path)

    # 只断 plugin/。utils/ 在打包版被编进可执行文件、磁盘上不存在，拿它当
    # 判据等于把「只在源码树成立」的假设钉进测试。
    assert (resolved / "plugin").is_dir()


def test_fallback_never_returns_the_plugin_directory(tmp_path: Path) -> None:
    """插件自己的目录绝不能成为子进程的 sys.path[0]。"""
    config_path = _make_config(
        tmp_path.joinpath(*_DEEPER_THAN_WALK_LIMIT, "Documents"),
        "N.E.K.O", "plugins", "demo", "plugin.toml",
    )

    resolved = host_module._find_project_root(config_path)

    assert resolved != config_path.parent.resolve()
    assert config_path.parent.resolve() not in resolved.parents


def test_packaged_layout_without_utils_dir_still_resolves_to_the_app_root(
    tmp_path: Path, monkeypatch: "pytest.MonkeyPatch",
) -> None:
    """打包版里 utils/ 被编进可执行文件、磁盘上不存在，判据不能跟着它一起挂。

    源码树里 utils/ 总是存在，所以只在真实仓库上跑的用例分辨不出这条判据对不对；
    这里把 dist 的形状（有 plugin/、没有 utils/）造出来，并把宿主模块的 __file__
    指进去。
    """
    app_root = tmp_path.joinpath(*_DEEPER_THAN_WALK_LIMIT, "dist", "Xiao8")
    (app_root / "plugin" / "plugins").mkdir(parents=True)
    assert not (app_root / "utils").exists()
    monkeypatch.setattr(host_module, "__file__", str(app_root / "plugin" / "core" / "host.py"))

    config_path = _make_config(
        tmp_path / "userdata" / "Documents",
        *_DEEPER_THAN_WALK_LIMIT, "N.E.K.O", "plugins", "demo", "plugin.toml",
    )

    resolved = host_module._find_project_root(config_path)

    assert resolved == app_root.resolve()
    assert resolved != config_path.parent.resolve()


def test_packaged_layout_keeps_builtin_plugins_off_their_own_directory(
    tmp_path: Path, monkeypatch: "pytest.MonkeyPatch",
) -> None:
    """打包版里内置插件同样不能把自己的目录当成项目根。"""
    # 嵌得比向上探测的步数上限深，否则本机 tmp_path 落在仓库树内、探测会一路
    # 走到真实仓库根，兜底分支根本不会被执行。
    app_root = tmp_path.joinpath(*_DEEPER_THAN_WALK_LIMIT, "dist", "Xiao8")
    (app_root / "plugin" / "plugins").mkdir(parents=True)
    monkeypatch.setattr(host_module, "__file__", str(app_root / "plugin" / "core" / "host.py"))
    config_path = _make_config(app_root, "plugin", "plugins", "demo", "plugin.toml")

    resolved = host_module._find_project_root(config_path)

    assert resolved == app_root.resolve()
