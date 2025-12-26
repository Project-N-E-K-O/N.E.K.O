from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import threading
import time

from plugin.sdk.base import NekoPluginBase
from plugin.sdk.decorators import lifecycle, neko_plugin, plugin_entry
from plugin.sdk import ok, SystemInfo, MemoryClient


@neko_plugin
class HelloPlugin(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)  # 传递 ctx 给基类
        # 启用文件日志(同时输出到文件和控制台)
        self.file_logger = self.enable_file_logging(log_level="INFO")
        self.logger = self.file_logger  # 使用file_logger作为主要logger
        self.plugin_id = ctx.plugin_id  # 使用 plugin_id
        self._debug_executor = ThreadPoolExecutor(max_workers=32)
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
        except Exception:
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
        except Exception as e:
            self.file_logger.exception("Failed to start debug timer: {}", e)

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
            except Exception:
                pass
            self.ctx.push_message(
                source="testPlugin.debug.config",
                message_type="text",
                description="config debug snapshot",
                priority=1,
                content=str(result)[:2000] + ("...(truncated)" if len(str(result)) > 2000 else ""),
            )
        except Exception as e:
            self.file_logger.exception("Config debug failed: {}", e)

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
        except Exception as e:
            self.file_logger.exception("Memory debug failed: {}", e)

    @lifecycle(id="startup")
    def startup(self, **_):
        cfg = self._read_local_toml()
        debug_cfg = cfg.get("debug") if isinstance(cfg.get("debug"), dict) else {}
        timer_cfg = debug_cfg.get("timer") if isinstance(debug_cfg.get("timer"), dict) else {}
        config_cfg = debug_cfg.get("config") if isinstance(debug_cfg.get("config"), dict) else {}
        mem_cfg = debug_cfg.get("memory") if isinstance(debug_cfg.get("memory"), dict) else {}

        timer_enabled = bool(timer_cfg.get("enable")) if "enable" in timer_cfg else bool(debug_cfg.get("enable", False))
        config_enabled = bool(config_cfg.get("enable", False))
        memory_enabled = bool(mem_cfg.get("enable", False))

        if not timer_enabled and not config_enabled and not memory_enabled:
            self.file_logger.info("Debug disabled, skipping startup debug actions")
            return ok(data={"status": "disabled"})

        if timer_enabled:
            threading.Thread(target=self._start_debug_timer, daemon=True, name="testPlugin-debug-timer").start()
        if config_enabled:
            threading.Thread(target=self._startup_config_debug, daemon=True, name="testPlugin-debug-config").start()
        if memory_enabled:
            threading.Thread(target=self._startup_memory_debug, daemon=True, name="testPlugin-debug-memory").start()

        return ok(
            data={
                "status": "enabled",
                "timer": timer_enabled,
                "config": config_enabled,
                "memory": memory_enabled,
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
        description="Debug memory: query memory_server via IPC using current lanlan_name",
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

        result = MemoryClient(self.ctx).query(ln, query, timeout=timeout)
        return ok(data={"lanlan_name": ln, "query": query, "result": result})

    def run(self, message: str | None = None, **kwargs):
        # 简单返回一个字典结构
        self.file_logger.info(f"Running HelloPlugin with message: {message}")
        return {
            "hello": message or "world",
            "extra": kwargs,
        }
