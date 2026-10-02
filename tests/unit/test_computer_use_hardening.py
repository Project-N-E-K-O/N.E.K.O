import ast
import threading
from types import SimpleNamespace

import pytest

from brain import computer_use as cua


@pytest.mark.parametrize('code', [
    'import pyautogui\nx = 500\npyautogui.click(x, 300)',
    'import time as t\nt.sleep(0)',
    'from pyautogui import scroll as scroll_down\nscroll_down(-3)',
    'from time import sleep\nsleep(0)',
    'pyautogui.hscroll(-3)\npyautogui.mouseDown()\npyautogui.mouseUp()',
])
def test_generated_actions_preserve_safe_imports_and_literals(code):
    calls = []
    backend = SimpleNamespace(**{
        name: lambda *a, _name=name, **kw: calls.append((_name, a, kw))
        for name in cua._CUA_ALLOWED_METHODS
    })
    env = {'__builtins__': {}, 'pyautogui': backend,
           'time': SimpleNamespace(sleep=lambda seconds: calls.append(('sleep', (seconds,), {})))}
    sanitized = cua._sanitize_generated_code(code)
    assert all(isinstance(stmt, ast.Expr) for stmt in ast.parse(sanitized).body)
    exec(sanitized, env)
    assert calls
    if 'click(x' in code:
        assert calls == [('click', (500, 300), {})]
    if 'scroll_down' in code:
        assert calls == [('scroll', (-3,), {})]


@pytest.mark.parametrize('payload', [
    'time.sleep.__globals__["os"].system("calc")',
    'pyautogui.click.__func__.__globals__["os"].system("calc")',
    'pyautogui._backend.system("calc")',
    'def gen():\n    yield g.gi_frame.f_back.f_globals\ng = gen()\nfor G in g:\n    G["os"].system("calc")',
    'import os\nos.system("calc")',
    'from pyautogui import __builtins__',
    'pyautogui.click(**{"x": 3})',
    'pyautogui.write({**other, "x": 1})',
    'x = pyautogui._backend\npyautogui.click(x)',
    'pyautogui.click(int("500"), 300)',
])
def test_entire_generated_program_rejects_reflection_and_unpacking(payload):
    with pytest.raises((ValueError, SyntaxError, TypeError)):
        cua._sanitize_generated_code('pyautogui.click(500, 300)\n' + payload)


def test_cleanup_keeps_failed_keys_retries_and_restores_failsafe():
    calls = []
    failures = {'shift'}
    class Backend:
        FAILSAFE = True
        def keyUp(self, key, **kwargs):
            assert self.FAILSAFE is False
            calls.append(key)
            if key in failures:
                raise RuntimeError('backend failure')
        def mouseUp(self, **kwargs):
            calls.append(kwargs['button'])
    keys = ['shift', 'w']
    buttons = ['left']
    backend = Backend()
    gui = cua._ScaledPyAutoGUI(backend, held_keys=keys, held_buttons=buttons)
    assert not gui.release_held_keys()
    assert keys == ['shift'] and buttons == []
    assert calls == ['shift', 'w', 'left']
    assert backend.FAILSAFE is True
    failures.clear()
    assert gui.release_held_keys()
    assert keys == []


def test_desktop_lock_lasts_until_synchronous_worker_exits():
    entered = threading.Event()
    finish = threading.Event()
    first = object.__new__(cua.ComputerUseAdapter)
    second = object.__new__(cua.ComputerUseAdapter)
    def worker(*args):
        entered.set()
        assert finish.wait(5)
        return {'success': True}
    first._run_instruction = worker
    second._run_instruction = lambda *args: {'success': True}
    thread = threading.Thread(target=first.run_instruction, args=('first',))
    thread.start()
    try:
        assert entered.wait(5)
        assert second.run_instruction('second')['success'] is False
    finally:
        finish.set()
        thread.join(5)
    assert second.run_instruction('second')['success'] is True


def test_completion_is_signaled_after_input_cleanup(monkeypatch):
    adapter = object.__new__(cua.ComputerUseAdapter)
    adapter._llm_client = object()
    adapter._cancel_event = threading.Event()
    adapter._done_event = threading.Event()
    adapter._cancelled = False
    adapter.max_steps = 0
    adapter.actions = []
    adapter.reset = lambda: None
    calls = []
    def release():
        assert not adapter._done_event.is_set()
        calls.append('release')
        return True
    adapter._build_exec_env = lambda: {'pyautogui': SimpleNamespace(release_held_keys=release)}
    adapter.run_instruction('test')
    assert calls == ['release', 'release']
    assert adapter._done_event.is_set()


def test_rejected_action_is_reported_in_next_step_history(monkeypatch):
    adapter = object.__new__(cua.ComputerUseAdapter)
    adapter._llm_client = object()
    adapter._cancel_event = threading.Event()
    adapter._done_event = threading.Event()
    adapter._cancelled = False
    adapter.max_steps = 1
    adapter.actions = []
    adapter.cots = []
    adapter.reset = lambda: None
    adapter._interruptible_sleep = lambda seconds: None
    adapter._build_exec_env = lambda: {'pyautogui': SimpleNamespace(release_held_keys=lambda: True)}
    def predict(*args):
        adapter.cots.append({'action': 'click'})
        adapter.actions.append('click')
        return {'action': 'click'}, 'pyautogui.click(int("500"), 300)'
    adapter.predict = predict
    monkeypatch.setattr(cua, '_capture_computer_use_frame', lambda *args: object())
    monkeypatch.setattr(cua, 'compress_screenshot', lambda *args, **kwargs: b'image')
    adapter.run_instruction('test')
    assert 'Execution failed (ValueError)' in adapter.cots[-1]['action']
