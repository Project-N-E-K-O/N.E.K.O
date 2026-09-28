"""插件源码热重载：监视插件目录变更并自动 reload。

实现方式是 stdlib 轮询（``Path.stat`` 的 mtime_ns + size 签名），不引入
watchdog 之类的第三方依赖，也不依赖平台文件系统通知。每轮扫描：

1. 目标集合 = 注册表插件的 config 目录（``PLUGIN_CONFIG_ROOTS`` 下）+
   开发模式注册的 ``source_dir``；
2. 对每个目录收集 ``*.py`` / ``plugin.toml`` 的签名，与上一轮比较；
3. 有变化的插件进入 pending，等防抖窗口（变更静默
   ``PLUGIN_HOT_RELOAD_DEBOUNCE`` 秒）后 reload。

reload 复用 ``PluginLifecycleService.reload_plugin``（stop + start，杀进程
重启），因此自动触发与手动点按钮走完全相同的加锁与事务路径。

安全边界（与手动 reload 的差异全部在触发侧收口）：

- 只 reload **正在运行**的插件。用户手动停下的插件不会因为一次文件
  变更被拉起来——那是把"改了代码"偷换成"改变了我的启动意图"。
- reload 前先做语法 preflight（compile 全部 ``.py`` + 解析 ``plugin.toml``），
  语法坏掉的编辑直接跳过这一轮，保住旧实例；下次变更再试。开发模式
  插件在 ``reload_plugin`` 内部另有完整 preflight，这里对普通（内置/安装）
  插件补上同等的保护。
- 与用户操作撞车（``PluginOperationBusy``）时顺延重试，不插队。
"""

from __future__ import annotations

import asyncio
import os
import time as time_module
from dataclasses import dataclass
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    import tomli as tomllib  # type: ignore[no-redef]

from plugin.core.state import state
from plugin.logging_config import get_logger
from plugin.server.application.plugins import development as development_store
from plugin.server.application.plugins.lifecycle_service import (
    PluginLifecycleService,
    _plugin_is_running_sync,
)
from plugin.server.application.plugins.operation_lock import PluginOperationBusy
from plugin.server.domain.errors import ServerDomainError
from plugin.server.messaging.lifecycle_events import emit_lifecycle_event
from plugin.settings import (
    PLUGIN_CONFIG_ROOTS,
    PLUGIN_HOT_RELOAD,
    PLUGIN_HOT_RELOAD_DEBOUNCE,
    PLUGIN_HOT_RELOAD_INTERVAL,
)
from plugin.utils.time_utils import now_iso

logger = get_logger("server.application.plugins.hot_reload")

# 防抖到期后的最小检查间隔。有 pending 时轮询间隔会收缩到接近这个值，
# 让"静默结束 → reload"的延迟不受整秒级轮询间隔拖累。
_MIN_TICK_SECONDS = 0.05
# stop() 等待 watcher 退出的上限。in-flight reload 被 operation lock 屏蔽
# 取消时，超时说明它还在跑完最后一步，而不是泄漏。保持在整体 shutdown
# 预算（PLUGIN_SHUTDOWN_TOTAL_TIMEOUT 默认 3s）之内。
_STOP_TIMEOUT_SECONDS = 1.5
# 与 dev preflight 保持一致的目录排除表：这些目录里的 .py 不是插件源码。
_EXCLUDED_DIR_NAMES = frozenset(
    {"vendor", ".venv", ".git", "__pycache__", "node_modules"}
)


@dataclass(slots=True)
class _WatchTarget:
    plugin_id: str
    root: Path
    is_development: bool


def _signature_sync(root: Path) -> dict[str, tuple[int, int]]:
    """Collect ``{relative_path: (mtime_ns, size)}`` for watched files.

    只看 ``*.py`` 和 ``plugin.toml``：资源文件变更不影响已加载数据的
    正确性，而 reload 的代价是整个进程重启，不值得为一张图片付。
    """
    signature: dict[str, tuple[int, int]] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [name for name in dirnames if name not in _EXCLUDED_DIR_NAMES]
        for name in filenames:
            if name != "plugin.toml" and not name.lower().endswith(".py"):
                continue
            path = Path(dirpath) / name
            try:
                stat = path.stat()
            except OSError:
                # 文件在 walk 和 stat 之间被删掉：这一轮当作没看见，
                # 下一轮签名里少了它自然会触发 diff。
                continue
            signature[path.relative_to(root).as_posix()] = (
                stat.st_mtime_ns,
                stat.st_size,
            )
    return signature


def _preflight_compile_sync(root: Path) -> str | None:
    """Syntax-check a plugin source tree without importing it.

    返回错误消息（或 ``None`` 表示通过）。stop 一个健康进程之前先确认
    新代码至少能编译——语法坏掉的编辑不应该杀死正在运行的旧实例。
    """
    manifest_path = root / "plugin.toml"
    try:
        tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        return f"plugin.toml: {exc}"
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [name for name in dirnames if name not in _EXCLUDED_DIR_NAMES]
        for name in filenames:
            if not name.lower().endswith(".py"):
                continue
            path = Path(dirpath) / name
            try:
                compile(path.read_bytes(), str(path), "exec")
            except (SyntaxError, OSError) as exc:
                return str(exc)
    return None


class PluginHotReloadService:
    """Watch plugin source directories and reload running plugins on change.

    单实例跨 ``start``/``stop`` 复用：stop 后再次 start 会重建签名基线，
    避免把停机窗口里的变更误报成新一轮 reload。
    """

    def __init__(self, lifecycle_service: PluginLifecycleService | None = None) -> None:
        self._lifecycle_service = lifecycle_service or PluginLifecycleService()
        self._task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None
        # plugin_id -> 上一次看到的签名。None 值表示"目录本轮不可见"。
        self._signatures: dict[str, dict[str, tuple[int, int]]] = {}
        # plugin_id -> 防抖截止时刻（monotonic）。
        self._pending: dict[str, float] = {}
        # 正在 reload 的 plugin_id，防止同一插件重复排队。
        self._reloading: set[str] = set()

    # ---------- lifecycle ----------

    def start(self) -> bool:
        """Start the watcher task on the running loop. Idempotent.

        返回是否真的启动了。``PLUGIN_HOT_RELOAD`` 关闭时是 no-op，让
        ``startup()`` 可以无条件调用而不必各自记忆配置。
        """
        if not PLUGIN_HOT_RELOAD:
            logger.debug(
                "plugin hot-reload disabled (set NEKO_PLUGIN_HOT_RELOAD=true to enable)"
            )
            return False
        if self._task is not None and not self._task.done():
            return True
        # 重启场景：上一轮的 pending/签名描述的是上一个进程世代的磁盘，
        # 保留只会产生一次假 reload。
        self._signatures.clear()
        self._pending.clear()
        self._reloading.clear()
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name="plugin-hot-reload-watcher")
        logger.info(
            "plugin hot-reload watcher started (interval={}s, debounce={}s)",
            PLUGIN_HOT_RELOAD_INTERVAL,
            PLUGIN_HOT_RELOAD_DEBOUNCE,
        )
        return True

    async def stop(self, timeout: float = _STOP_TIMEOUT_SECONDS) -> None:
        """Stop the watcher. Safe to call when not running."""
        event = self._stop_event
        if event is not None:
            event.set()
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        # 不 await task 本身：被取消时它会向外抛 CancelledError，而这里要
        # 的语义是"等它退出，超时就报告并继续关停"。
        done, _pending_tasks = await asyncio.wait({task}, timeout=timeout)
        if not done:
            logger.warning(
                "plugin hot-reload watcher did not stop within {}s; "
                "an in-flight reload will finish on its own",
                timeout,
            )
        elif not task.cancelled():
            exc = task.exception()
            if exc is not None:
                logger.warning("plugin hot-reload watcher exited with error: {}", exc)

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    # ---------- watcher loop ----------

    async def _run(self) -> None:
        # 局部捕获而不是 assert：``_run`` 只能由 ``start()`` 里的 create_task
        # 启动（先设置 event 再建 task），但 ``python -O`` 会剥离 assert，
        # 防御性检查不能依赖它。
        stop_event = self._stop_event
        if stop_event is None:
            logger.warning("plugin hot-reload watcher started without a stop event")
            return
        while not stop_event.is_set():
            try:
                await self._tick(stop_event)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # 一次磁盘毛刺不能杀掉整个 watcher；签名部分更新没关系，
                # 下一轮会基于新状态继续 diff。
                logger.warning(
                    "plugin hot-reload tick failed: err_type={}, err={}",
                    type(exc).__name__,
                    exc,
                )
            await asyncio.sleep(self._next_sleep_seconds())

    def _next_sleep_seconds(self) -> float:
        interval = PLUGIN_HOT_RELOAD_INTERVAL
        if not self._pending:
            return interval
        nearest = min(self._pending.values())
        remaining = nearest - time_module.monotonic()
        return max(_MIN_TICK_SECONDS, min(interval, remaining))

    async def _tick(self, stop_event: asyncio.Event) -> None:
        targets = await asyncio.to_thread(self._collect_targets_sync)

        now = time_module.monotonic()
        visible_ids: set[str] = set()
        for target in targets:
            visible_ids.add(target.plugin_id)
            signature = await asyncio.to_thread(_signature_sync, target.root)
            previous = self._signatures.get(target.plugin_id)
            self._signatures[target.plugin_id] = signature
            if previous is None:
                # 首次基线（或目录重新可见）：只记录，不触发。
                continue
            if signature != previous:
                self._pending[target.plugin_id] = now + PLUGIN_HOT_RELOAD_DEBOUNCE

        # 目标消失（卸载/解绑）：清掉对应状态。
        for plugin_id in list(self._signatures.keys() - visible_ids):
            self._signatures.pop(plugin_id, None)
            self._pending.pop(plugin_id, None)

        due = [
            plugin_id
            for plugin_id, deadline in self._pending.items()
            if deadline <= time_module.monotonic() and plugin_id not in self._reloading
        ]
        target_by_id = {target.plugin_id: target for target in targets}
        for plugin_id in due:
            if stop_event.is_set():
                break
            target = target_by_id.get(plugin_id)
            if target is None:
                self._pending.pop(plugin_id, None)
                continue
            await self._reload_target(target)

    def _collect_targets_sync(self) -> list[_WatchTarget]:
        """Resolve watchable directories: dev source dirs + registered configs."""
        targets: dict[str, _WatchTarget] = {}
        try:
            for snapshot in development_store.list_registration_records_sync():
                if snapshot.source_dir.is_dir():
                    targets[snapshot.plugin_id] = _WatchTarget(
                        plugin_id=snapshot.plugin_id,
                        root=snapshot.source_dir,
                        is_development=True,
                    )
        except Exception as exc:
            logger.debug(
                "failed to list development registrations for hot-reload: err={}", exc
            )
        try:
            with state.acquire_plugins_read_lock():
                registered_ids = [
                    plugin_id
                    for plugin_id in state.plugins.keys()
                    if isinstance(plugin_id, str)
                ]
        except Exception as exc:
            logger.debug("failed to read plugin registry for hot-reload: err={}", exc)
            registered_ids = []
        for plugin_id in registered_ids:
            if plugin_id in targets:
                continue
            for base in PLUGIN_CONFIG_ROOTS:
                candidate = Path(base) / plugin_id
                if (candidate / "plugin.toml").is_file():
                    targets[plugin_id] = _WatchTarget(
                        plugin_id=plugin_id,
                        root=candidate,
                        is_development=False,
                    )
                    break
        return list(targets.values())

    # ---------- reload execution ----------

    async def _reload_target(self, target: _WatchTarget) -> None:
        plugin_id = target.plugin_id
        self._reloading.add(plugin_id)
        try:
            is_running = await asyncio.to_thread(_plugin_is_running_sync, plugin_id)
            if not is_running:
                # 停着的插件不自动拉起。下次手动 start 时 start_plugin 自己
                # 会从磁盘刷新注册表条目，新代码不会漏掉。
                logger.debug(
                    "hot-reload skipped (plugin not running): plugin_id={}", plugin_id
                )
                self._pending.pop(plugin_id, None)
                return

            if not target.is_development:
                # dev 插件在 reload_plugin 内部有完整 preflight；普通插件
                # 在这里补一道语法检查，坏编辑不杀健康进程。
                error = await asyncio.to_thread(_preflight_compile_sync, target.root)
                if error is not None:
                    logger.warning(
                        "hot-reload skipped (source failed preflight, keeping the "
                        "running instance): plugin_id={}, error={}",
                        plugin_id,
                        error,
                    )
                    self._emit_event("plugin_hot_reload_skipped", plugin_id)
                    self._pending.pop(plugin_id, None)
                    return

            logger.info("hot-reload triggered: plugin_id={}", plugin_id)
            self._emit_event("plugin_hot_reload_triggered", plugin_id)
            try:
                await self._lifecycle_service.reload_plugin(plugin_id)
                logger.info("hot-reload completed: plugin_id={}", plugin_id)
            except PluginOperationBusy:
                # 用户操作正在持有锁：顺延一个防抖窗口再试，不报错误。
                self._pending[plugin_id] = (
                    time_module.monotonic() + PLUGIN_HOT_RELOAD_DEBOUNCE
                )
                logger.debug(
                    "hot-reload deferred (operation busy): plugin_id={}", plugin_id
                )
            except ServerDomainError as exc:
                # reload_plugin 自己的 preflight/启动失败等：放弃这一轮，
                # 等下一次文件变更再触发（签名已经同步，不会自动重燃）。
                logger.warning(
                    "hot-reload failed: plugin_id={}, code={}, message={}",
                    plugin_id,
                    exc.code,
                    exc.message,
                )
                self._pending.pop(plugin_id, None)
                self._emit_event("plugin_hot_reload_failed", plugin_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(
                    "hot-reload raised unexpectedly: plugin_id={}, err_type={}, err={}",
                    plugin_id,
                    type(exc).__name__,
                    exc,
                )
                self._pending.pop(plugin_id, None)
                self._emit_event("plugin_hot_reload_failed", plugin_id)
            else:
                self._pending.pop(plugin_id, None)
        finally:
            self._reloading.discard(plugin_id)

    @staticmethod
    def _emit_event(event_type: str, plugin_id: str) -> None:
        try:
            emit_lifecycle_event(
                {"type": event_type, "plugin_id": plugin_id, "time": now_iso()}
            )
        except Exception as exc:
            logger.debug("failed to emit {} event: {}", event_type, exc)


# 模块级单例，与 lifecycle.py 的其它服务用法保持一致。
hot_reload_service = PluginHotReloadService()
