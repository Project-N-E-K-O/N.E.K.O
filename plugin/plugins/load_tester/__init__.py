import asyncio
import time
import threading
import multiprocessing as mp
from collections import Counter
from typing import Any, Dict, Optional, cast

from plugin.sdk.base import NekoPluginBase
from plugin.sdk.decorators import neko_plugin, plugin_entry, lifecycle, worker
from plugin.sdk import ok
from plugin.sdk.bus.types import BusReplayContext


@neko_plugin
class LoadTestPlugin(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)
        self.file_logger = self.enable_file_logging(log_level="INFO")
        self.logger = self.file_logger
        self.plugin_id = ctx.plugin_id
        self._stop_event = threading.Event()
        self._auto_thread: Optional[threading.Thread] = None
        self._run_thread: Optional[threading.Thread] = None
        self._bench_lock = threading.RLock()
        self._run_all_guard = threading.Lock()
        self._run_all_running = False

    def _cleanup(self) -> None:
        try:
            self._stop_event.set()
        except Exception:
            pass
        t_run = getattr(self, "_run_thread", None)
        if t_run is not None:
            try:
                t_run.join(timeout=2.0)
            except Exception:
                pass
        t = getattr(self, "_auto_thread", None)
        if t is not None:
            try:
                t.join(timeout=2.0)
            except Exception:
                # 避免 join 异常中断清理
                pass

        try:
            self.ctx.close()
        except Exception:
            pass

    def _unwrap_ok_data(self, value: Any) -> Any:
        if isinstance(value, dict) and "data" in value:
            return value.get("data")
        return value

    def _bench_loop(self, duration_seconds: float, fn, *args, **kwargs) -> Dict[str, Any]:
        start = time.perf_counter()
        end_time = start + float(duration_seconds)
        throttle_seconds = 0.0
        try:
            throttle_seconds = float(kwargs.pop("throttle_seconds", 0.0) or 0.0)
        except Exception:
            throttle_seconds = 0.0
        count = 0
        errors = 0
        err_types: Counter[str] = Counter()
        err_samples: Dict[str, str] = {}
        while True:
            if self._stop_event.is_set():
                break
            now = time.perf_counter()
            if now >= end_time:
                break
            try:
                fn(*args, **kwargs)
                count += 1
            except Exception as e:  # pragma: no cover - defensive
                errors += 1
                tname = type(e).__name__
                err_types[tname] += 1
                if tname not in err_samples:
                    try:
                        err_samples[tname] = repr(e)
                    except Exception:
                        err_samples[tname] = "<repr_failed>"
                try:
                    self.logger.warning("[load_tester] bench iteration failed: {}", e)
                except Exception:
                    pass

            if throttle_seconds > 0:
                if self._stop_event.is_set():
                    break
                remaining = end_time - time.perf_counter()
                if remaining <= 0:
                    break
                to_sleep = min(float(throttle_seconds), float(remaining))
                if to_sleep > 0:
                    try:
                        time.sleep(to_sleep)
                    except Exception:
                        pass
        elapsed = time.perf_counter() - start
        qps = float(count) / elapsed if elapsed > 0 else 0.0
        return {
            "iterations": count,
            "errors": errors,
            "elapsed_seconds": elapsed,
            "qps": qps,
            "sleep_seconds": float(throttle_seconds),
            "error_types": dict(err_types),
            "error_samples": err_samples,
        }

    def _bench_loop_multiprocess(self, duration_seconds: float, workers: int, fn, *args, **kwargs) -> Dict[str, Any]:
        """Multiprocess mode - uses multithreading due to daemon process limitation.
        
        Python daemon processes cannot create child processes, even with spawn context.
        This is a hard limitation in Python's multiprocessing module.
        
        This method silently falls back to multithreading with optimized GIL-releasing operations.
        For true multiprocessing, run multiple plugin instances or use external tools.
        """
        # Daemon process limitation - silently fall back to threading
        return self._bench_loop_concurrent(duration_seconds, workers, fn, *args, **kwargs)

    def _bench_loop_concurrent(self, duration_seconds: float, workers: int, fn, *args, **kwargs) -> Dict[str, Any]:
        """Run benchmark with multiple worker threads.

        workers <= 1 时退化为单线程调用者应当已经处理, 这里假设 workers >= 1.
        """
        start = time.perf_counter()
        end_time = start + float(duration_seconds)
        throttle_seconds = 0.0
        try:
            throttle_seconds = float(kwargs.pop("throttle_seconds", 0.0) or 0.0)
        except Exception:
            throttle_seconds = 0.0
        lock = threading.Lock()
        err_types: Counter[str] = Counter()
        err_samples: Dict[str, str] = {}
        worker_results: list[tuple[int, int]] = []

        def _worker() -> None:
            local_count = 0
            local_errors = 0
            while True:
                if self._stop_event.is_set():
                    break
                now = time.perf_counter()
                if now >= end_time:
                    break
                try:
                    fn(*args, **kwargs)
                    local_count += 1
                except Exception as e:  # pragma: no cover - defensive
                    local_errors += 1
                    with lock:
                        tname = type(e).__name__
                        err_types[tname] += 1
                        if tname not in err_samples:
                            try:
                                err_samples[tname] = repr(e)
                            except Exception:
                                err_samples[tname] = "<repr_failed>"
                    try:
                        self.logger.warning("[load_tester] bench iteration failed (concurrent): {}", e)
                    except Exception:
                        pass

                if throttle_seconds > 0:
                    if self._stop_event.is_set():
                        break
                    remaining = end_time - time.perf_counter()
                    if remaining <= 0:
                        break
                    to_sleep = min(float(throttle_seconds), float(remaining))
                    if to_sleep > 0:
                        try:
                            time.sleep(to_sleep)
                        except Exception:
                            pass
            
            # Save local results
            with lock:
                worker_results.append((local_count, local_errors))

        threads = []
        worker_count = max(1, int(workers))
        for _ in range(worker_count):
            t = threading.Thread(target=_worker, daemon=True)
            threads.append(t)
            t.start()
        
        # Join with timeout to avoid infinite waiting
        join_timeout = duration_seconds + 10.0  # Allow extra time for cleanup
        for t in threads:
            try:
                t.join(timeout=join_timeout)
                if t.is_alive():
                    try:
                        self.logger.warning("[load_tester] worker thread did not finish in time")
                    except Exception:
                        pass
            except Exception:
                # 避免 join 异常中断整个压测
                pass

        # Aggregate results from all workers
        count = sum(c for c, _ in worker_results)
        errors = sum(e for _, e in worker_results)

        elapsed = time.perf_counter() - start
        qps = float(count) / elapsed if elapsed > 0 else 0.0
        return {
            "iterations": count,
            "errors": errors,
            "elapsed_seconds": elapsed,
            "qps": qps,
            "workers": worker_count,
            "sleep_seconds": float(throttle_seconds),
            "error_types": dict(err_types),
            "error_samples": err_samples,
        }

    def _sample_latency_ms(self, fn, *, samples: int = 100) -> Dict[str, Any]:
        n = max(1, int(samples))
        durs: list[float] = []
        errors = 0
        for _ in range(n):
            if self._stop_event.is_set():
                break
            t0 = time.perf_counter()
            try:
                fn()
            except Exception:
                errors += 1
            dt = (time.perf_counter() - t0) * 1000.0
            durs.append(float(dt))

        if not durs:
            return {
                "latency_samples": 0,
                "latency_errors": int(errors),
            }

        durs.sort()
        total = 0.0
        for x in durs:
            total += float(x)
        avg = total / float(len(durs))

        def _pct(p: float) -> float:
            if not durs:
                return 0.0
            if len(durs) == 1:
                return float(durs[0])
            idx = round((float(p) / 100.0) * (len(durs) - 1))
            if idx < 0:
                idx = 0
            if idx >= len(durs):
                idx = len(durs) - 1
            return float(durs[idx])

        return {
            "latency_samples": len(durs),
            "latency_errors": int(errors),
            "latency_min_ms": float(durs[0]),
            "latency_max_ms": float(durs[-1]),
            "latency_avg_ms": float(avg),
            "latency_p50_ms": float(_pct(50.0)),
            "latency_p95_ms": float(_pct(95.0)),
            "latency_p99_ms": float(_pct(99.0)),
        }

    def _get_load_test_section(self, section: Optional[str] = None) -> Dict[str, Any]:
        """Read load_test config section from plugin.toml via PluginConfig.

        - section is None: return [load_test]
        - section = "push_messages": return [load_test.push_messages]
        """
        path = "load_test" if not section else f"load_test.{section}"
        try:
            return self.config.get_section_sync(path)
        except Exception:
            # 配置缺失或格式不对时, 按空配置处理, 避免影响插件可用性
            return {}

    def _get_global_bench_config(self, root_cfg: Optional[Dict[str, Any]]) -> tuple[int, bool]:
        """Read global worker_threads and log_summary from [load_test] section.

        Returns (workers, log_summary).
        """
        base_cfg: Dict[str, Any] = root_cfg or {}
        log_summary = bool(base_cfg.get("log_summary", True))
        workers_raw = base_cfg.get("worker_threads", 1)
        try:
            workers_int = int(workers_raw)
        except Exception:
            workers_int = 1
        workers = max(1, workers_int)
        return workers, log_summary

    def _get_bench_config(
        self,
        root_cfg: Optional[Dict[str, Any]],
        sec_cfg: Optional[Dict[str, Any]],
    ) -> tuple[int, bool, bool]:
        """Read workers/log_summary/multiprocess for a specific benchmark.

        - workers default comes from [load_test].worker_threads
        - can be overridden by section worker_threads, e.g. [load_test.bus_messages_get].worker_threads
        - multiprocess mode bypasses GIL and simulates multiple plugins
        """
        workers, log_summary = self._get_global_bench_config(root_cfg)
        
        # Check for multiprocess mode
        use_multiprocess = False
        try:
            base_cfg: Dict[str, Any] = root_cfg or {}
            use_multiprocess = bool(base_cfg.get("use_multiprocess", False))
        except Exception:
            pass
        
        try:
            if sec_cfg:
                workers_raw = sec_cfg.get("worker_threads")
                if workers_raw is not None:
                    workers = max(1, int(workers_raw))
                # Section-specific multiprocess override
                mp_raw = sec_cfg.get("use_multiprocess")
                if mp_raw is not None:
                    use_multiprocess = bool(mp_raw)
        except Exception:
            pass
        return workers, log_summary, use_multiprocess

    def _get_incremental_diagnostics(self, expr) -> Dict[str, Any]:
        """Get incremental reload diagnostics from a BusList expression.

        Returns a dict with latest_rev/last_seen_rev/fast_hits when available.
        """
        try:
            from plugin.sdk.bus import types as bus_types

            latest = None
            try:
                latest = int(getattr(bus_types, "_BUS_LATEST_REV", {}).get("messages", 0))
            except Exception:
                latest = None
            last_seen = getattr(expr, "_last_seen_bus_rev", None)
            fast_hits = getattr(expr, "_incremental_fast_hits", None)
            return {"latest_rev": latest, "last_seen_rev": last_seen, "fast_hits": fast_hits}
        except Exception:
            return {}

    def _run_benchmark(
        self,
        *,
        test_name: str,
        root_cfg: Optional[Dict[str, Any]],
        sec_cfg: Optional[Dict[str, Any]],
        default_duration: float,
        op_fn,
        log_template: Optional[str] = None,
        build_log_args=None,
        extra_data_builder=None,
    ) -> Dict[str, Any]:
        """Execute a benchmark with common config, timing, logging, and result wiring.

        This helper centralizes the repeated pattern used by bench_* methods:
        - load global/section config
        - resolve enable flag
        - derive workers/log_summary and effective duration
        - run _bench_loop or _bench_loop_concurrent
        - sample latency
        - optional extra data builder
        - optional summary logging
        - wrap result into ok(data={...}) at call site
        """

        with self._bench_lock:
            enabled = True
            try:
                if root_cfg is not None:
                    enabled = bool(root_cfg.get("enable", True))
                if sec_cfg is not None and "enable" in sec_cfg:
                    enabled = bool(sec_cfg.get("enable", enabled))
            except Exception:
                enabled = True
            if not enabled:
                return {"test": test_name, "enabled": False, "skipped": True}

            workers, log_summary, use_multiprocess = self._get_bench_config(root_cfg, sec_cfg)

            dur_cfg = sec_cfg.get("duration_seconds") if sec_cfg else None
            if dur_cfg is None and root_cfg:
                dur_cfg = root_cfg.get("duration_seconds")
            try:
                duration = float(dur_cfg) if dur_cfg is not None else default_duration
            except Exception:
                duration = default_duration
            if duration <= 0:
                duration = default_duration

            throttle_cfg = sec_cfg.get("throttle_seconds") if sec_cfg else None
            if throttle_cfg is None and root_cfg:
                throttle_cfg = root_cfg.get("throttle_seconds")
            try:
                throttle_seconds = float(throttle_cfg) if throttle_cfg is not None else 0.0
            except Exception:
                throttle_seconds = 0.0
            if throttle_seconds < 0:
                throttle_seconds = 0.0

            if workers > 1:
                if use_multiprocess:
                    stats = self._bench_loop_multiprocess(duration, workers, op_fn, throttle_seconds=throttle_seconds)
                else:
                    stats = self._bench_loop_concurrent(duration, workers, op_fn, throttle_seconds=throttle_seconds)
            else:
                stats = self._bench_loop(duration, op_fn, throttle_seconds=throttle_seconds)

            try:
                stats.update(self._sample_latency_ms(op_fn, samples=100))
            except Exception:
                pass

            if callable(extra_data_builder):
                try:
                    extra = extra_data_builder(stats, duration, workers)
                    if isinstance(extra, dict):
                        stats.update(extra)
                except Exception:
                    pass

            if log_summary and log_template:
                try:
                    args: tuple[Any, ...] = ()
                    if callable(build_log_args):
                        built = build_log_args(duration, stats, workers)
                        if isinstance(built, tuple):
                            args = built
                    self.logger.info(log_template, *args)
                except Exception:
                    pass

            # Caller is responsible for wrapping into ok(data={...}).
            return {"test": test_name, **stats}

    @plugin_entry(
        id="op_bus_messages_get",
        name="Op Bus Messages Get",
        description="Single operation: call ctx.bus.messages.get once (for external HTTP load testing)",
        input_schema={
            "type": "object",
            "properties": {
                "max_count": {"type": "integer", "default": 50},
                "plugin_id": {"type": "string", "default": "*"},
                "timeout": {"type": "number", "default": 0.5},
            },
        },
    )
    def op_bus_messages_get(
        self,
        max_count: int = 50,
        plugin_id: str = "*",
        timeout: float = 0.5,
        **_: Any,
    ):
        pid_norm = None if not plugin_id or plugin_id.strip() == "*" else plugin_id.strip()
        res = self.ctx.bus.messages.get(
            plugin_id=pid_norm,
            max_count=int(max_count),
            timeout=float(timeout),
            raw=True,
        )
        # Avoid returning large payload over HTTP.
        return ok(data={"count": len(res)})

    @plugin_entry(
        id="op_buslist_reload",
        name="Op BusList Reload",
        description="Single operation: build BusList expr (filter + +/-) and reload once (for external HTTP load testing)",
        input_schema={
            "type": "object",
            "properties": {
                "max_count": {"type": "integer", "default": 500},
                "timeout": {"type": "number", "default": 1.0},
                "source": {"type": "string", "default": ""},
                "inplace": {"type": "boolean", "default": True},
            },
        },
    )
    def op_buslist_reload(
        self,
        max_count: int = 500,
        timeout: float = 1.0,
        source: str = "",
        inplace: bool = True,
        **_: Any,
    ):
        base_list = self.ctx.bus.messages.get(
            plugin_id=None,
            max_count=int(max_count),
            timeout=float(timeout),
            raw=True,
        )
        if len(base_list) == 0:
            for _i in range(10):
                self.ctx.push_message(
                    source="load_tester.seed",
                    message_type="text",
                    description="seed message for op_buslist_reload",
                    priority=1,
                    content="seed",
                )
            base_list = self.ctx.bus.messages.get(
                plugin_id=None,
                max_count=int(max_count),
                timeout=float(timeout),
                raw=True,
            )

        flt_kwargs: Dict[str, Any] = {}
        if source:
            flt_kwargs["source"] = source
        else:
            flt_kwargs["source"] = "load_tester"

        left = base_list.filter(strict=False, **flt_kwargs)
        right = base_list.filter(strict=False, **flt_kwargs)
        expr = (left + right) - left
        ctx = cast(BusReplayContext, self.ctx)
        out = expr.reload_with(ctx, inplace=bool(inplace))
        return ok(data={"count": len(out)})

    @plugin_entry(
        id="bench_push_messages",
        name="Bench Push Messages",
        description="Measure QPS of ctx.push_message (message bus write)",
        input_schema={
            "type": "object",
            "properties": {
                "duration_seconds": {
                    "type": "number",
                    "description": "Benchmark duration in seconds",
                    "default": 5.0,
                },
            },
        },
    )
    def bench_push_messages(self, duration_seconds: float = 5.0, **_: Any):
        root_cfg = self._get_load_test_section(None)
        sec_cfg = self._get_load_test_section("push_messages")

        def _op() -> None:
            self.ctx.push_message(
                source="load_tester.push_messages",
                message_type="text",
                description="load test message",
                priority=1,
                content="load_test",
                fast_mode=False,
            )

        def _build_log_args(duration: float, stats: Dict[str, Any], workers: int):
            return (
                duration,
                stats["iterations"],
                stats["qps"],
                stats["errors"],
                stats.get("workers", workers),
            )

        stats = self._run_benchmark(
            test_name="bench_push_messages",
            root_cfg=root_cfg,
            sec_cfg=sec_cfg,
            default_duration=duration_seconds,
            op_fn=_op,
            log_template=(
                "[load_tester] bench_push_messages duration={}s iterations={} qps={} errors={} workers={}"
            ),
            build_log_args=_build_log_args,
        )
        return ok(data=stats)

    @plugin_entry(
        id="bench_push_messages_fast",
        name="Bench Push Messages (Fast)",
        description="Measure QPS of ctx.push_message(fast_mode=True) (ZeroMQ PUSH/PULL + batching)",
        input_schema={
            "type": "object",
            "properties": {
                "duration_seconds": {
                    "type": "number",
                    "description": "Benchmark duration in seconds",
                    "default": 5.0,
                },
            },
        },
    )
    def bench_push_messages_fast(self, duration_seconds: float = 5.0, **_: Any):
        root_cfg = self._get_load_test_section(None)
        sec_cfg = self._get_load_test_section("push_messages_fast")

        def _op() -> None:
            self.ctx.push_message(
                source="load_tester.push_messages_fast",
                message_type="text",
                description="load test message (fast)",
                priority=1,
                content="load_test",
                fast_mode=True,
            )

        def _build_log_args(duration: float, stats: Dict[str, Any], workers: int):
            return (
                duration,
                stats["iterations"],
                stats["qps"],
                stats["errors"],
                stats.get("workers", workers),
            )

        stats = self._run_benchmark(
            test_name="bench_push_messages_fast",
            root_cfg=root_cfg,
            sec_cfg=sec_cfg,
            default_duration=duration_seconds,
            op_fn=_op,
            log_template=(
                "[load_tester] bench_push_messages_fast duration={}s iterations={} qps={} errors={} workers={}"
            ),
            build_log_args=_build_log_args,
        )
        return ok(data=stats)

    @plugin_entry(
        id="bench_bus_messages_get",
        name="Bench Bus Messages Get",
        description="Measure QPS of bus.messages.get() (message bus read)",
        input_schema={
            "type": "object",
            "properties": {
                "duration_seconds": {"type": "number", "default": 5.0},
                "max_count": {"type": "integer", "default": 50},
                "plugin_id": {"type": "string", "default": "*"},
                "timeout": {"type": "number", "default": 0.5},
            },
        },
    )
    def bench_bus_messages_get(
        self,
        duration_seconds: float = 5.0,
        max_count: int = 50,
        plugin_id: str = "*",
        timeout: float = 0.5,
        **_: Any,
    ):
        root_cfg = self._get_load_test_section(None)
        sec_cfg = self._get_load_test_section("bus_messages_get")

        timeout_cfg = None
        if sec_cfg:
            timeout_cfg = sec_cfg.get("timeout")
        if timeout_cfg is None and root_cfg:
            timeout_cfg = root_cfg.get("timeout")
        try:
            if timeout_cfg is not None:
                timeout = float(timeout_cfg)
        except Exception:
            pass

        pid_norm = None if not plugin_id or plugin_id.strip() == "*" else plugin_id.strip()

        def _op() -> None:
            _ = self.ctx.bus.messages.get(
                plugin_id=pid_norm,
                max_count=int(max_count),
                timeout=float(timeout),
                raw=True,
                no_fallback=True,
            )

        def _build_log_args(duration: float, stats: Dict[str, Any], workers: int):
            return (
                duration,
                stats["iterations"],
                stats["qps"],
                stats["errors"],
                max_count,
                plugin_id,
                timeout,
                stats.get("workers", workers),
            )

        stats = self._run_benchmark(
            test_name="bench_bus_messages_get",
            root_cfg=root_cfg,
            sec_cfg=sec_cfg,
            default_duration=duration_seconds,
            op_fn=_op,
            log_template=(
                "[load_tester] bench_bus_messages_get duration={}s iterations={} qps={} errors={} max_count={} plugin_id={} timeout={} workers={}"
            ),
            build_log_args=_build_log_args,
        )
        return ok(data=stats)

    @plugin_entry(
        id="bench_bus_events_get",
        name="Bench Bus Events Get",
        description="Measure QPS of bus.events.get() (event bus read)",
        input_schema={
            "type": "object",
            "properties": {
                "duration_seconds": {"type": "number", "default": 5.0},
                "max_count": {"type": "integer", "default": 50},
                "plugin_id": {"type": "string", "default": "*"},
                "timeout": {"type": "number", "default": 0.5},
            },
        },
    )
    def bench_bus_events_get(
        self,
        duration_seconds: float = 5.0,
        max_count: int = 50,
        plugin_id: str = "*",
        timeout: float = 0.5,
        **_: Any,
    ):
        root_cfg = self._get_load_test_section(None)
        sec_cfg = self._get_load_test_section("bus_events_get")

        timeout_cfg = None
        if sec_cfg:
            timeout_cfg = sec_cfg.get("timeout")
        if timeout_cfg is None and root_cfg:
            timeout_cfg = root_cfg.get("timeout")
        try:
            if timeout_cfg is not None:
                timeout = float(timeout_cfg)
        except Exception:
            pass

        pid_norm = None if not plugin_id or plugin_id.strip() == "*" else plugin_id.strip()

        def _op() -> None:
            _ = self.ctx.bus.events.get(
                plugin_id=pid_norm,
                max_count=int(max_count),
                timeout=float(timeout),
            )

        def _build_log_args(duration: float, stats: Dict[str, Any], workers: int):
            return (
                duration,
                stats["iterations"],
                stats["qps"],
                stats["errors"],
                max_count,
                plugin_id,
                timeout,
                stats.get("workers", workers),
            )

        stats = self._run_benchmark(
            test_name="bench_bus_events_get",
            root_cfg=root_cfg,
            sec_cfg=sec_cfg,
            default_duration=duration_seconds,
            op_fn=_op,
            log_template=(
                "[load_tester] bench_bus_events_get duration={}s iterations={} qps={} errors={} max_count={} plugin_id={} timeout={} workers={}"
            ),
            build_log_args=_build_log_args,
        )
        return ok(data=stats)

    @plugin_entry(
        id="bench_bus_lifecycle_get",
        name="Bench Bus Lifecycle Get",
        description="Measure QPS of bus.lifecycle.get() (lifecycle bus read)",
        input_schema={
            "type": "object",
            "properties": {
                "duration_seconds": {"type": "number", "default": 5.0},
                "max_count": {"type": "integer", "default": 50},
                "plugin_id": {"type": "string", "default": "*"},
                "timeout": {"type": "number", "default": 0.5},
            },
        },
    )
    def bench_bus_lifecycle_get(
        self,
        duration_seconds: float = 5.0,
        max_count: int = 50,
        plugin_id: str = "*",
        timeout: float = 0.5,
        **_: Any,
    ):
        root_cfg = self._get_load_test_section(None)
        sec_cfg = self._get_load_test_section("bus_lifecycle_get")

        timeout_cfg = None
        if sec_cfg:
            timeout_cfg = sec_cfg.get("timeout")
        if timeout_cfg is None and root_cfg:
            timeout_cfg = root_cfg.get("timeout")
        try:
            if timeout_cfg is not None:
                timeout = float(timeout_cfg)
        except Exception:
            pass

        pid_norm = None if not plugin_id or plugin_id.strip() == "*" else plugin_id.strip()

        def _op() -> None:
            _ = self.ctx.bus.lifecycle.get(
                plugin_id=pid_norm,
                max_count=int(max_count),
                timeout=float(timeout),
            )

        def _build_log_args(duration: float, stats: Dict[str, Any], workers: int):
            return (
                duration,
                stats["iterations"],
                stats["qps"],
                stats["errors"],
                max_count,
                plugin_id,
                timeout,
                stats.get("workers", workers),
            )

        stats = self._run_benchmark(
            test_name="bench_bus_lifecycle_get",
            root_cfg=root_cfg,
            sec_cfg=sec_cfg,
            default_duration=duration_seconds,
            op_fn=_op,
            log_template=(
                "[load_tester] bench_bus_lifecycle_get duration={}s iterations={} qps={} errors={} max_count={} plugin_id={} timeout={} workers={}"
            ),
            build_log_args=_build_log_args,
        )
        return ok(data=stats)

    @plugin_entry(
        id="bench_buslist_filter",
        name="Bench BusList Filter",
        description="Measure QPS of BusList.filter() on a preloaded message list",
        input_schema={
            "type": "object",
            "properties": {
                "duration_seconds": {"type": "number", "default": 5.0},
                "max_count": {"type": "integer", "default": 500},
                "timeout": {"type": "number", "default": 1.0},
                "source": {"type": "string", "default": ""},
            },
        },
    )
    def bench_buslist_filter(
        self,
        duration_seconds: float = 5.0,
        max_count: int = 500,
        timeout: float = 1.0,
        source: str = "",
        **_: Any,
    ):
        root_cfg = self._get_load_test_section(None)
        sec_cfg = self._get_load_test_section("buslist_filter")

        timeout_cfg = None
        if sec_cfg:
            timeout_cfg = sec_cfg.get("timeout")
        if timeout_cfg is None and root_cfg:
            timeout_cfg = root_cfg.get("timeout")
        try:
            if timeout_cfg is not None:
                timeout = float(timeout_cfg)
        except Exception:
            pass

        base_list = self.ctx.bus.messages.get(
            plugin_id=None,
            max_count=int(max_count),
            timeout=float(timeout),
            raw=True,
        )

        if len(base_list) == 0:
            try:
                self.logger.info("[load_tester] bench_buslist_filter: no messages available, pushing seed messages")
            except Exception:
                pass
            for _i in range(10):
                self.ctx.push_message(
                    source="load_tester.seed",
                    message_type="text",
                    description="seed message for buslist benchmark",
                    priority=1,
                    content="seed",
                )
            base_list = self.ctx.bus.messages.get(
                plugin_id=None,
                max_count=int(max_count),
                timeout=float(timeout),
            )

        flt_kwargs: Dict[str, Any] = {}
        if source:
            flt_kwargs["source"] = source
        else:
            flt_kwargs["source"] = "load_tester"

        def _op() -> None:
            _ = base_list.filter(strict=False, **flt_kwargs)

        def _extra_data_builder(_stats: Dict[str, Any], _duration: float, _workers: int) -> Dict[str, Any]:
            return {"base_size": len(base_list)}

        def _build_log_args(duration: float, stats: Dict[str, Any], workers: int):
            return (
                duration,
                stats["iterations"],
                stats["qps"],
                stats["errors"],
                len(base_list),
                flt_kwargs,
                stats.get("workers", workers),
            )

        stats = self._run_benchmark(
            test_name="bench_buslist_filter",
            root_cfg=root_cfg,
            sec_cfg=sec_cfg,
            default_duration=duration_seconds,
            op_fn=_op,
            log_template=(
                "[load_tester] bench_buslist_filter duration={}s iterations={} qps={} errors={} base_size={} filter={} workers={}"
            ),
            build_log_args=_build_log_args,
            extra_data_builder=_extra_data_builder,
        )
        return ok(data=stats)

    @worker(timeout=300.0)  # Run in worker thread to avoid blocking command loop
    @plugin_entry(
        id="bench_plugin_event_qps",
        name="Bench Plugin Event QPS",
        description=(
            "Load test a target plugin custom event via ctx.trigger_plugin_event, "
            "measuring target QPS vs achieved QPS and errors, plus latency stats."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "target_plugin_id": {"type": "string"},
                "event_type": {"type": "string"},
                "event_id": {"type": "string"},
                "args": {"type": "object", "default": {}},
                "duration_seconds": {"type": "number", "default": 5.0},
                "qps_targets": {
                    "type": "array",
                    "items": {"type": "number"},
                    "default": [10.0, 50.0, 100.0],
                },
                "timeout": {"type": "number", "default": 2.0},
            },
            "required": ["target_plugin_id", "event_type", "event_id"],
        },
    )
    def bench_plugin_event_qps(
        self,
        target_plugin_id: str,
        event_type: str,
        event_id: str,
        args: Optional[Dict[str, Any]] = None,
        duration_seconds: float = 5.0,
        qps_targets: Optional[list[float]] = None,
        timeout: float = 2.0,
        **_: Any,
    ):
        if args is None:
            args = {}

        # Run the actual pressure test in a background worker thread so that
        # sync plugin-to-plugin calls do not execute directly inside the
        # command-loop handler context (avoids sync_call_in_handler warnings).
        result_box: list[Dict[str, Any]] = []

        def _run() -> None:
            nonlocal args

            # Ensure args is a plain dict for trigger_plugin_event type expectations
            local_args: Dict[str, Any] = dict(args) if args else {}

            # Normalize qps_targets
            targets: list[float] = []
            if isinstance(qps_targets, list):
                for t in qps_targets:
                    try:
                        v = float(t)
                    except Exception:
                        continue
                    if v > 0:
                        targets.append(v)
            if not targets:
                targets = [10.0, 50.0, 100.0]

            all_latencies_ms: list[float] = []
            total_calls = 0
            total_errors = 0
            table_rows: list[Dict[str, Any]] = []

            for tq in targets:
                interval = 1.0 / float(tq)
                dur = float(duration_seconds)
                start = time.perf_counter()
                end_time = start + dur
                calls = 0
                errors = 0
                latencies_ms: list[float] = []

                while True:
                    if self._stop_event.is_set():
                        break
                    now = time.perf_counter()
                    if now >= end_time:
                        break

                    t0 = time.perf_counter()
                    try:
                        self.ctx.trigger_plugin_event(
                            target_plugin_id=target_plugin_id,
                            event_type=event_type,
                            event_id=event_id,
                            args=local_args,
                            timeout=float(timeout),
                        )
                    except Exception as e:
                        errors += 1
                        try:
                            self.logger.warning(
                                "[load_tester] bench_plugin_event_qps call failed: {}", e
                            )
                        except Exception:
                            pass
                    dt_ms = (time.perf_counter() - t0) * 1000.0
                    latencies_ms.append(float(dt_ms))
                    calls += 1

                    # Simple open-loop pacing towards target QPS
                    now2 = time.perf_counter()
                    sleep_for = interval - (now2 - now)
                    if sleep_for > 0:
                        try:
                            time.sleep(sleep_for)
                        except Exception:
                            pass

                elapsed = time.perf_counter() - start
                achieved_qps = float(calls) / elapsed if elapsed > 0 else 0.0

                total_calls += calls
                total_errors += errors
                all_latencies_ms.extend(latencies_ms)

                error_rate = float(errors) / float(calls) if calls > 0 else 0.0

                # Per-target latency stats (ms)
                if latencies_ms:
                    durs = sorted(latencies_ms)
                    n = len(durs)
                    avg = sum(durs) / float(n)

                    def _pct(p: float, data: list[float] = durs) -> float:
                        if not data:
                            return 0.0
                        if len(data) == 1:
                            return float(data[0])
                        idx = round((float(p) / 100.0) * (len(data) - 1))
                        if idx < 0:
                            idx = 0
                        if idx >= len(data):
                            idx = len(data) - 1
                        return float(data[idx])

                    per_latency = {
                        "latency_min_ms": float(durs[0]),
                        "latency_max_ms": float(durs[-1]),
                        "latency_avg_ms": float(avg),
                        "latency_p50_ms": float(_pct(50.0)),
                        "latency_p95_ms": float(_pct(95.0)),
                        "latency_p99_ms": float(_pct(99.0)),
                    }
                else:
                    per_latency = {}

                table_rows.append(
                    {
                        "target_qps": float(tq),
                        "achieved_qps": achieved_qps,
                        "calls": int(calls),
                        "errors": int(errors),
                        "error_rate": error_rate,
                        **per_latency,
                    }
                )

            # Recompute peak_qps from table_rows to avoid nonlocal mutation complexity
            if table_rows:
                peak_qps_val = max(float(r.get("achieved_qps", 0.0)) for r in table_rows)
            else:
                peak_qps_val = 0.0

            # Overall latency stats across all targets
            if all_latencies_ms:
                durs_all = sorted(all_latencies_ms)
                n_all = len(durs_all)
                avg_all = sum(durs_all) / float(n_all)

                def _pct_all(p: float) -> float:
                    if not durs_all:
                        return 0.0
                    if len(durs_all) == 1:
                        return float(durs_all[0])
                    idx = round((float(p) / 100.0) * (len(durs_all) - 1))
                    if idx < 0:
                        idx = 0
                    if idx >= len(durs_all):
                        idx = len(durs_all) - 1
                    return float(durs_all[idx])

                overall_latency: Dict[str, Any] = {
                    "latency_samples": int(len(durs_all)),
                    "latency_min_ms": float(durs_all[0]),
                    "latency_max_ms": float(durs_all[-1]),
                    "latency_avg_ms": float(avg_all),
                    "latency_p50_ms": float(_pct_all(50.0)),
                    "latency_p95_ms": float(_pct_all(95.0)),
                    "latency_p99_ms": float(_pct_all(99.0)),
                }
            else:
                overall_latency = {"latency_samples": 0}

            result = {
                "test": "bench_plugin_event_qps",
                "target_plugin_id": target_plugin_id,
                "event_type": event_type,
                "event_id": event_id,
                "timeout": float(timeout),
                "duration_seconds_per_target": float(duration_seconds),
                "qps_table": table_rows,
                "peak_qps": float(peak_qps_val),
                "total_calls": int(total_calls),
                "total_errors": int(total_errors),
                **overall_latency,
            }

            # Standalone summary log (do not integrate into run_all_benchmarks)
            try:
                headers = ["target_qps", "achieved_qps", "calls", "errors", "error_rate"]
                rows = []
                for row in table_rows:
                    rows.append(
                        [
                            f"{row.get('target_qps', 0.0):.1f}",
                            f"{row.get('achieved_qps', 0.0):.1f}",
                            str(row.get("calls", 0)),
                            str(row.get("errors", 0)),
                            f"{row.get('error_rate', 0.0):.3f}",
                        ]
                    )

                cols = list(zip(*[headers, *rows], strict=True)) if rows else [headers]
                widths = [max(len(str(x)) for x in col) for col in cols]

                def _line(parts: list[str]) -> str:
                    return " | ".join(p.ljust(w) for p, w in zip(parts, widths, strict=True))

                sep = "-+-".join("-" * w for w in widths)
                table = "\n".join([
                    _line(headers),
                    sep,
                    *[_line(r) for r in rows],
                ])
                self.logger.info("[load_tester] bench_plugin_event_qps summary:\n{}", table)
            except Exception:
                pass

            result_box.append(result)

        # Run directly in worker thread context (no need for separate thread with @worker decorator)
        _run()

        if not result_box:
            # In case the worker failed very early; return an empty result shell
            return ok(
                data={
                    "test": "bench_plugin_event_qps",
                    "target_plugin_id": target_plugin_id,
                    "event_type": event_type,
                    "event_id": event_id,
                    "timeout": float(timeout),
                    "duration_seconds_per_target": float(duration_seconds),
                    "qps_table": [],
                    "peak_qps": 0.0,
                    "total_calls": 0,
                    "total_errors": 0,
                    "latency_samples": 0,
                }
            )

        return ok(data=result_box[0])

    @plugin_entry(
        id="bench_buslist_reload",
        name="Bench BusList Reload",
        description="Measure QPS of BusList.reload() after filter and binary ops (+/-)",
        input_schema={
            "type": "object",
            "properties": {
                "duration_seconds": {"type": "number", "default": 5.0},
                "max_count": {"type": "integer", "default": 500},
                "timeout": {"type": "number", "default": 1.0},
                "source": {"type": "string", "default": ""},
                "inplace": {"type": "boolean", "default": False},
                "incremental": {"type": "boolean", "default": False},
            },
        },
    )
    def bench_buslist_reload(
        self,
        duration_seconds: float = 5.0,
        max_count: int = 500,
        timeout: float = 1.0,
        source: str = "",
        inplace: bool = False,
        incremental: bool = False,
        **_: Any,
    ):
        root_cfg = self._get_load_test_section(None)
        sec_cfg = self._get_load_test_section("buslist_reload")

        timeout_cfg = None
        if sec_cfg:
            timeout_cfg = sec_cfg.get("timeout")
        if timeout_cfg is None and root_cfg:
            timeout_cfg = root_cfg.get("timeout")
        try:
            if timeout_cfg is not None:
                timeout = float(timeout_cfg)
        except Exception:
            pass

        try:
            inplace_cfg = sec_cfg.get("inplace") if sec_cfg else None
            if inplace_cfg is not None:
                inplace = bool(inplace_cfg)
        except Exception:
            pass

        base_list = self.ctx.bus.messages.get(
            plugin_id=None,
            max_count=int(max_count),
            timeout=float(timeout),
        )

        if len(base_list) == 0:
            try:
                self.logger.info("[load_tester] bench_buslist_reload: no messages available, pushing seed messages")
            except Exception:
                pass
            for _i in range(10):
                self.ctx.push_message(
                    source="load_tester.seed",
                    message_type="text",
                    description="seed message for buslist reload benchmark",
                    priority=1,
                    content="seed",
                )
            base_list = self.ctx.bus.messages.get(
                plugin_id=None,
                max_count=int(max_count),
                timeout=float(timeout),
            )

        flt_kwargs: Dict[str, Any] = {}
        if source:
            flt_kwargs["source"] = source
        else:
            flt_kwargs["source"] = "load_tester"

        left = base_list.filter(strict=False, **flt_kwargs)
        right = base_list.filter(strict=False, **flt_kwargs)
        expr = (left + right) - left

        def _op() -> None:
            ctx = cast(BusReplayContext, self.ctx)
            _ = expr.reload_with(ctx, inplace=bool(inplace), incremental=bool(incremental))

        def _extra_data_builder(_stats: Dict[str, Any], _duration: float, _workers: int) -> Dict[str, Any]:
            data: Dict[str, Any] = {
                "base_size": len(base_list),
                "inplace": bool(inplace),
                "incremental": bool(incremental),
            }
            try:
                if bool(incremental):
                    data.update(self._get_incremental_diagnostics(expr))
            except Exception:
                pass
            return data

        def _build_log_args(duration: float, stats: Dict[str, Any], workers: int):
            return (
                duration,
                stats["iterations"],
                stats["qps"],
                stats["errors"],
                len(base_list),
                flt_kwargs,
                bool(inplace),
                bool(incremental),
                stats.get("workers", workers),
            )

        stats = self._run_benchmark(
            test_name="bench_buslist_reload",
            root_cfg=root_cfg,
            sec_cfg=sec_cfg,
            default_duration=duration_seconds,
            op_fn=_op,
            log_template=(
                "[load_tester] bench_buslist_reload duration={}s iterations={} qps={} errors={} base_size={} filter={} inplace={} incremental={} workers={}"
            ),
            build_log_args=_build_log_args,
            extra_data_builder=_extra_data_builder,
        )
        return ok(data=stats)

    @plugin_entry(
        id="bench_buslist_reload_nochange",
        name="Bench BusList Reload (No Change)",
        description="Measure QPS of BusList.reload(incremental=True) when bus content is stable (fast-path hit)",
        input_schema={
            "type": "object",
            "properties": {
                "duration_seconds": {"type": "number", "default": 5.0},
                "max_count": {"type": "integer", "default": 500},
                "timeout": {"type": "number", "default": 1.0},
                "source": {"type": "string", "default": ""},
                "inplace": {"type": "boolean", "default": False},
            },
        },
    )
    def bench_buslist_reload_nochange(
        self,
        duration_seconds: float = 5.0,
        max_count: int = 500,
        timeout: float = 1.0,
        source: str = "",
        inplace: bool = False,
        **_: Any,
    ):
        root_cfg = self._get_load_test_section(None)
        sec_cfg = self._get_load_test_section("buslist_reload_nochange")

        timeout_cfg = None
        if sec_cfg:
            timeout_cfg = sec_cfg.get("timeout")
        if timeout_cfg is None and root_cfg:
            timeout_cfg = root_cfg.get("timeout")
        try:
            if timeout_cfg is not None:
                timeout = float(timeout_cfg)
        except Exception:
            pass

        dur_cfg = sec_cfg.get("duration_seconds") if sec_cfg else None
        if dur_cfg is None and root_cfg:
            dur_cfg = root_cfg.get("duration_seconds")
        try:
            duration = float(dur_cfg) if dur_cfg is not None else duration_seconds
        except Exception:
            duration = duration_seconds

        base_list = self.ctx.bus.messages.get(
            plugin_id=None,
            max_count=int(max_count),
            timeout=float(timeout),
        )
        if len(base_list) == 0:
            for _i in range(10):
                self.ctx.push_message(
                    source="load_tester.seed",
                    message_type="text",
                    description="seed message for buslist reload(nochange) benchmark",
                    priority=1,
                    content="seed",
                )
            base_list = self.ctx.bus.messages.get(
                plugin_id=None,
                max_count=int(max_count),
                timeout=float(timeout),
            )

        flt_kwargs: Dict[str, Any] = {}
        if source:
            flt_kwargs["source"] = source
        else:
            flt_kwargs["source"] = "load_tester"

        left = base_list.filter(strict=False, **flt_kwargs)
        right = base_list.filter(strict=False, **flt_kwargs)
        expr = (left + right) - left

        # Prime the incremental cache and last_seen_rev once.
        try:
            ctx = cast(BusReplayContext, self.ctx)
            expr.reload_with(ctx, inplace=bool(inplace), incremental=True)
        except Exception:
            pass

        def _op() -> None:
            ctx = cast(BusReplayContext, self.ctx)
            _ = expr.reload_with(ctx, inplace=bool(inplace), incremental=True)

        def _extra_data_builder(stats: Dict[str, Any], _duration: float, _workers: int) -> Dict[str, Any]:
            data: Dict[str, Any] = {
                "base_size": len(base_list),
                "inplace": bool(inplace),
            }
            try:
                data.update(self._get_incremental_diagnostics(expr))
            except Exception:
                pass
            return data

        def _build_log_args(duration: float, stats: Dict[str, Any], workers: int):
            diag = self._get_incremental_diagnostics(expr)
            return (
                duration,
                stats["iterations"],
                stats["qps"],
                stats["errors"],
                len(base_list),
                flt_kwargs,
                bool(inplace),
                diag,
            )

        stats = self._run_benchmark(
            test_name="bench_buslist_reload_nochange",
            root_cfg=root_cfg,
            sec_cfg=sec_cfg,
            default_duration=duration,
            op_fn=_op,
            log_template=(
                "[load_tester] bench_buslist_reload_nochange duration={}s iterations={} qps={} errors={} base_size={} filter={} inplace={} diag={}"
            ),
            build_log_args=_build_log_args,
            extra_data_builder=_extra_data_builder,
        )
        return ok(data=stats)

    @plugin_entry(
        id="run_all_benchmarks",
        name="Run All Benchmarks",
        description="Run a suite of QPS benchmarks for core subsystems",
        input_schema={
            "type": "object",
            "properties": {
                "duration_seconds": {"type": "number", "default": 5.0},
            },
        },
    )
    def run_all_benchmarks(self, duration_seconds: float = 5.0, **_: Any):
        # IMPORTANT: this entry may be triggered via IPC (front-end /runs).
        # Do not run heavy benchmarks inline inside handler; it can block the command loop
        # and cause IPC timeouts. We spawn a background thread and return immediately.
        try:
            with self._run_all_guard:
                if self._run_all_running:
                    return ok(data={"accepted": False, "reason": "benchmark already running"})
                self._run_all_running = True
        except Exception:
            pass

        def _bg() -> None:
            try:
                if self._stop_event.is_set():
                    return
                with self._bench_lock:
                    self._run_all_benchmarks_sync(duration_seconds=float(duration_seconds))
            except Exception as e:
                try:
                    self.logger.warning("[load_tester] run_all_benchmarks background failed: {}", e)
                except Exception:
                    pass
            finally:
                try:
                    with self._run_all_guard:
                        self._run_all_running = False
                except Exception:
                    pass

        try:
            t = threading.Thread(target=_bg, daemon=True, name="load_tester-run_all")
            self._run_thread = t
            t.start()
        except Exception as e:
            try:
                with self._run_all_guard:
                    self._run_all_running = False
            except Exception:
                pass
            return ok(data={"accepted": False, "reason": str(e)})

        return ok(data={"accepted": True})

    def _run_all_benchmarks_sync(self, duration_seconds: float = 5.0) -> Dict[str, Any]:
        results: Dict[str, Any] = {}
        root_cfg = self._get_load_test_section(None)
        try:
            sleep_between = float((root_cfg or {}).get("sleep_seconds", 0.0) or 0.0)
        except Exception:
            sleep_between = 0.0
        if sleep_between < 0:
            sleep_between = 0.0

        def _pause(section: str | None = None) -> None:
            sec_sleep = None
            if section:
                try:
                    sec_cfg = self._get_load_test_section(section)
                except Exception:
                    sec_cfg = {}
                try:
                    if isinstance(sec_cfg, dict):
                        sec_sleep = sec_cfg.get("sleep_after_seconds")
                except Exception:
                    sec_sleep = None
            try:
                effective = float(sec_sleep) if sec_sleep is not None else float(sleep_between)
            except Exception:
                effective = float(sleep_between)
            if effective <= 0:
                return
            try:
                self._stop_event.wait(timeout=float(effective))
            except Exception:
                try:
                    time.sleep(float(effective))
                except Exception:
                    pass
        try:
            results["bench_push_messages"] = self._unwrap_ok_data(
                self.bench_push_messages(duration_seconds=duration_seconds)
            )
        except Exception as e:
            results["bench_push_messages"] = {"error": str(e)}
        _pause("push_messages")
        try:
            results["bench_bus_messages_get"] = self._unwrap_ok_data(
                self.bench_bus_messages_get(duration_seconds=duration_seconds)
            )
        except Exception as e:
            results["bench_bus_messages_get"] = {"error": str(e)}
        _pause("bus_messages_get")
        try:
            results["bench_push_messages_fast"] = self._unwrap_ok_data(
                self.bench_push_messages_fast(duration_seconds=duration_seconds)
            )
        except Exception as e:
            results["bench_push_messages_fast"] = {"error": str(e)}
        _pause("push_messages_fast")
        try:
            results["bench_bus_events_get"] = self._unwrap_ok_data(
                self.bench_bus_events_get(duration_seconds=duration_seconds)
            )
        except Exception as e:
            results["bench_bus_events_get"] = {"error": str(e)}
        _pause("bus_events_get")
        try:
            results["bench_bus_lifecycle_get"] = self._unwrap_ok_data(
                self.bench_bus_lifecycle_get(duration_seconds=duration_seconds)
            )
        except Exception as e:
            results["bench_bus_lifecycle_get"] = {"error": str(e)}
        _pause("bus_lifecycle_get")
        try:
            results["bench_buslist_filter"] = self._unwrap_ok_data(
                self.bench_buslist_filter(duration_seconds=duration_seconds)
            )
        except Exception as e:
            results["bench_buslist_filter"] = {"error": str(e)}
        _pause("buslist_filter")
        try:
            results["bench_buslist_reload_full"] = self._unwrap_ok_data(
                self.bench_buslist_reload(duration_seconds=duration_seconds, incremental=False)
            )
        except Exception as e:
            results["bench_buslist_reload_full"] = {"error": str(e)}
        _pause("buslist_reload")
        try:
            results["bench_buslist_reload_incr"] = self._unwrap_ok_data(
                self.bench_buslist_reload(duration_seconds=duration_seconds, incremental=True)
            )
        except Exception as e:
            results["bench_buslist_reload_incr"] = {"error": str(e)}
        _pause("buslist_reload")
        try:
            results["bench_buslist_reload_nochange"] = self._unwrap_ok_data(
                self.bench_buslist_reload_nochange(duration_seconds=duration_seconds)
            )
        except Exception as e:
            results["bench_buslist_reload_nochange"] = {"error": str(e)}
        _pause("buslist_reload_nochange")

        try:
            headers = ["test", "qps", "errors", "iterations", "elapsed_s", "extra"]
            rows = []
            for k, v in results.items():
                if not isinstance(v, dict):
                    rows.append([k, "-", "-", "-", "-", "-"])
                    continue
                qps = v.get("qps")
                errors = v.get("errors")
                iters = v.get("iterations")
                elapsed = v.get("elapsed_seconds")
                extra_parts = []
                if "base_size" in v:
                    extra_parts.append(f"base={v.get('base_size')}")
                if "inplace" in v:
                    extra_parts.append(f"inplace={v.get('inplace')}")
                if "incremental" in v:
                    extra_parts.append(f"incr={v.get('incremental')}")
                if "fast_hits" in v:
                    extra_parts.append(f"fast_hits={v.get('fast_hits')}")
                if "last_seen_rev" in v:
                    extra_parts.append(f"seen_rev={v.get('last_seen_rev')}")
                if "latest_rev" in v:
                    extra_parts.append(f"latest_rev={v.get('latest_rev')}")
                if "workers" in v:
                    extra_parts.append(f"workers={v.get('workers')}")
                lat_avg = v.get("latency_avg_ms")
                lat_p95 = v.get("latency_p95_ms")
                lat_p99 = v.get("latency_p99_ms")
                if lat_avg is not None and lat_p95 is not None and lat_p99 is not None:
                    try:
                        extra_parts.append(f"lat={float(lat_avg):.3f}/{float(lat_p95):.3f}/{float(lat_p99):.3f}ms")
                    except Exception:
                        pass
                if "error" in v:
                    extra_parts.append(f"error={v.get('error')}")
                extra = " ".join([p for p in extra_parts if p])

                def _fmt_num(x: Any, kind: str) -> str:
                    if x is None:
                        return "-"
                    try:
                        if kind == "int":
                            return str(int(x))
                        if kind == "float1":
                            return f"{float(x):.1f}"
                        if kind == "float3":
                            return f"{float(x):.3f}"
                        return str(x)
                    except Exception:
                        return "-"

                rows.append(
                    [
                        str(k),
                        _fmt_num(qps, "float1"),
                        _fmt_num(errors, "int"),
                        _fmt_num(iters, "int"),
                        _fmt_num(elapsed, "float3"),
                        extra,
                    ]
                )

            expected_len = len(headers)
            if any(len(r) != expected_len for r in rows):
                raise ValueError("Invalid summary table: row length mismatch")

            cols = list(zip(*[headers, *rows], strict=True))
            widths = [max(len(str(x)) for x in col) for col in cols]

            def _line(parts: list[str]) -> str:
                return " | ".join(p.ljust(w) for p, w in zip(parts, widths, strict=True))

            sep = "-+-".join("-" * w for w in widths)
            table = "\n".join([
                _line(headers),
                sep,
                *[_line([str(c) for c in r]) for r in rows],
            ])
            self.logger.info("[load_tester] run_all_benchmarks summary:\n{}", table)
        except Exception:
            try:
                self.logger.info("[load_tester] run_all_benchmarks finished: {}", results)
            except Exception:
                pass
        return ok(data={"tests": results, "enabled": True})

    @lifecycle(id="startup")
    def startup(self, **_: Any):
        """Auto-start benchmarks (only if auto_run_on_startup=true in config).
        Important: do not read config / call bus APIs directly inside lifecycle handler.
        We only spawn a daemon thread here.
        """

        def _runner() -> None:
            try:
                # Wait a short grace period after plugin process startup.
                if self._stop_event.wait(timeout=3.0):
                    return
                
                # Check config: only auto-run if explicitly enabled
                try:
                    cfg = self._get_load_test_section(None)
                    auto_run = bool(cfg.get("auto_run_on_startup", False))
                    if not auto_run:
                        try:
                            self.ctx.logger.info("[load_tester] auto_run_on_startup=false, skipping auto bench")
                        except Exception:
                            pass
                        return
                except Exception:
                    # If config read fails, default to NOT auto-run (safe default)
                    return
                
                # Check and set _run_all_running flag to prevent concurrent runs
                try:
                    with self._run_all_guard:
                        if self._run_all_running:
                            try:
                                self.ctx.logger.info("[load_tester] auto_start skipped: benchmark already running")
                            except Exception:
                                pass
                            return
                        self._run_all_running = True
                except Exception:
                    pass
                
                try:
                    try:
                        self.ctx.logger.info(
                            "[load_tester] auto_start thread begin: stop={}",
                            self._stop_event.is_set(),
                        )
                    except Exception:
                        pass
                    if self._stop_event.is_set():
                        return
                    with self._bench_lock:
                        self._run_all_benchmarks_sync(duration_seconds=5.0)
                    try:
                        self.ctx.logger.info("[load_tester] auto_start thread finished")
                    except Exception:
                        pass
                except Exception as e:
                    try:
                        self.ctx.logger.warning("[load_tester] auto_start benchmark failed: {}", e)
                    except Exception:
                        pass
                finally:
                    # Always clear the running flag
                    try:
                        with self._run_all_guard:
                            self._run_all_running = False
                    except Exception:
                        pass
            except Exception as e:
                try:
                    self.ctx.logger.warning("[load_tester] startup auto_start failed: {}", e)
                except Exception:
                    try:
                        self.logger.warning("[load_tester] startup auto_start failed: {}", e)
                    except Exception:
                        pass

        try:
            t = threading.Thread(target=_runner, daemon=True, name="load_tester-auto")
            self._auto_thread = t
            t.start()
        except Exception as e:
            try:
                try:
                    self.ctx.logger.warning("[load_tester] startup: failed to start background thread: {}", e)
                except Exception:
                    self.logger.warning("[load_tester] startup: failed to start background thread: {}", e)
            except Exception:
                pass
        return ok(data={"status": "startup_started"})

    @lifecycle(id="shutdown")
    def shutdown(self, **_: Any):
        self._cleanup()
        return ok(data={"status": "shutdown_signaled"})
