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
"""Anonymous telemetry metadata and rollout-bucket helpers."""

import hashlib
import os
import secrets
from datetime import datetime
from pathlib import Path
from typing import Optional
from utils.config_manager import get_config_manager

from ._shared import logger

def _get_app_version_from_changelog() -> str:
    """Read the highest version number from the config/changelog/ directory as the current app version."""
    changelog_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "config", "changelog"
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

    install_salt = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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

_TELEMETRY_BRANCH_FILE = ".telemetry_branch"

_TELEMETRY_BRANCHES: tuple = ("main",)

_telemetry_branch_cache: dict = {}

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
        # UI-language discovery is optional telemetry and may be unavailable.
        pass
    try:
        import locale as _locale
        sys_locale = _locale.getlocale()[0]
        if sys_locale:
            return str(sys_locale)[:32]
    except Exception:
        # System locale lookup is a best-effort fallback on minimal hosts.
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
                # Workshop subscription probing is optional evidence only.
                pass
    except Exception:
        # Steamworks can be unavailable while the application is still usable.
        pass

    # 磁盘兜底：之前任何一次会话写过 workshop_config.json 即证明跑过 Steam
    # 版，即使本次 Steam 客户端没开（cloudsave 会把它带走）。
    try:
        from utils.config_manager import get_config_manager
        cm = get_config_manager()
        if (cm.config_dir / "workshop_config.json").exists():
            return "steam", ""
    except Exception:
        # Missing or inaccessible workshop config simply means no disk signal.
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
        # tzlocal is optional; the UTC-offset fallback below remains available.
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
        # Timezone metadata must not interfere with telemetry collection.
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
