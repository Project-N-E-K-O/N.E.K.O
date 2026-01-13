import time
import threading
from collections import Counter
from typing import Any, Dict, Optional, cast

from plugin.sdk.base import NekoPluginBase
from plugin.sdk.decorators import neko_plugin, plugin_entry, lifecycle
from plugin.sdk import ok
from plugin.sdk.bus.types import BusReplayContext


@neko_plugin
class LoadTestPlugin(NekoPluginBase):
    def __init__(self, ctx):
        """
        Initialize the plugin: configure file logging, record the plugin id, and prepare thread-synchronization primitives.
        
        Parameters:
            ctx: Plugin context object that provides runtime services and contains `plugin_id`.
        """
        super().__init__(ctx)
        self.file_logger = self.enable_file_logging(log_level="INFO")
        self.logger = self.file_logger
        self.plugin_id = ctx.plugin_id
        self._stop_event = threading.Event()
        self._auto_thread: Optional[threading.Thread] = None
        self._bench_lock = threading.Lock()

    def _cleanup(self) -> None:
        """
        Signal plugin shutdown and stop background work.
        
        Sets the internal stop event and, if an auto-start thread exists, attempts to join it with a 2.0 second timeout; any exceptions during signaling or joining are suppressed to ensure cleanup completes.
        """
        try:
            self._stop_event.set()
        except Exception:
            pass
        t = getattr(self, "_auto_thread", None)
        if t is not None:
            try:
                t.join(timeout=2.0)
            except Exception:
                # 避免 join 异常中断清理
                pass

    def _unwrap_ok_data(self, value: Any) -> Any:
        """
        Unwraps a value that follows the plugin 'ok' response shape by returning its inner `data` payload when present.
        
        Parameters:
            value: The value to inspect; if it's a dict containing a "data" key, that key's value will be returned.
        
        Returns:
            The inner `data` value when `value` is a dict with a "data" key, otherwise `value` unchanged.
        """
        if isinstance(value, dict) and "data" in value:
            return value.get("data")
        return value

    def _bench_loop(self, duration_seconds: float, fn, *args, **kwargs) -> Dict[str, Any]:
        """
        Run a single-threaded benchmark loop that repeatedly calls `fn` for the given duration.
        
        The loop stops when the specified duration elapses or when the plugin's stop event is set. Exceptions raised by `fn` are counted, grouped by exception type, and a representative sample `repr` for the first occurrence of each exception type is recorded.
        
        Parameters:
            duration_seconds (float): Total time to run the loop in seconds.
            fn (Callable): Function to invoke each iteration.
            *args: Positional arguments forwarded to `fn`.
            **kwargs: Keyword arguments forwarded to `fn`.
        
        Returns:
            dict: Benchmark statistics containing:
                - "iterations" (int): Number of successful `fn` invocations.
                - "errors" (int): Total number of exceptions raised.
                - "elapsed_seconds" (float): Measured wall-clock time the loop ran.
                - "qps" (float): Completed iterations per second (iterations / elapsed_seconds).
                - "error_types" (dict): Mapping of exception type name to occurrence count.
                - "error_samples" (dict): Mapping of exception type name to `repr` of the first captured exception.
        """
        start = time.perf_counter()
        end_time = start + float(duration_seconds)
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
        elapsed = time.perf_counter() - start
        qps = float(count) / elapsed if elapsed > 0 else 0.0
        return {
            "iterations": count,
            "errors": errors,
            "elapsed_seconds": elapsed,
            "qps": qps,
            "error_types": dict(err_types),
            "error_samples": err_samples,
        }

    def _bench_loop_concurrent(self, duration_seconds: float, workers: int, fn, *args, **kwargs) -> Dict[str, Any]:
        """
        Run the provided callable concurrently across multiple worker threads for a fixed duration and collect aggregated statistics.
        
        Parameters:
            duration_seconds (float): Total time in seconds to run the benchmark.
            workers (int): Number of worker threads to spawn (minimum 1).
            fn (Callable): Function to invoke repeatedly from each worker.
            *args: Positional arguments forwarded to `fn`.
            **kwargs: Keyword arguments forwarded to `fn`.
        
        Returns:
            dict: Aggregated benchmark results with the following keys:
                - `iterations` (int): Total successful invocations across all workers.
                - `errors` (int): Total number of exceptions raised during invocations.
                - `elapsed_seconds` (float): Actual elapsed wall-clock time of the run.
                - `qps` (float): Observed successful invocations per second.
                - `workers` (int): Number of worker threads actually used.
                - `error_types` (dict): Mapping of exception type name to occurrence count.
                - `error_samples` (dict): One representative stringified sample for each exception type.
        """
        start = time.perf_counter()
        end_time = start + float(duration_seconds)
        count = 0
        errors = 0
        lock = threading.Lock()
        err_types: Counter[str] = Counter()
        err_samples: Dict[str, str] = {}

        def _worker() -> None:
            """
            Worker loop that repeatedly calls the benchmark operation until the stop event is signaled or the end time is reached.
            
            Each successful invocation increments the shared iteration counter; exceptions increment the shared error counter, record the exception type count, capture a sample representation for the first occurrence of each error type, and attempt to log a warning. This function does not return a value.
            """
            nonlocal count, errors
            while True:
                if self._stop_event.is_set():
                    break
                now = time.perf_counter()
                if now >= end_time:
                    break
                try:
                    fn(*args, **kwargs)
                    with lock:
                        count += 1
                except Exception as e:  # pragma: no cover - defensive
                    with lock:
                        errors += 1
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

        threads = []
        worker_count = max(1, int(workers))
        for _ in range(worker_count):
            t = threading.Thread(target=_worker, daemon=True)
            threads.append(t)
            t.start()
        for t in threads:
            try:
                t.join()
            except Exception:
                # 避免 join 异常中断整个压测
                pass

        elapsed = time.perf_counter() - start
        qps = float(count) / elapsed if elapsed > 0 else 0.0
        return {
            "iterations": count,
            "errors": errors,
            "elapsed_seconds": elapsed,
            "qps": qps,
            "workers": worker_count,
            "error_types": dict(err_types),
            "error_samples": err_samples,
        }

    def _sample_latency_ms(self, fn, *, samples: int = 100) -> Dict[str, Any]:
        """
        Measure per-call latency of a zero-argument callable and return aggregated statistics in milliseconds.
        
        This will call `fn()` up to `samples` times (at least once) and record each call's duration in milliseconds. The loop may stop early if the plugin stop event is set. Exceptions raised by `fn` are counted but do not raise.
        
        Parameters:
            fn (Callable[[], Any]): A callable that takes no arguments; its execution time will be measured.
            samples (int): Number of times to invoke `fn`. Values less than 1 are treated as 1.
        
        Returns:
            dict: Aggregated latency statistics containing:
                - `latency_samples` (int): Number of recorded samples.
                - `latency_errors` (int): Count of calls that raised exceptions.
                - `latency_min_ms` (float): Minimum observed latency in milliseconds.
                - `latency_max_ms` (float): Maximum observed latency in milliseconds.
                - `latency_avg_ms` (float): Average latency in milliseconds.
                - `latency_p50_ms` (float): 50th percentile latency in milliseconds.
                - `latency_p95_ms` (float): 95th percentile latency in milliseconds.
                - `latency_p99_ms` (float): 99th percentile latency in milliseconds.
        
        If no samples were recorded, the returned dict contains only `latency_samples` and `latency_errors`.
        """
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
            """
            Select the value at percentile `p` from the enclosing `durs` sample list using nearest-rank indexing.
            
            Parameters:
                p (float): Percentile to select (interpreted as 0–100).
            
            Returns:
                float: The value from `durs` corresponding to percentile `p`; `0.0` if `durs` is empty. If `durs` contains one element that element is returned. Out-of-range percentile positions are clamped to the first or last element.
            """
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
        """
        Retrieve the plugin's load_test configuration subsection.
        
        Parameters:
            section (str | None): Optional subsection name under "load_test" (e.g., "push_messages"); when None, the top-level "load_test" section is returned.
        
        Returns:
            Dict[str, Any]: The requested configuration mapping, or an empty dict if the section is missing or malformed.
        """
        path = "load_test" if not section else f"load_test.{section}"
        try:
            return self.config.get_section(path)
        except Exception:
            # 配置缺失或格式不对时, 按空配置处理, 避免影响插件可用性
            return {}

    def _get_global_bench_config(self, root_cfg: Optional[Dict[str, Any]]) -> tuple[int, bool]:
        """
        Read global benchmark options from the `load_test` configuration.
        
        Parameters:
            root_cfg (Optional[Dict[str, Any]]): Root configuration mapping (the `[load_test]` section or its parent). May be None.
        
        Returns:
            tuple[int, bool]: A pair `(workers, log_summary)` where `workers` is the parsed `worker_threads` coerced to an integer and constrained to at least 1 (invalid or missing values default to 1), and `log_summary` is the boolean value of `log_summary` (defaults to True).
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
    ) -> tuple[int, bool]:
        """
        Resolve worker count and whether to log a summary for a specific benchmark.
        
        The worker count defaults to the global `[load_test].worker_threads` value and may be
        overridden by `worker_threads` in the section-specific config. `root_cfg` and
        `sec_cfg` are optional configuration mappings for the global and section-level
        `load_test` settings respectively.
        
        Parameters:
            root_cfg (Optional[Dict[str, Any]]): Global load_test configuration (may be None).
            sec_cfg (Optional[Dict[str, Any]]): Section-specific load_test configuration (may be None).
        
        Returns:
            tuple[int, bool]: `(workers, log_summary)` where `workers` is an integer >= 1
            representing the number of worker threads, and `log_summary` indicates whether
            a summary line should be logged.
        """
        workers, log_summary = self._get_global_bench_config(root_cfg)
        try:
            if sec_cfg:
                workers_raw = sec_cfg.get("worker_threads")
                if workers_raw is not None:
                    workers = max(1, int(workers_raw))
        except Exception:
            pass
        return workers, log_summary

    def _get_incremental_diagnostics(self, expr) -> Dict[str, Any]:
        """
        Extract incremental-reload diagnostic values from a BusList expression.
        
        Parameters:
            expr: BusList-like object to inspect for incremental reload state.
        
        Returns:
            dict: Mapping with keys:
                - `latest_rev` (int or None): Latest known bus revision if available.
                - `last_seen_rev` (int or None): Revision last observed by `expr` if present.
                - `fast_hits` (int or None): Count of incremental fast-path hits if present.
            Returns an empty dict if diagnostics cannot be retrieved.
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
        """
        Run a benchmark using shared configuration, duration resolution, execution, optional latency sampling, and optional summary logging.
        
        Parameters:
            test_name (str): Identifier used in the returned results under the "test" key.
            root_cfg (Optional[Dict[str, Any]]): Root configuration (e.g., global load_test section) used to resolve defaults.
            sec_cfg (Optional[Dict[str, Any]]): Section-specific configuration that can override root settings (e.g., duration_seconds, worker_threads, log_summary).
            default_duration (float): Fallback duration in seconds if no duration is specified in configs.
            op_fn (callable): Operation to execute for each benchmark iteration; called with no arguments by the runner.
            log_template (Optional[str]): Optional logging format string used when summary logging is enabled.
            build_log_args (Optional[callable]): Optional callable(duration, stats, workers) -> tuple used to produce positional args for the log_template.
            extra_data_builder (Optional[callable]): Optional callable(stats, duration, workers) -> dict whose keys will be merged into the final stats.
        
        Returns:
            dict: A result dictionary containing the "test" name and collected benchmark statistics (iterations, errors, elapsed_seconds, qps, error_types, error_samples, latency summary, and any extra data added by extra_data_builder).
        """

        with self._bench_lock:
            workers, log_summary = self._get_bench_config(root_cfg, sec_cfg)

            dur_cfg = sec_cfg.get("duration_seconds") if sec_cfg else None
            if dur_cfg is None and root_cfg:
                dur_cfg = root_cfg.get("duration_seconds")
            try:
                duration = float(dur_cfg) if dur_cfg is not None else default_duration
            except Exception:
                duration = default_duration

            if workers > 1:
                stats = self._bench_loop_concurrent(duration, workers, op_fn)
            else:
                stats = self._bench_loop(duration, op_fn)

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
        """
        Fetches up to `max_count` messages from the bus and returns only the result count.
        
        Parameters:
            max_count (int): Maximum number of messages to request.
            plugin_id (str): Plugin id filter; empty string or "*" is treated as no filter (all plugins).
            timeout (float): Request timeout in seconds.
        
        Returns:
            dict: `{"count": N}` where `N` is the number of messages retrieved.
        """
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
        """
        Reloads a BusList expression built from current messages and returns the resulting message count.
        
        This fetches up to `max_count` messages from the bus (with the given `timeout`), seeds messages if none are present, builds an expression ( (left + right) - left ) using a filter by `source`, and calls reload on that expression.
        
        Parameters:
            max_count (int): Maximum number of messages to fetch for building the expression.
            timeout (float): Timeout in seconds for the initial message fetch.
            source (str): Source filter applied to the BusList; if empty, `"load_tester"` is used.
            inplace (bool): If True, perform an in-place reload on the expression.
        
        Returns:
            dict: `{"count": n}` where `n` is the number of messages produced by the reload.
        """
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
        """
        Measure ctx.push_message throughput by running repeated push operations for the given duration.
        
        Parameters:
            duration_seconds (float): Total time to run the benchmark in seconds (default 5.0). Additional keyword arguments are accepted and ignored.
        
        Returns:
            dict: A result object containing benchmark statistics (under the `data` key) such as `test`, `iterations`, `errors`, `qps`, `elapsed_seconds`, and optionally latency samples and other extra data.
        """
        root_cfg = self._get_load_test_section(None)
        sec_cfg = self._get_load_test_section("push_messages")

        def _op() -> None:
            """
            Pushes a single test message to the bus with predefined fields.
            
            The message is sent with source "load_tester.push_messages", type "text", description "load test message",
            priority 1, content "load_test", and fast_mode disabled.
            """
            self.ctx.push_message(
                source="load_tester.push_messages",
                message_type="text",
                description="load test message",
                priority=1,
                content="load_test",
                fast_mode=False,
            )

        def _build_log_args(duration: float, stats: Dict[str, Any], workers: int):
            """
            Builds a standardized tuple of benchmark values used for logging.
            
            Parameters:
                duration (float): Benchmark duration in seconds.
                stats (Dict[str, Any]): Benchmark results containing at least `iterations`, `qps`, and `errors`.
                workers (int): Fallback worker count to use if `stats` does not include a `workers` entry.
            
            Returns:
                tuple: (duration, iterations, qps, errors, workers) where `workers` is taken from `stats["workers"]` if present, otherwise the provided `workers` argument.
            """
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
        """
        Benchmark the throughput of ctx.push_message using fast_mode.
        
        Parameters:
            duration_seconds (float): Number of seconds to run the benchmark.
        
        Returns:
            dict: Result wrapper produced by `ok` containing benchmark statistics (e.g., `iterations`, `errors`, `qps`, `elapsed_seconds`, and `workers`).
        """
        root_cfg = self._get_load_test_section(None)
        sec_cfg = self._get_load_test_section("push_messages_fast")

        def _op() -> None:
            """
            Pushes a single short load-test message to the bus using fast_mode.
            
            This operation enqueues a message with source "load_tester.push_messages_fast", type "text",
            description "load test message (fast)", priority 1, and content "load_test", and sets `fast_mode=True`.
            """
            self.ctx.push_message(
                source="load_tester.push_messages_fast",
                message_type="text",
                description="load test message (fast)",
                priority=1,
                content="load_test",
                fast_mode=True,
            )

        def _build_log_args(duration: float, stats: Dict[str, Any], workers: int):
            """
            Builds a standardized tuple of benchmark values used for logging.
            
            Parameters:
                duration (float): Benchmark duration in seconds.
                stats (Dict[str, Any]): Benchmark results containing at least `iterations`, `qps`, and `errors`.
                workers (int): Fallback worker count to use if `stats` does not include a `workers` entry.
            
            Returns:
                tuple: (duration, iterations, qps, errors, workers) where `workers` is taken from `stats["workers"]` if present, otherwise the provided `workers` argument.
            """
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
        try:
            self.ctx.close()
        except Exception:
            pass
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
        """
        Benchmark bus.messages.get throughput using configured load_test settings.
        
        This runs repeated calls to ctx.bus.messages.get for the specified duration and collects throughput and error statistics. The function respects timeout values defined in the `load_test` root section or the `load_test.bus_messages_get` subsection, and normalizes `plugin_id` by treating "*" or empty strings as no plugin filter. The plugin context is closed once before the benchmark to reduce interference.
        
        Parameters:
            duration_seconds (float): Target duration of the benchmark in seconds.
            max_count (int): `max_count` passed to `bus.messages.get`.
            plugin_id (str): Plugin ID filter; "*" or empty is treated as no filter.
            timeout (float): Default timeout passed to `bus.messages.get`, overridden by config when provided.
            **_ (Any): Ignored extra keyword arguments (used for uniform plugin op signatures).
        
        Returns:
            dict: Benchmark statistics merged into the plugin `ok` response data. The stats include keys such as `iterations`, `errors`, `elapsed_seconds`, `qps`, and may include `workers`, `error_types`, `error_samples`, and latency sampling fields when available.
        """
        root_cfg = self._get_load_test_section(None)
        sec_cfg = self._get_load_test_section("bus_messages_get")

        try:
            self.ctx.close()
        except Exception:
            pass

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
            """
            Invoke a single bus.messages.get call using the resolved plugin id and configured max_count/timeout.
            
            This performs a raw get request against the bus and discards the result; used as the operation to benchmark.
            """
            _ = self.ctx.bus.messages.get(
                plugin_id=pid_norm,
                max_count=int(max_count),
                timeout=float(timeout),
                raw=True,
            )

        def _build_log_args(duration: float, stats: Dict[str, Any], workers: int):
            """
            Builds the argument tuple used to format and log a benchmark summary.
            
            Parameters:
                duration (float): Requested benchmark duration in seconds.
                stats (dict): Benchmark results dictionary; must contain "iterations", "qps", and "errors". May include "workers".
                workers (int): Default worker count to use if `stats` does not include a "workers" entry.
            
            Returns:
                tuple: (duration, iterations, qps, errors, max_count, plugin_id, timeout, workers_used)
                    - duration (float): Requested run duration in seconds.
                    - iterations (int): Total iterations performed.
                    - qps (float): Measured queries per second.
                    - errors (int): Total error count.
                    - max_count: The `max_count` value from outer scope used for the operation.
                    - plugin_id: The normalized plugin identifier from outer scope.
                    - timeout: Operation timeout in seconds from outer scope.
                    - workers_used (int): Worker count reported in `stats` or the provided `workers` fallback.
            """
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
        """
        Run a throughput benchmark that repeatedly calls bus.events.get for the specified duration.
        
        Parameters:
            duration_seconds (float): Benchmark run duration in seconds.
            max_count (int): max_count passed to bus.events.get on each call.
            plugin_id (str): Target plugin id; "*" or empty string is treated as no plugin filter.
            timeout (float): Default timeout (seconds) passed to bus.events.get; can be overridden by `load_test` or `load_test.bus_events_get` config.
        
        Returns:
            dict: Benchmark results (merged into the plugin `ok` response) containing keys such as `test`, `iterations`, `errors`, `elapsed_seconds`, `qps`, `workers`, and any sampled latency or extra data.
        """
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
            """
            Perform a single bus.events.get call using the resolved plugin_id, max_count, and timeout, discarding the returned events.
            """
            _ = self.ctx.bus.events.get(
                plugin_id=pid_norm,
                max_count=int(max_count),
                timeout=float(timeout),
            )

        def _build_log_args(duration: float, stats: Dict[str, Any], workers: int):
            """
            Builds the argument tuple used to format and log a benchmark summary.
            
            Parameters:
                duration (float): Requested benchmark duration in seconds.
                stats (dict): Benchmark results dictionary; must contain "iterations", "qps", and "errors". May include "workers".
                workers (int): Default worker count to use if `stats` does not include a "workers" entry.
            
            Returns:
                tuple: (duration, iterations, qps, errors, max_count, plugin_id, timeout, workers_used)
                    - duration (float): Requested run duration in seconds.
                    - iterations (int): Total iterations performed.
                    - qps (float): Measured queries per second.
                    - errors (int): Total error count.
                    - max_count: The `max_count` value from outer scope used for the operation.
                    - plugin_id: The normalized plugin identifier from outer scope.
                    - timeout: Operation timeout in seconds from outer scope.
                    - workers_used (int): Worker count reported in `stats` or the provided `workers` fallback.
            """
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
        """
        Benchmark the throughput and error rate of bus.lifecycle.get over a configurable duration.
        
        Reads load_test and load_test.bus_lifecycle_get configuration sections to allow overriding the per-call `timeout`. The `plugin_id` value of `"*"` or an empty string is normalized to `None` to request lifecycle entries for all plugins. `max_count` limits the number of items requested per call.
        
        Parameters:
            duration_seconds (float): Total benchmark run time in seconds (default 5.0).
            max_count (int): Maximum number of lifecycle entries to request per call.
            plugin_id (str): Target plugin id to filter lifecycle entries; `"*"` or empty means all plugins.
            timeout (float): Per-call timeout in seconds; may be overridden by configuration.
        
        Returns:
            dict: Benchmark statistics including `iterations`, `errors`, `elapsed_seconds`, `qps`, `workers` (when applicable), `error_types`, `error_samples`, and any collected latency or extra data.
        """
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
            """
            Perform a single bus.lifecycle.get call using the preconfigured plugin_id, max_count, and timeout.
            
            This operation executes the lifecycle.get request and intentionally ignores the returned value; it is used as the unit operation for benchmarking.
            """
            _ = self.ctx.bus.lifecycle.get(
                plugin_id=pid_norm,
                max_count=int(max_count),
                timeout=float(timeout),
            )

        def _build_log_args(duration: float, stats: Dict[str, Any], workers: int):
            """
            Builds the argument tuple used to format and log a benchmark summary.
            
            Parameters:
                duration (float): Requested benchmark duration in seconds.
                stats (dict): Benchmark results dictionary; must contain "iterations", "qps", and "errors". May include "workers".
                workers (int): Default worker count to use if `stats` does not include a "workers" entry.
            
            Returns:
                tuple: (duration, iterations, qps, errors, max_count, plugin_id, timeout, workers_used)
                    - duration (float): Requested run duration in seconds.
                    - iterations (int): Total iterations performed.
                    - qps (float): Measured queries per second.
                    - errors (int): Total error count.
                    - max_count: The `max_count` value from outer scope used for the operation.
                    - plugin_id: The normalized plugin identifier from outer scope.
                    - timeout: Operation timeout in seconds from outer scope.
                    - workers_used (int): Worker count reported in `stats` or the provided `workers` fallback.
            """
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
        """
        Benchmarks BusList.filter() against a preloaded message list using the configured duration and worker settings.
        
        Parameters:
            duration_seconds (float): Duration to run the benchmark in seconds.
            max_count (int): Number of messages to load from the bus to build the base list used for filtering.
            timeout (float): Timeout in seconds for initial bus message retrieval.
            source (str): Filter `source` value to pass to BusList.filter(); when empty, uses "load_tester".
        
        Returns:
            dict: Benchmark statistics including keys such as `test`, `iterations`, `errors`, `elapsed_seconds`, `qps`,
            `workers`, and any `extra` fields (for example `base_size`) merged by the benchmark runner, wrapped as the
            `data` value in the plugin's `ok` response.
        """
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
            """
            Execute a single BusList.filter operation on the preloaded `base_list` using the configured filter kwargs.
            
            The filter result is discarded; this function exists solely as the operation invoked by the benchmark loop.
            """
            _ = base_list.filter(strict=False, **flt_kwargs)

        def _extra_data_builder(_stats: Dict[str, Any], _duration: float, _workers: int) -> Dict[str, Any]:
            """
            Build an extra-data mapping that reports the size of the preloaded base_list.
            
            Parameters:
                _stats (Dict[str, Any]): Unused benchmark statistics passed by the runner.
                _duration (float): Unused duration value passed by the runner.
                _workers (int): Unused worker count passed by the runner.
            
            Returns:
                Dict[str, Any]: A dict containing `base_size` (int) equal to len(base_list).
            """
            return {"base_size": len(base_list)}

        def _build_log_args(duration: float, stats: Dict[str, Any], workers: int):
            """
            Builds the tuple of values used to format a benchmark summary log line.
            
            Parameters:
                duration (float): Benchmark duration in seconds.
                stats (Dict[str, Any]): Collected benchmark statistics; must contain "iterations", "qps", and "errors".
                workers (int): Default worker count to use if `stats` does not include a "workers" entry.
            
            Returns:
                tuple: (duration, iterations, qps, errors, base_size, filter_kwargs, workers_used)
                - duration: same as the `duration` parameter.
                - iterations: total iterations from `stats["iterations"]`.
                - qps: queries per second from `stats["qps"]`.
                - errors: total error count from `stats["errors"]`.
                - base_size: number of items in the surrounding `base_list`.
                - filter_kwargs: surrounding `flt_kwargs` used for the benchmark filter.
                - workers_used: `stats["workers"]` if present, otherwise the `workers` parameter.
            """
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
        """
        Run a QPS sweep by repeatedly triggering an event on a target plugin and collect per-target and overall latency and error statistics.
        
        Parameters:
            target_plugin_id (str): Plugin ID that will receive the triggered event.
            event_type (str): Event type name passed to the target plugin.
            event_id (str): Event identifier passed to the target plugin.
            args (Optional[Dict[str, Any]]): Arguments passed to each triggered event (copied before use).
            duration_seconds (float): Duration, in seconds, to run each target QPS trial.
            qps_targets (Optional[list[float]]): Sequence of target QPS values to test; invalid or non-positive entries are ignored. If empty or None, defaults to [10.0, 50.0, 100.0].
            timeout (float): Per-call timeout (seconds) forwarded to ctx.trigger_plugin_event.
            **_ (Any): Ignored extra keyword arguments.
        
        Returns:
            dict: A result object with the following keys:
              - test: "bench_plugin_event_qps"
              - target_plugin_id, event_type, event_id, timeout, duration_seconds_per_target
              - qps_table: list of per-target rows with keys:
                  - target_qps, achieved_qps, calls, errors, error_rate,
                    and optional latency_min_ms/latency_max_ms/latency_avg_ms/latency_p50_ms/latency_p95_ms/latency_p99_ms
              - peak_qps: peak achieved QPS observed across targets
              - total_calls, total_errors
              - latency_samples: total latency sample count (0 if none)
              - overall latency stats when samples exist: latency_min_ms, latency_max_ms, latency_avg_ms, latency_p50_ms, latency_p95_ms, latency_p99_ms
        
        The function logs a human-readable per-target table as an informational message but does not include that table in the returned structure.
        """
        if args is None:
            args = {}

        # Run the actual pressure test in a background worker thread so that
        # sync plugin-to-plugin calls do not execute directly inside the
        # command-loop handler context (avoids sync_call_in_handler warnings).
        result_box: list[Dict[str, Any]] = []

        def _run() -> None:
            """
            Execute event triggers at multiple target QPS levels, measure per-call latency and error rates, and produce a summary result.
            
            Runs the target plugin event at a sequence of target QPS values, collects per-target statistics (achieved QPS, calls, errors, error_rate and latency percentiles when available) and aggregates overall latency metrics across all targets. Appends a result dictionary to the outer-scope `result_box` and emits a standalone textual summary to the plugin logger. The result dictionary includes keys such as `test`, `target_plugin_id`, `event_type`, `event_id`, `timeout`, `duration_seconds_per_target`, `qps_table`, `peak_qps`, `total_calls`, `total_errors`, and overall latency fields like `latency_samples`, `latency_min_ms`, `latency_max_ms`, `latency_avg_ms`, `latency_p50_ms`, `latency_p95_ms`, and `latency_p99_ms`.
            """
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
                        """
                        Return the value at the given percentile p (0–100) from a list of numeric durations.
                        
                        Parameters:
                            p (float): Percentile to compute, expressed as a percentage (e.g., 50.0 for median).
                            data (list[float]): Sequence of numeric values to sample. If empty, the function returns 0.0.
                        
                        Returns:
                            float: The value from `data` corresponding to percentile `p`; returns 0.0 when `data` is empty.
                        """
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
                    """
                    Map a percentile p (0–100) to a value from the enclosing sequence `durs_all` by nearest-index selection.
                    
                    Parameters:
                        p (float): Percentile to sample, where 0 represents the minimum and 100 the maximum.
                    
                    Returns:
                        float: If `durs_all` is empty returns 0.0. If `durs_all` contains one item returns that item. Otherwise returns the element at the index nearest to the fractional position (p/100) across `durs_all` (index rounded and clamped to valid range).
                    """
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
                    """
                    Format a list of column values into a single aligned table row.
                    
                    Parameters:
                        parts (list[str]): Column text values; each element is padded to its corresponding column width and then joined.
                    
                    Returns:
                        str: A single string containing the columns left-padded to their widths and separated by " | ".
                    """
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

        worker = threading.Thread(target=_run, daemon=True)
        worker.start()
        worker.join()

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
        """
        Benchmarks BusList.reload() by constructing an expression ((left + right) - left) from a filtered message list and repeatedly invoking reload.
        
        Constructs a base message list (seeding messages if empty), builds left and right filtered sublists using the provided source filter, forms expr = (left + right) - left, and measures reload throughput and errors for the specified duration and worker configuration. When incremental is enabled, incremental diagnostics are included in the returned extra data.
        
        Parameters:
            duration_seconds (float): Default benchmark duration in seconds if not overridden by config.
            max_count (int): Maximum number of messages to load into the base list.
            timeout (float): I/O timeout in seconds used when loading the base list (can be overridden by config).
            source (str): Filter source value to apply when building left/right sublists; defaults to "load_tester" when empty.
            inplace (bool): Whether to call reload_with with inplace=True.
            incremental (bool): Whether to call reload_with with incremental=True and include incremental diagnostics.
        
        Returns:
            dict: Benchmark result wrapped as plugin ok data containing the test name and statistics (iterations, errors, elapsed_seconds, qps, workers, latency samples when available) plus extra data including `base_size`, `inplace`, `incremental`, and diagnostic fields when incremental is true.
        """
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
            """
            Invoke reload on the prepared BusList expression using the plugin context and the configured `inplace` and `incremental` flags.
            
            This performs the reload operation and ignores its return value.
            """
            ctx = cast(BusReplayContext, self.ctx)
            _ = expr.reload_with(ctx, inplace=bool(inplace), incremental=bool(incremental))

        def _extra_data_builder(_stats: Dict[str, Any], _duration: float, _workers: int) -> Dict[str, Any]:
            """
            Builds extra metadata for a BusList reload benchmark containing the base list size and reload options.
            
            Returns:
                A dict with:
                  - `base_size` (int): number of messages in the preloaded base list.
                  - `inplace` (bool): whether reloads were performed inplace.
                  - `incremental` (bool): whether incremental reloads were requested.
                  - additional keys from incremental diagnostics when `incremental` is true (e.g., `latest_rev`, `last_seen_rev`, `fast_hits`).
            """
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
            """
            Builds the positional arguments used to format a benchmark summary log line.
            
            Parameters:
            	duration (float): Intended benchmark duration in seconds.
            	stats (Dict[str, Any]): Collected benchmark statistics; must contain `iterations`, `qps`, and `errors`. May include `workers` to override the provided workers.
            	workers (int): Fallback worker count to use if `stats` does not specify `workers`.
            
            Returns:
            	tuple: (
            		duration,
            		iterations,
            		qps,
            		errors,
            		base_size,        # number of items in the preloaded base list
            		filter_kwargs,    # filter keyword arguments used for the benchmark
            		inplace,          # boolean flag indicating inplace behavior
            		incremental,      # boolean flag indicating incremental behavior
            		workers_used      # effective worker count (from stats or fallback)
            	)
            """
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
        """
        Benchmark BusList.reload with incremental=True on a stable dataset to measure the fast-path (no-change) performance.
        
        Parameters:
            duration_seconds (float): Default duration in seconds to run the benchmark if not overridden by config.
            max_count (int): Maximum number of messages to load into the base BusList.
            timeout (float): Timeout (seconds) used when loading the base message list.
            source (str): Optional source filter applied to the BusList; when empty, "load_tester" is used.
            inplace (bool): Whether to perform reloads inplace on the BusList expression.
        
        Returns:
            dict: Plugin response (wrapped by `ok`) whose `data` field contains the benchmark statistics and any extra diagnostics (e.g., iterations, errors, elapsed_seconds, qps, latency samples, base_size, inplace, incremental diagnostics).
        """
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
            """
            Reload the prepared BusList expression using incremental reload semantics and the captured `inplace` setting.
            
            This helper performs the reload with the plugin's BusReplayContext and does not return a value.
            """
            ctx = cast(BusReplayContext, self.ctx)
            _ = expr.reload_with(ctx, inplace=bool(inplace), incremental=True)

        def _extra_data_builder(stats: Dict[str, Any], _duration: float, _workers: int) -> Dict[str, Any]:
            """
            Builds extra metadata for the benchmark containing the base list size, inplace flag, and any available incremental diagnostics.
            
            Parameters:
                stats (Dict[str, Any]): Benchmark statistics collected so far (unused by this builder but provided for extensibility).
                _duration (float): Benchmark duration in seconds (unused).
                _workers (int): Number of worker threads used (unused).
            
            Returns:
                Dict[str, Any]: A dictionary with keys:
                    - `base_size` (int): Number of items in the base list.
                    - `inplace` (bool): Whether the reload was performed inplace.
                    - additional diagnostic keys returned by incremental diagnostics when available.
            """
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
            """
            Builds the positional arguments tuple used to format a benchmark summary log line.
            
            Parameters:
            	duration (float): Total benchmark duration in seconds.
            	stats (Dict[str, Any]): Collected benchmark statistics; expected keys include `"iterations"`, `"qps"`, and `"errors"`.
            	workers (int): Number of worker threads for the benchmark (accepted for signature compatibility).
            
            Returns:
            	tuple: (duration_seconds, iterations, qps, errors, base_list_size, filter_kwargs, inplace_flag, incremental_diagnostics)
            	- duration_seconds (float): Same as `duration`.
            	- iterations (int): Total iterations from `stats["iterations"]`.
            	- qps (float): Queries-per-second value from `stats["qps"]`.
            	- errors (int): Total error count from `stats["errors"]`.
            	- base_list_size (int): Length of the preloaded base message list.
            	- filter_kwargs (dict): Filter keyword arguments used for the benchmark.
            	- inplace_flag (bool): Whether the operation was performed inplace.
            	- incremental_diagnostics (dict): Diagnostic info for incremental reloads when available.
            """
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
        """
        Run all built-in load tests sequentially and collect their results.
        
        Parameters:
            duration_seconds (float): Target duration, in seconds, to run each individual benchmark.
        
        Returns:
            result (dict): A mapping with keys:
                - "tests": dict mapping benchmark names to their result dicts (containing metrics such as
                  "iterations", "errors", "elapsed_seconds", "qps", latency stats, extra fields, or an
                  {"error": "<message>"} entry if that benchmark raised).
                - "enabled": bool set to True.
        """
        results: Dict[str, Any] = {}
        try:
            results["bench_push_messages"] = self._unwrap_ok_data(
                self.bench_push_messages(duration_seconds=duration_seconds)
            )
        except Exception as e:
            results["bench_push_messages"] = {"error": str(e)}
        try:
            results["bench_bus_messages_get"] = self._unwrap_ok_data(
                self.bench_bus_messages_get(duration_seconds=duration_seconds)
            )
        except Exception as e:
            results["bench_bus_messages_get"] = {"error": str(e)}
        try:
            results["bench_push_messages_fast"] = self._unwrap_ok_data(
                self.bench_push_messages_fast(duration_seconds=duration_seconds)
            )
        except Exception as e:
            results["bench_push_messages_fast"] = {"error": str(e)}
        try:
            results["bench_bus_events_get"] = self._unwrap_ok_data(
                self.bench_bus_events_get(duration_seconds=duration_seconds)
            )
        except Exception as e:
            results["bench_bus_events_get"] = {"error": str(e)}
        try:
            results["bench_bus_lifecycle_get"] = self._unwrap_ok_data(
                self.bench_bus_lifecycle_get(duration_seconds=duration_seconds)
            )
        except Exception as e:
            results["bench_bus_lifecycle_get"] = {"error": str(e)}
        try:
            results["bench_buslist_filter"] = self._unwrap_ok_data(
                self.bench_buslist_filter(duration_seconds=duration_seconds)
            )
        except Exception as e:
            results["bench_buslist_filter"] = {"error": str(e)}
        try:
            results["bench_buslist_reload_full"] = self._unwrap_ok_data(
                self.bench_buslist_reload(duration_seconds=duration_seconds, incremental=False)
            )
        except Exception as e:
            results["bench_buslist_reload_full"] = {"error": str(e)}
        try:
            results["bench_buslist_reload_incr"] = self._unwrap_ok_data(
                self.bench_buslist_reload(duration_seconds=duration_seconds, incremental=True)
            )
        except Exception as e:
            results["bench_buslist_reload_incr"] = {"error": str(e)}
        try:
            results["bench_buslist_reload_nochange"] = self._unwrap_ok_data(
                self.bench_buslist_reload_nochange(duration_seconds=duration_seconds)
            )
        except Exception as e:
            results["bench_buslist_reload_nochange"] = {"error": str(e)}

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
                    """
                    Format a numeric value according to a simple kind specifier.
                    
                    Parameters:
                        x (Any): The value to format; if None or conversion fails, a dash ("-") is returned.
                        kind (str): Format specifier: "int" to format as integer, "float1" for one decimal place,
                            "float3" for three decimal places; any other value uses str(x).
                    
                    Returns:
                        str: The formatted string or "-" when input is None or conversion fails.
                    """
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

            cols = list(zip(*[headers, *rows], strict=True))
            widths = [max(len(str(x)) for x in col) for col in cols]

            def _line(parts: list[str]) -> str:
                """
                Format and join parts into a column-aligned line.
                
                Parameters:
                    parts (list[str]): Sequence of text segments to render as columns. Each segment is left-justified to its corresponding column width.
                
                Returns:
                    str: A single string with each part left-justified to its column width and separated by " | ".
                
                Raises:
                    ValueError: If the number of parts does not match the number of column widths.
                """
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
        """
        Start a background daemon thread that runs all benchmarks after a short startup grace period.
        
        The lifecycle handler does not perform benchmarking work itself; it only spawns a named daemon thread ("load_tester-auto")
        that waits briefly and then invokes run_all_benchmarks() unless the plugin stop event is set.
        
        Returns:
            dict: {"status": "startup_started"} indicating the auto-start thread was requested.
        """

        def _runner() -> None:
            """
            Background runner that waits briefly for startup stabilization and then runs all benchmarks unless stopped.
            
            Waits up to 3 seconds for the plugin to stabilize; if the plugin has been signaled to stop, the runner exits early. Otherwise it invokes run_all_benchmarks() and attempts to log start/finish. Any exceptions raised while running are suppressed; a warning is logged when available.
            """
            try:
                # Wait a short grace period after plugin process startup.
                if self._stop_event.wait(timeout=3.0):
                    return
                try:
                    self.ctx.logger.info(
                        "[load_tester] auto_start thread begin: stop={}",
                        self._stop_event.is_set(),
                    )
                except Exception:
                    pass
                if self._stop_event.is_set():
                    return
                self.run_all_benchmarks()
                try:
                    self.ctx.logger.info("[load_tester] auto_start thread finished")
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
        """
        Signal the plugin to stop and perform cleanup.
        
        Returns:
            response (dict): Response with data {"status": "shutdown_signaled"}.
        """
        self._cleanup()
        return ok(data={"status": "shutdown_signaled"})