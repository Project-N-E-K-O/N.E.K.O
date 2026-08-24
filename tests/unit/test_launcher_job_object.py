import ctypes
import sys

import pytest

# 模块级导入是有意的，别挪回函数体里。``launcher_core.bootstrap`` 在 import 时会
# 检查 ``ssl`` 是否已经被别人先 import 走，是就往 stdout 打一条 Warning。整套
# tests/unit 跑起来 ssl 几乎必然已在 sys.modules 里，于是「谁第一个 import
# launcher_core」决定了那条 Warning 落进谁的 capsys ——
# test_relax_job_kill_on_close_clears_the_limit_flag 断言的恰好是
# ``capsys.readouterr().out == ""``，轮到它当第一个导入者就红（seed 90210 实测）。
# 放在模块级 = 导入发生在收集期，落在任何用例的捕获窗口之外。
from launcher_core import runtime as launcher


class _FakeJobApi:
    def __init__(self, *, set_information_result=1):
        self.current_process = 0x123456789ABC
        self.job = 0xABCDEF123456
        self.assigned = None
        self.limit_flags = None
        self.set_information_result = set_information_result
        self.set_information_calls = []

    def GetCurrentProcess(self):
        return self.current_process

    def IsProcessInJob(self, process, _job, _result):
        assert process == self.current_process
        return 1

    def CreateJobObjectW(self, _security, _name):
        return self.job

    def SetInformationJobObject(self, job, _info_class, info_pointer, _size):
        assert job == self.job
        info = ctypes.cast(
            info_pointer,
            ctypes.POINTER(launcher._JOBOBJECT_EXTENDED_LIMIT_INFORMATION),
        ).contents
        self.limit_flags = info.BasicLimitInformation.LimitFlags
        self.set_information_calls.append((_info_class, _size))
        return self.set_information_result

    def AssignProcessToJobObject(self, job, process):
        self.assigned = (job, process)
        return 1

    def CloseHandle(self, _handle):
        return 1


@pytest.mark.unit
def test_setup_job_object_preserves_pointer_sized_handles(monkeypatch):
    api = _FakeJobApi()
    previous_handle = launcher.JOB_HANDLE
    monkeypatch.setattr(launcher.sys, "platform", "win32")
    monkeypatch.setattr(launcher, "_get_windows_job_api", lambda: api)

    try:
        launcher.JOB_HANDLE = None
        result = launcher.setup_job_object()
    finally:
        launcher.JOB_HANDLE = previous_handle

    assert result == api.job
    assert api.assigned == (api.job, api.current_process)
    assert api.limit_flags == 0x2000


@pytest.mark.unit
def test_relax_job_kill_on_close_clears_the_limit_flag(monkeypatch, capsys):
    api = _FakeJobApi()
    previous_handle = launcher.JOB_HANDLE
    monkeypatch.setattr(launcher.sys, "platform", "win32")
    monkeypatch.setattr(launcher, "_get_windows_job_api", lambda: api)

    try:
        launcher.JOB_HANDLE = api.job
        launcher._relax_job_kill_on_close()
    finally:
        launcher.JOB_HANDLE = previous_handle

    assert api.limit_flags == 0
    assert api.set_information_calls == [
        (9, ctypes.sizeof(launcher._JOBOBJECT_EXTENDED_LIMIT_INFORMATION))
    ]
    assert capsys.readouterr().out == ""


@pytest.mark.unit
def test_relax_job_kill_on_close_reports_win32_failure(monkeypatch, capsys):
    api = _FakeJobApi(set_information_result=0)
    previous_handle = launcher.JOB_HANDLE
    monkeypatch.setattr(launcher.sys, "platform", "win32")
    monkeypatch.setattr(launcher, "_get_windows_job_api", lambda: api)
    monkeypatch.setattr(launcher, "_get_last_error", lambda: 5)

    try:
        launcher.JOB_HANDLE = api.job
        launcher._relax_job_kill_on_close()
    finally:
        launcher.JOB_HANDLE = previous_handle

    assert api.limit_flags == 0
    assert "failed to relax Job kill-on-close (err=5)" in capsys.readouterr().out


@pytest.mark.unit
@pytest.mark.skipif(sys.platform != "win32", reason="real Win32 function signatures")
def test_job_object_api_declares_pointer_sized_handle_signatures():
    from ctypes import wintypes

    api = launcher._get_windows_job_api()

    assert ctypes.sizeof(wintypes.HANDLE) == ctypes.sizeof(ctypes.c_void_p)
    assert api.GetCurrentProcess.restype is wintypes.HANDLE
    assert api.CreateJobObjectW.restype is wintypes.HANDLE
    assert api.AssignProcessToJobObject.argtypes == [wintypes.HANDLE, wintypes.HANDLE]
    assert api.CloseHandle.argtypes == [wintypes.HANDLE]
