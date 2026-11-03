"""Market installation commit path: no synchronous blocking calls on the event loop under the global lock.

``upload_and_install`` carries ``@serialized_plugin_operation``, holding the global cross-process
plugin-operation lock throughout (the single ``_PROCESS_LOCK`` in ``operation_lock.py:205`` plus
a ``.plugin-operation.lock`` file lock). Any time spent sleeping on the event loop under this lock halts
all plugin-server routes (``/plugins``, ``/plugin_cli``, ``/runs``, ``/websocket``, plugin UI streams)
together.

Specifically guard these synchronous calls:

* ``mgr.record_market_install`` / ``record_market_upgrade`` ultimately reach
  ``install_source/manager.py``'s ``_atomic_write``, where Windows
  ``PermissionError`` (brief AV/Explorer handle contention, explicitly **expected** in source comments)
  triggers ``for attempt_ms in (0, 50, 100, 200): time.sleep(attempt_ms / 1000)``,
  totaling up to **350ms of time.sleep**.
* ``classify_plugin_path`` performs 3 ``Path.resolve()`` calls + NFC normalization.
* ``_read_installed_plugin_toml_id`` reads/parses tomllib once.

The same ``record_market_install`` already uses ``asyncio.to_thread`` inside
``install_builtin_override``'s ``commit_lock`` in this file (:893), so this completes
consistency rather than introducing a convention.

Scope applies **only to ``upload_and_install``**: direct same-named calls in
``_install_via_staging_sync`` / ``_stage_builtin_override_sync`` are correct because these are themselves ``_sync``
functions dispatched to threads by callers.

Mutation: remove any ``asyncio.to_thread(...)`` in ``upload_and_install`` and call directly instead.
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
    """Return (direct-call list, set of names passed through to_thread)."""
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
    # Do not make the preceding test pass by deleting calls; this work must still happen.
    assert _GUARDED <= threaded, (
        f"这些调用不见了，不是被挪进线程了：缺失 {sorted(_GUARDED - threaded)}"
    )


def test_upload_and_install_still_holds_the_global_lock() -> None:
    """The opposite guard: moving blocking work to threads must not move the lock away too.

    These steps must remain under lock because they modify the installation-source ledger; mutual exclusion against
    concurrent install/uninstall/start/stop is the lock's purpose. Mutation: remove @serialized_plugin_operation.
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
    """Reverse guard: do not overgeneralize this rule to *_sync functions.

    ``_install_via_staging_sync`` / ``_stage_builtin_override_sync`` are already synchronous functions
    dispatched to threads by their callers. Wrapping their calls in to_thread adds no value and splits one call into two
    thread round trips. This fails if someone attempts casual uniformity.
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
