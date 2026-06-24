# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Global LLM token usage tracking module

Monkey-patches the OpenAI SDK's chat.completions.create (sync + async) to
automatically intercept usage data of all LLM calls (including LangChain's
underlying calls). Uses ContextVar to tag call types, ensuring Nuitka/PyInstaller
compatibility.

Usage:
    from utils.token_tracker import TokenTracker, install_hooks, llm_call_context

    # install hooks at startup
    install_hooks()
    TokenTracker.get_instance().start_periodic_save()

    # tag call_type at the calling module
    with llm_call_context("conversation"):
        async for chunk in llm.astream(messages):
            ...
"""
import atexit
import asyncio
import copy
import functools
import gzip
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
    """Context manager that tags the current LLM call type within the block."""
    token = _current_call_type.set(call_type)
    try:
        yield
    finally:
        _current_call_type.reset(token)


def set_call_type(call_type: str):
    """Simply set the current call type (for scenarios where wrapping is inconvenient)."""
    _current_call_type.set(call_type)


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _deep_copy_day(day: dict) -> dict:
    """Deep-copy one day's statistics."""
    return copy.deepcopy(day)


def _merge_day_stats(target: dict, source: dict):
    """Accumulate source's statistics into target (modifying target in place)."""
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
    """File-system based cross-process mutex.

    Atomically creates the lock file with O_CREAT | O_EXCL, ensuring only one process
    holds the lock at a time. PID + timestamp are written into the lock file for
    stale-lock detection after timeouts.
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

# 上报间隔（60 秒）
# 节流设计：
#   record() → 即时写入内存（零 I/O）
#   save()   → 每 60s 本地落盘，然后调用 _report_to_server()
#   _report_to_server() → 仅当距上次上报 ≥ 60s 时才真正发 HTTP
#   所以每个进程最多每 1 分钟发一次请求。3 个 server 进程 = 180 req/h/device。
_TELEMETRY_REPORT_INTERVAL = 60

# 上报超时
_TELEMETRY_TIMEOUT = 10  # 秒

# Gzip 上报阈值：< 1KB 的 payload 不压缩。gzip 头 + CRC 有 ~20B 固定开销，
# 小 payload 压缩比往往 < 2x，不值得。典型 daily_stats payload 5-50KB raw，
# gzip 后通常压到 1/5-1/10。服务端 v2 起支持 Content-Encoding: gzip；老服
# 务端不解析就直接 415，故首次发布要 server 先升级再开客户端 gzip。
_TELEMETRY_GZIP_THRESHOLD = 1024


def _get_app_version_from_changelog() -> str:
    """Read the highest version number from the config/changelog/ directory as the current app version."""
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
    """Sanity-check the OS machine ID, preventing placeholder values or un-reset cloned-image
    IDs from folding multiple machines into the same device_id.

    Requires exactly 32 hex digits after stripping GUID separators, and absence from the
    known placeholder blacklist. On failure, callers should fall back to the legacy
    algorithm instead of using the invalid value as a fingerprint.
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
    """Read the OS-level stable machine identifier.

    - Windows: HKLM\\SOFTWARE\\Microsoft\\Cryptography\\MachineGuid
    - macOS:   IOPlatformUUID (ioreg -rd1 -c IOPlatformExpertDevice)
    - Linux:   /etc/machine-id or /var/lib/dbus/machine-id

    These IDs are generated at system installation and bound to the motherboard/system
    rather than network config, so they don't drift with NIC changes (VPN / Docker /
    external NIC) or install path changes (Steam library migration, source/packaged
    switching).

    Each source's return value passes the _is_valid_machine_id sanity check, so
    placeholders (systemd `uninitialized`, all-zero/all-F GUIDs) are not taken as valid
    fingerprints. Returns None on read failure or failed validation; callers must fall
    back to the legacy algorithm.
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
    """Legacy device_id algorithm (kept for migration-period folding).

    SHA256(uuid.getnode() | install_dir | "neko-telemetry"). getnode is unstable on
    multi-NIC machines (VPN / Docker / external NIC enumeration order changes), and
    install_dir changes with install location, so this ID "drifts" easily and long-term
    retention data gets shattered. The new version keeps it only so the server can fold
    historical data: the client reports both old and new IDs in the payload, and the
    server can later build a mapping via the device_id_legacy field of the events table.
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
    """Generate a stable anonymous device fingerprint.

    Prefers the OS-level stable identifier (_read_os_machine_id), falling back to the
    legacy algorithm on failure so an empty value is never written. The result is a
    64-char hex SHA256, irreversible, no PII.

    The namespace differs from the legacy algorithm via "neko-telemetry-v2", ensuring
    old and new IDs never collide in hash space.

    Following vLLM: generate the anonymous ID from hardware/system info only, no user PII.
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
#             扩展 _TELEMETRY_BRANCHES 元组即可触发 split，新用户随机进新池。注意：
#             从池里移除某分支会让落盘旧值被严格校验判非法、按当前池重抽迁组（见
#             privacy_default_off_v1 退役说明），这是退役实验的有意行为，不是
#             append-only 扩展场景。
#   - locale / timezone：每次上报时取当下值；同一设备换语言/换时区都视为同
#             一个 device_id，server 端按 "latest seen" 覆写即可。
# ---------------------------------------------------------------------------

_TELEMETRY_BRANCH_FILE = ".telemetry_branch"
# A/B 池（只决定「首启默认值」实验分组；首启后用户行为已落盘、不再响应覆写，其分组
# 归因对默认值实验无意义，分析端按真·首启样本过滤即可）：
#   - "main"：当前唯一在跑的分支（控制组）。主动搭话里的「屏幕分享来源」
#     （proactiveVisionChatEnabled）首启默认开；隐私模式仍按地区分流。原
#     vision_chat_default_off 实验组的情境弹窗机制（进游戏/娱乐弹「要不要开屏幕分享
#     搭话」、进专注工作弹「要不要关屏幕分享避嫌」）已合并进 main，对所有用户生效、
#     不再受 branch 限制。
#
# 已退役实验（老落盘值被 _read 严格校验判非法 → 下次启动按当前池随机重抽，落 main。
# 都是已过首启的用户，重抽只改 telemetry 标签、不动已落盘的用户偏好，对「默认值」实
# 验无影响，故（除特别注明者外）不为其单独做确定性迁移）：
#   - "vision_chat_default_off"（试屏幕分享来源首启默认关 + 情境弹窗）：情境弹窗机制
#     效果保留、整合进 main 对所有人开放；屏幕分享来源默认值则回到控制组「开」。仅改
#     默认值、**不**对存量实验组做偏好回滚——已落盘成关的用户保持关，除非自己再打开。
#   - "privacy_default_off_v1"（试国外隐私默认关）：前期数据效果差，已下线。
#   - "privacy_default_off_v2"（试国内隐私默认开）：改方向去测屏幕分享来源，已下线。
#   - "proactive_interval_25s"（试海外搭话间隔 20s→25s）：数据点没能通过 A/A 测试，
#     曾下线回退到 proactive_interval_20s 重测；现整条搭话间隔实验线终止（见下条），
#     不再重新上线。
#   - "proactive_interval_20s"（试海外搭话间隔 15s→20s）：CN 本应是 AA no-op 对照，
#     但 D1 留存出现低于 main 的偏离（单边 p≈0.1，且主要由单个放量日 cohort 驱动）。
#     AA 组刷出「显著」本身说明噪声地板已超过判定门槛、该实验线欠功率；权衡后决定不再
#     投入，直接下线、不重测。前端的间隔默认值覆写逻辑（app-settings.js）同步移除。
#     与上面「通则不做确定性迁移」不同，本条是例外：对存量额外做一次性偏好回滚——落盘
#     branch 正是本实验组的 install，在「判非法 → 重抽」时把仍停在 20s 的
#     proactiveChatInterval 拉回控制组 15s（见 _rollback_retired_proactive_interval；
#     重抽即天然幂等标记，不压制用户日后再手选 20s）。
_TELEMETRY_BRANCHES: tuple = ("main",)

# 进程级缓存：keyed by str(config_dir)。写盘失败的环境下（只读 FS / 权限拒绝），
# 不缓存就每次 secrets.choice 重抽，导致同一 install 的 TokenTracker 上报和
# 前端 `/conversation-settings` 拿到不同分支，A/B 归因被打散。dict.setdefault
# 在 CPython GIL 下是原子的，足以扛住模块内的并发首抽。
_telemetry_branch_cache: dict = {}

# 退役实验 proactive_interval_20s 的一次性偏好回滚常量。该实验曾把海外用户首启的
# proactiveChatInterval 默认从控制组 15s 覆写成 20s（见上方退役清单）。下线后既要让
# 落盘的 20s 回到 15s，又不能误伤自己手动拖到 20s 的用户——而能精确区分两者的唯一
# 信号，就是这台机器的 .telemetry_branch 是否曾经正是该实验组。
_RETIRED_INTERVAL_ROLLBACK_BRANCH = "proactive_interval_20s"
_RETIRED_INTERVAL_EXPERIMENT_VALUE = 20
_CONTROL_PROACTIVE_INTERVAL = 15


def _rollback_retired_proactive_interval(branch_path: Path) -> None:
    """One-shot rollback of proactiveChatInterval overridden by the retired experiment proactive_interval_20s.

    Acts only when the raw value persisted at ``branch_path`` is exactly that retired
    experiment branch — narrowing the rollback surface to installs that were ever drawn
    into the experiment pool, not touching ordinary users who manually chose 20s and
    never entered the experiment.

    Known collateral (impossible to eliminate from persisted data): the draw was random
    across all regions, so CN users also landed in this branch — the frontend gate
    (!_isUserRegionChina()) just never overrode their interval. So in the CN experiment
    cohort, interval==20 must be a manual choice — this function cannot distinguish it
    from "overseas, overridden to 20" and rolls both back to 15s. This falls within the
    accepted scope of "manual 20s at migration time gets clobbered" (intersection scale:
    CN ∩ landed in this branch ∩ manually picked exactly 20s ∩ still sitting at 20s —
    tiny). Backend region gating was weighed, but the backend locale notion mismatches
    the frontend tz+lang one and could instead hurt CN users on English systems, so it
    was not introduced.

    Idempotency is guaranteed by the call site: this function only fires on the slow
    path "persisted branch invalid, about to redraw and overwrite"; after the redraw the
    branch file is no longer the retired value, the next startup hits the fast path with
    a valid value and never re-enters — no extra migration flag needed.

    best-effort: when the preferences write fails (e.g. cloudsave maintenance state),
    skip silently without blocking branch resolution; the branch still gets redrawn and
    that install misses this rollback (rare, acceptable).
    """
    try:
        raw = branch_path.read_text(encoding="utf-8").strip()
    except OSError:
        return
    if raw != _RETIRED_INTERVAL_ROLLBACK_BRANCH:
        return
    try:
        from utils.preferences import (
            load_global_conversation_settings,
            save_global_conversation_settings,
        )

        settings = load_global_conversation_settings()
        interval = settings.get("proactiveChatInterval")
        # 只回滚仍停在实验覆写值（20s）的；用户首启后又手动改过（!=20）的保留其选择。
        if interval == _RETIRED_INTERVAL_EXPERIMENT_VALUE:
            save_global_conversation_settings(
                {"proactiveChatInterval": _CONTROL_PROACTIVE_INTERVAL}
            )
            logger.info(
                "Telemetry: rolled back retired proactive_interval_20s default "
                "(proactiveChatInterval 20s -> %ss)",
                _CONTROL_PROACTIVE_INTERVAL,
            )
        else:
            # 实验组 install 但偏好已不是 20s：用户改过值 / 国内本就没被覆写（停在 15s）/
            # 偏好读取失败返回 {} → None。打出实际值方便排查「这台为何没回滚」（None 多半
            # 是缺 key 或读取失败，具体数值是用户手选）。
            logger.debug(
                "Telemetry: retired proactive_interval_20s install skipped rollback "
                "(proactiveChatInterval=%r)",
                interval,
            )
    except Exception as e:
        logger.debug(f"Token tracker: proactive_interval rollback skipped (error): {e}")


def _get_telemetry_branch(config_dir: Path) -> str:
    """Read or draw the A/B test branch identifier, persisted under config_dir.

    Multi-process cold-start safe: atomic creation with ``O_CREAT | O_EXCL`` guarantees
    only one process can write; other concurrent processes get FileExistsError and read
    back the same file, ensuring device-stable cohorting (different workers on the same
    device never land in different branches). Same pattern as _file_lock's implementation.

    Process-level cache: the first resolve lands in `_telemetry_branch_cache`; later
    calls hit it directly. Mainly a fallback for environments where persistence fails
    (read-only FS / permission errors) — without this cache, with multiple cohorts every
    `secrets.choice` would redraw, and different callers within one process would
    observe different branches.
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
        # 严格校验：活跃分支都在 _TELEMETRY_BRANCHES 里，所以正常情况下不会误杀。
        # 唯一例外是退役实验（如 privacy_default_off_v1）——它被有意移出池，落盘旧值
        # 在这里判非法、触发按当前池重抽（见上方退役说明），正是「让老实验群退出原
        # 分支」的预期路径。
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
        # 修盘前：若旧值是会覆写用户偏好的退役实验（proactive_interval_20s），趁这次
        # 「判非法 → 重抽」做一次性偏好回滚（见函数 docstring；重抽即天然幂等标记）。
        _rollback_retired_proactive_interval(p)
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
    """Externally exposed A/B test branch read entry.

    `_get_telemetry_branch` is the internal implementation (parameterized config_dir for
    testing); this function takes config_dir from the global config_manager and forwards.
    After the frontend fetches the branch via the API it can pick default behavior by
    branch on first launch, consistent with the branch the token tracker itself reports.
    """
    return _get_telemetry_branch(get_config_manager().config_dir)


def _get_telemetry_locale() -> str:
    """Get the user's UI locale (zh-CN / en-US / ja-JP …).

    Prefers language_utils.get_global_language_full — it checks Steam settings first
    then falls back to the system language, the codebase's ground truth for "the UI
    language the user actually uses". Falls back to stdlib locale on failure.
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
    """Whether we're packaged — PyInstaller (``sys.frozen``) or Nuitka (``__compiled__`` /
    ``__nuitka_binary_dir``). Both packagers must be recognized: PyInstaller goes
    through the spec chain, Nuitka through the build_nuitka.bat chain."""
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


def _get_telemetry_metadata() -> tuple[str, str]:
    """Return ``(distribution, steam_user_id)`` at once; both fields share one source and one observation point.

    Merged from the original ``_get_telemetry_distribution()`` and
    ``_get_telemetry_steam_user_id()``: Steamworks ``Users.GetSteamID()`` is **called
    only once**, with distribution and steam_user_id derived from the same observation.
    Originally each function called ``GetSteamID()`` once; with the Steamworks SDK's
    async init the two calls could straddle the ready boundary — the first returning 0
    (distribution goes ``release``), the second returning a Steam64 (steam_user_id
    obtained), producing the contradictory ``release + non-empty Steam64`` state. The
    merge eliminates that state at the source.

    **Invariant**: a non-empty returned steam_user_id ⟹ distribution == ``steam``.
    (The converse doesn't hold: steam + empty ID is a legal tail, see rule 3.)

    Decision order (following the original logic):
    1. non-release build → ``("source", "")``. A source run counts as source even with
       the Steam client open — only release can be the Steam edition.
    2. release + ``GetSteamID()`` returns a nonzero Steam64 → ``("steam", str(sid))``.
       Anchored to the first signal; distribution and ID share one observation.
    3. release + workshop subscriptions > 0 or ``workshop_config.json`` exists →
       ``("steam", "")``. Proves this machine has run the Steam edition (cloudsave
       packs workshop_config.json along), but this run got no logged-in user from
       the Steam client (not open / offline).
    4. release with no Steam signal at all → ``("release", "")``.

    Steam64 is reported as a string rather than an int, avoiding u64 (often > 2^53)
    precision loss in JS / some JSON consumers. All exceptions are swallowed —
    instrumentation must not throw.
    """
    if not _is_release_build():
        return "source", ""

    # 实时探测：GetSteamID() 只调一次，结果同时决定 distribution 和
    # steam_user_id —— 这是修复 race 的核心，不再分两次调用跨越 ready 边界。
    try:
        from utils.steam_state import get_steamworks
        sw = get_steamworks()
        if sw is not None:
            sid = 0
            try:
                sid = int(sw.Users.GetSteamID() or 0)
            except Exception:
                sid = 0
            if sid > 0:
                return "steam", str(sid)
            # 没拿到登录用户，但订阅过工坊也算 Steam 版（steam + 空 ID）。
            try:
                if int(sw.Workshop.GetNumSubscribedItems() or 0) > 0:
                    return "steam", ""
            except Exception:
                pass
    except Exception:
        pass

    # 磁盘兜底：之前任何一次会话写过 workshop_config.json 即证明跑过 Steam
    # 版，即使本次 Steam 客户端没开（cloudsave 会把它带走）。
    try:
        from utils.config_manager import get_config_manager
        cm = get_config_manager()
        if (cm.config_dir / "workshop_config.json").exists():
            return "steam", ""
    except Exception:
        pass

    return "release", ""


def _get_telemetry_timezone() -> str:
    """Get the local timezone. Prefers IANA (Asia/Shanghai), falling back to a UTC offset (+08:00)."""
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


_DEVICE_HW_CACHE: Optional[str] = None


def _get_device_hw() -> str:
    """Device hardware profile (a low-cardinality enum composite string), computed once per process.

    Shaped like ``win|x86_64|16to32|9to16`` (os|arch|ram_tier|cpu_tier). Reported as a
    **device attribute** (not a count) on the devices table, used to JOIN retention for
    "first-day churn rate on low-end devices" — distinguishing "left because it can't
    run" from "left because they didn't like it".

    All dimensions are bucketed enums; **raw values are never sent** (RAM bytes / GPU
    model / machine name) — keeping dims low-cardinality + zero PII (same as #1426 T3).

    Detection is fully inline (psutil / platform / os): no importing memory.embeddings —
    that would trip module-layering's utils(L1)→memory(L2) inversion + create a
    memory↔utils cycle (check_module_layering counts lazy in-function imports too). RAM
    detection is a psutil one-liner anyway with no reuse value; the genuinely reusable
    CPU AVX/VNNI cpuid detection is a second-order signal for "can't-run churn" (most
    users run remote LLMs), so it's not collected for now — if wanted, extract the
    detection into a shared utils-level util. Any failed dimension falls back to
    'unknown'; the whole thing never throws (instrumentation must not block reporting).
    """
    global _DEVICE_HW_CACHE
    if _DEVICE_HW_CACHE is not None:
        return _DEVICE_HW_CACHE
    import platform as _plat

    sysname = (_plat.system() or "").lower()
    os_tag = {"windows": "win", "darwin": "mac", "linux": "linux"}.get(sysname, "other")

    mach = (_plat.machine() or "").lower()
    if mach in ("x86_64", "amd64", "x64"):
        arch = "x86_64"
    elif mach in ("arm64", "aarch64"):
        arch = "arm64"
    else:
        arch = "other"

    try:
        import psutil
        gb = psutil.virtual_memory().total / (1024 ** 3)
        ram_tag = ("lt8" if gb < 8 else "8to16" if gb < 16
                   else "16to32" if gb < 32 else "ge32")
    except Exception:
        ram_tag = "unknown"  # psutil 缺失/异常：降级 unknown，埋点不能挡上报

    try:
        n = os.cpu_count() or 0
        cpu_tag = ("unknown" if n <= 0 else "le4" if n <= 4 else "5to8" if n <= 8
                   else "9to16" if n <= 16 else "gt16")
    except Exception:
        cpu_tag = "unknown"  # cpu_count 异常：降级 unknown，不抛

    _DEVICE_HW_CACHE = f"{os_tag}|{arch}|{ram_tag}|{cpu_tag}"
    return _DEVICE_HW_CACHE


def _compute_telemetry_signature(payload_json: str, timestamp: float) -> str:
    """Compute the HMAC-SHA256 signature for telemetry reporting."""
    body_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    message = f"{timestamp}|{body_hash}"
    return hmac.new(
        _TELEMETRY_HMAC_SECRET.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


# ---------------------------------------------------------------------------
# 主动搭话 / 隐私模式设置快照埋点
# ---------------------------------------------------------------------------

def _bucket_proactive_interval(seconds) -> str:
    """Bucket proactiveChatInterval (1-3600 s) into a low-cardinality enum.

    **Raw seconds are not reported** — that's a continuous value; putting it in a dim
    explodes metric_key cardinality (same lesson as lanlan_name before). 5 buckets
    cover the typical configuration range.
    """
    try:
        s = float(seconds)
    except (TypeError, ValueError):
        return "unknown"
    if s < 10:
        return "<10s"
    if s < 30:
        return "10-30s"
    if s < 60:
        return "30-60s"
    if s < 300:
        return "60-300s"
    return ">=300s"


def record_settings_state() -> None:
    """Read the current proactive-chat / privacy-mode settings and emit one settings_state counter.

    Trigger timing: **only** at app start (record_app_start, main_server process only).

    Semantics (finalized after CodeRabbit feedback): the server-side instrument_counters
    UPSERTs cumulatively by (stat_date, device_id, metric_key). This function only emits
    at startup, so one record = "the user's settings combo at this launch". N launches
    per device per day add +N — an **observation count**, not a gauge-style "current
    final state" — but good enough for analyzing "which tiers heavy users habitually
    use": per device, the combo with the highest count is its habitual tier.

    Deliberately **not** instrumented in save_global_conversation_settings: that would
    +1 a new combo every time a user flips a setting within a day, polluting the
    distribution into a "switching trajectory". Precise per-device-per-day final state
    would need server-side gauge/overwrite semantics, which the current instrument
    pipeline doesn't support and this analysis doesn't require.

    Use: the server slices out heavy users by device active days / event_count, then
    looks at the distribution of their settings_state dim combos — i.e. "where do heavy
    users set proactive chat / privacy mode".

    Dims are all low-cardinality enums; interval is bucketed, raw seconds not sent:
    - proactive: on / off (proactiveChatEnabled)
    - interval: <10s / 10-30s / ... / >=300s ("off" when off)
    - vision_chat: on / off (proactiveVisionChatEnabled)
    - privacy: on / off (privacy mode = inverse of proactiveVisionEnabled, default off)
    """
    if _DO_NOT_TRACK:
        return
    try:
        from utils.preferences import load_global_conversation_settings
        from utils.instrument import counter as _c
        s = load_global_conversation_settings()
        proactive_on = bool(s.get("proactiveChatEnabled", False))
        _c(
            "settings_state", 1,
            proactive="on" if proactive_on else "off",
            interval=(_bucket_proactive_interval(s.get("proactiveChatInterval", 0))
                      if proactive_on else "off"),
            vision_chat="on" if s.get("proactiveVisionChatEnabled", False) else "off",
            # 隐私模式 = proactiveVisionEnabled 的反面（default True → 默认隐私关）
            privacy="on" if not s.get("proactiveVisionEnabled", True) else "off",
        )
    except Exception:
        # 埋点失败不影响业务，静默
        pass


# ---------------------------------------------------------------------------
# TokenTracker 单例
# ---------------------------------------------------------------------------

class TokenTracker:
    """Thread-safe + multi-process-safe global LLM token usage tracker.

    Design:
    - all processes share a single token_usage.json file
    - memory only tracks the "not yet persisted increments" (delta)
    - save() does read-merge-write under a file lock, so multiple processes lose no data
    - get_stats() reads disk + merges the in-memory delta, never deleting any file
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
        # batch_seq：当前正在上报或重传中的窗口标识。新窗口首次进入 _report_to_server
        # 时分配一次（secrets.token_hex），失败重传时保留同一个值，让 server
        # seen_batches 能 dedupe "网络 timeout 但 server 已经 commit" 的重传。
        # 成功 200 后清空，下次窗口再分配新 seq。跟 _unsent_daily 一起持久化。
        self._pending_batch_seq: Optional[str] = None
        self._has_recorded_app_start: bool = False  # 🔒 app_start 单次上报锁
        self._session_start_ts: float = 0.0  # session_end 计算 duration 用
        self._session_process: str = "unknown"
        # 本 session 用户消息轮数。note_user_message 累加，record_app_start 重置，
        # _atexit_save(session_end) emit 成 session_turn_count histogram —— 含 0
        # 即"零消息会话"（开了 app 一句没聊就走），D1 流失最直接信号。
        self._session_msg_count: int = 0
        self._first_user_message_recorded: bool = False  # 🔒 首条用户消息单次锁
        self._core_loop_recorded: bool = False  # 🔒 首次完成核心 loop 单次锁

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
        """Persistence file for the unsent remote-reporting queue.

        _unsent_daily is lost when the process is killed (pure memory).
        Writing the queue to this file lets a restart recover and resend it.
        """
        return self._config_manager.config_dir / ".telemetry_unsent.json"

    # ---- atexit / unsent 持久化 ----

    def _atexit_save(self):
        """atexit safety net: a final best effort to save before process exit.

        Covers: SIGTERM / uncaught exceptions / normal exit / sys.exit()
        Not covered: SIGKILL (kill -9) / power loss — at most 60s of data lost then

        Ordering matters: first emit session_end into the instrument buffer, then
        save(). save() → _report_to_server snapshots through instrument, so the emit
        must happen first; otherwise session_end's counter/histogram enters the buffer
        but never gets snapshotted, and the remote dashboard never sees session_end —
        the paired session_start is visible while session_end is forever missing, so
        the dashboard's "abnormal exit rate" gets miscomputed as 100%. The event goes
        separately through event_logger.flush into local jsonl.
        """
        # global 声明提到函数开头：下面 3b 步骤会读 _TELEMETRY_SERVER_URL，
        # Python 要求 global 声明先于任何使用（否则 SyntaxError）。
        global _TELEMETRY_SERVER_URL
        # ── 1) session_end 先落 instrument buffer，让随后的 save() 带上 ──
        try:
            from utils.instrument import (
                event as _instr_event,
                counter as _instr_counter,
                histogram as _instr_histogram,
            )
            duration = (time.time() - self._session_start_ts) if self._session_start_ts > 0 else 0.0
            _instr_event(
                "session_end",
                process=self._session_process,
                duration_sec=round(duration, 1),
            )
            _instr_counter("session_end", process=self._session_process)
            if duration > 0:
                # 直接传秒；instrument bounds 是数字通用，没绑定单位
                _instr_histogram("session_duration_sec", duration, process=self._session_process)
            # 本 session 用户消息轮数（无条件 emit，含 0）——0 即零消息会话。
            # 配合 session_duration_sec 看：短时长+0 轮 = 开了就走；长时长+0 轮 =
            # 挂着没互动。是 D1 浅尝 vs 上瘾的核心区分。
            _instr_histogram("session_turn_count", self._session_msg_count, process=self._session_process)
        except Exception:
            # instrument import / emit 失败不能让进程退出卡住 —— 实在丢一条
            # 也比 atexit 抛出强（atexit 异常会让 SIGTERM 退出码变化）。
            pass

        # ── 2) Bypass 60s throttle —— atexit 是最后机会，错过没下次 ──
        # _report_to_server 内部 ``now - self._last_report_time < interval``
        # 在短 session（启动后不到 60s 就退出）下会阻止上报，让刚 emit 的
        # session_end counter / histogram 永远留在 instrument buffer。这里
        # 显式归零让那条 if 一定不命中。会带来一个理论副作用：如果 atexit
        # 之前距上次成功上报 < 60s，这次再发一份；server seen_batches 靠
        # batch_seq dedupe，所以不会双倍计数。
        with self._lock:
            self._last_report_time = 0.0

        # ── 3) save() 把 daily_stats + 上面刚 emit 的 instrument snapshot 一起发 ──
        try:
            # save() first: persists delta to disk and attempts remote report
            # (best-effort final push). Then disable remote URL so no further
            # network calls happen during interpreter teardown.
            self.save()
        except Exception:
            # save 失败不抛进 atexit（同上）。失败时 unsent 已经被持久化，
            # 下次进程启动会重传。
            pass

        # ── 3b) 若第一次 save 是「重传」（进程带着早先失败遗留的 _pending_batch_seq），
        # _report_to_server 会按 is_retry 跳过 instrument snapshot，刚 emit 的
        # session_end / session_duration_sec 仍留在 buffer。常见"网络早先挂、
        # 退出前恢复"场景下重传会成功并清掉 batch_seq，但没有第二次发送，
        # session 指标就在关 URL 前静默丢了（Codex）。所以这里检查：instrument
        # 还有数据 + batch_seq 已清（说明重传成功、下次是 fresh 窗口会 snapshot）
        # → 再 bypass throttle 发一次。
        try:
            from utils.instrument import has_data as _instrument_has_data
            if (_instrument_has_data() and self._pending_batch_seq is None
                    and _TELEMETRY_SERVER_URL and not _DO_NOT_TRACK):
                with self._lock:
                    self._last_report_time = 0.0
                self.save()
        except Exception:
            pass

        # ── 4) flush event_logger —— event 不走远程 instrument 通道，本地 jsonl 兜底 ──
        try:
            from utils.event_logger import EventLogger
            EventLogger.get_instance().flush()
        except Exception:
            # event_logger flush 失败丢的是本地 jsonl 的稀疏事件，下次启动
            # 没有恢复路径 —— 但 counter/histogram 已经走 instrument 通道
            # 发出去了，这里失败影响的只是诊断细节，不阻塞 atexit。
            pass
        finally:
            _TELEMETRY_SERVER_URL = ""

    def _load_unsent_queue(self):
        """Load remote data that previously failed to report, at startup."""
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
            loaded_batch_seq = data.get("batch_seq")
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
                    # 恢复 batch_seq：进程上次没发出去的窗口，重启后下次上报
                    # 仍用同一 seq，让 server seen_batches 能 dedupe 那次的
                    # 不确定成败（client 进程被 kill 时 server 可能已 commit）。
                    if isinstance(loaded_batch_seq, str) and loaded_batch_seq:
                        self._pending_batch_seq = loaded_batch_seq
                logger.debug(f"Token tracker: loaded {len(loaded_daily)} days of unsent telemetry from disk")
            # 加载成功后删除文件，避免下次重复加载
            p.unlink(missing_ok=True)
        except Exception as e:
            logger.debug(f"Token tracker: failed to load unsent queue: {e}")

    def _save_unsent_queue(self):
        """Persist currently unsent remote data to disk.

        When called:
        1. after a successful save(), if unsent data awaits remote reporting
        2. at the atexit safety net (via save → _report_to_server → failure → persist)

        batch_seq is persisted along: failure + process crash + restart → the retry
        uses the same seq, letting server seen_batches dedupe commits of uncertain
        outcome.
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
                    "batch_seq": self._pending_batch_seq,
                    "saved_at": time.time(),
                }
            atomic_write_json(self._unsent_queue_path, data)
        except Exception as e:
            logger.debug(f"Token tracker: failed to persist unsent queue: {e}")

    # ---- 旧版文件迁移 ----

    def _migrate_legacy_files(self):
        """Merge legacy token_usage_{instance_id}.json files into the new single file.

        Runs only once at first instantiation. Old files are deleted after migration.
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
        """Record token usage of one LLM call. Thread-safe.

        Data first goes into the in-memory delta, persisted by the periodic save.

        Args:
            prompt_tokens: total prompt tokens (including the cached part)
            completion_tokens: generated tokens
            total_tokens: prompt + completion
            cached_tokens: the cache-hit part of the prompt (OpenAI prompt_tokens_details.cached_tokens)
            prompt_chars: input characters for char-billed SKUs. Use this for TTS / ASR /
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
        """Return usage statistics for the last N days.

        Reads the disk file + merges the in-memory not-yet-persisted delta; modifies nothing.
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
        """Return today's usage statistics."""
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

    def record_app_start(self, process: str = "main_server"):
        """Record the client startup event (app_start).

        Used for DAU statistics, counted separately from LLM calls.
        Guaranteed to report only once per process lifetime (thread-safe).

        Besides the old ``record(call_type='app_start')`` path (the dashboard's
        by_call_type still uses it), also emits an instrument event ``session_start``
        and stashes the start time on self so _atexit_save can compute session_end's
        duration.
        """
        with self._lock:
            if self._has_recorded_app_start:
                return
            self._has_recorded_app_start = True
            self._session_start_ts = time.time()
            self._session_process = process
            self._session_msg_count = 0  # 新 session 起点，轮数清零

        # 新埋点：sparse event 走本地 events.jsonl（诊断），同时打 counter
        # 走远程聚合通道（dashboard 看 DAU / session 总数）。event 因为带
        # context 字段、暂未集成进远程上报；counter 是聚合数字、走 60s 通道。
        try:
            from utils.instrument import event as _instr_event, counter as _instr_counter
            _instr_event("session_start", process=process)
            _instr_counter("session_start", process=process)
        except Exception:
            # 埋点失败不能挡 app 启动 —— 老 record() 路径下面已经跑过，
            # DAU 仍能从 by_call_type='app_start' 统计出来，不会丢用户。
            pass

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

        # 启动快照：当前主动搭话 / 隐私设置。只在 main_server 打，避免多进程
        # （agent/memory_server 也跑 record_app_start）把同一份设置重复计 3 次。
        # settings 是 user-facing 概念，跟 main 进程绑定最自然。
        if process == "main_server":
            record_settings_state()

    def note_first_user_message(self, input_type: str = "text"):
        """Record the user's first message within this process (a key D1-funnel milestone).

        Recorded once per process lifetime (thread-safe). Distinguishes two kinds of D1 churn:
        - first_message_sent counter: the user actually "spoke" — absence = installed,
          opened, left without speaking (onboarding / configuration obstacles)
        - time_to_first_message_sec histogram: time from app start to first message.
          Stuck on config / character choice / mic permission = long; seconds = smooth onboarding

        input_type: text / voice (low-cardinality)
        """
        with self._lock:
            if self._first_user_message_recorded:
                return
            self._first_user_message_recorded = True
            anchor = self._session_start_ts

        try:
            from utils.instrument import counter as _c, histogram as _h
            _c("first_message_sent", input_type=input_type)
            if anchor > 0:
                _h("time_to_first_message_sec", max(0.0, time.time() - anchor))
        except Exception:
            # 埋点失败不能挡用户消息处理
            pass

    def note_user_message(self, input_type: str = "text"):
        """Called for every user message (unlike note_first_user_message, which records only the first).

        - emits the ``user_message_sent`` counter (input_type dim): sum = total chat
          turns; sliced by input_type = voice/text modality share
        - accumulates this session's turn count ``_session_msg_count``; at session_end
          emits the ``session_turn_count`` histogram (including 0 = zero-message session)

        Callers must ensure each real user message calls this exactly once (see core.py:
        only at the text-side on_user_message entry and the true voice-message point,
        avoiding the openclaw handoff reuse path).
        input_type: text / voice (low-cardinality)
        """
        with self._lock:
            self._session_msg_count += 1
        try:
            from utils.instrument import counter as _c
            _c("user_message_sent", input_type=input_type)
        except Exception:
            # 埋点失败不能挡用户消息处理
            pass

    def note_core_loop_completed(self):
        """The user completed one core-experience loop: send message → get reply → hear speech. Recorded once per process.

        Counted only after the user has sent a message (_first_user_message_recorded=True) —
        purely proactive-triggered speech doesn't count as "the user actively experienced
        the core loop".

        The key distinguishing signal for D1 churn analysis:
        - has first_message_sent but no core_loop_completed = the user spoke but never
          heard a reply (stuck on LLM failure / TTS failure / too slow) →
          first-experience-obstacle churn
        - has core_loop_completed = fully experienced the product core; later churn is
          more likely "tried it, didn't like it" (a product-value problem) — entirely
          different operational responses for the two kinds
        """
        with self._lock:
            if self._core_loop_recorded:
                return
            if not self._first_user_message_recorded:
                return  # 用户还没开口，不算用户发起的核心 loop
            self._core_loop_recorded = True

        try:
            from utils.instrument import counter as _c
            _c("core_loop_completed")
        except Exception:
            # 埋点 best-effort；前面已置位 _core_loop_recorded，丢一次计数
            # 不影响幂等，也不该影响调用方（音频投递路径）。
            pass

    def has_completed_core_loop(self) -> bool:
        """Whether the user has completed a core-experience loop in this process (send → reply → speech).

        For error instrumentation sites to set the ``before_first_loop`` dimension:
        False = the error happened before the user experienced the product core =
        first-experience-obstacle churn (most worth saving); True = an error after
        experiencing the core, where churn is more likely a product-value problem.
        Different operational responses.
        """
        return self._core_loop_recorded

    # ---- 持久化 ----

    def save(self):
        """Persist incremental data to disk. Multi-process safe.

        Flow:
        1. take a delta snapshot and clear it inside the thread lock (swap pattern)
        2. read-merge-write inside the file lock
        3. on write failure, put the delta back into memory

        Not-dirty must still trigger remote reporting: for users with pure frontend
        interaction (counter/histogram, no LLM calls), self._dirty stays False forever;
        the old logic's early return kept the instrument accumulation window from ever
        being sent. Skip the local disk write but still call _report_to_server, letting
        it decide internally via has_data() whether to actually POST.
        """
        with self._lock:
            if not self._dirty:
                report_only = True
                delta_daily: dict = {}
                delta_records: list = []
            else:
                report_only = False
                # 取出 delta（swap 模式：先取出，成功后不放回）
                delta_daily = self._delta_daily
                delta_records = list(self._delta_records)
                self._delta_daily = {}
                self._delta_records.clear()
                self._dirty = False

        if report_only:
            # 没 LLM 数据写盘，只问问 instrument 有没有要发的
            try:
                self._report_to_server(delta_daily, delta_records)
            except Exception:
                # 远程失败不影响 idle path —— 已经没本地数据要写，错误就是
                # 纯网络的，下次 60s 周期或 atexit 会再试。_report_to_server
                # 自己内部已有失败 unsent 持久化逻辑，这里不重复打日志。
                pass
            return

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
        """Report incremental data to the remote telemetry server.

        Data-loss prevention design:
        - _unsent_daily accumulates in memory while also persisted to .telemetry_unsent.json
        - after the process is killed and restarted, _load_unsent_queue() recovers unsent data
        - the unsent queue file is cleared after a successful send
        - on send failure, put back into memory + persisted, retried next time
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

        # peek instrument 累积 —— 即使 daily_stats 是空的（用户没触发 LLM 调
        # 用，但有前端互动 counter），只要 instrument 里有东西，就值得发一次。
        try:
            from utils.instrument import has_data as _instrument_has_data
            has_instruments = _instrument_has_data()
        except Exception:
            has_instruments = False

        # 取出待发送数据。同时区分两种状态：
        #   is_retry=False：新窗口，分配新 batch_seq，正常带 instrument snapshot
        #   is_retry=True ：上次失败遗留下来的重传，复用同一 batch_seq，**不**
        #                   附带任何新 instrument —— retry 的 batch_id 已经
        #                   在 server seen_batches 里，整个 batch 会被 dedupe
        #                   返回 duplicate，跟进去的 instrument 会被一起静默
        #                   丢掉。把新 instrument 留在 buffer，下个新窗口
        #                   （新 batch_seq）单独发出去。
        with self._lock:
            if not self._unsent_daily and not has_instruments:
                return
            send_daily = self._unsent_daily
            send_records = self._unsent_records
            self._unsent_daily = {}
            self._unsent_records = []
            is_retry = self._pending_batch_seq is not None
            if self._pending_batch_seq is None:
                self._pending_batch_seq = secrets.token_hex(8)
            batch_seq = self._pending_batch_seq
        # 标记这次发送是否带 daily/records —— instrument-only 失败后清
        # stale batch_seq 时要用（见 except 路径注释）。
        had_unsent_payload = bool(send_daily or send_records)

        # 仅新窗口才 snapshot instrument。重传时跳过保留 buffer 等下窗口。
        instruments_snapshot: dict = {}
        if not is_retry:
            try:
                from utils.instrument import snapshot as _instrument_snapshot
                instruments_snapshot = _instrument_snapshot()
            except Exception as e:
                logger.debug(f"Token tracker: instrument snapshot failed (non-critical): {e}")

        try:
            if not self._device_id:
                self._device_id = _get_anonymous_device_id()
            if not self._branch:
                self._branch = _get_telemetry_branch(self._config_manager.config_dir)

            app_version = _get_app_version_from_changelog()
            telemetry_locale = _get_telemetry_locale()
            telemetry_timezone = _get_telemetry_timezone()
            # 一次调用同时拿 distribution + steam_user_id，两个字段同源 ——
            # 杜绝原本两次独立 GetSteamID() 跨 SDK ready 边界产生的
            # release + 非空 Steam64 矛盾态。
            telemetry_distribution, telemetry_steam_user_id = _get_telemetry_metadata()
            telemetry_device_hw = _get_device_hw()

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
                # 设备硬件画像（低基数 enum 复合串）。设备属性，server preserve-known
                # UPSERT；用来 JOIN 留存做"低配设备首日流失"分析。
                "device_hw": telemetry_device_hw,
                "daily_stats": send_daily,
                "recent_records": send_records,
            }
            # instrument snapshot 走 optional 字段：老 server 不识别会 ignore，
            # 新 server 原样存进 events.payload，dashboard 端可后续解析。HMAC
            # 签名覆盖整个 payload dict，所以加字段不影响验签。
            if instruments_snapshot:
                payload["instruments"] = instruments_snapshot
            payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)

            ts = time.time()
            sig = _compute_telemetry_signature(payload_json, ts)

            # batch_id 用于 server seen_batches 幂等去重，必须满足两个目标：
            #   (a) 失败重传 / 网络 timeout（server commit 了 client 没收到 200）
            #       下次重发同一份 daily 时 batch_id 必须**不变**，让 server
            #       识别重复 commit 并跳过。
            #   (b) 不同窗口（含纯 instrument-only 窗口、daily 都空时）的
            #       batch_id 必须**唯一**，否则被前一窗口的 seen_batches dedupe
            #       误伤，后续 instrument 数据全丢。
            #
            # batch_seq 同时满足：进程内首次进入此窗口时分配新值，失败重传
            # （含进程 kill 后重启）保留同一 seq，成功 200 后清空。把 seq 放进
            # hash，daily / records / instruments 自己不需要进 hash —— 尤其
            # instruments 是 clear-on-read、不会在重传中复现，把它进 hash 反而
            # 破坏 (a)。
            #
            # batch_core **只用 retry-stable 字段**：device_id + batch_seq。
            # app_version 故意不进 —— 它在每次上报时实时读 changelog，重试之间
            # 若用户更新了 app，同一份 unsent batch 会算出不同 batch_id；
            # timeout-after-commit 后在新版本上重启重传就绕过 seen_batches、
            # 把已 commit 的 daily_stats 重复计（Codex P1）。device_id_legacy
            # 同理也不进（依赖 uuid.getnode()，多网卡枚举顺序不稳）。
            # batch_seq 已是 per-window 唯一 + 跨重试稳定，device_id 保证跨设备
            # 不撞，二者足够。签名 (HMAC) 仍覆盖完整 payload（含 app_version）。
            batch_core = {
                "device_id": payload["device_id"],
                "batch_seq": batch_seq,
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
            headers = {"Content-Type": "application/json"}

            # >= 1KB 才 gzip：小 payload 不划算（见 _TELEMETRY_GZIP_THRESHOLD 注释）。
            # mtime=0 让同一 body 总是产出相同压缩字节，便于 diff 调试和 fuzzing
            # 期不会因为时间戳差异看起来像两次上报。
            if len(body) >= _TELEMETRY_GZIP_THRESHOLD:
                body = gzip.compress(body, compresslevel=6, mtime=0)
                headers["Content-Encoding"] = "gzip"

            req = urllib.request.Request(
                f"{_TELEMETRY_SERVER_URL}/api/v1/telemetry",
                data=body,
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=_TELEMETRY_TIMEOUT) as resp:
                if resp.status == 200:
                    self._last_report_time = now
                    # 发送成功：清 batch_seq，下次窗口重新分配；清 unsent 文件。
                    with self._lock:
                        self._pending_batch_seq = None
                    self._unsent_queue_path.unlink(missing_ok=True)
                    logger.debug("Token tracker: telemetry reported successfully")
                    return

            raise Exception(f"HTTP {resp.status}")

        except Exception as e:
            logger.debug(f"Token tracker: telemetry report failed (non-critical): {e}")
            # 发送失败：放回 unsent + 持久化。daily-bearing 失败时**不清**
            # _pending_batch_seq —— 下次重试用同一 seq，让 server seen_batches
            # dedupe "网络 timeout 但 server 已经 commit" 的不确定成败重传。
            #
            # 但 instrument-only 失败（send_daily 和 send_records 都空，
            # had_unsent_payload=False）必须清 batch_seq：instruments 是
            # clear-on-read 没东西放回，留着 stale seq 会让**下一个新窗口**
            # 复用它算出与已 commit 的 batch_id 相同的 hash，server 直接
            # 返回 "duplicate, skipped"，新窗口的数据被静默丢弃。
            with self._lock:
                for day_key, day_delta in send_daily.items():
                    if day_key not in self._unsent_daily:
                        self._unsent_daily[day_key] = day_delta
                    else:
                        _merge_day_stats(self._unsent_daily[day_key], day_delta)
                restored = send_records + self._unsent_records
                self._unsent_records = restored[-200:]
                if not had_unsent_payload:
                    # 没有真要重传的内容 —— 防 stale seq 误伤下一窗口
                    self._pending_batch_seq = None
            self._save_unsent_queue()

    @staticmethod
    def _load_file(path: Path) -> dict:
        """Load data from the file; returns an empty dict when the file is invalid or missing."""
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
        """Start the background periodic save task. Must be called inside an asyncio loop."""
        if self._save_task is None or self._save_task.done():
            self._save_task = asyncio.create_task(self._periodic_save_loop())
            logger.info("Token tracker periodic save started")

    async def _periodic_save_loop(self):
        while True:
            await asyncio.sleep(self._save_interval)
            # 两种触发 save() 的条件：
            #   (a) self._dirty —— 有 LLM token delta 要本地写盘 + 远程上报
            #   (b) instrument has_data —— 纯前端互动（前端 ws telemetry /
            #       ws_connect / 各种 feature counter）不会让 _dirty=True，
            #       但 instrument 已经累积了一窗口需要 60s 节奏上报
            # save() 内部对 (b) 走 report-only path（跳过本地 write，只调
            # _report_to_server）；对 (a) 走完整 write + report。
            need_save = self._dirty
            if not need_save:
                try:
                    from utils.instrument import has_data as _instrument_has_data
                    need_save = _instrument_has_data()
                except Exception:
                    # has_data 在锁内只做 dict bool 检查，正常不会抛；
                    # import 失败 fall through，本轮跳过，下轮重试。
                    pass
            if need_save:
                await asyncio.to_thread(self.save)
            # 顺手让 event_logger 落地稀疏事件 buffer + 跑 retention 清理。
            # 即使本周期 token_tracker 没有 dirty，event_logger 也可能有
            # session/crash 之类的事件等着写 —— 不挂在 self._dirty 后面，避免
            # 纯前端互动（不触发 LLM 调用）的事件被一直憋在内存里。
            # event_logger.flush 自带节流（cleanup 5min 一次），nothing-to-do
            # 路径 ~微秒级。
            try:
                from utils.event_logger import EventLogger
                await asyncio.to_thread(EventLogger.get_instance().flush)
            except Exception as e:
                logger.debug(f"Token tracker: event_logger flush failed (non-critical): {e}")

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
        """Dedupe + sort + truncate recent_records."""
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

# install_hooks() 单次安装守卫（见 install_hooks 文档）
_hooks_install_lock = threading.Lock()


def _get_base_url(self_obj) -> str:
    """Extract base_url from an OpenAI client instance."""
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
    """Normalize the usage object into a dict so all fields (including provider-custom ones) can be retrieved.

    The OpenAI SDK parses usage with a Pydantic model; non-standard fields (like
    StepFun's cached_tokens) hide in model_extra in v2, and in v1 may be dropped but
    remain in __dict__.
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
    """Extract cached_tokens from the usage dict, compatible with multiple provider formats.

    Known formats:
    1. official OpenAI: usage.prompt_tokens_details.cached_tokens
    2. StepFun: usage.cached_tokens (top level)
    3. Gemini/others: possibly in nested structures
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
    """Compute the cache hit rate.

    Args:
        prompt_tokens: total prompt tokens (cache hits and misses included)
        cached_tokens: cache-hit tokens

    Returns:
        Cache hit rate in the range 0.0 ~ 1.0
        Returns 0.0 when prompt_tokens is 0

    Example:
        >>> calculate_cache_hit_rate(2911, 2888)
        0.9920989350738585
    """
    if prompt_tokens <= 0:
        return 0.0
    cached_tokens = max(0, min(cached_tokens, prompt_tokens))
    return cached_tokens / prompt_tokens


def _record_usage_from_response(response, call_type: str):
    """Extract usage from an OpenAI SDK response and record it.

    Extracted fields:
    - usage.prompt_tokens: total prompt tokens (including cached)
    - usage.completion_tokens: generated tokens
    - usage.total_tokens: total
    - usage.prompt_tokens_details.cached_tokens: the cache-hit part of the prompt
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


def record_anthropic_usage(model: str, usage, call_type: str | None = None):
    """Record usage returned by Anthropic Messages API calls.

    Anthropic reports ``input_tokens`` / ``output_tokens`` instead of the
    OpenAI SDK's ``prompt_tokens`` / ``completion_tokens`` names, so it cannot
    be observed by the OpenAI monkey-patch above.
    """
    try:
        usage_dict = _usage_to_dict(usage)
        if not usage_dict:
            return
        prompt_tokens = int(usage_dict.get('input_tokens') or usage_dict.get('prompt_tokens') or 0)
        completion_tokens = int(usage_dict.get('output_tokens') or usage_dict.get('completion_tokens') or 0)
        total_tokens = int(usage_dict.get('total_tokens') or (prompt_tokens + completion_tokens))
        cached_tokens = _extract_cached_tokens(usage_dict)
        TokenTracker.get_instance().record(
            model=model or "unknown",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cached_tokens=cached_tokens,
            call_type=call_type or _current_call_type.get('unknown'),
        )
    except Exception:
        pass


def _should_inject_stream_options(base_url: str) -> bool:
    """Check whether this base_url is in the blocklist."""
    if not base_url:
        return True
    with _blocklist_lock:
        return base_url not in _stream_options_blocklist


def _add_to_blocklist(base_url: str):
    """Add a base_url that doesn't support stream_options to the blocklist."""
    if base_url:
        with _blocklist_lock:
            _stream_options_blocklist.add(base_url)
        logger.info(f"Token tracker: added base_url to stream_options blocklist: {base_url[:60]}...")


def _install_crash_excepthook():
    """Install a global sys.excepthook that turns unhandled exceptions into crash events.

    Chain pattern: keeps the original hook (the system default prints the traceback to
    stderr), only prepending a telemetry layer. Existing logging / error display logic
    is untouched; we just take a note in passing.

    Idempotent: multiple installs take effect once (avoiding nested chains when both
    main_server and memory_server import this).
    """
    import sys
    if getattr(sys, "_neko_crash_hook_installed", False):
        return
    _orig_excepthook = sys.excepthook

    def _crash_excepthook(exc_type, exc_value, exc_tb):
        try:
            # KeyboardInterrupt 是用户主动 ctrl-c，不算 crash
            if not issubclass(exc_type, KeyboardInterrupt):
                import traceback as _tb
                import hashlib as _hl
                from utils.instrument import event as _e, counter as _c
                tb_text = "".join(_tb.format_exception(exc_type, exc_value, exc_tb))
                # traceback_hash 是 12 字符摘要：足以 dedupe 同源 crash，不
                # 反向还原 stack（隐私）。dashboard 看哪个 hash 最频繁即可。
                tb_hash = _hl.sha256(tb_text.encode("utf-8", errors="replace")).hexdigest()[:12]
                _e("crash", error_class=exc_type.__name__, traceback_hash=tb_hash)
                _c("crash", error_class=exc_type.__name__)
                # 强制 flush event_logger —— 进程接下来可能立刻 die，不 flush
                # 就丢了。flush 自身有 try/except 不会再抛。
                from utils.event_logger import EventLogger
                EventLogger.get_instance().flush()
        except Exception:
            # crash hook 自己绝不能 raise —— 否则原始 traceback 被它的异常
            # 替换，用户看不到真正 crash 在哪。telemetry 失败相比之下不值一提。
            pass
        # 让默认 hook 继续打 stack —— 不打断现有行为
        try:
            _orig_excepthook(exc_type, exc_value, exc_tb)
        except Exception:
            # 原 hook 自己崩了（罕见，比如 sys.stderr 已经被关）—— 这种情况
            # 我们没什么能做的，最多让进程退出，原 traceback 已经丢了。
            pass

    sys.excepthook = _crash_excepthook
    sys._neko_crash_hook_installed = True
    logger.info("Token tracker: crash excepthook installed")


def install_hooks():
    """
    Install the OpenAI SDK monkey-patch, automatically tracking token usage of all chat.completions.create calls.
    Also covers LangChain's underlying calls (LangChain ChatOpenAI calls the OpenAI SDK underneath).

    Along the way: installs sys.excepthook to catch unhandled exceptions as crash events.

    Idempotent: merged single-process mode (packaged / Steam edition, see the launcher's
    _run_merged) runs the main / memory / agent uvicorn apps in one process; all three
    apps' startup calls this function, patching the same process-level
    ``Completions.create``. Without the guard the wrappers would stack — every
    chat.completions call gets recorded multiple times, and hook-based call_types like
    conversation / emotion / proactive / galgame_options inflate exactly N-fold in
    telemetry (×3 measured live on the three-app Steam edition). tts /
    conversation_realtime / agent_cua, which book directly via ``TokenTracker.record()``,
    bypass the hook and are unaffected; app_start is held by the
    ``_has_recorded_app_start`` singleton lock — this guard is its dual on the hook side.
    """
    # crash hook 跟 openai 库无关，独立装；幂等。
    _install_crash_excepthook()

    try:
        from openai.resources.chat.completions import Completions, AsyncCompletions
    except ImportError:
        logger.warning("Token tracker: openai package not found, hooks not installed")
        return

    # 已装则直接返回（cheap path），避免叠加 wrapper。真正的安装走下面的双检锁。
    if getattr(Completions.create, "_neko_token_tracker_hooked", False):
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

    # 标记 wrapper，供幂等守卫识别"已装"。functools.wraps 不会复制这个自定义属性，
    # 所以原始 SDK 方法上不会有它，只有我们包过的才有。
    patched_create._neko_token_tracker_hooked = True
    patched_async_create._neko_token_tracker_hooked = True

    # 双检锁：合并模式下三个 startup 协程在同一 event loop 串行跑，cheap path 已能
    # 挡住；锁是为多线程初始化路径（agent / memory watchdog 线程）兜底，确保
    # "检测已装 → 赋值"这段不被并发穿插成叠加安装。
    with _hooks_install_lock:
        if getattr(Completions.create, "_neko_token_tracker_hooked", False):
            return
        Completions.create = patched_create
        AsyncCompletions.create = patched_async_create
    logger.info("Token tracker: OpenAI SDK hooks installed")


# ---------------------------------------------------------------------------
# Streaming wrappers
# ---------------------------------------------------------------------------

def _handle_sync_stream(self_obj, original_fn, args, kwargs, call_type):
    """Handle sync streaming calls: inject stream_options + wrap Stream."""
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
    """Handle async streaming calls: inject stream_options + wrap AsyncStream."""
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
    """Wrap a sync Stream, extracting usage after iteration completes.

    Key point: record only once after the stream ends (taking the last chunk carrying
    usage). Some OpenAI-compatible APIs (StepFun, Qwen, etc.) return cumulative usage
    in every chunk; recording every chunk would cause severe double counting.
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
    """Wrap an async AsyncStream, extracting usage after iteration completes.

    Same as _SyncStreamWrapper: record only once after the stream ends.
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
