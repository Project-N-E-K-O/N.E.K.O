# -*- coding: utf-8 -*-
"""
N.E.K.O. 统一启动器
启动所有服务器，等待它们准备就绪后启动主程序，并监控主程序状态
"""
import sys
import os
import io
import signal

# 强制 UTF-8 编码
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    
# 处理 PyInstaller 和 Nuitka 打包后的路径
if getattr(sys, 'frozen', False):
    # 运行在打包后的环境
    if hasattr(sys, '_MEIPASS'):
        # PyInstaller
        bundle_dir = sys._MEIPASS
    else:
        # Nuitka 或其他
        bundle_dir = os.path.dirname(os.path.abspath(__file__))
    
else:
    # 运行在正常 Python 环境
    bundle_dir = os.path.dirname(os.path.abspath(__file__))

sys.path.insert(0, bundle_dir)
os.chdir(bundle_dir)

import subprocess
import socket
import time
import threading
import itertools
import ctypes
import atexit
import signal
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Dict
from multiprocessing import Process, freeze_support, Event
from config import APP_NAME, MAIN_SERVER_PORT, MEMORY_SERVER_PORT, TOOL_SERVER_PORT
from utils.port_utils import (
    probe_neko_health,
    acquire_startup_lock,
    release_startup_lock,
    get_hyperv_excluded_ranges,
    is_port_in_excluded_range,
)

# 本次 launcher 启动的唯一标识
LAUNCH_ID = uuid.uuid4().hex
# 实例 ID：若父进程已设置则复用，否则生成新值，确保所有子进程共享同一实例标识
INSTANCE_ID = os.environ.get("NEKO_INSTANCE_ID") or uuid.uuid4().hex
os.environ.setdefault("NEKO_INSTANCE_ID", INSTANCE_ID)

# 确保本地服务间通信不走系统代理（防止 Clash/Surge 等代理软件拦截 localhost 请求）
# httpx 优先读小写 no_proxy，因此大小写都需要设置
# 使用精确 token 匹配，防止 "127.0.0.1" in "127.0.0.10" 这类子串误判
for _key in ("NO_PROXY", "no_proxy"):
    _no_proxy_raw = os.environ.get(_key, "")
    _tokens = set(map(str.strip, filter(None, _no_proxy_raw.split(","))))
    for _host in ("127.0.0.1", "localhost"):
        _tokens.add(_host)
    os.environ[_key] = ",".join(_tokens)

JOB_HANDLE = None
_cleanup_lock = threading.Lock()
_cleanup_done = False
_existing_neko_services: set[str] = set()  # 已有 N.E.K.O 实例占用的端口键
DEFAULT_PORTS = {
    "MAIN_SERVER_PORT": MAIN_SERVER_PORT,
    "MEMORY_SERVER_PORT": MEMORY_SERVER_PORT,
    "TOOL_SERVER_PORT": TOOL_SERVER_PORT,
}
INTERNAL_DEFAULT_PORTS = {
    "USER_PLUGIN_SERVER_PORT": 48916,
    "AGENT_MQ_PORT": 48917,
    "MAIN_AGENT_EVENT_PORT": 48918,
    "ZMQ_SESSION_PUB_PORT": 48961,
    "ZMQ_AGENT_PUSH_PORT": 48962,
    "ZMQ_ANALYZE_PUSH_PORT": 48963,
}
# 该区间保留给 N.E.K.O 已知默认端口，避免 fallback 与伴生服务冲突。
AVOID_FALLBACK_PORTS = set(range(48911, 48919)) | {48961, 48962, 48963}

# 模块名到端口键的映射（用于判断已有 N.E.K.O 实例是否占用对应端口）
MODULE_TO_PORT_KEY: dict[str, str] = {
    "memory_server": "MEMORY_SERVER_PORT",
    "agent_server": "TOOL_SERVER_PORT",
    "main_server": "MAIN_SERVER_PORT",
}


def _install_logging_brace_compat() -> None:
    if getattr(logging, "_neko_brace_compat_installed", False):
        return

    original_get_message = logging.LogRecord.getMessage

    def _compat_get_message(record: logging.LogRecord) -> str:
        try:
            return original_get_message(record)
        except TypeError:
            msg = str(record.msg)
            args = record.args
            if not args or "%" in msg or "{" not in msg or "}" not in msg:
                raise
            try:
                if isinstance(args, dict):
                    return msg.format(**args)
                if not isinstance(args, tuple):
                    args = (args,)
                return msg.format(*args)
            except Exception:
                return f"{msg} | args={record.args!r}"

    logging.LogRecord.getMessage = _compat_get_message
    logging._neko_brace_compat_installed = True


_install_logging_brace_compat()


def _show_error_dialog(message: str):
    """在 Windows 打包场景显示错误弹窗。"""
    if sys.platform != 'win32':
        return
    try:
        ctypes.windll.user32.MessageBoxW(None, message, f"{APP_NAME} 启动失败", 0x10)
    except Exception:
        pass


def emit_frontend_event(event_type: str, payload: dict | None = None):
    """向 Electron stdout 发送机器可读事件。

    每个事件都带有 *launch_id*，前端可据此忽略历史（僵尸）进程事件。
    """
    envelope = {
        "source": "neko_launcher",
        "event": event_type,
        "ts": datetime.now(timezone.utc).isoformat(),
        "launch_id": LAUNCH_ID,
        "payload": payload or {},
    }
    print(f"NEKO_EVENT {json.dumps(envelope, ensure_ascii=True, separators=(',', ':'))}", flush=True)


def report_startup_failure(message: str, show_dialog: bool = True):
    """统一报告启动失败信息：终端 + （可选）弹窗。"""
    print(message, flush=True)
    emit_frontend_event("startup_failure", {"message": message})
    if show_dialog and getattr(sys, 'frozen', False):
        _show_error_dialog(message)


def _get_last_error() -> int:
    """获取最近一次 Win32 错误码。"""
    if sys.platform != 'win32':
        return 0
    return ctypes.windll.kernel32.GetLastError()


def setup_job_object():
    """
    创建 Windows Job Object 并将当前进程加入其中。
    设置 JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE 标志，
    这样当主进程被 kill 时，OS 会自动终止所有子进程，
    防止孤儿进程悬挂。
    """
    global JOB_HANDLE
    if sys.platform != 'win32':
        return None

    try:
        kernel32 = ctypes.windll.kernel32

        # Job Object 常量
        JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000

        # 先检查当前进程是否已在某个 Job 中（Steam 场景常见）
        is_in_job = ctypes.c_int(0)
        current_process = kernel32.GetCurrentProcess()
        if not kernel32.IsProcessInJob(current_process, None, ctypes.byref(is_in_job)):
            print(f"[Launcher] Warning: IsProcessInJob failed (err={_get_last_error()})", flush=True)
            is_in_job.value = 0

        # 创建 Job Object
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            print(f"[Launcher] Warning: Failed to create Job Object (err={_get_last_error()})", flush=True)
            return None

        # 设置 Job Object 信息
        # JOBOBJECT_EXTENDED_LIMIT_INFORMATION 结构体
        # 我们只需要设置 BasicLimitInformation.LimitFlags
        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ('PerProcessUserTimeLimit', ctypes.c_int64),
                ('PerJobUserTimeLimit', ctypes.c_int64),
                ('LimitFlags', ctypes.c_uint32),
                ('MinimumWorkingSetSize', ctypes.c_size_t),
                ('MaximumWorkingSetSize', ctypes.c_size_t),
                ('ActiveProcessLimit', ctypes.c_uint32),
                ('Affinity', ctypes.c_size_t),
                ('PriorityClass', ctypes.c_uint32),
                ('SchedulingClass', ctypes.c_uint32),
            ]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ('ReadOperationCount', ctypes.c_uint64),
                ('WriteOperationCount', ctypes.c_uint64),
                ('OtherOperationCount', ctypes.c_uint64),
                ('ReadTransferCount', ctypes.c_uint64),
                ('WriteTransferCount', ctypes.c_uint64),
                ('OtherTransferCount', ctypes.c_uint64),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ('BasicLimitInformation', JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ('IoInfo', IO_COUNTERS),
                ('ProcessMemoryLimit', ctypes.c_size_t),
                ('JobMemoryLimit', ctypes.c_size_t),
                ('PeakProcessMemoryUsed', ctypes.c_size_t),
                ('PeakJobMemoryUsed', ctypes.c_size_t),
            ]

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE

        result = kernel32.SetInformationJobObject(
            job,
            JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info),
            ctypes.sizeof(info)
        )
        if not result:
            print(f"[Launcher] Warning: Failed to set Job Object info (err={_get_last_error()})", flush=True)
            kernel32.CloseHandle(job)
            return None

        # 将当前进程加入 Job Object
        result = kernel32.AssignProcessToJobObject(job, current_process)
        if not result:
            err = _get_last_error()
            if is_in_job.value:
                print(
                    f"[Launcher] Warning: Process is already inside another Job; "
                    f"nested Job assignment failed (err={err}). "
                    "Will rely on explicit process-tree cleanup fallback.",
                    flush=True
                )
            else:
                print(f"[Launcher] Warning: Failed to assign process to Job Object (err={err})", flush=True)
            kernel32.CloseHandle(job)
            return None

        # 保持 handle 在进程生命周期内有效（模块级引用）
        # 进程退出时句柄会关闭，触发 KILL_ON_JOB_CLOSE
        JOB_HANDLE = job
        print("[Launcher] Job Object created - child processes will auto-terminate on exit", flush=True)
        return job

    except Exception as e:
        print(f"[Launcher] Warning: Job Object setup failed: {e}", flush=True)
        return None

# 服务器配置（按内存占用从轻到重排列，用于分步启动以降低峰值内存）
SERVERS = [
    {
        'name': 'Memory Server',
        'module': 'memory_server',
        'port': MEMORY_SERVER_PORT,
        'process': None,
        'ready_event': None,
    },
    {
        'name': 'Main Server',
        'module': 'main_server',
        'port': MAIN_SERVER_PORT,
        'process': None,
        'ready_event': None,
    },
    {
        'name': 'Agent Server',
        'module': 'agent_server',
        'port': TOOL_SERVER_PORT,
        'process': None,
        'ready_event': None,
    },
]

# 不再启动主程序，用户自己启动 lanlan_frd.exe

def run_memory_server(ready_event: Event, import_event: Event | None = None):
    """运行 Memory Server"""
    try:
        # 确保工作目录正确
        if getattr(sys, 'frozen', False):
            if hasattr(sys, '_MEIPASS'):
                # PyInstaller
                os.chdir(sys._MEIPASS)
            else:
                # Nuitka
                os.chdir(os.path.dirname(os.path.abspath(__file__)))
            # 禁用 typeguard（子进程需要重新禁用）
            try:
                import typeguard
                def dummy_typechecked(func=None, **kwargs):
                    return func if func else (lambda f: f)
                typeguard.typechecked = dummy_typechecked
                if hasattr(typeguard, '_decorators'):
                    typeguard._decorators.typechecked = dummy_typechecked
            except: # noqa
                pass
        
        import memory_server
        import uvicorn
        if import_event:
            import_event.set()
        
        print(f"[Memory Server] Starting on port {MEMORY_SERVER_PORT}")
        
        _behind_proxy = os.environ.get("NEKO_BEHIND_PROXY", "").strip().lower() in ("1", "true", "yes")
        # 使用 Server 对象，在启动后通知父进程
        config = uvicorn.Config(
            app=memory_server.app,
            host="127.0.0.1",
            port=MEMORY_SERVER_PORT,
            log_level="error",
            proxy_headers=_behind_proxy,
            forwarded_allow_ips="*" if _behind_proxy else None,
        )
        server = uvicorn.Server(config)
        
        # 在后台线程中运行服务器
        import asyncio
        
        async def run_with_notify():
            # 启动服务器
            await server.serve()
        
        # 启动线程来运行服务器，并在启动后通知
        def run_server():
            # 创建事件循环
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            # 添加启动完成的回调
            async def startup():
                print(f"[Memory Server] Running on port {MEMORY_SERVER_PORT}")
                ready_event.set()
            
            # 将 startup 添加到服务器的启动事件
            server.config.app.add_event_handler("startup", startup)
            
            # 运行服务器
            loop.run_until_complete(server.serve())
        
        run_server()
        
    except Exception as e:
        print(f"Memory Server error: {e}")
        import traceback
        traceback.print_exc()

def run_agent_server(ready_event: Event, import_event: Event | None = None):
    """运行 Agent Server (不需要等待初始化)"""
    try:
        # 确保工作目录正确
        if getattr(sys, 'frozen', False):
            if hasattr(sys, '_MEIPASS'):
                # PyInstaller
                os.chdir(sys._MEIPASS)
            else:
                # Nuitka
                os.chdir(os.path.dirname(os.path.abspath(__file__)))
            # 禁用 typeguard（子进程需要重新禁用）
            try:
                import typeguard
                def dummy_typechecked(func=None, **kwargs):
                    return func if func else (lambda f: f)
                typeguard.typechecked = dummy_typechecked
                if hasattr(typeguard, '_decorators'):
                    typeguard._decorators.typechecked = dummy_typechecked
            except: # noqa
                pass
        
        import agent_server
        import uvicorn
        if import_event:
            import_event.set()
        
        print(f"[Agent Server] Starting on port {TOOL_SERVER_PORT}")
        
        # Agent Server 不需要等待，立即通知就绪
        ready_event.set()
        
        _behind_proxy = os.environ.get("NEKO_BEHIND_PROXY", "").strip().lower() in ("1", "true", "yes")
        uvicorn.run(
            agent_server.app,
            host="127.0.0.1",
            port=TOOL_SERVER_PORT,
            log_level="error",
            proxy_headers=_behind_proxy,
            forwarded_allow_ips="*" if _behind_proxy else None,
        )
    except Exception as e:
        print(f"Agent Server error: {e}")
        import traceback
        traceback.print_exc()

def run_main_server(ready_event: Event, import_event: Event | None = None):
    """运行 Main Server"""
    try:
        # 确保工作目录正确
        if getattr(sys, 'frozen', False):
            if hasattr(sys, '_MEIPASS'):
                # PyInstaller
                os.chdir(sys._MEIPASS)
            else:
                # Nuitka
                os.chdir(os.path.dirname(os.path.abspath(__file__)))
        
        print("[Main Server] Importing main_server module...")
        import main_server
        import uvicorn
        if import_event:
            import_event.set()
        
        print(f"[Main Server] Starting on port {MAIN_SERVER_PORT}")
        
        _behind_proxy = os.environ.get("NEKO_BEHIND_PROXY", "").strip().lower() in ("1", "true", "yes")
        # 直接运行 FastAPI app，不依赖 main_server 的 __main__ 块
        config = uvicorn.Config(
            app=main_server.app,
            host="127.0.0.1",
            port=MAIN_SERVER_PORT,
            log_level="error",
            loop="asyncio",
            reload=False,
            proxy_headers=_behind_proxy,
            forwarded_allow_ips="*" if _behind_proxy else None,
        )
        server = uvicorn.Server(config)
        
        # 添加启动完成的回调
        async def startup():
            print(f"[Main Server] Running on port {MAIN_SERVER_PORT}")
            ready_event.set()
        
        # 将 startup 添加到服务器的启动事件
        main_server.app.add_event_handler("startup", startup)
        
        # 运行服务器
        server.run()
    except Exception as e:
        # 兜底崩溃日志：即使主日志系统未初始化，也能保留首个异常原因
        try:
            import traceback
            crash_file = os.path.join(os.getcwd(), "main_server_bootstrap_crash.log")
            with open(crash_file, "a", encoding="utf-8") as f:
                f.write("\n" + "=" * 80 + "\n")
                f.write(f"[{datetime.now().isoformat()}] Main Server bootstrap error: {e}\n")
                f.write(traceback.format_exc())
                f.write("\n")
        except Exception:
            pass
        print(f"Main Server error: {e}")
        import traceback
        traceback.print_exc()

def check_port(port: int, timeout: float = 0.5) -> bool:
    """检查端口是否已开放"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex(('127.0.0.1', port))
        sock.close()
        return result == 0
    except: # noqa
        return False


def get_port_owners(port: int) -> list[int]:
    """查询监听指定端口的进程 PID 列表（尽力而为）。"""
    pids: set[int] = set()
    try:
        if sys.platform == 'win32':
            result = subprocess.run(
                ["netstat", "-ano", "-p", "tcp"],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            needle = f":{port}"
            for raw in result.stdout.splitlines():
                line = raw.strip()
                if "LISTENING" not in line or needle not in line:
                    continue
                parts = line.split()
                if not parts:
                    continue
                pid_str = parts[-1]
                if pid_str.isdigit():
                    pids.add(int(pid_str))
        else:
            result = subprocess.run(
                ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            for line in result.stdout.splitlines():
                s = line.strip()
                if s.isdigit():
                    pids.add(int(s))
    except Exception:
        pass
    return sorted(pids)


def _is_port_bindable(port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _pick_fallback_port(preferred_port: int, reserved: set[int]) -> int | None:
    # 1) Prefer nearby ports first
    for port in range(preferred_port + 1, min(preferred_port + 101, 65535)):
        if port in reserved or port in AVOID_FALLBACK_PORTS:
            continue
        if _is_port_bindable(port):
            return port
    # 2) Fallback to any OS-assigned free port
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
        sock.close()
        if port not in reserved and port not in AVOID_FALLBACK_PORTS:
            return port
    except Exception:
        pass
    return None


def _classify_port_conflict(
    port: int,
    excluded_ranges: list[tuple[int, int]] | None = None,
) -> tuple[str, list]:
    """对端口不可用原因进行分类。

    返回 ``(reason, owners)``，其中 reason 为以下之一：
    - ``"neko"``            已有 N.E.K.O 服务占用
    - ``"hyperv_excluded"`` 位于 Hyper-V / WSL 保留端口范围
    - ``"other_process"``   被非 N.E.K.O 进程监听
    - ``"unknown"``         无法绑定但原因不明确
    owners 为监听该端口的进程 ID 列表。
    """
    health = probe_neko_health(port)
    if health is not None:
        return "neko", get_port_owners(port)
    # 将 excluded_ranges 解析一次，避免重复 netsh 子进程调用
    ranges = excluded_ranges if excluded_ranges is not None else get_hyperv_excluded_ranges()
    if is_port_in_excluded_range(port, ranges):
        return "hyperv_excluded", []
    owners = get_port_owners(port)
    if owners:
        return "other_process", owners
    return "unknown", []


def apply_port_strategy() -> bool | str:
    """优先使用默认端口，必要时自动规避冲突。

    返回值：
        ``True``      端口规划完成，可继续启动服务。
        ``False``     发生致命错误，需中止启动。
        ``"attach"`` 默认端口已由现有 N.E.K.O 后端完整占用。

    策略：
    1. 默认端口若已是 N.E.K.O 服务，则视为可复用。
    2. 若被 Hyper-V/WSL 保留或其他进程占用，则选择 fallback 端口。
    """
    global MAIN_SERVER_PORT, MEMORY_SERVER_PORT, TOOL_SERVER_PORT
    chosen: dict[str, int] = {}
    chosen_internal: dict[str, int] = {}
    fallback_details: list[dict] = []
    internal_fallback_details: list[dict] = []
    reserved: set[int] = set()

    # 预先查询 Hyper-V 保留端口范围，避免重复子进程调用
    excluded_ranges = get_hyperv_excluded_ranges()
    if excluded_ranges:
        print(f"[Launcher] Detected {len(excluded_ranges)} Hyper-V/WSL excluded port range(s)", flush=True)

    for key in ("MEMORY_SERVER_PORT", "TOOL_SERVER_PORT", "MAIN_SERVER_PORT"):
        preferred = int(DEFAULT_PORTS[key])
        if preferred not in reserved and _is_port_bindable(preferred):
            chosen[key] = preferred
            reserved.add(preferred)
            continue

        # 端口不可绑定，识别具体原因（同时获取 owners 避免重复查询）
        reason, owners = _classify_port_conflict(preferred, excluded_ranges)

        if reason == "neko":
            # 已有 N.E.K.O 实例占用该端口。
            # 仍记录为 chosen，并打标记供前端决定“附加复用”而非“重复拉起”。
            chosen[key] = preferred
            reserved.add(preferred)
            fallback_details.append(
                {
                    "port_key": key,
                    "preferred": preferred,
                    "selected": preferred,
                    "reason": "existing_neko",
                    "owners": owners,
                }
            )
            continue

        # 需要选择回退端口
        fallback = _pick_fallback_port(preferred, reserved)
        if fallback is None:
            report_startup_failure(
                f"Startup failed: no fallback port available for {key} "
                f"(preferred={preferred}, reason={reason}, owners={owners})"
            )
            return False

        chosen[key] = fallback
        reserved.add(fallback)
        fallback_details.append(
            {
                "port_key": key,
                "preferred": preferred,
                "selected": fallback,
                "reason": reason,
                "owners": owners,
            }
        )

    MAIN_SERVER_PORT = chosen["MAIN_SERVER_PORT"]
    MEMORY_SERVER_PORT = chosen["MEMORY_SERVER_PORT"]
    TOOL_SERVER_PORT = chosen["TOOL_SERVER_PORT"]

    for key, preferred in INTERNAL_DEFAULT_PORTS.items():
        if preferred not in reserved and _is_port_bindable(preferred):
            chosen_internal[key] = preferred
            reserved.add(preferred)
            continue

        owners = get_port_owners(preferred)
        fallback = _pick_fallback_port(preferred, reserved)
        if fallback is None:
            report_startup_failure(
                f"Startup failed: no fallback port available for {key} (preferred={preferred}, owners={owners})"
            )
            return False

        chosen_internal[key] = fallback
        reserved.add(fallback)
        internal_fallback_details.append(
            {
                "port_key": key,
                "preferred": preferred,
                "selected": fallback,
                "owners": owners,
            }
        )

    for key, value in chosen.items():
        os.environ[f"NEKO_{key}"] = str(value)
    for key, value in chosen_internal.items():
        os.environ[f"NEKO_{key}"] = str(value)

    for server in SERVERS:
        if server["module"] == "memory_server":
            server["port"] = MEMORY_SERVER_PORT
        elif server["module"] == "agent_server":
            server["port"] = TOOL_SERVER_PORT
        elif server["module"] == "main_server":
            server["port"] = MAIN_SERVER_PORT

    emit_frontend_event(
        "port_plan",
        {
            "instance_id": INSTANCE_ID,
            "defaults": DEFAULT_PORTS,
            "selected": chosen,
            "internal_defaults": INTERNAL_DEFAULT_PORTS,
            "internal_selected": chosen_internal,
            "fallbacks": fallback_details,
            "internal_fallbacks": internal_fallback_details,
            "fallback_applied": bool(fallback_details or internal_fallback_details),
        },
    )

    # 检查默认端口是否全部由既有 N.E.K.O 占用（existing_neko）。
    # 若是，则 launcher 不应继续拉起新服务。
    existing_neko_keys = {
        d["port_key"]
        for d in fallback_details
        if d.get("reason") == "existing_neko"
    }

    # 记录已存在实例的服务端口键，供 start_server() 跳过重复启动。
    global _existing_neko_services
    _existing_neko_services = existing_neko_keys

    if existing_neko_keys == set(DEFAULT_PORTS.keys()):
        # 默认端口上的完整 N.E.K.O 后端已在运行。
        emit_frontend_event(
            "attach_existing",
            {
                "selected": chosen,
                "message": "All default ports occupied by an existing N.E.K.O backend",
            },
        )
        print("[Launcher] Existing N.E.K.O backend detected on all default ports; attaching.", flush=True)
        return "attach"

    # 区分“复用已有实例”与“真正端口回退”的日志
    real_fallbacks = [d for d in fallback_details if d.get("reason") != "existing_neko"]
    if real_fallbacks or internal_fallback_details:
        print(
            f"[Launcher] Port fallback applied: public={real_fallbacks}, internal={internal_fallback_details}",
            flush=True,
        )
    elif existing_neko_keys:
        print(
            f"[Launcher] Ports reused from existing N.E.K.O instance: {sorted(existing_neko_keys)}",
            flush=True,
        )
    else:
        print("[Launcher] Preferred ports available; no fallback needed.", flush=True)
    return True

def show_spinner(stop_event: threading.Event, message: str = "正在启动服务器"):
    """显示转圈圈动画"""
    spinner = itertools.cycle(['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'])
    while not stop_event.is_set():
        sys.stdout.write(f'\r{message}... {next(spinner)} ')
        sys.stdout.flush()
        time.sleep(0.1)
    sys.stdout.write('\r' + ' ' * 60 + '\r')  # 清除动画行
    sys.stdout.write('\n')  # 换行，确保后续输出在新行
    sys.stdout.flush()

def start_server(server: Dict) -> bool:
    """启动单个服务器"""
    try:
        port = server.get('port')

        port_key = MODULE_TO_PORT_KEY.get(server['module'])

        # If this service's port already has a running N.E.K.O instance,
        # skip launching (the existing process will serve requests).
        if port_key and port_key in _existing_neko_services:
            print(f"✓ {server['name']} already running on port {port} (existing N.E.K.O instance)", flush=True)
            server['ready_event'] = Event()
            server['ready_event'].set()  # Mark as ready immediately
            return True

        if isinstance(port, int) and check_port(port):
            owner_pids = get_port_owners(port)
            owner_suffix = f", owner_pids={owner_pids}" if owner_pids else ""
            report_startup_failure(f"Start failed: {server['name']} port {port} already in use{owner_suffix}")
            return False

        # 根据模块名选择启动函数
        if server['module'] == 'memory_server':
            target_func = run_memory_server
        elif server['module'] == 'agent_server':
            target_func = run_agent_server
        elif server['module'] == 'main_server':
            target_func = run_main_server
        else:
            report_startup_failure(f"Start failed: {server['name']} has unknown module")
            return False
        
        # 创建进程间同步事件
        server['ready_event'] = Event()
        server['import_event'] = Event()
        
        # 使用 multiprocessing 启动服务器
        # 注意：不能设置 daemon=True，因为 main_server 自己会创建子进程
        server['process'] = Process(
            target=target_func,
            args=(server['ready_event'], server['import_event']),
            daemon=False,
        )
        server['process'].start()
        
        print(f"✓ {server['name']} 已启动 (PID: {server['process'].pid})", flush=True)
        return True
    except Exception as e:
        report_startup_failure(f"Start failed: {server['name']} exception: {e}")
        return False

def wait_for_servers(timeout: int = 60) -> bool:
    """等待所有服务器启动完成"""
    print("\n等待服务器准备就绪...", flush=True)
    
    # 启动动画线程
    stop_spinner = threading.Event()
    spinner_thread = threading.Thread(target=show_spinner, args=(stop_spinner, "检查服务器状态"))
    spinner_thread.daemon = True
    spinner_thread.start()
    
    start_time = time.time()
    all_ready = False
    
    # 第一步：等待所有端口就绪
    while time.time() - start_time < timeout:
        # 若某个子进程提前退出，立即报错而不是等到超时
        for server in SERVERS:
            proc = server.get('process')
            if proc is not None and not proc.is_alive() and not check_port(server['port']):
                report_startup_failure(
                    f"Startup failed: {server['name']} exited early (exitcode={proc.exitcode})"
                )
                stop_spinner.set()
                spinner_thread.join()
                return False

        ready_count = 0
        for server in SERVERS:
            if check_port(server['port']):
                ready_count += 1
        
        if ready_count == len(SERVERS):
            break
        
        time.sleep(0.5)
    
    # 第二步：等待所有服务器的 ready_event（同步初始化完成）
    if ready_count == len(SERVERS):
        for server in SERVERS:
            remaining_time = timeout - (time.time() - start_time)
            if remaining_time > 0:
                if server['ready_event'].wait(timeout=remaining_time):
                    continue
                else:
                    # 超时
                    break
        else:
            # 所有服务器都就绪了
            all_ready = True
    
    # 停止动画
    stop_spinner.set()
    spinner_thread.join()
    
    if all_ready:
        print("\n", flush=True)
        print("=" * 60, flush=True)
        print("✓✓✓  所有服务器已准备就绪！  ✓✓✓", flush=True)
        print("=" * 60, flush=True)
        print("\n", flush=True)
        return True
    else:
        print("\n", flush=True)
        print("=" * 60, flush=True)
        print("✗ 服务器启动超时，请检查日志文件", flush=True)
        print("=" * 60, flush=True)
        print("\n", flush=True)
        report_startup_failure("Startup timeout: at least one service did not become ready")
        # 显示未就绪的服务器
        for server in SERVERS:
            if not server['ready_event'].is_set():
                print(f"  - {server['name']} 初始化未完成", flush=True)
            elif not check_port(server['port']):
                print(f"  - {server['name']} 端口 {server['port']} 未就绪", flush=True)
        return False


def cleanup_servers():
    """清理所有服务器进程"""
    global _cleanup_done
    with _cleanup_lock:
        if _cleanup_done:
            return
        _cleanup_done = True

    print("\n正在关闭服务器...", flush=True)
    for server in SERVERS:
        proc = server.get('process')
        if not proc:
            continue

        try:
            # 先尝试温和终止
            if proc.is_alive():
                proc.terminate()
                proc.join(timeout=3)

            # 第二步：仍存活则 kill
            if proc.is_alive():
                proc.kill()
                proc.join(timeout=2)

            # 第三步：兜底强杀整个进程树，防止孙进程残留
            pid = proc.pid
            if pid:
                if sys.platform == 'win32':
                    subprocess.run(
                        ["taskkill", "/PID", str(pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False
                    )
                else:
                    # macOS / Linux 下兜底强杀整个进程树
                    try:
                        import psutil
                        try:
                            parent = psutil.Process(pid)
                            for child in parent.children(recursive=True):
                                child.kill()
                            parent.kill()
                        except psutil.NoSuchProcess:
                            pass
                    except ImportError:
                        try:
                            # 尽力而为的 pkill 兜底
                            subprocess.run(
                                ["pkill", "-9", "-P", str(pid)],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                check=False
                            )
                        except Exception:
                            pass

            print(f"✓ {server['name']} 已关闭", flush=True)
        except Exception as e:
            print(f"✗ {server['name']} 关闭失败: {e}", flush=True)

    # 显式关闭 Job handle（如果存在）
    if JOB_HANDLE and sys.platform == 'win32':
        try:
            ctypes.windll.kernel32.CloseHandle(JOB_HANDLE)
        except Exception:
            pass


def _handle_termination_signal(signum, _frame):
    """处理终止信号，尽量保证清理逻辑被触发。"""
    print(f"\n收到终止信号 ({signum})，正在关闭...", flush=True)
    cleanup_servers()
    raise SystemExit(0)


def register_shutdown_hooks():
    """注册退出钩子，覆盖更多退出路径。"""
    atexit.register(cleanup_servers)
    try:
        signal.signal(signal.SIGTERM, _handle_termination_signal)
    except Exception:
        pass

def _ensure_playwright_browsers():
    """Auto-install Playwright Chromium if missing (needed by browser-use).

    Uses playwright's bundled driver binary directly, so it works inside
    a Nuitka standalone build where ``python -m playwright`` is unavailable.
    The ``install chromium`` command is idempotent – if the browser already
    exists it returns almost instantly.

    When running frozen (Nuitka/PyInstaller), PLAYWRIGHT_BROWSERS_PATH is set
    to the bundled ``playwright_browsers`` dir so that build-time cached
    Chromium is used and no on-site download is needed.
    """
    try:
        from playwright._impl._driver import compute_driver_executable, get_driver_env
    except ImportError:
        return

    try:
        if getattr(sys, "frozen", False):
            if hasattr(sys, "_MEIPASS"):
                _bundle = sys._MEIPASS
            else:
                _bundle = os.path.dirname(os.path.abspath(__file__))
            _bundled_browsers = os.path.join(_bundle, "playwright_browsers")
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = _bundled_browsers

            if os.path.isdir(_bundled_browsers) and os.listdir(_bundled_browsers):
                print("[Launcher] ✓ Playwright Chromium ready (bundled)", flush=True)
                emit_frontend_event("playwright_check", {"status": "ready"})
                return

        driver = str(compute_driver_executable())
        env = get_driver_env()
        print("[Launcher] Checking Playwright Chromium browser...", flush=True)
        emit_frontend_event("playwright_check", {"status": "checking"})

        result = subprocess.run(
            [driver, "install", "chromium"],
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )

        if result.returncode == 0:
            print("[Launcher] ✓ Playwright Chromium ready", flush=True)
            emit_frontend_event("playwright_check", {"status": "ready"})
        else:
            msg = (result.stderr or "").strip()[:300]
            logging.getLogger(__name__).info("[Launcher] Playwright install warning: %s", msg)
            emit_frontend_event("playwright_check", {"status": "warning", "message": msg})
    except subprocess.TimeoutExpired:
        logging.getLogger(__name__).info("[Launcher] Playwright browser install timed out (300s)")
        emit_frontend_event("playwright_check", {"status": "timeout"})
    except Exception as e:
        logging.getLogger(__name__).info("[Launcher] Playwright browser check skipped: %s", e)
        emit_frontend_event("playwright_check", {"status": "skipped", "message": str(e)})


def main():
    """主函数"""
    # 支持 multiprocessing 在 Windows 上的打包
    freeze_support()

    # ── 发送 startup_begin，便于前端绑定本次启动会话 ──
    emit_frontend_event("startup_begin", {"instance_id": INSTANCE_ID})

    # ── 单实例启动锁 ──────────────────────────────────
    if not acquire_startup_lock():
        msg = "Another N.E.K.O launcher is already starting up"
        print(f"[Launcher] {msg}", flush=True)
        emit_frontend_event("startup_in_progress", {
            "message": msg,
        })
        return 0  # 非错误场景：前端应附加到已有进程

    try:
        port_result = apply_port_strategy()
        if port_result == "attach":
            # 已有 N.E.K.O 后端在运行，无需再次拉起。
            return 0
        if not port_result:
            return 1

        register_shutdown_hooks()

        # 创建 Job Object，确保主进程被 kill 时子进程也会被终止
        setup_job_object()

        # 自动安装 Playwright Chromium（browser-use 依赖）
        _ensure_playwright_browsers()

        print("=" * 60, flush=True)
        print("N.E.K.O. 服务器启动器", flush=True)
        print("=" * 60, flush=True)

        # 1. 分步启动服务器（错开 import 阶段以降低内存峰值）
        #    Windows spawn 模式下每个子进程独立加载所有依赖，
        #    同时 import 会导致 3 个进程同时分配大量临时对象，
        #    在 <=4GB 内存的机器上容易 OOM。
        #    只需等 import 完成（内存稳定）即可放行下一个，
        #    后续 uvicorn 初始化很轻量，可并行。
        print("\n正在启动服务器...\n", flush=True)
        all_started = True
        import_timeout = 90  # 单个服务 import 阶段超时秒数
        for i, server in enumerate(SERVERS):
            if not start_server(server):
                all_started = False
                break

            evt = server.get('import_event')
            is_last = (i == len(SERVERS) - 1)
            if evt and not is_last:
                print(f"  等待 {server['name']} 模块加载...", flush=True)
                proc = server.get('process')
                poll_interval = 2  # seconds
                remaining = import_timeout
                import_ok = False
                while remaining > 0:
                    if evt.wait(timeout=min(poll_interval, remaining)):
                        import_ok = True
                        break
                    remaining -= poll_interval
                    if proc and not proc.is_alive():
                        report_startup_failure(
                            f"Startup failed: {server['name']} exited early "
                            f"(exitcode={proc.exitcode})"
                        )
                        break
                if not import_ok:
                    if not (proc and not proc.is_alive()):
                        report_startup_failure(
                            f"Startup timeout: {server['name']} import not complete "
                            f"within {import_timeout}s"
                        )
                    all_started = False
                    break
                print(f"  ✓ {server['name']} 模块加载完成", flush=True)

        if not all_started:
            print("\n启动失败，正在清理...", flush=True)
            report_startup_failure("Startup aborted: at least one service failed to start", show_dialog=False)
            cleanup_servers()
            return 1

        # 2. 等待最后一个服务器也准备就绪
        if not wait_for_servers():
            print("\n启动失败，正在清理...", flush=True)
            report_startup_failure("Startup aborted: services did not become ready before timeout", show_dialog=False)
            cleanup_servers()
            return 1

        # 3. 服务器已启动，通知前端
        emit_frontend_event("startup_ready", {
            "instance_id": INSTANCE_ID,
            "selected": {
                "MAIN_SERVER_PORT": MAIN_SERVER_PORT,
                "MEMORY_SERVER_PORT": MEMORY_SERVER_PORT,
                "TOOL_SERVER_PORT": TOOL_SERVER_PORT,
            },
        })

        print("", flush=True)
        print("=" * 60, flush=True)
        print("  🎉 所有服务器已启动完成！", flush=True)
        print("\n  现在你可以：", flush=True)
        print("  1. 启动 lanlan_frd.exe 使用系统", flush=True)
        print(f"  2. 在浏览器访问 http://localhost:{MAIN_SERVER_PORT}", flush=True)
        print("\n  按 Ctrl+C 关闭所有服务器", flush=True)
        print("=" * 60, flush=True)
        print("", flush=True)

        # 持续运行，监控服务器状态
        while True:
            time.sleep(5)
            # 检查已实际启动的进程
            started = [s for s in SERVERS if s.get('process') is not None]
            if started and not all(s['process'].is_alive() for s in started):
                print("\n检测到服务器异常退出！", flush=True)
                break
            # 对复用已有实例的服务进行健康探测
            reused = [s for s in SERVERS if s.get('process') is None and s.get('port')]
            for s in reused:
                if probe_neko_health(s['port']) is None:
                    print(f"\n复用的 {s['name']}(port {s['port']}) 已不可达！", flush=True)
                    break
            else:
                continue
            break  # 内层 for 触发 break 时跳出外层 while

    except KeyboardInterrupt:
        print("\n\n收到中断信号，等待子进程退出...", flush=True)
        # 子进程已经收到了 SIGINT，给它们一点时间自己退出
        start_wait = time.time()
        while time.time() - start_wait < 3:
            all_dead = True
            for server in SERVERS:
                if server.get('process') and server['process'].is_alive():
                    all_dead = False
                    break
            if all_dead:
                break
            time.sleep(0.1)
            
    except Exception as e:
        print(f"\n发生错误: {e}", flush=True)
        report_startup_failure(f"Launcher unhandled exception: {e}")
    finally:
        print("\n正在关闭所有进程...", flush=True)
        
        # 尝试优雅关闭
        cleanup_servers()
        
        # 等待一段时间，确认进程是否真的无法终止
        print("\n等待进程清理完成...", flush=True)
        
        # 检查是否还有存活的进程
        has_alive = any(
            server.get('process') and server['process'].is_alive()
            for server in SERVERS
        )
        
        if has_alive:
            print("\n检测到进程未能正常退出，尝试强制终止...", flush=True)
            
            try:
                if hasattr(os, 'killpg'):
                    # POSIX: 逐个终止子进程，避免向自身进程组发送 SIGKILL
                    for server in SERVERS:
                        proc = server.get('process')
                        if not proc or not proc.is_alive():
                            continue
                        pid = getattr(proc, 'pid', None)
                        if not pid:
                            continue
                        try:
                            os.kill(pid, signal.SIGTERM)
                        except ProcessLookupError:
                            pass
                    time.sleep(1)

                    for server in SERVERS:
                        proc = server.get('process')
                        if not proc or not proc.is_alive():
                            continue
                        pid = getattr(proc, 'pid', None)
                        if not pid:
                            continue
                        try:
                            os.kill(pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    time.sleep(0.5)
                else:
                    # Windows: 使用 taskkill 强制杀死进程树
                    import subprocess
                    for server in SERVERS:
                        proc = server.get('process')
                        if not proc or not proc.is_alive():
                            continue
                        pid = getattr(proc, 'pid', None)
                        if not pid:
                            continue
                        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    time.sleep(0.5)
            except Exception as e:
                # 强制终止失败，忽略错误（进程可能已经退出）
                print(f"强制终止进程组时出错（可能进程已退出）: {e}", flush=True)

            # 强制终止后重新检查是否还有存活的进程
            has_alive = any(
                server.get('process') and server['process'].is_alive()
                for server in SERVERS
            )
        
        print("\n清理完成", flush=True)
        release_startup_lock()
        # 如果还有残留进程，使用非零退出码
        if has_alive:
            sys.exit(1)
    
        print("\n所有服务器已关闭", flush=True)
        print("再见！\n", flush=True)
    return 0

if __name__ == "__main__":
    sys.exit(main())
