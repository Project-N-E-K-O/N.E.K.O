"""市场安装提交路径：全局锁内、事件循环上，不得有同步阻塞调用。

``upload_and_install`` 带 ``@serialized_plugin_operation``，也就是**整段都持有那把
全局跨进程插件操作锁**（``operation_lock.py:205`` 的单个 ``_PROCESS_LOCK`` + 一个
``.plugin-operation.lock`` 文件锁）。锁内任何睡在事件循环上的时间，都是插件服务器
所有路由（``/plugins``、``/plugin_cli``、``/runs``、``/websocket``、插件 UI 流）
一起停摆的时间。

具体被钉住的是这几个同步调用：

* ``mgr.record_market_install`` / ``record_market_upgrade`` —— 最终走到
  ``install_source/manager.py`` 的 ``_atomic_write``，那里在 Windows 上撞到
  ``PermissionError``（AV / Explorer 短暂持有句柄，源码注释说明这是**预期会发生**的）
  会执行 ``for attempt_ms in (0, 50, 100, 200): time.sleep(attempt_ms / 1000)``，
  累计最多 **350ms 的 time.sleep**。
* ``classify_plugin_path`` —— 3 次 ``Path.resolve()`` + NFC 归一化。
* ``_read_installed_plugin_toml_id`` —— 一次 tomllib 读+解析。

同一个 ``record_market_install`` 在本文件 ``install_builtin_override`` 的
``commit_lock`` 里早就是 ``asyncio.to_thread`` 的（:893），所以这不是新约定，是补齐
一致性。

注意作用域**只限 ``upload_and_install``**：``_install_via_staging_sync`` 与
``_stage_builtin_override_sync`` 里的同名直接调用是正确的——它们本身就是 ``_sync``
函数，由调用方丢进线程执行。

变异：把 ``upload_and_install`` 里任一处 ``asyncio.to_thread(...)`` 摘掉、改回直接调用。
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from plugin.server.application.plugin_cli import service as service_module

pytestmark = pytest.mark.plugin_unit

_GUARDED = {
    "record_market_install",
    "record_market_upgrade",
    "classify_plugin_path",
    "_read_installed_plugin_toml_id",
}
_TARGET_FUNCTION = "upload_and_install"


def _call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _target_function() -> ast.AsyncFunctionDef:
    source = Path(inspect.getfile(service_module)).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == _TARGET_FUNCTION:
            return node
    raise AssertionError(f"前提没成立：找不到 async def {_TARGET_FUNCTION}")


def _scan(fn: ast.AST) -> tuple[list[tuple[str, int]], set[str]]:
    """返回 (直接调用列表, 经 to_thread 传递的名字集合)。"""
    direct: list[tuple[str, int]] = []
    threaded: set[str] = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if name in _GUARDED:
            direct.append((name, node.lineno))
        if name == "to_thread" and node.args:
            first = node.args[0]
            arg_name = (
                first.attr
                if isinstance(first, ast.Attribute)
                else (first.id if isinstance(first, ast.Name) else None)
            )
            if arg_name in _GUARDED:
                threaded.add(arg_name)
    return direct, threaded


def test_no_guarded_helper_is_called_directly_inside_upload_and_install() -> None:
    fn = _target_function()
    direct, threaded = _scan(fn)

    assert not direct, (
        f"{_TARGET_FUNCTION} 里有同步阻塞调用直接落在事件循环上（而且是在全局插件操作"
        f"锁内）：{direct}。record_market_* 会走到 _atomic_write 的 Windows AV 重试，"
        "那里最多 time.sleep 350ms —— 这段时间插件服务器的所有路由都停摆。"
        "改成 await asyncio.to_thread(...)，和 install_builtin_override 的 :893 一致。"
    )
    # 别靠"把调用删掉"来让上面那条通过：这些活必须还在做。
    assert _GUARDED <= threaded, (
        f"这些调用不见了，不是被挪进线程了：缺失 {sorted(_GUARDED - threaded)}"
    )


def test_upload_and_install_still_holds_the_global_lock() -> None:
    """另一侧的守卫：把阻塞挪进线程不能顺手把锁也挪走。

    这几步必须仍在锁内——它们改的是安装来源台账，与并发的安装/卸载/启停互斥是
    这把锁存在的理由。变异：摘掉 @serialized_plugin_operation。
    """
    fn = getattr(service_module.PluginCliService, _TARGET_FUNCTION, None) or getattr(
        service_module, _TARGET_FUNCTION, None
    )
    assert fn is not None, f"前提没成立：找不到 {_TARGET_FUNCTION}"
    assert getattr(fn, "__wrapped__", None) is not None, (
        f"{_TARGET_FUNCTION} 不再被 @serialized_plugin_operation 装饰——"
        "安装提交就此失去与卸载/启停的互斥"
    )


def test_the_sync_helpers_are_allowed_to_call_it_directly() -> None:
    """反向守卫：别把这条规则过度推广到 *_sync 函数上。

    ``_install_via_staging_sync`` / ``_stage_builtin_override_sync`` 本身就是同步函数、
    由调用方丢进线程执行；在它们里面再套 to_thread 不但没用，还会把一次调用拆成两次
    线程往返。有人"顺手统一"时这条会红。
    """
    source = Path(inspect.getfile(service_module)).read_text(encoding="utf-8")
    tree = ast.parse(source)
    checked = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name.endswith("_sync"):
            direct = [name for name, _ in _scan(node)[0]]
            if direct:
                checked += 1
    assert checked >= 1, (
        "前提没成立：*_sync 函数里已经没有直接调用了——这条反向守卫失效了，"
        "请确认上面的规则没有被过度推广"
    )
