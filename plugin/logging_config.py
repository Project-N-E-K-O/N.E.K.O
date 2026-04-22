"""
插件日志配置模块（stdlib 薄壳）

╔══════════════════════════════════════════════════════════════════════════╗
║                                                                          ║
║                        ⚠⚠⚠  警  告  ⚠⚠⚠                                ║
║                                                                          ║
║   本仓库 **彻底禁止** 引入 loguru / structlog / logbook 等第三方        ║
║   日志库。所有 Python 代码（含 plugin/、utils/、main_*、agent_*、       ║
║   memory_* 等）统一走 utils.logger_config.RobustLoggerConfig，          ║
║   日志会自动落到 我的文档/N.E.K.O/logs/。                              ║
║                                                                          ║
║   过去多次有人偷偷加 loguru，结果：                                     ║
║     1. AppImage 打包后 cwd 在只读 squashfs，loguru 默认相对路径直接     ║
║        崩溃；                                                            ║
║     2. 主进程一套日志、loguru 一套日志，排障时找不到地方；             ║
║     3. 加 loguru→stdlib bridge 把简单事情搞复杂。                      ║
║                                                                          ║
║   再有人在本仓库引入 loguru / 把 plugin 日志写到 cwd / 自创落盘路径，  ║
║   按维护者口径：**就把谁杀了。**                                        ║
║                                                                          ║
║   加 lint 守门：scripts/check_no_loguru.py 在 CI（analyze.yml）里强制   ║
║   执行，PR 含 loguru import 直接 fail，无法合并。                       ║
║                                                                          ║
╚══════════════════════════════════════════════════════════════════════════╝

本模块对外提供的接口（全部走 stdlib logging）：
  - get_logger(component) / logger     —— 获取 plugin 命名空间下的 logger
  - setup_logging(...)                 —— 转发到 utils.logger_config.setup_logging
  - configure_default_logger(level)    —— 兼容性 no-op（本体已统一管理 sink）
  - intercept_standard_logging()       —— 兼容性 no-op（本体即 stdlib）
  - format_log_text(...)               —— 截断/换行工具

PluginLoggerAdapter：
  兼容现有 plugin 内部的 loguru 风格调用（bind / opt / braces 风格 / 关键字 extra），
  内部全部转发到 stdlib logging.Logger。**不是为了在仓库里继续写 loguru 风格代码，
  仅仅是不想动 1000+ 处调用。新代码请直接用 stdlib `logger.info(f"...")` 风格。**

环境变量（向下兼容旧 NEKO_LOG_* 变量；新代码请用 utils.logger_config）：
  NEKO_LOG_LEVEL          全局日志级别 (TRACE/DEBUG/INFO/WARNING/ERROR/CRITICAL)
"""
from __future__ import annotations

import logging
import os
import re
import sys
import threading
from enum import Enum
from pathlib import Path
from typing import Any, Optional


# ──────────────────────────────────────────────────────────────────────────
#  brace-format 兼容（让 stdlib 支持 logger.info("msg {}", x) 这种 loguru 风格）
#  这段代码原本在 plugin/user_plugin_server.py 里，移到这里让所有进程共享。
# ──────────────────────────────────────────────────────────────────────────

def _install_logging_brace_compat() -> None:
    """patch stdlib LogRecord.getMessage 让 {} 风格不抛异常。

    stdlib 默认是 %-style，遇到 logger.info("msg {}", x) 会抛 TypeError；
    我们 fallback 到 str.format(*args)，让两种风格都能工作。
    """
    if getattr(logging, "_neko_brace_compat_installed", False):
        return

    original_get_message = logging.LogRecord.getMessage

    def _compat_get_message(self: logging.LogRecord) -> str:
        try:
            return original_get_message(self)
        except TypeError:
            msg = str(self.msg)
            args = self.args
            if not args or "%" in msg or "{" not in msg or "}" not in msg:
                raise
            try:
                if isinstance(args, dict):
                    return msg.format(**args)
                if not isinstance(args, tuple):
                    args = (args,)
                return msg.format(*args)
            except Exception:
                return f"{msg} | args={self.args!r}"

    setattr(logging.LogRecord, "getMessage", _compat_get_message)
    setattr(logging, "_neko_brace_compat_installed", True)


_install_logging_brace_compat()


# ──────────────────────────────────────────────────────────────────────────
#  LogLevel 枚举（向下兼容旧 plugin SDK 接口；映射到 stdlib int 等级）
# ──────────────────────────────────────────────────────────────────────────

class LogLevel(str, Enum):
    TRACE = "TRACE"
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


_LEVEL_TO_INT = {
    LogLevel.TRACE: logging.DEBUG,  # stdlib 没有 TRACE，降级到 DEBUG
    LogLevel.DEBUG: logging.DEBUG,
    LogLevel.INFO: logging.INFO,
    LogLevel.WARNING: logging.WARNING,
    LogLevel.ERROR: logging.ERROR,
    LogLevel.CRITICAL: logging.CRITICAL,
}


def _level_to_int(level: Any) -> int:
    if isinstance(level, int):
        return level
    if isinstance(level, LogLevel):
        return _LEVEL_TO_INT[level]
    if isinstance(level, str):
        try:
            return _LEVEL_TO_INT[LogLevel(level.upper())]
        except (KeyError, ValueError):
            mapped = logging.getLevelName(level.upper())
            return mapped if isinstance(mapped, int) else logging.INFO
    return logging.INFO


def _get_log_level() -> LogLevel:
    raw = os.getenv("NEKO_LOG_LEVEL", "INFO").upper()
    try:
        return LogLevel(raw)
    except ValueError:
        return LogLevel.INFO


# ──────────────────────────────────────────────────────────────────────────
#  全局配置常量（向下兼容；新代码请直接用 utils.logger_config）
# ──────────────────────────────────────────────────────────────────────────

LOG_LEVEL = _get_log_level()
# LOG_DIR 仅作为兼容性 stub。**绝对不要往这里写**——本体 RobustLoggerConfig
# 会自动选择可写目录（Documents/N.E.K.O/logs/）。
LOG_DIR = Path(os.getenv("NEKO_LOG_DIR", "log"))
LOG_MAX_SIZE = os.getenv("NEKO_LOG_MAX_SIZE", "10 MB")
LOG_RETENTION = os.getenv("NEKO_LOG_RETENTION", "7 days")
LOG_COMPRESSION = os.getenv("NEKO_LOG_COMPRESSION", "gz")

# 旧 loguru 格式字符串保留为常量；stdlib 不会用，但有几处旧代码 import 它们。
FORMAT_CONSOLE = (
    "{asctime} | {levelname:<8} | {name:<20} | {message}"
)
FORMAT_FILE = (
    "{asctime} | {levelname:<8} | {name:<20} | {message}"
)
FORMAT_CONSOLE_SIMPLE = "{asctime} | {levelname:<8} | {message}"
FORMAT_FILE_SIMPLE = "{asctime} | {levelname:<8} | {message}"


def get_plugin_format_console(plugin_id: str) -> str:
    return f"{{asctime}} | {{levelname:<8}} | [{plugin_id}] {{message}}"


def get_plugin_format_file(plugin_id: str) -> str:
    return f"{{asctime}} | {{levelname:<8}} | [{plugin_id}] {{message}}"


# ──────────────────────────────────────────────────────────────────────────
#  敏感信息过滤
# ──────────────────────────────────────────────────────────────────────────

REDACT_PATTERNS = [
    re.compile(r'(password|passwd|pwd)["\']?\s*[:=]\s*["\']?[^"\'\s]+', re.I),
    re.compile(r'(token|api_key|apikey|secret)["\']?\s*[:=]\s*["\']?[^"\'\s]+', re.I),
    re.compile(r'(authorization|auth)["\']?\s*[:=]\s*["\']?[^"\'\s]+', re.I),
]


def _redact_sensitive(message: str) -> str:
    for pattern in REDACT_PATTERNS:
        message = pattern.sub(r'\1=***REDACTED***', message)
    return message


# ──────────────────────────────────────────────────────────────────────────
#  本体 logger 初始化（懒加载，避免 import 循环）
# ──────────────────────────────────────────────────────────────────────────

_setup_lock = threading.Lock()
_root_initialised = False


def _ensure_root_logger() -> None:
    """确保 plugin 命名空间挂在本体 RobustLoggerConfig 之下。

    多次调用幂等。但**只在真正成功 setup 之后**才把 ``_root_initialised``
    锁死成 True，否则 import 早期 ``utils`` 还没在 sys.path / bootstrap 临时
    抛错 时会永久禁用重试，后续真正能 setup 的调用直接被短路掉，日志链路
    就再也挂不上了。
    """
    global _root_initialised
    if _root_initialised:
        return
    with _setup_lock:
        if _root_initialised:
            return
        # 通过 utils.logger_config 走本体落盘路径。
        try:
            from utils.logger_config import setup_logging as _bootstrap_setup_logging
        except ModuleNotFoundError:
            # 极早期 import（plugin SDK 单元测试场景）utils 不在 sys.path 时，
            # 静默 return —— 不要把 _root_initialised 锁死，下次再调能重试。
            return

        service_name = os.getenv("NEKO_PLUGIN_SERVICE_NAME") or "Plugin"

        try:
            from config import APP_NAME as _app_name
        except Exception:
            _app_name = "N.E.K.O"

        service_logger = logging.getLogger(f"{_app_name}.{service_name}")

        # Short-circuit：上层（user_plugin_server.py / _setup_plugin_logger）
        # 已经为该 service_name 调过 setup_logging，对应 logger 已挂 file/console
        # handler。再调一次会触发 RobustLoggerConfig.setup_logger() 的 "幂等重建"
        # （清掉重建），既浪费 IO，又会多打一行 "日志系统已初始化"。
        if not service_logger.handlers:
            # ``Plugin_<id>`` 子进程 → 收纳到 ``logs/plugin/``；
            # ``PluginServer`` 宿主进程 → 留在顶层 ``logs/``。
            # 前缀匹配故意卡 ``Plugin_`` 下划线，避免把 ``PluginServer`` 误路由。
            log_subdir = "plugin" if service_name.startswith("Plugin_") else None
            try:
                _bootstrap_setup_logging(
                    service_name=service_name,
                    log_level=_level_to_int(LOG_LEVEL),
                    silent=True,
                    log_subdir=log_subdir,
                )
            except Exception:
                # bootstrap 失败 —— 同样别锁死 flag，下次真正能跑时再重试。
                return
            # bootstrap 应该已经把 handlers 挂到 service_logger；重新拿一次。
            service_logger = logging.getLogger(f"{_app_name}.{service_name}")

        # 桥接 root logger → service handlers，让 uvicorn / aiohttp / urllib3 等
        # 通过 ``logging.getLogger("xxx")`` 拿 logger 的第三方库，propagate 到
        # root 时也能落盘。loguru 时代靠 ``intercept_standard_logging`` 在 root
        # 上挂转发 handler；拆 loguru 后那条 bridge 没了，必须显式重建。
        _install_root_bridge(service_logger)

        _root_initialised = True


def _install_root_bridge(service_logger: logging.Logger) -> None:
    """让 root logger 把记录复制到 service_logger 的 handlers 上。

    只在第一个真正配过 handler 的 service 上执行一次，幂等。

    注意：``N.E.K.O.<service>`` 自己挂的 logger ``propagate=False``，所以
    plugin 自己的日志走 service handler 一次，不会被 root 再写一遍；只有
    propagate=True 的第三方 logger 会经 root 走到这里。
    """
    if not service_logger.handlers:
        return
    root = logging.getLogger()
    if getattr(root, "_neko_plugin_root_bridged", False):
        return
    for h in service_logger.handlers:
        if h not in root.handlers:
            root.addHandler(h)
    # root 默认 WARNING；要拉到 service 级别，否则 INFO 在到达 handler 前
    # 就被 root 砍掉了。
    if root.level == logging.NOTSET or root.level > service_logger.level:
        root.setLevel(service_logger.level or logging.INFO)
    setattr(root, "_neko_plugin_root_bridged", True)


# ──────────────────────────────────────────────────────────────────────────
#  PluginLoggerAdapter
#  -------------------------------------------------------------------------
#  兼容 plugin/ 现存 1000+ 处 loguru 风格调用：
#    .info / .warning / .error / .debug / .critical / .exception
#    .info("msg {}", x)         braces 风格 → 内部转 .format
#    .info("msg", key=value)    关键字 extra → 走 stdlib extra
#    .bind(plugin_id=...)       → 返回带 extra 的新 adapter
#    .opt(exception=True)       → 临时 wrapper，下一次 log 自动 exc_info=True
#    .add / .remove / .configure → no-op
#    .level(name)               → 返回 LoguruLevel 兼容对象
#    .success / .trace          → 映射到 INFO / DEBUG
#  -------------------------------------------------------------------------
#  ⚠ 该 adapter 只是历史兼容。新代码请用 stdlib 风格：
#       logger.info(f"foo {x}")
#       logger.warning("bar", extra={"plugin_id": pid})
# ──────────────────────────────────────────────────────────────────────────

_LOGGER_KWARGS = {"exc_info", "stack_info", "stacklevel"}
_LOGURU_KW_BLACKLIST = {"colors", "lazy", "raw", "capture", "depth"}


def _format_brace_msg(msg: Any, args: tuple) -> tuple[str, tuple]:
    """如果 msg 含 {} 且 args 非空，尝试 .format(*args)；否则原样返回。"""
    if not args:
        return msg, args
    text = msg if isinstance(msg, str) else str(msg)
    if "{" not in text or "}" not in text:
        return msg, args
    # 优先 stdlib %-style；若失败则 fallback 到 .format
    try:
        text % args
        return msg, args
    except (TypeError, ValueError):
        pass
    try:
        formatted = text.format(*args)
        return formatted, ()
    except Exception:
        return msg, args


class PluginLoggerAdapter:
    """对外暴露的 logger 实例。

    **Lazy logger resolution**：adapter 持有 *component 名*（str），每次
    log 时根据当前 ``NEKO_PLUGIN_SERVICE_NAME`` 环境变量重算
    ``N.E.K.O.<service>.<component>`` 拿 stdlib logger。

    为什么不在 ``__init__`` 时绑定 ``logging.Logger`` 实例：
      子进程的某个共享 module 顶部写
      ``from plugin.logging_config import logger``，
      会在 import 时跑一次 ``get_logger("plugin")``。这一刻 host 的
      ``_setup_plugin_logger`` 还没把 ``NEKO_PLUGIN_SERVICE_NAME`` 设成
      ``Plugin_<id>``，所以 logger 会被冻结到默认的 ``Plugin`` 命名空间，
      之后再 setup_logging 也救不回来——日志都跑到 N.E.K.O_Plugin_*.log
      去了，新建的 N.E.K.O_Plugin_<id>_*.log 反而是空的。

      stdlib ``logging.getLogger(name)`` 自带 LRU，每次调用是 O(1) 拿同一
      个 Logger 实例，所以 lazy 解析没有性能开销。
    """

    __slots__ = ("_component", "_extra", "_opt_exc_info")

    def __init__(
        self,
        component: str,
        extra: Optional[dict] = None,
        opt_exc_info: Any = None,
    ):
        self._component = component
        self._extra: dict = dict(extra) if extra else {}
        self._opt_exc_info = opt_exc_info  # 仅由 .opt(exception=...) 设置，单次有效

    # ── 懒解析底层 stdlib logger ────────────────────────────────────
    def _resolve_logger(self) -> logging.Logger:
        try:
            from config import APP_NAME as _app_name
        except Exception:
            _app_name = "N.E.K.O"
        service_name = os.getenv("NEKO_PLUGIN_SERVICE_NAME") or "Plugin"
        return logging.getLogger(f"{_app_name}.{service_name}.{self._component}")

    # ── 内部分发 ─────────────────────────────────────────────────────
    def _log(self, level: int, msg: Any, args: tuple, kwargs: dict) -> None:
        msg, args = _format_brace_msg(msg, args)

        std_kwargs: dict[str, Any] = {}
        for key in list(kwargs):
            if key in _LOGGER_KWARGS:
                std_kwargs[key] = kwargs.pop(key)
            elif key in _LOGURU_KW_BLACKLIST:
                kwargs.pop(key)  # 静默丢弃 loguru-only 关键字

        # .opt(exception=...) 一次性附加
        if self._opt_exc_info is not None and "exc_info" not in std_kwargs:
            std_kwargs["exc_info"] = self._opt_exc_info
            self._opt_exc_info = None

        # 合并 binding extra + 调用 extra（既支持 extra={...}，也支持 logger.info("msg", k=v)）
        merged_extra = dict(self._extra)
        user_extra = kwargs.pop("extra", None)
        if isinstance(user_extra, dict):
            merged_extra.update(user_extra)
        if kwargs:
            # 剩余的 kwargs 当成 loguru 风格的 extra 字段
            merged_extra.update(kwargs)

        if merged_extra:
            std_kwargs["extra"] = merged_extra

        # 让 caller filename/lineno 跳过 adapter 本身
        std_kwargs.setdefault("stacklevel", 2)

        self._resolve_logger().log(level, msg, *args, **std_kwargs)

    # ── 标准方法 ─────────────────────────────────────────────────────
    def trace(self, msg: Any, *args, **kwargs) -> None:
        self._log(logging.DEBUG, msg, args, kwargs)

    def debug(self, msg: Any, *args, **kwargs) -> None:
        self._log(logging.DEBUG, msg, args, kwargs)

    def info(self, msg: Any, *args, **kwargs) -> None:
        self._log(logging.INFO, msg, args, kwargs)

    def success(self, msg: Any, *args, **kwargs) -> None:
        self._log(logging.INFO, msg, args, kwargs)

    def warning(self, msg: Any, *args, **kwargs) -> None:
        self._log(logging.WARNING, msg, args, kwargs)

    warn = warning

    def error(self, msg: Any, *args, **kwargs) -> None:
        self._log(logging.ERROR, msg, args, kwargs)

    def critical(self, msg: Any, *args, **kwargs) -> None:
        self._log(logging.CRITICAL, msg, args, kwargs)

    def exception(self, msg: Any, *args, **kwargs) -> None:
        kwargs.setdefault("exc_info", True)
        self._log(logging.ERROR, msg, args, kwargs)

    def log(self, level: Any, msg: Any, *args, **kwargs) -> None:
        self._log(_level_to_int(level), msg, args, kwargs)

    # ── loguru 兼容 ──────────────────────────────────────────────────
    def bind(self, **extra: Any) -> "PluginLoggerAdapter":
        merged = dict(self._extra)
        merged.update(extra)
        return PluginLoggerAdapter(self._component, extra=merged)

    def opt(self, *, exception: Any = None, depth: int = 0, lazy: bool = False,
            colors: bool = False, raw: bool = False, capture: bool = True) -> "PluginLoggerAdapter":
        # depth/lazy/colors/raw/capture 在 stdlib 下没有意义，保留签名兼容。
        # exception=True 触发下一次 log 自动 exc_info；exception=<exc_info_tuple> 直接透传。
        return PluginLoggerAdapter(
            self._component,
            extra=self._extra,
            opt_exc_info=True if exception is True else exception if exception else None,
        )

    def add(self, *args, **kwargs) -> int:  # noqa: ARG002 — loguru API 兼容
        # 历史代码偶尔在运行时 logger.add(...) 加 sink，已迁到 stdlib handler 管理。
        # 这里 no-op 并返回一个 fake sink id（loguru 风格）。
        return 0

    def remove(self, *args, **kwargs) -> None:  # noqa: ARG002 — loguru API 兼容
        return None

    def configure(self, *args, **kwargs) -> None:  # noqa: ARG002 — loguru API 兼容
        return None

    def level(self, name: str) -> Any:
        no = _level_to_int(name)
        return type("Level", (), {"name": name, "no": no})

    def patch(self, *args, **kwargs) -> "PluginLoggerAdapter":  # noqa: ARG002
        return self

    def contextualize(self, *args, **kwargs):  # noqa: ARG002
        # 返回 no-op context manager
        from contextlib import nullcontext
        return nullcontext()

    @property
    def extra(self) -> dict:
        return dict(self._extra)


# ──────────────────────────────────────────────────────────────────────────
#  对外 API
# ──────────────────────────────────────────────────────────────────────────

def setup_logging(
    component: str = "main",  # noqa: ARG001 — 兼容旧签名；本体接管后 component 只作命名空间
    level: Optional[Any] = None,  # noqa: ARG001 — 同上
    force: bool = False,  # noqa: ARG001 — 同上
) -> None:
    """配置组件日志（兼容旧签名）。

    实际配置由 utils.logger_config.RobustLoggerConfig 接管，本函数只确保
    本体 logger 已初始化。组件名仅用作 logger 命名空间，不再单独 sink。
    """
    _ensure_root_logger()


def get_logger(component: str) -> PluginLoggerAdapter:
    """获取带组件命名空间的 logger。

    底层 logger 名为 ``N.E.K.O.<service>.<component>``，每次调用时根据当前
    ``NEKO_PLUGIN_SERVICE_NAME`` 环境变量动态解析（见 PluginLoggerAdapter
    docstring）。这样保证模块顶部的 ``logger = get_logger(...)`` 可以跟随
    后续 host 设置的 service_name。
    """
    _ensure_root_logger()
    safe = component.strip(".") or "plugin"
    return PluginLoggerAdapter(safe)


def configure_default_logger(level: str = "INFO") -> None:
    """兼容性 no-op。

    本体 RobustLoggerConfig 已经在主进程入口（user_plugin_server.py 等）
    调过 setup_logging()，不需要这里再配置 sink。保留函数签名给老代码 import。
    """
    _ensure_root_logger()


def intercept_standard_logging() -> None:
    """兼容性 no-op。

    本体本来就是 stdlib logging，不需要"拦截"。
    """
    return None


def format_log_text(value: Any, max_len: Optional[int] = None, wrap: Optional[int] = None) -> str:
    """格式化日志文本，支持截断和换行。

    Args:
        value: 要格式化的值
        max_len: 最大长度，默认从环境变量 NEKO_PLUGIN_LOG_CONTENT_MAX 读取（默认 200）
        wrap:    换行宽度，默认从环境变量 NEKO_PLUGIN_LOG_WRAP 读取（默认 0 = 不换行）
    """
    s = "" if value is None else str(value)

    if max_len is None:
        try:
            max_len = int(os.getenv("NEKO_PLUGIN_LOG_CONTENT_MAX", "200"))
        except (ValueError, TypeError):
            max_len = 200
    if max_len <= 0:
        max_len = 200

    truncated = False
    if len(s) > max_len:
        s = s[:max_len]
        truncated = True

    if wrap is None:
        try:
            wrap = int(os.getenv("NEKO_PLUGIN_LOG_WRAP", "0"))
        except (ValueError, TypeError):
            wrap = 0

    if wrap and wrap > 0:
        s = "\n".join(s[i:i + wrap] for i in range(0, len(s), wrap))

    if truncated:
        s = s + "...(truncated)"

    return s


# 模块级 default logger（兼容 `from plugin.logging_config import logger`）
logger: PluginLoggerAdapter = get_logger("plugin")


# ──────────────────────────────────────────────────────────────────────────
#  导出
# ──────────────────────────────────────────────────────────────────────────

__all__ = [
    "LogLevel",
    "LOG_LEVEL",
    "LOG_DIR",
    "LOG_MAX_SIZE",
    "LOG_RETENTION",
    "LOG_COMPRESSION",
    "logger",
    "get_logger",
    "setup_logging",
    "configure_default_logger",
    "intercept_standard_logging",
    "format_log_text",
    "PluginLoggerAdapter",
    # 旧格式常量（保留作兼容性 import；stdlib 不会用）
    "FORMAT_CONSOLE",
    "FORMAT_FILE",
    "FORMAT_CONSOLE_SIMPLE",
    "FORMAT_FILE_SIMPLE",
    "get_plugin_format_console",
    "get_plugin_format_file",
]
