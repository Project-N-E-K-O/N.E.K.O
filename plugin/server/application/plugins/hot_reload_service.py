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

- reload **正在运行**或上次自动重载启动失败的插件。失败仅在下一次源码
  变更后重试；任何显式 start/stop 都会在操作锁内撤销恢复许可。
  用户手动停下的插件不会因为一次文件
  变更被拉起来——那是把"改了代码"偷换成"改变了我的启动意图"。锁外先
  查一次做快速路径，拿到操作锁后 ``reload_plugin(only_if_running=True)``
  还会复查，兜住两次检查之间用户 Stop 的竞态。
- reload 前先做 preflight（复用开发插件的 manifest/entry/依赖验证与 ``.py`` 编译），
  语法坏掉的编辑直接跳过这一轮，保住旧实例；下次变更再试。开发模式
  插件在 ``reload_plugin`` 内部另有完整 preflight，这里对普通（内置/安装）
  插件补上同等的保护。
- 与用户操作撞车时，抢锁最多等一个防抖窗口，仍等不到
  （``PluginOperationBusy``）就顺延一个防抖窗口重试，不插队。
- 监视面假设插件运行时不会向自己的 config 目录写 ``*.py`` /
  ``plugin.toml``（2026-09 普查 ``plugin/plugins``：插件运行期写的都是
  json/媒体/模型文件，``plugin.toml`` 的写入点全部在测试里，均不在签名
  范围内）。若未来某插件需要在运行期生成源码文件，必须写进已被排除的
  子目录（如 ``vendor``），否则会形成 reload 回环。
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
from plugin.utils.source_paths import is_vendor_sync_path
from plugin.server.application.plugins import development as development_store
from plugin.server.application.plugins.lifecycle_service import (
    PluginLifecycleService,
    _DEFAULT_METADATA_SCAN_TIMEOUT as _DEFAULT_SCAN_TIMEOUT_SECONDS,
    _resolve_registered_config_path_sync,
    active_startup_timeout,
    plugin_is_running_sync,
    plugin_needs_hot_reload_recovery,
)
from plugin.server.application.plugins.operation_lock import (
    PluginOperationBusy,
    bounded_operation_wait,
)
from plugin.server.domain.errors import ServerDomainError
from plugin.server.messaging.lifecycle_events import emit_lifecycle_event
from plugin.settings import (
    PLUGIN_CONFIG_ROOTS,
    PLUGIN_HOT_RELOAD,
    PLUGIN_HOT_RELOAD_DEBOUNCE,
    PLUGIN_HOT_RELOAD_INTERVAL,
    PLUGIN_HOT_RELOAD_MIN_INTERVAL_SECONDS,
    PLUGIN_STARTUP_TIMEOUT,
    PLUGIN_SHUTDOWN_TIMEOUT,
    PROCESS_SHUTDOWN_TIMEOUT,
)
from plugin.utils.time_utils import now_iso

logger = get_logger("server.application.plugins.hot_reload")

# 防抖到期后的最小检查间隔。有 pending 时轮询间隔会收缩到接近这个值，
# 让"静默结束 → reload"的延迟不受整秒级轮询间隔拖累。
_MIN_TICK_SECONDS = PLUGIN_HOT_RELOAD_MIN_INTERVAL_SECONDS
# stop() 等待 watcher 退出的上限。in-flight reload 被 operation lock 屏蔽
# 取消时，超时说明它还在跑完最后一步。服务器关停另传 0.05s，
# 为 host teardown 保留总预算；注册门闩拒绝迟到的 host。
_STOP_TIMEOUT_SECONDS = 1.5
# Restart has a different budget from shutdown: a healthy in-flight reload may
# still consume the host start/stop and isolated metadata scan limits.
_RESTART_DRAIN_OVERHEAD_SECONDS = (
    PLUGIN_SHUTDOWN_TIMEOUT + PROCESS_SHUTDOWN_TIMEOUT
    + _DEFAULT_SCAN_TIMEOUT_SECONDS + 5.0
)
_RESTART_DRAIN_SECONDS = PLUGIN_STARTUP_TIMEOUT + _RESTART_DRAIN_OVERHEAD_SECONDS
# The in-flight plugin's budget is re-derived this often while draining, so the
# timeout start_plugin records once it has read the config is picked up.
_DRAIN_RECHECK_SECONDS = 1.0
_BUSY_RETRY_SECONDS = 1.0
# 与 dev preflight 共用的目录排除表：这些目录里的 .py 不是插件源码。
_EXCLUDED_DIR_NAMES = development_store.SOURCE_EXCLUDED_DIR_NAMES


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
        dirnames[:] = [
            name
            for name in dirnames
            if name not in _EXCLUDED_DIR_NAMES
            # `neko-plugin sync` writes thousands of third-party files into
            # its staging/backup trees at the plugin root; they are not source.
            and not (Path(dirpath) == root and is_vendor_sync_path(Path(name)))
        ]
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
    """Validate a plugin source tree without importing it.

    返回错误消息（或 ``None`` 表示通过）。stop 一个健康进程之前先确认
    manifest/entry/依赖有效、插件自有 ``.py`` 都能编译——坏掉的编辑不应该
    杀死正在运行的旧实例。
    """
    manifest_path = root / "plugin.toml"
    try:
        manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_id = manifest.get("plugin", {}).get("id")
    except (
        OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, AttributeError,
    ) as exc:
        return f"plugin.toml: {exc}"
    # Reuse discovery validation (entry, dependencies) and the plugin-owned
    # ``.py`` compile pass without importing the plugin in the server process.
    # Manifest IDs may differ from conflict-suffixed runtime IDs.
    from plugin.server.application.plugins.development_service import (
        _preflight_source_sync,
    )

    try:
        _preflight_source_sync(root.resolve(), manifest_id)
    except ServerDomainError as exc:
        return exc.message
    except Exception as exc:
        # Any rejection keeps the running instance; an unexpected error type
        # must not escape and leave the plugin pending for an immediate retry.
        return f"{type(exc).__name__}: {exc}"
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
        self._restart_requested = False
        # 正在进行 reload 的目标；重启排空预算按它的启动超时计算。
        self._inflight_target: _WatchTarget | None = None
        # plugin_id -> 上一次看到的签名。None 值表示"目录本轮不可见"。
        self._signatures: dict[str, dict[str, tuple[int, int]]] = {}
        # plugin_id -> 防抖截止时刻（monotonic）。
        self._pending: dict[str, float] = {}

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
            if self._stop_event is not None and self._stop_event.is_set():
                # Defer the replacement until the old task has finished touching
                # shared signatures; never run two generations concurrently.
                self._restart_requested = True
            return True
        # 重启场景：上一轮的 pending/签名描述的是上一个进程世代的磁盘，
        # 保留只会产生一次假 reload。
        self._signatures.clear()
        self._pending.clear()
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name="plugin-hot-reload-watcher")
        self._task.add_done_callback(self._watcher_done)
        logger.info(
            "plugin hot-reload watcher started (interval={}s, debounce={}s)",
            PLUGIN_HOT_RELOAD_INTERVAL,
            PLUGIN_HOT_RELOAD_DEBOUNCE,
        )
        return True

    async def stop(self, timeout: float = _STOP_TIMEOUT_SECONDS) -> None:
        """Stop the watcher. Safe to call when not running."""
        self._restart_requested = False
        event = self._stop_event
        if event is not None:
            event.set()
        task = self._task
        if task is None:
            return
        task.cancel()
        # 不 await task 本身：被取消时它会向外抛 CancelledError，而这里要
        # 的语义是"等它退出，超时就报告并继续关停"。
        done, _pending_tasks = await asyncio.wait({task}, timeout=timeout)
        if not done:
            # 超时：in-flight reload 还在跑。必须保留 task 引用，否则
            # start() 会看不到存活中的 watcher 而另起一个，两个 watcher
            # 短暂并发写 _signatures。orphan 的 stop_event 已置位，跑完
            # 当前一步会自行退出；期间 start() 会预约退出后的 replacement。
            self._task = task
            logger.warning(
                "plugin hot-reload watcher did not stop within {}s; "
                "an in-flight reload will finish on its own",
                timeout,
            )
        else:
            if self._task is task:
                self._task = None
            if not task.cancelled():
                exc = task.exception()
                if exc is not None:
                    logger.warning(
                        "plugin hot-reload watcher exited with error: {}", exc
                    )

    def _watcher_done(self, task: asyncio.Task[None]) -> None:
        if self._task is not task:
            return
        self._task = None
        if not task.cancelled():
            task.exception()  # Retrieve errors even after a timed-out stop.
        if self._restart_requested:
            self._restart_requested = False
            self.start()

    async def wait_for_stopped(self, timeout: float | None = None) -> None:
        """Drain a stopping generation before reopening the server host gate.

        Shutdown need not spend its host cleanup budget on an in-flight reload,
        but startup must not let that old transaction join the new server run.
        asyncio.wait observes task completion without propagating its cancellation.
        Cancelling startup itself still propagates and leaves the gate closed.
        A stalled transaction fails startup promptly instead of hanging it;
        reopening the gate while it still lives would admit an old-generation host.
        The default budget follows the in-flight plugin's own startup timeout
        (``[plugin_runtime].timeout`` may raise it above the global setting),
        re-derived every recheck interval.
        """
        task = self._task
        if task is None or self._stop_event is None or not self._stop_event.is_set():
            return
        if timeout is not None:
            done, _pending_tasks = await asyncio.wait({task}, timeout=timeout)
            if not done:
                self._raise_drain_timeout()
            return
        started = time_module.monotonic()
        # Sticky across the drain: start_plugin drops its record when the start
        # ends, just before the reload finishes, and the budget must not shrink
        # back under a reload that legitimately ran long.
        granted_seen: float | None = None
        while True:
            granted = self._inflight_granted_timeout()
            if granted is not None:
                granted_seen = granted if granted_seen is None else max(granted_seen, granted)
            remaining = started + self._restart_drain_budget(granted_seen) - time_module.monotonic()
            if remaining <= 0:
                self._raise_drain_timeout()
            done, _pending_tasks = await asyncio.wait(
                {task}, timeout=min(remaining, _DRAIN_RECHECK_SECONDS)
            )
            if done:
                return

    @staticmethod
    def _raise_drain_timeout() -> None:
        raise RuntimeError(
            "Previous plugin hot reload is still running; "
            "retry server startup after it finishes"
        )

    def _inflight_granted_timeout(self) -> float | None:
        target = self._inflight_target
        return None if target is None else active_startup_timeout(target.plugin_id)

    @staticmethod
    def _restart_drain_budget(granted: float | None) -> float:
        """Drain budget, from in-memory state only.

        Until start_plugin records the timeout it granted, the default budget
        applies: the steps before that record (stopping the old process, the
        bounded lock wait, reading the config) take seconds, so a reload still
        short of it after the default budget is stalled. Once recorded, the
        plugin's own timeout extends the budget. No file is read here: a drain
        must never block on, or leak threads into, a stalled filesystem.
        """
        if granted is None:
            return _RESTART_DRAIN_SECONDS
        return max(_RESTART_DRAIN_SECONDS, _RESTART_DRAIN_OVERHEAD_SECONDS + granted)

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
            if not stop_event.is_set():
                await asyncio.sleep(self._next_sleep_seconds())

    def _next_sleep_seconds(self) -> float:
        interval = PLUGIN_HOT_RELOAD_INTERVAL
        if not self._pending:
            return interval
        nearest = min(self._pending.values())
        remaining = nearest - time_module.monotonic()
        return max(_MIN_TICK_SECONDS, min(interval, remaining))

    async def _tick(self, stop_event: asyncio.Event) -> None:
        def collect_signatures():
            return [
                (target, _signature_sync(target.root))
                for target in self._collect_targets_sync()
            ]

        scanned = await asyncio.to_thread(collect_signatures)
        targets = [target for target, _signature in scanned]

        now = time_module.monotonic()
        visible_ids: set[str] = set()
        for target, signature in scanned:
            visible_ids.add(target.plugin_id)
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
            if deadline <= time_module.monotonic()
        ]
        target_by_id = {target.plugin_id: target for target in targets}
        for plugin_id in due:
            if stop_event.is_set():
                break
            target = target_by_id.get(plugin_id)
            if target is None:
                self._pending.pop(plugin_id, None)
                continue
            try:
                await self._reload_target(target)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Escaping here would keep the overdue entry pending and retry it
                # at the minimum tick; drop it until the next source change.
                self._pending.pop(plugin_id, None)
                logger.warning(
                    "hot-reload attempt aborted: plugin_id={}, err_type={}, err={}",
                    plugin_id,
                    type(exc).__name__,
                    exc,
                )

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
                registered_plugins = [
                    (plugin_id, dict(meta) if isinstance(meta, dict) else None)
                    for plugin_id, meta in state.plugins.items()
                    if isinstance(plugin_id, str)
                ]
        except Exception as exc:
            logger.debug("failed to read plugin registry for hot-reload: err={}", exc)
            registered_plugins = []
        for plugin_id, meta in registered_plugins:
            if plugin_id in targets:
                continue
            config_path = _resolve_registered_config_path_sync(meta)
            candidate = (
                config_path.parent
                if config_path is not None and config_path.is_file()
                else None
            )
            if candidate is None:
                for base in PLUGIN_CONFIG_ROOTS:
                    fallback = Path(base) / plugin_id
                    if (fallback / "plugin.toml").is_file():
                        candidate = fallback
                        break
            if candidate is not None:
                targets[plugin_id] = _WatchTarget(plugin_id, candidate, False)
        return [
            target for target in targets.values()
            if plugin_is_running_sync(target.plugin_id)
            or plugin_needs_hot_reload_recovery(target.plugin_id)
        ]

    # ---------- reload execution ----------

    async def _reload_target(self, target: _WatchTarget) -> None:
        plugin_id = target.plugin_id
        is_running = await asyncio.to_thread(plugin_is_running_sync, plugin_id)
        if not is_running and not plugin_needs_hot_reload_recovery(plugin_id):
            # 停着的插件不自动拉起。这是锁外的快速路径；拿到锁之后
            # reload_plugin(only_if_running=True) 还会复查一次，兜住
            # "这里查完、用户 Stop 落进窗口"的竞态。下次手动 start 时
            # start_plugin 自己会从磁盘刷新注册表条目，新代码不会漏掉。
            logger.debug(
                "hot-reload skipped (plugin not running): plugin_id={}", plugin_id
            )
            self._pending.pop(plugin_id, None)
            self._emit_event(
                "plugin_hot_reload_skipped", plugin_id, reason="not_running"
            )
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
                self._emit_event(
                    "plugin_hot_reload_skipped", plugin_id, reason="preflight_failed"
                )
                self._pending.pop(plugin_id, None)
                return

        self._inflight_target = target
        logger.info("hot-reload triggered: plugin_id={}", plugin_id)
        self._emit_event("plugin_hot_reload_triggered", plugin_id)
        try:
            # 给等锁一个截止期：没有预算的话 reload_plugin 内部的
            # serialized_plugin_operation 会无界等待，busy 永远抛不出来，
            # 下面的 except 分支就是死路径。预算取防抖窗口——和顺延窗口一致。
            with bounded_operation_wait(PLUGIN_HOT_RELOAD_DEBOUNCE):
                result = await self._lifecycle_service.reload_plugin(
                    plugin_id, only_if_running=True
                )
            if result.get("skipped"):
                self._pending.pop(plugin_id, None)
                self._emit_event(
                    "plugin_hot_reload_skipped", plugin_id, reason="not_running"
                )
                return
            logger.info("hot-reload completed: plugin_id={}", plugin_id)
        except PluginOperationBusy:
            # 用户操作正在持有锁：顺延一个防抖窗口再试，不报错误。
            self._pending[plugin_id] = (
                time_module.monotonic()
                + max(PLUGIN_HOT_RELOAD_DEBOUNCE, _BUSY_RETRY_SECONDS)
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
            self._emit_event("plugin_hot_reload_failed", plugin_id, reason=exc.code)
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
            self._emit_event(
                "plugin_hot_reload_failed", plugin_id, reason=type(exc).__name__
            )
        else:
            self._pending.pop(plugin_id, None)
        finally:
            self._inflight_target = None

    @staticmethod
    def _emit_event(event_type: str, plugin_id: str, reason: str | None = None) -> None:
        payload: dict[str, object] = {
            "type": event_type,
            "plugin_id": plugin_id,
            "time": now_iso(),
        }
        if reason is not None:
            # 前端靠它区分 preflight 拒绝 / 域错误 / 意外异常，
            # 不用去日志里对时间戳。
            payload["reason"] = reason
        try:
            emit_lifecycle_event(payload)
        except Exception as exc:
            logger.debug("failed to emit {} event: {}", event_type, exc)


# 模块级单例，与 lifecycle.py 的其它服务用法保持一致。
hot_reload_service = PluginHotReloadService()
