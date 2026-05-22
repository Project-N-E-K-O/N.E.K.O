from __future__ import annotations

import atexit
import ctypes
import subprocess
import sys
import threading


_TEXTRACTOR_PROCESS_LOCK = threading.Lock()
_TEXTRACTOR_PROCESSES: set[subprocess.Popen] = set()
_TEXTRACTOR_JOB_HANDLES: dict[subprocess.Popen, int] = {}
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000


class _JobObjectBasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _JobObjectExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JobObjectBasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _track_textractor_process(process: subprocess.Popen) -> None:
    with _TEXTRACTOR_PROCESS_LOCK:
        _TEXTRACTOR_PROCESSES.add(process)
        job_handle = _create_kill_on_close_job_for_process(process)
        if job_handle:
            _TEXTRACTOR_JOB_HANDLES[process] = job_handle


def _untrack_textractor_process(process: subprocess.Popen) -> None:
    job_handle = 0
    with _TEXTRACTOR_PROCESS_LOCK:
        _TEXTRACTOR_PROCESSES.discard(process)
        job_handle = int(_TEXTRACTOR_JOB_HANDLES.pop(process, 0) or 0)
    _close_windows_handle(job_handle)


def _cleanup_tracked_textractor_processes() -> None:
    with _TEXTRACTOR_PROCESS_LOCK:
        processes = list(_TEXTRACTOR_PROCESSES)
        _TEXTRACTOR_PROCESSES.clear()
        job_handles = list(_TEXTRACTOR_JOB_HANDLES.values())
        _TEXTRACTOR_JOB_HANDLES.clear()
    for process in processes:
        try:
            if process.poll() is None:
                process.terminate()
        except Exception:
            continue
    for job_handle in job_handles:
        _close_windows_handle(int(job_handle or 0))


def _close_windows_handle(handle: int) -> None:
    if not handle or not sys.platform.startswith("win"):
        return
    try:
        ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(handle))
    except Exception:
        return


def _create_kill_on_close_job_for_process(process: subprocess.Popen) -> int:
    if not sys.platform.startswith("win"):
        return 0
    process_handle = int(getattr(process, "_handle", 0) or 0)
    if not process_handle:
        return 0
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateJobObjectW.restype = ctypes.c_void_p
        job_handle = int(kernel32.CreateJobObjectW(None, None) or 0)
        if not job_handle:
            return 0
        info = _JobObjectExtendedLimitInformation()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = bool(
            kernel32.SetInformationJobObject(
                ctypes.c_void_p(job_handle),
                _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
                ctypes.byref(info),
                ctypes.sizeof(info),
            )
        )
        if not ok:
            _close_windows_handle(job_handle)
            return 0
        if not kernel32.AssignProcessToJobObject(
            ctypes.c_void_p(job_handle),
            ctypes.c_void_p(process_handle),
        ):
            _close_windows_handle(job_handle)
            return 0
        return job_handle
    except Exception:
        try:
            _close_windows_handle(int(locals().get("job_handle", 0) or 0))
        except Exception:
            pass
        return 0


atexit.register(_cleanup_tracked_textractor_processes)
