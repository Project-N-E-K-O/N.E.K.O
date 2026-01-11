from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import threading
import time
from typing import Any, Dict

from plugin.sdk.base import NekoPluginBase
from plugin.sdk.decorators import lifecycle, neko_plugin, plugin_entry, custom_event
from plugin.sdk import ok, SystemInfo


@neko_plugin
class HelloPlugin(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)  # 传递 ctx 给基类
        # 启用文件日志(同时输出到文件和控制台)
        self.file_logger = self.enable_file_logging(log_level="INFO")
        self.logger = self.file_logger  # 使用file_logger作为主要logger
        self.plugin_id = ctx.plugin_id  # 使用 plugin_id
        self._debug_executor = ThreadPoolExecutor(max_workers=8)
        self._active_watchers = []
        self._stop_events = []
        self.file_logger.info("HelloPlugin initialized with file logging enabled")

    def _read_local_toml(self) -> dict:
        try:
            try:
                import tomllib
            except ImportError:
                import tomli as tomllib  # type: ignore

            config_path = getattr(self.ctx, "config_path", None)
            if config_path is None:
                return {}
            with config_path.open("rb") as f:
                data = tomllib.load(f)
            return data if isinstance(data, dict) else {}
        except Exception as e:
            self.file_logger.warning("Failed to read local TOML config: {}", e)
            return {}

    def _start_debug_timer(self) -> None:
        # Delay to ensure the plugin command loop is running before we do sync IPC calls.
        time.sleep(0.8)
        try:
            cfg = self._read_local_toml()
            debug_cfg = cfg.get("debug") if isinstance(cfg.get("debug"), dict) else {}
            timer_cfg = debug_cfg.get("timer") if isinstance(debug_cfg.get("timer"), dict) else {}

            enabled = bool(
                timer_cfg.get("enable")
                if "enable" in timer_cfg
                else bool(debug_cfg.get("enable", False))
            )
            if not enabled:
                return

            interval_seconds = float(timer_cfg.get("interval_seconds", debug_cfg.get("interval_seconds", 3.0)))
            burst_size = int(timer_cfg.get("burst_size", debug_cfg.get("burst_size", 5)))
            max_count = int(timer_cfg.get("max_count", debug_cfg.get("max_count", 0)))
            timer_id = str(timer_cfg.get("timer_id", debug_cfg.get("timer_id", ""))).strip()
            if not timer_id:
                timer_id = f"testPlugin_debug_{int(time.time())}"
                self.config.set("debug.timer.timer_id", timer_id)

            # Keep startup non-blocking: do the update in background as well.
            loaded_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            self.config.set("debug.timer.loaded_at", loaded_at)

            # IMPORTANT: avoid immediate callback before command loop is ready.
            # We already delayed; immediate=True is now safe and gives fast feedback.
            self.plugins.call_entry(
                "timer_service:start_timer",
                {
                    "timer_id": timer_id,
                    "interval": interval_seconds,
                    "immediate": True,
                    "callback_plugin_id": self.ctx.plugin_id,
                    "callback_entry_id": "on_debug_tick",
                    "callback_args": {
                        "timer_id": timer_id,
                        "burst_size": burst_size,
                        "max_count": max_count,
                    },
                },
                timeout=10.0,
            )
            self.file_logger.info(
                "Debug timer started: timer_id={} interval={} burst_size={} max_count={} ",
                timer_id,
                interval_seconds,
                burst_size,
                max_count,
            )
        except Exception:
            self.file_logger.exception("Failed to start debug timer")

    def _startup_config_debug(self) -> None:
        time.sleep(0.8)
        try:
            cfg = self._read_local_toml()
            debug_cfg = cfg.get("debug") if isinstance(cfg.get("debug"), dict) else {}
            config_cfg = debug_cfg.get("config") if isinstance(debug_cfg.get("config"), dict) else {}
            enabled = bool(config_cfg.get("enable", False))
            if not enabled:
                return
            include_values = bool(config_cfg.get("include_values", False))

            result = self.config_debug(include_values=include_values)
            try:
                self.file_logger.info("[testPlugin.config_debug] {}", result)
            except Exception as log_err:
                self.file_logger.warning("Failed to log config_debug result: {}", log_err)
            self.ctx.push_message(
                source="testPlugin.debug.config",
                message_type="text",
                description="config debug snapshot",
                priority=1,
                content=str(result)[:2000] + ("...(truncated)" if len(str(result)) > 2000 else ""),
            )
        except Exception:
            self.file_logger.exception("Config debug failed")

    def _startup_memory_debug(self) -> None:
        time.sleep(0.8)
        try:
            cfg = self._read_local_toml()
            debug_cfg = cfg.get("debug") if isinstance(cfg.get("debug"), dict) else {}
            mem_cfg = debug_cfg.get("memory") if isinstance(debug_cfg.get("memory"), dict) else {}
            enabled = bool(mem_cfg.get("enable", False))
            if not enabled:
                return

            lanlan_name = str(mem_cfg.get("lanlan_name", "")).strip()
            query = str(mem_cfg.get("query", "hello")).strip() or "hello"
            timeout = float(mem_cfg.get("timeout", 5.0))

            kwargs = {}
            if lanlan_name:
                kwargs["_ctx"] = {"lanlan_name": lanlan_name}

            result = self.memory_debug(query=query, timeout=timeout, **kwargs)
            self.ctx.push_message(
                source="testPlugin.debug.memory",
                message_type="text",
                description="memory debug result",
                priority=1,
                content=str(result),
            )
        except Exception:
            self.file_logger.exception("Memory debug failed")

    def _startup_messages_debug(self) -> None:
        time.sleep(0.8)
        try:
            cfg = self._read_local_toml()
            debug_cfg = cfg.get("debug") if isinstance(cfg.get("debug"), dict) else {}
            msg_cfg = debug_cfg.get("messages") if isinstance(debug_cfg.get("messages"), dict) else {}
            enabled = bool(msg_cfg.get("enable", False))
            if not enabled:
                return

            delay_seconds = float(msg_cfg.get("delay_seconds", 1.0))
            if delay_seconds > 0:
                time.sleep(delay_seconds)

            plugin_id = str(msg_cfg.get("plugin_id", "")).strip()
            max_count = int(msg_cfg.get("max_count", 50))
            priority_min = int(msg_cfg.get("priority_min", 0))
            timeout = float(msg_cfg.get("timeout", 5.0))
            source = str(msg_cfg.get("source", "")).strip()

            pri_opt = priority_min if priority_min > 0 else None

            sent_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            seed_source = "testPlugin.simple.seed"
            for n in (1, 2, 3, 4):
                self.ctx.push_message(
                    source=seed_source,
                    message_type="text",
                    description=f"seed number {n}",
                    priority=n,
                    content=str(n),
                    metadata={
                        "seed": True,
                        "n": n,
                        "sent_at": sent_at,
                    },
                )

            time.sleep(float(msg_cfg.get("seed_settle_seconds", 0.1)))

            msg_list = self.ctx.bus.messages.get(
                plugin_id=plugin_id or None,
                max_count=max_count,
                priority_min=pri_opt,
                timeout=timeout,
            )

            filtered = msg_list
            if source:
                filtered = filtered.filter(source=source)
            filtered = filtered.limit(10)

            seed = msg_list.filter(source=seed_source, strict=False)

            def _as_int(rec) -> int:
                try:
                    return int(str(getattr(rec, "content", "")).strip())
                except Exception:
                    return 0

            def _nums(lst) -> list[int]:
                return [
                    _as_int(x)
                    for x in lst.dump_records()
                    if str(getattr(x, "content", "")).strip().lstrip("-").isdigit()
                ]

            # Use replayable predicates (required for reload): no lambda/closure.
            set_a = seed.where_in("content", ["1", "2", "3"])
            set_b = seed.where_in("content", ["2", "3", "4"])

            union_ab = set_a + set_b
            inter_ab = set_a & set_b

            # Use replayable sort (required for reload): avoid sort(key=callable).
            union_sorted = union_ab.sort(by="content", reverse=False)
            inter_sorted = inter_ab.sort(by="content", reverse=False)

            seed_nums = _nums(seed)
            a_nums = _nums(set_a)
            b_nums = _nums(set_b)
            u_nums = _nums(union_sorted)
            i_nums = _nums(inter_sorted)

            self.file_logger.info("[messages_debug.trace] union explain={}", union_ab.explain())
            self.file_logger.info("[messages_debug.trace] union tree={}", union_ab.trace_tree_dump())

            self.file_logger.info("[messages_debug.trace] intersection explain={}", inter_ab.explain())
            self.file_logger.info("[messages_debug.trace] intersection tree={}", inter_ab.trace_tree_dump())

            # Use ctx-provided logger to avoid stdout spam.
            self.file_logger.info(
                "[messages_debug] seed={}({}) A={}({}) B={}({}) U={}({}) I={}({})",
                seed_nums,
                len(seed),
                a_nums,
                len(set_a),
                b_nums,
                len(set_b),
                u_nums,
                len(union_ab),
                i_nums,
                len(inter_ab),
            )

            watch_source = "testPlugin.watch.demo"
            watch_list = self.ctx.bus.messages.get(
                plugin_id=plugin_id or None,
                max_count=max_count,
                priority_min=pri_opt,
                timeout=timeout,
            ).filter(source=watch_source, strict=True)
            watcher = watch_list.watch(self.ctx).start()
            try:
                self._active_watchers.append(watcher)
            except Exception:
                pass
            try:
                self.file_logger.info("[messages_watch] watcher started: source={}", watch_source)
            except Exception:
                pass
            @watcher.subscribe(on="del")
            @watcher.subscribe(on="add")
            def _on_new_message(delta) -> None:
                try:
                    self.file_logger.info(
                        "[messages_watch] bus={} op={} added_count={}",
                        watch_source,
                        getattr(delta, "kind", ""),
                        len(getattr(delta, "added", []) or []),
                    )
                except Exception:
                    return

            def _delayed_push() -> None:
                try:
                    e = threading.Event()
                    try:
                        self._stop_events.append(e)
                    except Exception:
                        pass

                    if e.wait(timeout=3.0):
                        return
                    self.ctx.push_message(
                        source=watch_source,
                        message_type="text",
                        description="watcher demo message",
                        priority=1,
                        content=str(int(time.time())),
                    )
                except Exception as e:
                    try:
                        self.file_logger.warning("Failed to push watcher demo message: {}", e)
                    except Exception:
                        pass

            try:
                self._debug_executor.submit(_delayed_push)
            except Exception as e:
                try:
                    self.file_logger.warning("Failed to schedule watcher demo message: {}", e)
                except Exception:
                    pass
        except Exception:
            self.file_logger.exception("Messages debug failed")

    @lifecycle(id="shutdown")
    def shutdown(self, **_) -> Any:
        for w in list(getattr(self, "_active_watchers", []) or []):
            try:
                w.stop()
            except Exception as e:
                try:
                    self.file_logger.warning("Failed to stop watcher: {}", e)
                except Exception:
                    pass
        try:
            self._active_watchers.clear()
        except Exception:
            pass

        for e in list(getattr(self, "_stop_events", []) or []):
            try:
                e.set()
            except Exception:
                pass
        try:
            self._stop_events.clear()
        except Exception:
            pass

        if getattr(self, "_debug_executor", None) is not None:
            try:
                self._debug_executor.shutdown(wait=False)
            except Exception:
                pass
        return ok(data={"status": "shutdown"})

    def _startup_events_debug(self) -> None:
        time.sleep(0.8)
        try:
            cfg = self._read_local_toml()
            debug_cfg = cfg.get("debug") if isinstance(cfg.get("debug"), dict) else {}
            ev_cfg = debug_cfg.get("events") if isinstance(debug_cfg.get("events"), dict) else {}
            enabled = bool(ev_cfg.get("enable", False))
            if not enabled:
                return

            delay_seconds = float(ev_cfg.get("delay_seconds", 1.0))
            if delay_seconds > 0:
                time.sleep(delay_seconds)

            plugin_id = str(ev_cfg.get("plugin_id", "")).strip()
            max_count = int(ev_cfg.get("max_count", 50))
            timeout = float(ev_cfg.get("timeout", 5.0))
            ev_type = str(ev_cfg.get("type", "")).strip()

            ev_list = self.ctx.bus.events.get(
                plugin_id=plugin_id or None,
                max_count=max_count,
                timeout=timeout,
            )

            filtered = ev_list
            if ev_type:
                filtered = filtered.filter(type=ev_type)
            filtered = filtered.limit(10)

            payload = {
                "plugin_id": plugin_id or self.ctx.plugin_id,
                "type": ev_type,
                "count": len(ev_list),
                "events": ev_list.dump(),
                "filtered": filtered.dump(),
            }
            self.ctx.push_message(
                source="testPlugin.debug.events",
                message_type="text",
                description="events bus debug result",
                priority=1,
                content=str(payload)[:2000] + ("...(truncated)" if len(str(payload)) > 2000 else ""),
            )
        except Exception:
            self.file_logger.exception("Events debug failed")

    def _startup_lifecycle_debug(self) -> None:
        time.sleep(0.8)
        try:
            cfg = self._read_local_toml()
            debug_cfg = cfg.get("debug") if isinstance(cfg.get("debug"), dict) else {}
            lc_cfg = debug_cfg.get("lifecycle") if isinstance(debug_cfg.get("lifecycle"), dict) else {}
            enabled = bool(lc_cfg.get("enable", False))
            if not enabled:
                return

            delay_seconds = float(lc_cfg.get("delay_seconds", 1.0))
            if delay_seconds > 0:
                time.sleep(delay_seconds)

            plugin_id = str(lc_cfg.get("plugin_id", "")).strip()
            max_count = int(lc_cfg.get("max_count", 50))
            timeout = float(lc_cfg.get("timeout", 5.0))
            lc_type = str(lc_cfg.get("type", "")).strip()

            lc_list = self.ctx.bus.lifecycle.get(
                plugin_id=plugin_id or None,
                max_count=max_count,
                timeout=timeout,
            )

            filtered = lc_list
            if lc_type:
                filtered = filtered.filter(type=lc_type)
            filtered = filtered.limit(10)

            payload = {
                "plugin_id": plugin_id or self.ctx.plugin_id,
                "type": lc_type,
                "count": len(lc_list),
                "events": lc_list.dump(),
                "filtered": filtered.dump(),
            }
            self.ctx.push_message(
                source="testPlugin.debug.lifecycle",
                message_type="text",
                description="lifecycle bus debug result",
                priority=1,
                content=str(payload)[:2000] + ("...(truncated)" if len(str(payload)) > 2000 else ""),
            )
        except Exception:
            self.file_logger.exception("Lifecycle debug failed")

    @lifecycle(id="startup")
    def startup(self, **_):
        cfg = self._read_local_toml()
        debug_cfg = cfg.get("debug") if isinstance(cfg.get("debug"), dict) else {}
        timer_cfg = debug_cfg.get("timer") if isinstance(debug_cfg.get("timer"), dict) else {}
        config_cfg = debug_cfg.get("config") if isinstance(debug_cfg.get("config"), dict) else {}
        mem_cfg = debug_cfg.get("memory") if isinstance(debug_cfg.get("memory"), dict) else {}
        msg_cfg = debug_cfg.get("messages") if isinstance(debug_cfg.get("messages"), dict) else {}
        ev_cfg = debug_cfg.get("events") if isinstance(debug_cfg.get("events"), dict) else {}
        lc_cfg = debug_cfg.get("lifecycle") if isinstance(debug_cfg.get("lifecycle"), dict) else {}

        timer_enabled = bool(timer_cfg.get("enable")) if "enable" in timer_cfg else bool(debug_cfg.get("enable", False))
        config_enabled = bool(config_cfg.get("enable", False))
        memory_enabled = bool(mem_cfg.get("enable", False))
        messages_enabled = bool(msg_cfg.get("enable", False))
        events_enabled = bool(ev_cfg.get("enable", False))
        lifecycle_enabled = bool(lc_cfg.get("enable", False))

        if not timer_enabled and not config_enabled and not memory_enabled and not messages_enabled and not events_enabled and not lifecycle_enabled:
            self.file_logger.info("Debug disabled, skipping startup debug actions")
            return ok(data={"status": "disabled"})

        if timer_enabled:
            threading.Thread(target=self._start_debug_timer, daemon=True, name="testPlugin-debug-timer").start()
        if config_enabled:
            threading.Thread(target=self._startup_config_debug, daemon=True, name="testPlugin-debug-config").start()
        if memory_enabled:
            threading.Thread(target=self._startup_memory_debug, daemon=True, name="testPlugin-debug-memory").start()
        if messages_enabled:
            threading.Thread(target=self._startup_messages_debug, daemon=True, name="testPlugin-debug-messages").start()
        if events_enabled:
            threading.Thread(target=self._startup_events_debug, daemon=True, name="testPlugin-debug-events").start()
        if lifecycle_enabled:
            threading.Thread(target=self._startup_lifecycle_debug, daemon=True, name="testPlugin-debug-lifecycle").start()

        return ok(
            data={
                "status": "enabled",
                "timer": timer_enabled,
                "config": config_enabled,
                "memory": memory_enabled,
                "messages": messages_enabled,
                "events": events_enabled,
                "lifecycle": lifecycle_enabled,
            }
        )



    @plugin_entry(id="on_debug_tick")
    def on_debug_tick(
        self,
        timer_id: str,
        burst_size: int = 5,
        current_count: int = 0,
        max_count: int = 0,
        **_,
    ):
        sent_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        def _send_one(i: int) -> None:
            self.ctx.push_message(
                source="testPlugin.debug",
                message_type="text",
                description="debug tick burst",
                priority=1,
                content=f"debug tick: timer_id={timer_id}, tick={current_count}, msg={i+1}/{burst_size}, at={sent_at}",
                metadata={
                    "timer_id": timer_id,
                    "tick": current_count,
                    "burst_index": i,
                    "burst_size": burst_size,
                    "sent_at": sent_at,
                },
            )

        n = max(0, int(burst_size))
        # Fire-and-forget: do not block the callback, avoid timer_service timeout.
        for i in range(n):
            self._debug_executor.submit(_send_one, i)

        return ok(
            data={
                "timer_id": timer_id,
                "tick": current_count,
                "burst_size": n,
                "max_count": max_count,
                "sent_at": sent_at,
            }
        )

    @plugin_entry(
        id="config_debug",
        name="Config Debug",
        description="Debug config: returns plugin config and system config snapshot",
        input_schema={
            "type": "object",
            "properties": {
                "include_values": {
                    "type": "boolean",
                    "description": "Include full config values (may be large)",
                    "default": False,
                }
            },
            "required": [],
        },
    )
    def config_debug(self, include_values: bool = False, **_):
        plugin_cfg = self.config.dump(timeout=5.0)
        sys_cfg = SystemInfo(self.ctx).get_system_config(timeout=5.0)
        py_env = SystemInfo(self.ctx).get_python_env()

        if include_values:
            data = {
                "plugin_config": plugin_cfg,
                "system_config": sys_cfg,
                "python_env": py_env,
            }
        else:
            data = {
                "plugin_config_keys": sorted(list(plugin_cfg.keys())) if isinstance(plugin_cfg, dict) else [],
                "system_config_keys": sorted(list((sys_cfg.get("config") or {}).keys())) if isinstance(sys_cfg, dict) else [],
                "python": {
                    "implementation": ((py_env.get("python") or {}).get("implementation") if isinstance(py_env, dict) else None),
                    "version": ((py_env.get("python") or {}).get("version") if isinstance(py_env, dict) else None),
                    "executable": ((py_env.get("python") or {}).get("executable") if isinstance(py_env, dict) else None),
                },
                "os": (py_env.get("os") if isinstance(py_env, dict) else None),
            }

        return ok(data=data)

    @plugin_entry(
        id="memory_debug",
        name="Memory Debug",
        description="Debug memory: query ctx.bus.memory using current lanlan_name as bucket_id",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Query text", "default": "hello"},
                "lanlan_name": {"type": "string", "description": "Override lanlan_name (optional)", "default": ""},
                "timeout": {"type": "number", "description": "Timeout seconds", "default": 5.0},
            },
            "required": [],
        },
    )
    def memory_debug(self, query: str = "hello", lanlan_name: str = "", timeout: float = 5.0, **kwargs):
        ln = str(lanlan_name).strip() if lanlan_name is not None else ""
        if not ln:
            ctx_obj = kwargs.get("_ctx")
            if isinstance(ctx_obj, dict):
                ln = str(ctx_obj.get("lanlan_name") or "").strip()

        if not ln:
            return ok(
                data={
                    "ok": False,
                    "error": "lanlan_name is missing (expected in args._ctx.lanlan_name or explicit lanlan_name)",
                }
            )

        memory_list = self.ctx.bus.memory.get(bucket_id=ln, limit=20, timeout=timeout)
        filtered = memory_list.filter(type="PLUGIN_TRIGGER").limit(10)
        return ok(
            data={
                "bucket_id": ln,
                "query": query,
                "count": len(memory_list),
                "history": memory_list.dump(),
                "filtered": filtered.dump(),
            }
        )

    def run(self, message: str | None = None, **kwargs):
        # 简单返回一个字典结构
        self.file_logger.info(f"Running HelloPlugin with message: {message}")
        return {
            "hello": message or "world",
            "extra": kwargs,
        }
