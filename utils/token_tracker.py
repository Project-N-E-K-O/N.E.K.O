# -*- coding: utf-8 -*-
"""
全局 LLM Token 用量追踪模块

通过 monkey-patch OpenAI SDK 的 chat.completions.create（同步 + 异步），
自动拦截所有 LLM 调用（包括 LangChain 底层调用）的 usage 数据。
用 ContextVar 标记调用类型，确保 Nuitka/PyInstaller 兼容。

Usage:
    from utils.token_tracker import TokenTracker, install_hooks, llm_call_context

    # 启动时安装 hooks
    install_hooks()
    TokenTracker.get_instance().start_periodic_save()

    # 在调用模块标记 call_type
    with llm_call_context("conversation"):
        async for chunk in llm.astream(messages):
            ...
"""
import atexit
import asyncio
import copy
import functools
import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
import urllib.request
import urllib.error
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from utils.config_manager import get_config_manager
from utils.file_utils import atomic_write_json
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__)

# ---------------------------------------------------------------------------
# ContextVar: 调用类型标记（替代 stack inspection，Nuitka/PyInstaller 兼容）
# ---------------------------------------------------------------------------

_current_call_type: ContextVar[str] = ContextVar('_llm_call_type', default='unknown')


@contextmanager
def llm_call_context(call_type: str):
    """Context manager，在代码块内标记当前 LLM 调用类型。"""
    token = _current_call_type.set(call_type)
    try:
        yield
    finally:
        _current_call_type.reset(token)


def set_call_type(call_type: str):
    """简单设置当前调用类型（适用于不方便 wrap 的场景）。"""
    _current_call_type.set(call_type)


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _deep_copy_day(day: dict) -> dict:
    """深拷贝一天的统计数据。"""
    return copy.deepcopy(day)


def _merge_day_stats(target: dict, source: dict):
    """将 source 的统计数据累加到 target 中（原地修改 target）。"""
    for k in ("total_prompt_tokens", "total_completion_tokens", "total_tokens",
              "cached_tokens", "total_prompt_chars", "call_count", "error_count"):
        target[k] = target.get(k, 0) + source.get(k, 0)

    # by_model
    t_bm = target.setdefault("by_model", {})
    for model, bucket in source.get("by_model", {}).items():
        if model not in t_bm:
            t_bm[model] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                           "cached_tokens": 0, "prompt_chars": 0, "call_count": 0}
        for k in ("prompt_tokens", "completion_tokens", "total_tokens",
                  "cached_tokens", "prompt_chars", "call_count"):
            t_bm[model][k] = t_bm[model].get(k, 0) + bucket.get(k, 0)

    # by_call_type
    t_bt = target.setdefault("by_call_type", {})
    for ct, bucket in source.get("by_call_type", {}).items():
        if ct not in t_bt:
            t_bt[ct] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                        "cached_tokens": 0, "prompt_chars": 0, "call_count": 0}
        for k in ("prompt_tokens", "completion_tokens", "total_tokens",
                  "cached_tokens", "prompt_chars", "call_count"):
            t_bt[ct][k] = t_bt[ct].get(k, 0) + bucket.get(k, 0)


# ---------------------------------------------------------------------------
# 跨进程文件锁（O_CREAT | O_EXCL 方式，跨平台）
# ---------------------------------------------------------------------------

@contextmanager
def _file_lock(lock_path: Path, timeout: float = 10.0):
    """基于文件系统的跨进程互斥锁。

    使用 O_CREAT | O_EXCL 原子创建锁文件，确保同一时刻只有一个进程持有锁。
    锁文件中写入 PID + 时间戳，用于超时后检测过期锁。
    """
    fd = -1
    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
            # 写入 PID 便于调试
            os.write(fd, f"{os.getpid()},{time.time()}".encode())
            break
        except (FileExistsError, PermissionError, OSError):
            # 检测过期锁（持有超过 30 秒视为进程崩溃后的残留）
            try:
                lock_age = time.time() - os.path.getmtime(str(lock_path))
                if lock_age > 30:
                    try:
                        os.unlink(str(lock_path))
                    except OSError:
                        pass
                    continue
            except OSError:
                pass

            if time.monotonic() >= deadline:
                logger.warning("Token tracker: file lock timeout, force removing stale lock")
                try:
                    os.unlink(str(lock_path))
                except OSError:
                    time.sleep(0.1)
                raise TimeoutError(f"file lock timeout after {timeout}s: {lock_path}")

            time.sleep(0.05)
    try:
        yield
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
        for _retry in range(3):
            try:
                os.unlink(str(lock_path))
                break
            except OSError:
                if _retry < 2:
                    time.sleep(0.05)


# ---------------------------------------------------------------------------
# 远程遥测上报配置（参考 vLLM DO_NOT_TRACK 机制）
#
# 设计与 vLLM 一致：秘钥和地址硬编码在源码中，无需用户配置环境变量。
# HMAC 不是为了防止逆向（代码本身可读），而是防止随机噪声和简单伪造。
# ---------------------------------------------------------------------------

# ★ 发版前修改：遥测服务器地址。为空则不上报。
_TELEMETRY_SERVER_URL = "http://118.31.122.91:8099"

if _TELEMETRY_SERVER_URL and not _TELEMETRY_SERVER_URL.startswith(("http://", "https://")):
    logger.warning("Token tracker: invalid telemetry URL scheme, disabling remote reporting")
    _TELEMETRY_SERVER_URL = ""

# ★ 发版前修改：HMAC 签名密钥（与 server.py 中的 HMAC_SECRET 保持一致）
_TELEMETRY_HMAC_SECRET = "neko-v1-a3f8b2c1d4e5f6789012345678abcdef"  # noqa: S105

# Opt-out 开关（标准 DO_NOT_TRACK 约定，用户可自行设置）
_DO_NOT_TRACK = any(
    os.getenv(v, "").strip() in ("1", "true", "yes")
    for v in ("NEKO_DO_NOT_TRACK", "DO_NOT_TRACK")
)

# 上报间隔（3 分钟）
# 节流设计：
#   record() → 即时写入内存（零 I/O）
#   save()   → 每 60s 本地落盘，然后调用 _report_to_server()
#   _report_to_server() → 仅当距上次上报 ≥ 60s 时才真正发 HTTP
#   所以每个进程最多每 1 分钟发一次请求。3 个 server 进程 = 180 req/h/device。
_TELEMETRY_REPORT_INTERVAL = 60

# 上报超时
_TELEMETRY_TIMEOUT = 10  # 秒


def _get_app_version_from_changelog() -> str:
    """从 config/changelog/ 目录中读取最高版本号作为当前 app 版本。"""
    changelog_dir = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "config", "changelog"
    )
    if not os.path.isdir(changelog_dir):
        return "unknown"
    best_ver: tuple[int, ...] = (0,)
    best_stem = "unknown"
    try:
        for fname in os.listdir(changelog_dir):
            if not fname.endswith(".md"):
                continue
            stem = fname[:-3]
            try:
                ver = tuple(int(x) for x in stem.split("."))
            except (ValueError, AttributeError):
                continue
            if ver > best_ver:
                best_ver = ver
                best_stem = stem
        return best_stem
    except OSError as e:
        logger.debug(f"Token tracker: failed to read changelog dir: {e}")
        return "unknown"


_MACHINE_ID_PLACEHOLDERS = {
    # systemd 在 first-boot 前的占位
    "uninitialized",
    # 全零/全 F：VM 镜像克隆未重置、sysprep 异常、虚拟主板默认值的常见非真实 ID
    "00000000000000000000000000000000",
    "ffffffffffffffffffffffffffffffff",
    "00000000-0000-0000-0000-000000000000",
    "ffffffff-ffff-ffff-ffff-ffffffffffff",
}


def _is_valid_machine_id(value: Optional[str]) -> bool:
    """合理性校验 OS 机器 ID，防止占位值或镜像克隆未重置的非真实 ID 把多台
    机器折叠到同一个 device_id。

    要求去掉 GUID 分隔符后正好 32 位十六进制，且不在已知占位符黑名单里。
    校验失败时调用方应 fallback 到 legacy 算法，而不是把无效值当指纹用。
    """
    if not value:
        return False
    normalized = value.strip().lower()
    if normalized in _MACHINE_ID_PLACEHOLDERS:
        return False
    hex_only = normalized.replace("-", "")
    if len(hex_only) != 32:
        return False
    return all(c in "0123456789abcdef" for c in hex_only)


def _read_os_machine_id() -> Optional[str]:
    """读取操作系统级稳定机器标识。

    - Windows: HKLM\\SOFTWARE\\Microsoft\\Cryptography\\MachineGuid
    - macOS:   IOPlatformUUID（ioreg -rd1 -c IOPlatformExpertDevice）
    - Linux:   /etc/machine-id 或 /var/lib/dbus/machine-id

    这些 ID 由系统安装时生成，绑定到主板/系统而非网络配置，不会因为
    网卡变化（VPN / Docker / 外接 NIC）或安装路径变化（Steam 库迁移、
    源码版 / 打包版切换）漂移。

    每个来源的返回值都会过 _is_valid_machine_id 合理性校验，避免占位值
    （systemd `uninitialized`、全零/全 F GUID）被当成有效指纹。读取失败
    或校验不通过返回 None，调用方需 fallback 到 legacy 算法。
    """
    import sys

    try:
        if sys.platform == "win32":
            import winreg
            try:
                key = winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    r"SOFTWARE\Microsoft\Cryptography",
                    0,
                    winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
                )
                try:
                    value, _ = winreg.QueryValueEx(key, "MachineGuid")
                finally:
                    winreg.CloseKey(key)
                candidate = value.strip() if isinstance(value, str) else None
                if _is_valid_machine_id(candidate):
                    return candidate
            except OSError:
                return None

        elif sys.platform == "darwin":
            import re
            import subprocess
            try:
                out = subprocess.run(
                    ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                return None
            if out.returncode == 0:
                m = re.search(r'"IOPlatformUUID"\s*=\s*"([^"]+)"', out.stdout)
                if m:
                    candidate = m.group(1).strip()
                    if _is_valid_machine_id(candidate):
                        return candidate

        else:
            for path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        value = f.read().strip()
                except (FileNotFoundError, PermissionError, OSError):
                    continue
                if _is_valid_machine_id(value):
                    return value
    except Exception:
        return None

    return None


def _get_legacy_device_id() -> str:
    """旧版 device_id 算法（保留用于迁移期 fold）。

    SHA256(uuid.getnode() | install_dir | "neko-telemetry")。getnode 在多网卡
    机器上不稳定（VPN / Docker / 外接网卡 enumeration order 变化），install_dir
    随安装位置变化，所以这个 ID 容易"漂"，长期 retention 数据会被打散。新版本
    保留它仅用于 server 端 fold 历史数据：客户端在 payload 中同时上报新旧两个
    ID，server 后续可通过 events 表里的 device_id_legacy 字段建立 mapping。
    """
    import uuid as _uuid
    import platform

    try:
        machine_id = str(_uuid.getnode())
    except Exception:
        machine_id = platform.node()

    install_salt = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    raw = f"{machine_id}|{install_salt}|neko-telemetry"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _get_anonymous_device_id() -> str:
    """生成稳定的匿名设备指纹。

    优先使用 OS 级稳定标识（_read_os_machine_id），失败时回退到 legacy 算法
    保证不会写入空值。结果为 64 字符十六进制 SHA256，不可逆，不含 PII。

    与 legacy 算法的命名空间用 "neko-telemetry-v2" 区分，确保新旧 ID 不会
    在哈希空间相撞。

    参考 vLLM: 只用硬件/系统信息生成匿名 ID，不含用户 PII。
    """
    os_id = _read_os_machine_id()
    if os_id:
        return hashlib.sha256(f"{os_id}|neko-telemetry-v2".encode("utf-8")).hexdigest()
    return _get_legacy_device_id()


# ---------------------------------------------------------------------------
# A/B test 分支 / 用户 locale / 时区
#
# 三者都是描述「这台机器/这个用户当前是谁」的副字段：
#   - branch：首次启动时随机抽签后落盘，后续启动只读不改，保证同一设备稳定。
#             当前 _TELEMETRY_BRANCHES 只有一个值，将来扩展元组即可触发 split；
#             已经落盘的老用户继续读到旧分支，新用户随机进新池。
#   - locale / timezone：每次上报时取当下值；同一设备换语言/换时区都视为同
#             一个 device_id，server 端按 "latest seen" 覆写即可。
# ---------------------------------------------------------------------------

_TELEMETRY_BRANCH_FILE = ".telemetry_branch"
# A/B 池：
#   - "main"：控制组，沿用历史默认（隐私模式按用户地区分流，仅中国地区默认关闭）
#   - "privacy_default_off_v1"：实验组，隐私模式一律默认关闭
_TELEMETRY_BRANCHES: tuple = ("main", "privacy_default_off_v1")

# 进程级缓存：keyed by str(config_dir)。写盘失败的环境下（只读 FS / 权限拒绝），
# 不缓存就每次 secrets.choice 重抽，导致同一 install 的 TokenTracker 上报和
# 前端 `/conversation-settings` 拿到不同分支，A/B 归因被打散。dict.setdefault
# 在 CPython GIL 下是原子的，足以扛住模块内的并发首抽。
_telemetry_branch_cache: dict = {}


def _get_telemetry_branch(config_dir: Path) -> str:
    """读取或抽签生成 A/B test 分支标识，持久化在 config_dir 下。

    多进程冷启动安全：用 ``O_CREAT | O_EXCL`` 原子创建保证只有一个进程能写入；
    其它并发进程拿到 FileExistsError 后回读同一文件，确保 device-stable
    cohorting（同 device 不同 worker 不会落到不同 branch）。同款模式见
    _file_lock 的实现。

    进程级缓存：首次 resolve 后落 `_telemetry_branch_cache`，后续调用直接命中。
    主要为只读 FS / 权限错误等持久化失败的环境兜底——多 cohort 下没有这层缓存，
    每次 `secrets.choice` 都会重抽，同一进程内不同调用方会观察到不同分支。
    """
    cache_key = str(config_dir)
    cached_proc = _telemetry_branch_cache.get(cache_key)
    if cached_proc is not None:
        return cached_proc

    p = config_dir / _TELEMETRY_BRANCH_FILE

    def _read() -> Optional[str]:
        # 返 None 只表示「文件不存在 / 内容非法」两种确定状态；transient I/O 错误
        # 故意向上冒泡。否则老设备一次读盘失败会被吞成 None，slow path 把
        # FileExistsError 当成「文件存在但内容坏」走自愈覆盖，静默把设备改组。
        # 让 OSError 透出，让 `/conversation-settings` 的 except 把 telemetryBranch
        # 返 None，前端保留 pending marker，下次启动 fast path 读到合法值收敛。
        #
        # 严格校验：append-only 池下迁移期老分支也该保留在 _TELEMETRY_BRANCHES
        # 里，所以这里不会误杀历史值。
        if not p.exists():
            return None
        value = p.read_text(encoding="utf-8").strip()
        if value in _TELEMETRY_BRANCHES:
            return value
        return None

    # Fast path：文件已存在直接读
    cached = _read()
    if cached is not None:
        return _telemetry_branch_cache.setdefault(cache_key, cached)

    branch = secrets.choice(_TELEMETRY_BRANCHES) if _TELEMETRY_BRANCHES else "main"
    try:
        config_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.debug(f"Token tracker: failed to create config dir for branch file: {e}")

    # Slow path：原子创建。两个进程同时走到这里只有一个成功，另一个回读拿到
    # 同一 branch，保证 device-stable。
    try:
        fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            os.write(fd, branch.encode("utf-8"))
        finally:
            os.close(fd)
        return _telemetry_branch_cache.setdefault(cache_key, branch)
    except FileExistsError:
        # 另一个进程抢先写了 —— 回读它写的值，确保两个进程返回同一 branch
        peer = _read()
        if peer is not None:
            return _telemetry_branch_cache.setdefault(cache_key, peer)
        # peer 是 None 说明文件存在但内容不在 _TELEMETRY_BRANCHES 里（截断/损坏/
        # 跨版本残留）。这种情况下若只返回本进程抽到的值不修盘，下次进程重启会
        # 再走一次「读到坏值 → fast path miss → slow path 拿到 FileExistsError →
        # 重抽」，cohort 在多次启动间反复翻滚。覆盖修盘保证只有这一次重抽，
        # 之后就稳定。
        try:
            with open(p, "w", encoding="utf-8") as f:
                f.write(branch)
        except Exception as e:
            logger.debug(f"Token tracker: failed to heal corrupt branch file: {e}")
        return _telemetry_branch_cache.setdefault(cache_key, branch)
    except Exception as e:
        # 写盘失败不致命：进程级缓存 setdefault 保证同一进程后续所有调用方拿到
        # 相同分支，TokenTracker 上报和前端 API 不会互相打架。下次进程重启时若
        # 写盘仍然失败，缓存重新随机抽——按设计这就是 server 端看到的分布噪声
        # 来源，不构成「同一 install 多个分支」的错误数据。
        logger.debug(f"Token tracker: failed to persist telemetry branch: {e}")
        return _telemetry_branch_cache.setdefault(cache_key, branch)


def get_telemetry_branch() -> str:
    """对外暴露的 A/B test 分支读取入口。

    `_get_telemetry_branch` 是内部实现（参数化 config_dir，方便测试）；本函数从
    全局 config_manager 取 config_dir 后转发。前端通过 API 拿到 branch 后可在
    首次启动时按分支选择默认行为，与 token tracker 自身上报的 branch 保持一致。
    """
    return _get_telemetry_branch(get_config_manager().config_dir)


def _get_telemetry_locale() -> str:
    """获取用户 UI locale (zh-CN / en-US / ja-JP …)。

    优先用 language_utils.get_global_language_full —— 它先看 Steam 设置再 fallback
    到系统语言，是 codebase 里 "用户真正在用的 UI 语言" 的真值。失败回退到
    stdlib locale。
    """
    try:
        from utils.language_utils import get_global_language_full
        loc = get_global_language_full()
        if loc:
            return str(loc)[:32]
    except Exception:
        pass
    try:
        import locale as _locale
        sys_locale = _locale.getlocale()[0]
        if sys_locale:
            return str(sys_locale)[:32]
    except Exception:
        pass
    return "unknown"


def _is_release_build() -> bool:
    """是否打包过 —— PyInstaller (``sys.frozen``) 或 Nuitka (``__compiled__`` /
    ``__nuitka_binary_dir``)。两种打包器都要识别：PyInstaller 走 spec 链路，
    Nuitka 走 build_nuitka.bat 链路。"""
    import sys

    if getattr(sys, "frozen", False):
        return True
    # Nuitka 在每个编译模块的 globals 里注入 __compiled__；主模块还有
    # __nuitka_binary_dir。先看当前模块 globals，再兜底主模块属性，确保 standalone
    # 和 onefile 两种 Nuitka 模式都能识别。
    if "__compiled__" in globals() or "__nuitka_binary_dir" in globals():
        return True
    main_mod = sys.modules.get("__main__")
    if main_mod is not None and (
        hasattr(main_mod, "__nuitka_binary_dir") or hasattr(main_mod, "__compiled__")
    ):
        return True
    return False


def _is_steam_sdk_engaged() -> bool:
    """Steam SDK 是否拿到/缓存过 user id 或 工坊订阅。

    任一信号为真即认为是 Steam 版：
    1. 当前进程 Steamworks SDK 实例的 ``Users.GetSteamID()`` 返回非零 —— 真
       的从 Steam 客户端拿到了登录用户。
    2. ``Workshop.GetNumSubscribedItems()`` 大于 0 —— 用户订阅过工坊内容。
    3. ``config_dir/workshop_config.json`` 存在 —— 之前任何一次会话写过工坊
       配置，足以证明这台机器跑过 Steam 版（cloudsave 会把它打包带走，所以
       即使本次会话 Steam 客户端没开，文件仍在就算）。

    1/2 是实时探测，覆盖正常 Steam session；3 是磁盘兜底，覆盖"上次跑过 Steam
    本次断网/客户端没开"的场景。
    """
    try:
        from utils.steam_state import get_steamworks
        sw = get_steamworks()
        if sw is not None:
            try:
                if int(sw.Users.GetSteamID() or 0) > 0:
                    return True
            except Exception:
                pass
            try:
                if int(sw.Workshop.GetNumSubscribedItems() or 0) > 0:
                    return True
            except Exception:
                pass
    except Exception:
        pass

    try:
        from utils.config_manager import get_config_manager
        cm = get_config_manager()
        if (cm.config_dir / "workshop_config.json").exists():
            return True
    except Exception:
        pass

    return False


def _get_telemetry_steam_user_id() -> str:
    """读取当前会话的 Steam64 user id，作为字符串返回。

    返回非空 string 表示 Steamworks SDK 真的从 Steam 客户端拿到了登录用户；
    返回空 string 表示 SDK 没起来 / Steam 客户端没开 / 源码模式没初始化
    SDK 等情形。

    用 string 而非 int 上报，避免 Steam64（u64，常超过 2^53）在某些 JSON
    消费方（JS / 部分 SDK）里精度丢失。
    """
    try:
        from utils.steam_state import get_steamworks
        sw = get_steamworks()
        if sw is None:
            return ""
        try:
            sid = int(sw.Users.GetSteamID() or 0)
        except Exception:
            return ""
        if sid > 0:
            return str(sid)
    except Exception:
        pass
    return ""


def _get_telemetry_distribution() -> str:
    """识别发行渠道：steam / release / source。

    判定顺序：
    1. 先看是否 release build（PyInstaller ``sys.frozen`` 或 Nuitka
       ``__compiled__`` / ``__nuitka_binary_dir``）。**只有 release 才可能是
       Steam 版** —— 源码运行哪怕开着 Steam 客户端也算 source。
    2. release + Steam SDK 拿到/缓存过 user id 或工坊订阅 → ``steam``。
    3. release 但 SDK 没动 → ``release``（独立发行版）。
    4. 非 release → ``source``（开源/开发模式）。
    """
    if not _is_release_build():
        return "source"
    if _is_steam_sdk_engaged():
        return "steam"
    return "release"


def _get_telemetry_timezone() -> str:
    """获取本地时区。优先 IANA (Asia/Shanghai)，回退到 UTC 偏移 (+08:00)。"""
    try:
        import tzlocal
        tz = tzlocal.get_localzone()
        if tz is not None:
            name = str(tz)
            if name:
                return name[:64]
    except Exception:
        pass
    try:
        now_local = datetime.now().astimezone()
        local_tz = now_local.tzinfo
        if local_tz is not None:
            name = str(local_tz)
            # Windows 上 astimezone 可能给出 "China Standard Time" 这类非 IANA 字串，
            # 没有 '/' 时退到 offset 表示，避免污染按 IANA 切片的分析。
            if name and "/" in name:
                return name[:64]
        # 取实际 UTC 偏移（aware datetime 反映当前 DST 状态）。time.altzone /
        # time.daylight 不行：time.daylight 只表示"locale 有没有 DST 制度"，
        # 不是"现在是不是 DST"，在有 DST 的时区会全年报 DST 偏移。
        offset = now_local.utcoffset()
        if offset is not None:
            total_sec = int(offset.total_seconds())
            sign = "+" if total_sec >= 0 else "-"
            abs_sec = abs(total_sec)
            return f"{sign}{abs_sec // 3600:02d}:{(abs_sec % 3600) // 60:02d}"
    except Exception:
        pass
    return "unknown"


def _compute_telemetry_signature(payload_json: str, timestamp: float) -> str:
    """计算遥测上报的 HMAC-SHA256 签名。"""
    body_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    message = f"{timestamp}|{body_hash}"
    return hmac.new(
        _TELEMETRY_HMAC_SECRET.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


# ---------------------------------------------------------------------------
# TokenTracker 单例
# ---------------------------------------------------------------------------

class TokenTracker:
    """线程安全 + 多进程安全的全局 LLM token 用量追踪器。

    设计：
    - 所有进程共享单个 token_usage.json 文件
    - 内存中只追踪"尚未落盘的增量"（delta）
    - save() 使用文件锁做 read-merge-write，保证多进程不丢数据
    - get_stats() 读磁盘 + 合并内存 delta，不做任何文件删除
    """

    _instance: Optional['TokenTracker'] = None
    _init_lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> 'TokenTracker':
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._lock = threading.Lock()
        self._config_manager = get_config_manager()

        # 尚未落盘的增量数据（save 成功后清空）
        self._delta_daily: dict = {}
        self._delta_records: deque = deque(maxlen=200)

        # 持久化控制
        self._save_interval = 60  # 秒
        self._dirty = False
        self._save_task: Optional[asyncio.Task] = None

        # 远程遥测上报
        self._device_id: str = ""  # 延迟生成
        self._branch: str = ""  # 延迟生成（首次上报时读盘/抽签）
        self._last_report_time: float = 0.0
        self._report_interval = _TELEMETRY_REPORT_INTERVAL
        self._unsent_daily: dict = {}  # 尚未成功上报到服务器的增量
        self._unsent_records: list = []
        self._has_recorded_app_start: bool = False  # 🔒 app_start 单次上报锁

        # 首次启动：迁移旧版 per-instance 文件
        self._migrate_legacy_files()

        # 恢复上次未成功上报的远程数据
        self._load_unsent_queue()

        # atexit 兜底：不管进程如何退出（SIGTERM / 异常 / 正常结束），都尝试保存
        # 注意：SIGKILL (kill -9) 无法被拦截，此时最多丢 60s 数据
        atexit.register(self._atexit_save)

    # ---- 存储路径 ----

    @property
    def _storage_path(self) -> Path:
        return self._config_manager.config_dir / "token_usage.json"

    @property
    def _lock_file_path(self) -> Path:
        return self._config_manager.config_dir / ".token_usage.lock"

    @property
    def _storage_dir(self) -> Path:
        return self._config_manager.config_dir

    @property
    def _unsent_queue_path(self) -> Path:
        """远程上报未发送队列的持久化文件。

        进程被 kill 时 _unsent_daily 会丢失（纯内存）。
        通过将队列写到这个文件，重启后可以恢复并重发。
        """
        return self._config_manager.config_dir / ".telemetry_unsent.json"

    # ---- atexit / unsent 持久化 ----

    def _atexit_save(self):
        """atexit 兜底：进程退出前尽最后努力保存。

        覆盖场景：SIGTERM / 未捕获异常 / 正常退出 / sys.exit()
        不覆盖：SIGKILL (kill -9) / 断电 — 此时最多丢 60s 数据
        """
        try:
            # save() first: persists delta to disk and attempts remote report
            # (best-effort final push). Then disable remote URL so no further
            # network calls happen during interpreter teardown.
            self.save()
        except Exception:
            pass
        finally:
            global _TELEMETRY_SERVER_URL
            _TELEMETRY_SERVER_URL = ""

    def _load_unsent_queue(self):
        """启动时加载上次未成功上报的远程数据。"""
        if _DO_NOT_TRACK or not _TELEMETRY_SERVER_URL:
            return
        try:
            p = self._unsent_queue_path
            if not p.exists():
                return
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return
            loaded_daily = data.get("daily", {})
            loaded_records = data.get("records", [])
            if loaded_daily:
                with self._lock:
                    for day_key, day_val in loaded_daily.items():
                        if day_key not in self._unsent_daily:
                            self._unsent_daily[day_key] = day_val
                        else:
                            _merge_day_stats(self._unsent_daily[day_key], day_val)
                    self._unsent_records.extend(loaded_records)
                    if len(self._unsent_records) > 200:
                        self._unsent_records = self._unsent_records[-200:]
                logger.debug(f"Token tracker: loaded {len(loaded_daily)} days of unsent telemetry from disk")
            # 加载成功后删除文件，避免下次重复加载
            p.unlink(missing_ok=True)
        except Exception as e:
            logger.debug(f"Token tracker: failed to load unsent queue: {e}")

    def _save_unsent_queue(self):
        """将当前未发送的远程数据持久化到磁盘。

        调用时机：
        1. save() 成功后，如果有 unsent 数据等待远程上报
        2. atexit 兜底时（通过 save → _report_to_server → 失败 → 持久化）
        """
        if _DO_NOT_TRACK or not _TELEMETRY_SERVER_URL:
            return
        try:
            with self._lock:
                if not self._unsent_daily:
                    # 无数据，清理残留文件
                    self._unsent_queue_path.unlink(missing_ok=True)
                    return
                data = {
                    "daily": copy.deepcopy(self._unsent_daily),
                    "records": list(self._unsent_records[-200:]),
                    "saved_at": time.time(),
                }
            atomic_write_json(self._unsent_queue_path, data)
        except Exception as e:
            logger.debug(f"Token tracker: failed to persist unsent queue: {e}")

    # ---- 旧版文件迁移 ----

    def _migrate_legacy_files(self):
        """将旧版 token_usage_{instance_id}.json 文件合并到新的单文件中。

        只在首次实例化时执行一次。迁移完成后删除旧文件。
        """
        try:
            legacy_files = list(self._storage_dir.glob("token_usage_*.json"))
            if not legacy_files:
                return

            logger.info(f"Token tracker: migrating {len(legacy_files)} legacy per-instance files")

            with _file_lock(self._lock_file_path):
                # 读取现有的合并文件（如果已存在）
                existing = self._load_file(self._storage_path)
                if not existing:
                    existing = self._empty_file_data()

                for p in legacy_files:
                    try:
                        data = self._load_file(p)
                        if data:
                            for day_key, day_val in data.get("daily_stats", {}).items():
                                if day_key not in existing["daily_stats"]:
                                    existing["daily_stats"][day_key] = day_val
                                else:
                                    _merge_day_stats(existing["daily_stats"][day_key], day_val)
                            existing["recent_records"].extend(data.get("recent_records", []))
                        # 迁移完毕，删除旧文件
                        p.unlink(missing_ok=True)
                    except Exception as e:
                        logger.debug(f"Token tracker: failed to migrate {p.name}: {e}")

                # 去重 recent_records
                existing["recent_records"] = self._dedupe_records(existing["recent_records"])
                existing["last_saved"] = datetime.now().isoformat()

                self._storage_dir.mkdir(parents=True, exist_ok=True)
                atomic_write_json(self._storage_path, existing)

            logger.info("Token tracker: legacy file migration complete")
        except Exception as e:
            logger.warning(f"Token tracker: legacy migration failed (non-critical): {e}")

    # ---- 记录 ----

    def record(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        cached_tokens: int = 0,
        call_type: str = "unknown",
        source: str = "",
        success: bool = True,
        prompt_chars: int = 0,
    ):
        """记录一次 LLM 调用的 token 用量。线程安全。

        数据先写入内存中的 delta，由 periodic save 定期落盘。

        Args:
            prompt_tokens: 总 prompt tokens（含 cached 部分）
            completion_tokens: 生成 tokens
            total_tokens: prompt + completion
            cached_tokens: prompt 中被缓存命中的部分（OpenAI prompt_tokens_details.cached_tokens）
            prompt_chars: 字符计费 SKU 的输入字符数。Use this for TTS / ASR /
                embedding-by-char endpoints whose pricing unit is characters,
                not tokens — keeps the token aggregates clean.
        """
        model = model or "unknown"
        prompt_tokens = prompt_tokens or 0
        completion_tokens = completion_tokens or 0
        total_tokens = total_tokens or 0
        cached_tokens = cached_tokens or 0
        prompt_chars = prompt_chars or 0

        today = date.today().isoformat()

        rec = {
            "ts": time.time(),
            "model": model,
            "pt": prompt_tokens,
            "ct": completion_tokens,
            "tt": total_tokens,
            "cch": cached_tokens,
            "pch": prompt_chars,
            "type": call_type,
            "src": source,
            "ok": success,
        }

        with self._lock:
            if today not in self._delta_daily:
                self._delta_daily[today] = self._empty_day()

            day = self._delta_daily[today]
            day["total_prompt_tokens"] += prompt_tokens
            day["total_completion_tokens"] += completion_tokens
            day["total_tokens"] += total_tokens
            day["cached_tokens"] += cached_tokens
            day["total_prompt_chars"] += prompt_chars
            day["call_count"] += 1
            if not success:
                day["error_count"] += 1

            # by_model
            bm = day["by_model"]
            if model not in bm:
                bm[model] = self._empty_bucket()
            b = bm[model]
            b["prompt_tokens"] += prompt_tokens
            b["completion_tokens"] += completion_tokens
            b["total_tokens"] += total_tokens
            b["cached_tokens"] += cached_tokens
            b["prompt_chars"] += prompt_chars
            b["call_count"] += 1

            # by_call_type
            bt = day["by_call_type"]
            if call_type not in bt:
                bt[call_type] = self._empty_bucket()
            c = bt[call_type]
            c["prompt_tokens"] += prompt_tokens
            c["completion_tokens"] += completion_tokens
            c["total_tokens"] += total_tokens
            c["cached_tokens"] += cached_tokens
            c["prompt_chars"] += prompt_chars
            c["call_count"] += 1

            self._delta_records.append(rec)
            self._dirty = True

    # ---- 查询 ----

    def get_stats(self, days: int = 7) -> dict:
        """返回最近 N 天的用量统计。

        读取磁盘文件 + 合并内存中尚未落盘的 delta，不做任何文件修改。
        """
        # 读磁盘（atomic_write_json 保证文件一致性，无需文件锁）
        disk_data = self._load_file(self._storage_path)
        if not disk_data:
            disk_data = self._empty_file_data()

        merged_daily = disk_data.get("daily_stats", {})
        all_records = disk_data.get("recent_records", [])

        # 合并内存中未落盘的 delta
        with self._lock:
            for day_key, day_delta in self._delta_daily.items():
                if day_key not in merged_daily:
                    merged_daily[day_key] = _deep_copy_day(day_delta)
                else:
                    _merge_day_stats(merged_daily[day_key], day_delta)
            all_records = all_records + list(self._delta_records)

        # 按 days 过滤
        today = date.today()
        daily = {}
        for i in range(days):
            d = (today - timedelta(days=i)).isoformat()
            if d in merged_daily:
                daily[d] = merged_daily[d]

        # 去重 recent_records
        unique_records = self._dedupe_records(all_records)

        return {
            "daily_stats": daily,
            "recent_records": unique_records[-20:],
        }

    def get_today_stats(self) -> dict:
        """返回今日用量统计。"""
        disk_data = self._load_file(self._storage_path)
        if not disk_data:
            disk_data = self._empty_file_data()

        today = date.today().isoformat()
        merged = disk_data.get("daily_stats", {}).get(today, self._empty_day())

        # 合并内存 delta
        with self._lock:
            if today in self._delta_daily:
                _merge_day_stats(merged, self._delta_daily[today])

        return {"date": today, "stats": merged}

    def record_app_start(self):
        """记录客户端启动事件（app_start）。

        用于统计 DAU，与 LLM 调用分开计数。
        保证在单次进程生命周期内只上报一次（线程安全）。
        """
        with self._lock:
            if self._has_recorded_app_start:
                return
            self._has_recorded_app_start = True

        self.record(
            model="app_start",
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            cached_tokens=0,
            call_type="app_start",
            source="",
            success=True,
        )

    # ---- 持久化 ----

    def save(self):
        """持久化增量数据到磁盘。多进程安全。

        流程：
        1. 线程锁内取出 delta 快照并清空（swap 模式）
        2. 文件锁内做 read-merge-write
        3. 如果写入失败，将 delta 放回内存
        """
        with self._lock:
            if not self._dirty:
                return
            # 取出 delta（swap 模式：先取出，成功后不放回）
            delta_daily = self._delta_daily
            delta_records = list(self._delta_records)
            self._delta_daily = {}
            self._delta_records.clear()
            self._dirty = False

        try:
            self._storage_dir.mkdir(parents=True, exist_ok=True)

            with _file_lock(self._lock_file_path):
                # 读取现有数据
                existing = self._load_file(self._storage_path)
                if not existing:
                    existing = self._empty_file_data()

                # 合并 delta 到 existing
                for day_key, day_delta in delta_daily.items():
                    if day_key not in existing["daily_stats"]:
                        existing["daily_stats"][day_key] = day_delta
                    else:
                        _merge_day_stats(existing["daily_stats"][day_key], day_delta)

                # 合并 recent_records
                existing["recent_records"].extend(delta_records)
                existing["recent_records"] = self._dedupe_records(existing["recent_records"])

                # 清理 90 天前的旧数据
                cutoff = (date.today() - timedelta(days=90)).isoformat()
                old_keys = [k for k in existing["daily_stats"] if k < cutoff]
                for k in old_keys:
                    del existing["daily_stats"][k]

                existing["last_saved"] = datetime.now().isoformat()
                atomic_write_json(self._storage_path, existing)

            # 本地保存成功后，尝试远程上报（在文件锁外，避免阻塞其他进程）
            try:
                self._report_to_server(delta_daily, delta_records)
            except Exception:
                pass  # 远程上报失败不影响本地保存，静默忽略

        except Exception as e:
            logger.warning(f"Failed to save token usage data: {e}")
            # 写入失败，将 delta 放回内存，下次重试
            with self._lock:
                for day_key, day_delta in delta_daily.items():
                    if day_key not in self._delta_daily:
                        self._delta_daily[day_key] = day_delta
                    else:
                        _merge_day_stats(self._delta_daily[day_key], day_delta)
                # 恢复 records（旧的在前，新的在后）
                restored = delta_records + list(self._delta_records)
                self._delta_records.clear()
                self._delta_records.extend(restored[-200:])
                self._dirty = True

    # ---- 远程遥测上报 ----

    def _report_to_server(self, delta_daily: dict, delta_records: list):
        """将增量数据上报到远程遥测服务器。

        防丢数据设计：
        - _unsent_daily 累积在内存中，同时持久化到 .telemetry_unsent.json
        - 进程被 kill 后重启时，_load_unsent_queue() 恢复未发送数据
        - 发送成功后清除 unsent 队列文件
        - 发送失败后放回内存 + 持久化，下次重试
        """
        if _DO_NOT_TRACK or not _TELEMETRY_SERVER_URL:
            return

        # 累积 unsent 数据
        with self._lock:
            for day_key, day_delta in delta_daily.items():
                if day_key not in self._unsent_daily:
                    self._unsent_daily[day_key] = copy.deepcopy(day_delta)
                else:
                    _merge_day_stats(self._unsent_daily[day_key], day_delta)
            self._unsent_records.extend(delta_records)
            if len(self._unsent_records) > 200:
                self._unsent_records = self._unsent_records[-200:]

        # 持久化 unsent 队列（防 kill 丢数据）
        self._save_unsent_queue()

        # 检查上报间隔
        now = time.time()
        if now - self._last_report_time < self._report_interval:
            return

        # 取出待发送数据
        with self._lock:
            if not self._unsent_daily:
                return
            send_daily = self._unsent_daily
            send_records = self._unsent_records
            self._unsent_daily = {}
            self._unsent_records = []

        try:
            if not self._device_id:
                self._device_id = _get_anonymous_device_id()
            if not self._branch:
                self._branch = _get_telemetry_branch(self._config_manager.config_dir)

            app_version = _get_app_version_from_changelog()
            telemetry_locale = _get_telemetry_locale()
            telemetry_timezone = _get_telemetry_timezone()
            telemetry_distribution = _get_telemetry_distribution()
            telemetry_steam_user_id = _get_telemetry_steam_user_id()

            payload = {
                "device_id": self._device_id,
                # 迁移期同时带旧算法 ID，便于 server 在 events.payload 里
                # 留底，将来可建 legacy→new 映射 fold 历史 cohort。server
                # 当前 Pydantic model 不声明此字段，会被默认 ignore；HMAC
                # 签名是基于完整 payload dict 的 canonical JSON 计算的，所以
                # server 端验签会自动覆盖到，不需要任何调整。
                "device_id_legacy": _get_legacy_device_id(),
                "app_version": app_version,
                "branch": self._branch,
                "locale": telemetry_locale,
                "timezone": telemetry_timezone,
                "distribution": telemetry_distribution,
                # 仅在 Steamworks SDK 起来 + 拿到 Steam64 时填值，其它情况为
                # 空 string。server 端按 preserve-known 处理：空值不覆写历史。
                "steam_user_id": telemetry_steam_user_id,
                "daily_stats": send_daily,
                "recent_records": send_records,
            }
            payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)

            ts = time.time()
            sig = _compute_telemetry_signature(payload_json, ts)

            # batch_id 用于 server seen_batches 幂等去重，必须在"同一份重试数据"
            # 上稳定。device_id_legacy 依赖 uuid.getnode()，在多网卡机器上枚举
            # 顺序不保证，重试期间可能漂；如果把它纳入 batch_id 计算，原本应该
            # 被 dedupe 的重发会变成新 batch 被累加，daily_aggregates 双倍计数。
            # 因此 batch_id 只覆盖核心幂等字段，签名仍覆盖完整 payload。
            batch_core = {
                "device_id": payload["device_id"],
                "app_version": payload["app_version"],
                "daily_stats": payload["daily_stats"],
                "recent_records": payload["recent_records"],
            }
            batch_id = hashlib.sha256(
                json.dumps(batch_core, ensure_ascii=False, sort_keys=True).encode()
            ).hexdigest()[:32]
            submission = {
                "timestamp": ts,
                "signature": sig,
                "payload": payload,
                "batch_id": batch_id,
            }
            body = json.dumps(submission, ensure_ascii=False).encode("utf-8")

            req = urllib.request.Request(
                f"{_TELEMETRY_SERVER_URL}/api/v1/telemetry",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=_TELEMETRY_TIMEOUT) as resp:
                if resp.status == 200:
                    self._last_report_time = now
                    # 发送成功，删除 unsent 队列文件
                    self._unsent_queue_path.unlink(missing_ok=True)
                    logger.debug("Token tracker: telemetry reported successfully")
                    return

            raise Exception(f"HTTP {resp.status}")

        except Exception as e:
            logger.debug(f"Token tracker: telemetry report failed (non-critical): {e}")
            # 发送失败，放回 unsent 数据 + 持久化
            with self._lock:
                for day_key, day_delta in send_daily.items():
                    if day_key not in self._unsent_daily:
                        self._unsent_daily[day_key] = day_delta
                    else:
                        _merge_day_stats(self._unsent_daily[day_key], day_delta)
                restored = send_records + self._unsent_records
                self._unsent_records = restored[-200:]
            self._save_unsent_queue()

    @staticmethod
    def _load_file(path: Path) -> dict:
        """从文件加载数据，返回空 dict 表示文件无效或不存在。"""
        try:
            if path.exists():
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and data.get("version") == 1:
                    return data
        except Exception:
            pass
        return {}

    # ---- 定时保存 ----

    def start_periodic_save(self):
        """启动后台定时保存任务。需在 asyncio loop 内调用。"""
        if self._save_task is None or self._save_task.done():
            self._save_task = asyncio.create_task(self._periodic_save_loop())
            logger.info("Token tracker periodic save started")

    async def _periodic_save_loop(self):
        while True:
            await asyncio.sleep(self._save_interval)
            if self._dirty:
                await asyncio.to_thread(self.save)

    # ---- helpers ----

    @staticmethod
    def _empty_day() -> dict:
        return {
            "total_prompt_tokens": 0,
            "total_completion_tokens": 0,
            "total_tokens": 0,
            "cached_tokens": 0,
            "total_prompt_chars": 0,
            "call_count": 0,
            "error_count": 0,
            "by_model": {},
            "by_call_type": {},
        }

    @staticmethod
    def _empty_bucket() -> dict:
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                "cached_tokens": 0, "prompt_chars": 0, "call_count": 0}

    @staticmethod
    def _empty_file_data() -> dict:
        return {"version": 1, "daily_stats": {}, "recent_records": [], "last_saved": ""}

    @staticmethod
    def _dedupe_records(records: list, max_keep: int = 200) -> list:
        """对 recent_records 去重 + 排序 + 截断。"""
        seen = set()
        unique = []
        for r in records:
            key = (r.get("ts"), r.get("model"), r.get("type"), r.get("src"))
            if key not in seen:
                seen.add(key)
                unique.append(r)
        unique.sort(key=lambda x: x.get("ts", 0))
        return unique[-max_keep:]


# ---------------------------------------------------------------------------
# OpenAI SDK Monkey-patch
# ---------------------------------------------------------------------------

# Streaming 不兼容 stream_options 的 base_url 缓存
_stream_options_blocklist: set = set()
_blocklist_lock = threading.Lock()


def _get_base_url(self_obj) -> str:
    """从 OpenAI client 实例提取 base_url。"""
    try:
        # self_obj 是 Completions / AsyncCompletions，其 _client 是 OpenAI / AsyncOpenAI
        client = getattr(self_obj, '_client', None)
        if client is None:
            return ""
        base_url = getattr(client, 'base_url', None)
        if base_url is None:
            return ""
        return str(base_url).rstrip('/')
    except Exception:
        return ""


def _usage_to_dict(usage) -> dict:
    """将 usage 对象统一转为 dict，确保所有字段（含 provider 自定义字段）都能被检索到。

    OpenAI SDK 用 Pydantic model 解析 usage，非标准字段（如阶跃的 cached_tokens）
    在 v2 中藏在 model_extra 里，在 v1 中可能被丢弃但留在 __dict__ 中。
    """
    if isinstance(usage, dict):
        return usage

    d = {}

    # Pydantic v2: model_dump() 不含 extra fields，需要合并 model_extra
    if hasattr(usage, 'model_dump'):
        try:
            d = usage.model_dump()
        except Exception:
            d = {}
        # model_extra 包含 Pydantic model 不认识的额外字段（如 Step 的 cached_tokens）
        extra = getattr(usage, 'model_extra', None)
        if extra and isinstance(extra, dict):
            d.update(extra)
    # Pydantic v1: .dict()
    elif hasattr(usage, 'dict'):
        try:
            d = usage.dict()
        except Exception:
            d = {}

    # 兜底：__dict__ 可能包含更多字段
    if hasattr(usage, '__dict__'):
        for k, v in usage.__dict__.items():
            if not k.startswith('_') and k not in d:
                d[k] = v

    return d


# 所有已知的 cached_tokens 字段名（各 provider）
_CACHED_TOKEN_FIELDS = (
    'cached_tokens',                # Step（阶跃星辰）: usage.cached_tokens
    'cache_read_input_tokens',      # Anthropic Claude
    'prompt_cache_hit_tokens',      # 部分国产 provider
    'cached_content_token_count',   # Google PaLM/旧版 Gemini
    'cache_tokens',                 # 其他变体
)

# 可能包含 cached_tokens 的嵌套字段
_NESTED_DETAIL_FIELDS = (
    'prompt_tokens_details',        # OpenAI 官方
    'details',                      # 通用
    'token_details',                # 通用
    'prompt_details',               # 通用
)


def _extract_cached_tokens(usage_dict: dict) -> int:
    """从 usage dict 中提取 cached_tokens，兼容多种 provider 格式。

    已知格式：
    1. OpenAI 官方: usage.prompt_tokens_details.cached_tokens
    2. 阶跃星辰 (Step): usage.cached_tokens（顶层）
    3. Gemini/其他: 可能在嵌套结构中
    """
    # 1) 检查嵌套结构（如 OpenAI 的 prompt_tokens_details.cached_tokens）
    for nested_key in _NESTED_DETAIL_FIELDS:
        nested = usage_dict.get(nested_key)
        if not nested:
            continue
        # 可能是 Pydantic 对象或 dict
        if not isinstance(nested, dict):
            nested = _usage_to_dict(nested)
        for field in _CACHED_TOKEN_FIELDS:
            val = nested.get(field)
            if val:
                return int(val)

    # 2) 顶层直接有 cached_tokens（如阶跃星辰）
    for field in _CACHED_TOKEN_FIELDS:
        val = usage_dict.get(field)
        if val:
            return int(val)

    return 0


def calculate_cache_hit_rate(prompt_tokens: int, cached_tokens: int) -> float:
    """计算缓存命中率。

    Args:
        prompt_tokens: 总 prompt tokens（含缓存命中和未命中）
        cached_tokens: 缓存命中的 tokens

    Returns:
        缓存命中率，范围 0.0 ~ 1.0
        如果 prompt_tokens 为 0，返回 0.0

    Example:
        >>> calculate_cache_hit_rate(2911, 2888)
        0.9920989350738585
    """
    if prompt_tokens <= 0:
        return 0.0
    cached_tokens = max(0, min(cached_tokens, prompt_tokens))
    return cached_tokens / prompt_tokens


def _record_usage_from_response(response, call_type: str):
    """从 OpenAI SDK response 提取 usage 并记录。

    提取字段：
    - usage.prompt_tokens: 总 prompt tokens（含 cached）
    - usage.completion_tokens: 生成 tokens
    - usage.total_tokens: 总计
    - usage.prompt_tokens_details.cached_tokens: prompt 缓存命中部分
    """
    try:
        if not hasattr(response, 'usage') or response.usage is None:
            return
        usage = response.usage
        model = getattr(response, 'model', None) or "unknown"

        # 把 usage 转成 dict，统一后续查找（兼容 Pydantic v1/v2 和原生 dict）
        usage_dict = _usage_to_dict(usage)

        # 调试：记录完整 usage 结构
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"Token tracker: usage for model={model}: {usage_dict}")

        cached_tokens = _extract_cached_tokens(usage_dict)

        TokenTracker.get_instance().record(
            model=model,
            prompt_tokens=usage_dict.get('prompt_tokens', 0) or 0,
            completion_tokens=usage_dict.get('completion_tokens', 0) or 0,
            total_tokens=usage_dict.get('total_tokens', 0) or 0,
            cached_tokens=cached_tokens,
            call_type=call_type,
        )
    except Exception:
        pass


def _should_inject_stream_options(base_url: str) -> bool:
    """检查该 base_url 是否在 blocklist 中。"""
    if not base_url:
        return True
    with _blocklist_lock:
        return base_url not in _stream_options_blocklist


def _add_to_blocklist(base_url: str):
    """将不支持 stream_options 的 base_url 加入 blocklist。"""
    if base_url:
        with _blocklist_lock:
            _stream_options_blocklist.add(base_url)
        logger.info(f"Token tracker: added base_url to stream_options blocklist: {base_url[:60]}...")


def install_hooks():
    """
    安装 OpenAI SDK monkey-patch，自动追踪所有 chat.completions.create 调用的 token 用量。
    同时覆盖 LangChain 底层调用（因为 LangChain ChatOpenAI 底层调用 OpenAI SDK）。
    """
    try:
        from openai.resources.chat.completions import Completions, AsyncCompletions
    except ImportError:
        logger.warning("Token tracker: openai package not found, hooks not installed")
        return

    _original_create = Completions.create
    _original_async_create = AsyncCompletions.create

    @functools.wraps(_original_create)
    def patched_create(self, *args, **kwargs):
        call_type = _current_call_type.get('unknown')
        is_stream = kwargs.get('stream', False)

        if is_stream:
            return _handle_sync_stream(self, _original_create, args, kwargs, call_type)

        try:
            result = _original_create(self, *args, **kwargs)
            _record_usage_from_response(result, call_type)
            return result
        except Exception as e:
            TokenTracker.get_instance().record(
                model=kwargs.get('model', 'unknown'),
                prompt_tokens=0, completion_tokens=0, total_tokens=0,
                call_type=call_type, success=False,
            )
            raise

    @functools.wraps(_original_async_create)
    async def patched_async_create(self, *args, **kwargs):
        call_type = _current_call_type.get('unknown')
        is_stream = kwargs.get('stream', False)

        if is_stream:
            return await _handle_async_stream(self, _original_async_create, args, kwargs, call_type)

        try:
            result = await _original_async_create(self, *args, **kwargs)
            _record_usage_from_response(result, call_type)
            return result
        except Exception as e:
            TokenTracker.get_instance().record(
                model=kwargs.get('model', 'unknown'),
                prompt_tokens=0, completion_tokens=0, total_tokens=0,
                call_type=call_type, success=False,
            )
            raise

    Completions.create = patched_create
    AsyncCompletions.create = patched_async_create
    logger.info("Token tracker: OpenAI SDK hooks installed")


# ---------------------------------------------------------------------------
# Streaming wrappers
# ---------------------------------------------------------------------------

def _handle_sync_stream(self_obj, original_fn, args, kwargs, call_type):
    """处理同步 streaming 调用：注入 stream_options + wrap Stream。"""
    base_url = _get_base_url(self_obj)
    injected = False

    # 尝试注入 stream_options
    if _should_inject_stream_options(base_url) and 'stream_options' not in kwargs:
        kwargs['stream_options'] = {"include_usage": True}
        injected = True

    try:
        result = original_fn(self_obj, *args, **kwargs)
        return _SyncStreamWrapper(result, call_type)
    except Exception as e:
        if injected:
            # stream_options 导致报错，去掉后重试
            _add_to_blocklist(base_url)
            kwargs.pop('stream_options', None)
            try:
                result = original_fn(self_obj, *args, **kwargs)
                return _SyncStreamWrapper(result, call_type)
            except Exception:
                TokenTracker.get_instance().record(
                    model=kwargs.get('model', 'unknown'),
                    prompt_tokens=0, completion_tokens=0, total_tokens=0,
                    call_type=call_type, success=False,
                )
                raise
        TokenTracker.get_instance().record(
            model=kwargs.get('model', 'unknown'),
            prompt_tokens=0, completion_tokens=0, total_tokens=0,
            call_type=call_type, success=False,
        )
        raise


async def _handle_async_stream(self_obj, original_fn, args, kwargs, call_type):
    """处理异步 streaming 调用：注入 stream_options + wrap AsyncStream。"""
    base_url = _get_base_url(self_obj)
    injected = False

    if _should_inject_stream_options(base_url) and 'stream_options' not in kwargs:
        kwargs['stream_options'] = {"include_usage": True}
        injected = True

    try:
        result = await original_fn(self_obj, *args, **kwargs)
        return _AsyncStreamWrapper(result, call_type)
    except Exception as e:
        if injected:
            _add_to_blocklist(base_url)
            kwargs.pop('stream_options', None)
            try:
                result = await original_fn(self_obj, *args, **kwargs)
                return _AsyncStreamWrapper(result, call_type)
            except Exception:
                TokenTracker.get_instance().record(
                    model=kwargs.get('model', 'unknown'),
                    prompt_tokens=0, completion_tokens=0, total_tokens=0,
                    call_type=call_type, success=False,
                )
                raise
        TokenTracker.get_instance().record(
            model=kwargs.get('model', 'unknown'),
            prompt_tokens=0, completion_tokens=0, total_tokens=0,
            call_type=call_type, success=False,
        )
        raise


class _SyncStreamWrapper:
    """Wrap 同步 Stream，在迭代结束后提取 usage。

    关键：只在流结束后记录一次（取最后一个带 usage 的 chunk）。
    部分 OpenAI 兼容 API（阶跃、通义等）在每个 chunk 都返回累计 usage，
    如果每个 chunk 都记录就会导致严重的重复计数。
    """

    def __init__(self, stream, call_type: str):
        self._stream = stream
        self._call_type = call_type

    def __iter__(self):
        last_usage_chunk = None
        for chunk in self._stream:
            if hasattr(chunk, 'usage') and chunk.usage is not None:
                last_usage_chunk = chunk
            yield chunk
        # 流结束后，只记录最后一个带 usage 的 chunk
        if last_usage_chunk is not None:
            _record_usage_from_response(last_usage_chunk, self._call_type)

    def __getattr__(self, name):
        return getattr(self._stream, name)

    def __enter__(self):
        if hasattr(self._stream, '__enter__'):
            self._stream.__enter__()
        return self

    def __exit__(self, *args):
        if hasattr(self._stream, '__exit__'):
            return self._stream.__exit__(*args)


class _AsyncStreamWrapper:
    """Wrap 异步 AsyncStream，在迭代结束后提取 usage。

    同 _SyncStreamWrapper：只在流结束后记录一次。
    """

    def __init__(self, stream, call_type: str):
        self._stream = stream
        self._call_type = call_type

    def __aiter__(self):
        return self._aiter_and_track()

    async def _aiter_and_track(self):
        last_usage_chunk = None
        async for chunk in self._stream:
            if hasattr(chunk, 'usage') and chunk.usage is not None:
                last_usage_chunk = chunk
            yield chunk
        # 流结束后，只记录最后一个带 usage 的 chunk
        if last_usage_chunk is not None:
            _record_usage_from_response(last_usage_chunk, self._call_type)

    def __getattr__(self, name):
        return getattr(self._stream, name)

    async def __aenter__(self):
        if hasattr(self._stream, '__aenter__'):
            await self._stream.__aenter__()
        return self

    async def __aexit__(self, *args):
        if hasattr(self._stream, '__aexit__'):
            return await self._stream.__aexit__(*args)
