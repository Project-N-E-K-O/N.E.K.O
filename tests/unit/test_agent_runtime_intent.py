# -*- coding: utf-8 -*-
"""Unit tests for agent runtime intent persistence + restore flow.

Scope:
1. ``app.agent_runtime_intent``: roundtrip / schema / cache isolation
2. ``brain.computer_use.check_connectivity``: new (ok, reason_code) signature
   + classification of permanent vs transient errors
3. Master-switch semantics: ``set_agent_enabled(False)`` no longer wipes
   sub flags (issue: cycling the master gate used to forget user intent
   for every sub feature)
4. Gate-fail branch: gate-fail does NOT touch ``user_plugin_enabled``
   anymore (plugins don't depend on the agent LLM)
5. Intent writes: ``_persist_intent=True`` hooks fire on explicit toggles;
   ``_persist_intent=False`` (restore replay) does not re-write intent
6. Restore: ``_maybe_restore_agent_intent`` once-flag + escape hatch +
   permanent error short-circuit
"""

from __future__ import annotations

import asyncio
import builtins
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_intent_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Redirect agent_runtime_intent JSON I/O to a tmp dir + reset the
    module's in-process cache. Yields the directory the JSON would land in."""
    from app import agent_runtime_intent as ari

    # Backing store: fake config_manager that proxies load/save to tmp_path.
    store_file = tmp_path / ari.INTENT_FILENAME

    class _FakeCM:
        def load_json_config(self, filename, default_value=None):
            path = tmp_path / filename
            if not path.exists():
                if default_value is not None:
                    import copy
                    return copy.deepcopy(default_value)
                raise FileNotFoundError(str(path))
            return json.loads(path.read_text(encoding="utf-8"))

        def save_json_config(self, filename, data, *, bypass_write_fence: bool = False):
            path = tmp_path / filename
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _get_cm():
        return _FakeCM()

    monkeypatch.setattr("utils.config_manager.get_config_manager", _get_cm)
    ari.reset_cache_for_testing()
    yield store_file
    ari.reset_cache_for_testing()


# ---------------------------------------------------------------------------
# 1. agent_runtime_intent module
# ---------------------------------------------------------------------------


def test_intent_set_get_clear_roundtrip(isolated_intent_store: Path):
    from app import agent_runtime_intent as ari

    assert ari.load_intent() == {}
    ari.set_intent("analyzer_enabled", True)
    ari.set_intent("computer_use_enabled", False)

    # Round-trip the cache by forcing reload from disk.
    ari.reset_cache_for_testing()
    assert ari.load_intent() == {
        "analyzer_enabled": True,
        "computer_use_enabled": False,
    }
    assert ari.get_intent("analyzer_enabled") is True
    assert ari.get_intent("computer_use_enabled") is False
    assert ari.get_intent("user_plugin_enabled") is None

    ari.clear_intent("analyzer_enabled")
    assert ari.get_intent("analyzer_enabled") is None
    assert ari.load_intent() == {"computer_use_enabled": False}


def test_intent_rejects_unknown_keys(isolated_intent_store: Path):
    """Schema closure: unknown keys are silently dropped on write/read so
    a malformed file from a future version can't bork restore."""
    from app import agent_runtime_intent as ari

    ari.set_intent("bogus_key", True)  # type: ignore[arg-type]
    assert ari.get_intent("bogus_key") is None  # type: ignore[arg-type]
    assert ari.load_intent() == {}


def test_intent_set_same_value_is_noop(isolated_intent_store: Path):
    """Writing the same value twice should not re-touch disk — useful when
    restore re-applies an unchanged state."""
    from app import agent_runtime_intent as ari

    ari.set_intent("analyzer_enabled", True)
    mtime_after_first = isolated_intent_store.stat().st_mtime_ns

    # Same value: should short-circuit before save_to_disk.
    ari.set_intent("analyzer_enabled", True)
    mtime_after_second = isolated_intent_store.stat().st_mtime_ns

    assert mtime_after_first == mtime_after_second


def test_intent_coerces_malformed_payload(
    isolated_intent_store: Path, tmp_path: Path
):
    """Stale/garbage JSON values must not crash startup — invalid entries
    are filtered out, valid ones still load."""
    from app import agent_runtime_intent as ari

    isolated_intent_store.write_text(
        json.dumps({
            "analyzer_enabled": True,
            "computer_use_enabled": "yes",    # wrong type → drop
            "bogus": True,                     # unknown key → drop
            42: True,                          # non-str key → won't survive JSON anyway
        }),
        encoding="utf-8",
    )
    ari.reset_cache_for_testing()
    assert ari.load_intent() == {"analyzer_enabled": True}


# ---------------------------------------------------------------------------
# 2. check_connectivity signature + classification
# ---------------------------------------------------------------------------


def test_classify_connectivity_exception_buckets():
    from brain.computer_use import _classify_connectivity_exception

    # Permanent reasons
    assert _classify_connectivity_exception(Exception("Invalid API key: 401")) == "AGENT_API_KEY_INVALID"
    assert _classify_connectivity_exception(Exception("403 Forbidden")) == "AGENT_API_KEY_INVALID"
    assert _classify_connectivity_exception(Exception("authentication failed")) == "AGENT_API_KEY_INVALID"
    assert _classify_connectivity_exception(Exception("Rate limit exceeded (429)")) == "AGENT_QUOTA_EXCEEDED"
    assert _classify_connectivity_exception(Exception("insufficient_quota: please add billing")) == "AGENT_QUOTA_EXCEEDED"
    assert _classify_connectivity_exception(Exception("nxdomain: no such host")) == "AGENT_DNS_NXDOMAIN"
    assert _classify_connectivity_exception(Exception("getaddrinfo failed")) == "AGENT_DNS_NXDOMAIN"

    # Transient (default bucket)
    assert _classify_connectivity_exception(Exception("connection refused")) == "AGENT_LLM_UNREACHABLE"
    assert _classify_connectivity_exception(Exception("Read timeout")) == "AGENT_LLM_UNREACHABLE"
    assert _classify_connectivity_exception(Exception("500 internal server error")) == "AGENT_LLM_UNREACHABLE"
    assert _classify_connectivity_exception(None) == "AGENT_LLM_UNREACHABLE"


def test_permanent_connectivity_reasons_set():
    """Restore path uses this frozenset to decide whether to bail early.
    Lock the membership so an accidental rename in computer_use.py is caught."""
    from brain.computer_use import PERMANENT_CONNECTIVITY_REASONS

    assert "AGENT_API_KEY_INVALID" in PERMANENT_CONNECTIVITY_REASONS
    assert "AGENT_QUOTA_EXCEEDED" in PERMANENT_CONNECTIVITY_REASONS
    assert "AGENT_DNS_NXDOMAIN" in PERMANENT_CONNECTIVITY_REASONS
    assert "AGENT_ENDPOINT_NOT_CONFIGURED" in PERMANENT_CONNECTIVITY_REASONS
    # Transient must NOT be in the permanent set:
    assert "AGENT_LLM_UNREACHABLE" not in PERMANENT_CONNECTIVITY_REASONS


def test_pyautogui_display_error_is_not_reported_as_missing(monkeypatch: pytest.MonkeyPatch):
    from brain import computer_use as cu_module

    monkeypatch.setattr(cu_module, "pyautogui", None)
    monkeypatch.setattr(
        cu_module,
        "_PYAUTOGUI_IMPORT_ERROR",
        Exception("DisplayConnectionError: Authorization required, but no authorization protocol specified"),
    )

    assert cu_module._pyautogui_unavailable_reason() == "AGENT_PYAUTOGUI_DISPLAY_UNAVAILABLE"


def test_pyautogui_lazy_import_can_recover_after_initial_failure(monkeypatch: pytest.MonkeyPatch):
    from brain import computer_use as cu_module

    fake_pyautogui = SimpleNamespace(size=lambda: (3840, 2400))
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "pyautogui":
            return fake_pyautogui
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(cu_module, "pyautogui", None)
    monkeypatch.setattr(cu_module, "_PYAUTOGUI_IMPORT_ERROR", Exception("previous display failure"))
    monkeypatch.setattr(builtins, "__import__", fake_import)

    assert cu_module._load_pyautogui() is fake_pyautogui
    assert cu_module._PYAUTOGUI_IMPORT_ERROR is None


def test_scaled_pyautogui_accepts_normalized_float_coordinates():
    from brain.computer_use import _ScaledPyAutoGUI

    class FakeBackend:
        def __init__(self):
            self.calls = []

        def moveTo(self, *args, **kwargs):
            self.calls.append(("moveTo", args, kwargs))

        def click(self, *args, **kwargs):
            self.calls.append(("click", args, kwargs))

    backend = FakeBackend()
    gui = _ScaledPyAutoGUI(backend, 1920, 1080)

    gui.click(0.097, 0.336)

    assert backend.calls[-1] == ("click", (186, 363), {})
    assert backend.calls[0][0] == "moveTo"
    assert backend.calls[0][1][:2] == (186, 363)


def test_scaled_pyautogui_accepts_mixed_positional_keyword_coordinates():
    from brain.computer_use import _ScaledPyAutoGUI

    class FakeBackend:
        def __init__(self):
            self.calls = []

        def moveTo(self, *args, **kwargs):
            self.calls.append(("moveTo", args, kwargs))

        def click(self, *args, **kwargs):
            self.calls.append(("click", args, kwargs))

    backend = FakeBackend()
    gui = _ScaledPyAutoGUI(backend, 1920, 1080)

    gui.click(0.5, y=0.5)

    assert backend.calls[0][0] == "moveTo"
    assert backend.calls[0][1][:2] == (960, 540)
    assert backend.calls[-1] == ("click", (960,), {"y": 540})


def test_scaled_pyautogui_clamps_model_coordinates_away_from_failsafe_corner():
    from brain.computer_use import _ScaledPyAutoGUI

    class FakeBackend:
        def __init__(self):
            self.calls = []

        def click(self, *args, **kwargs):
            self.calls.append(("click", args, kwargs))

        def moveTo(self, *args, **kwargs):
            self.calls.append(("moveTo", args, kwargs))

    backend = FakeBackend()
    gui = _ScaledPyAutoGUI(backend, 1920, 1080)

    gui.click(0, 0)
    gui.click(999, 999)

    assert backend.calls[1] == ("click", (4, 4), {})
    assert backend.calls[3] == ("click", (1915, 1075), {})


def test_scaled_pyautogui_does_not_treat_mixed_int_float_as_unit_coordinates():
    from brain.computer_use import _ScaledPyAutoGUI

    class FakeBackend:
        def __init__(self):
            self.calls = []

        def click(self, *args, **kwargs):
            self.calls.append(("click", args, kwargs))

        def moveTo(self, *args, **kwargs):
            self.calls.append(("moveTo", args, kwargs))

    backend = FakeBackend()
    gui = _ScaledPyAutoGUI(backend, 1920, 1080)

    gui.click(1, 0.5)

    assert backend.calls[1] == ("click", (4, 4), {})


def test_scaled_pyautogui_preserves_absolute_edge_pixels():
    from brain.computer_use import _ScaledPyAutoGUI

    class FakeBackend:
        def __init__(self):
            self.calls = []

        def click(self, *args, **kwargs):
            self.calls.append(("click", args, kwargs))

        def moveTo(self, *args, **kwargs):
            self.calls.append(("moveTo", args, kwargs))

    backend = FakeBackend()
    gui = _ScaledPyAutoGUI(backend, 1920, 1080)

    gui.click(1919, 1079)

    assert backend.calls[0][0] == "moveTo"
    assert backend.calls[0][1][:2] == (1919, 1079)
    assert backend.calls[1] == ("click", (1919, 1079), {})


def test_check_connectivity_returns_tuple_on_missing_config(monkeypatch: pytest.MonkeyPatch):
    """If base_url/model not configured, must return ``(False, 'AGENT_ENDPOINT_NOT_CONFIGURED')``
    immediately without trying to construct an LLM client."""
    from brain import computer_use as cu_module

    # Build a minimal adapter instance avoiding the heavy __init__.
    adapter = cu_module.ComputerUseAdapter.__new__(cu_module.ComputerUseAdapter)
    adapter._config_manager = MagicMock()
    adapter._config_manager.get_model_api_config = MagicMock(return_value={
        "api_key": "EMPTY",
        "base_url": "",
        "model": "",
    })
    adapter._llm_client = None
    adapter._llm_client_sig = None
    adapter.init_ok = True
    adapter.last_error = None

    ok, reason = adapter.check_connectivity()
    assert ok is False
    assert reason == "AGENT_ENDPOINT_NOT_CONFIGURED"
    assert adapter.init_ok is False


def test_check_connectivity_success_returns_empty_reason(monkeypatch: pytest.MonkeyPatch):
    from brain import computer_use as cu_module

    fake_llm = MagicMock()
    fake_choice = MagicMock()
    fake_choice.message = MagicMock(content="ok")
    fake_resp = MagicMock(choices=[fake_choice])
    fake_llm.invoke_raw = MagicMock(return_value=fake_resp)

    monkeypatch.setattr(cu_module, "create_chat_llm", lambda **kwargs: fake_llm)

    adapter = cu_module.ComputerUseAdapter.__new__(cu_module.ComputerUseAdapter)
    adapter._config_manager = MagicMock()
    adapter._config_manager.get_model_api_config = MagicMock(return_value={
        "api_key": "sk-test",
        "base_url": "https://api.example.com",
        "model": "test-model",
    })
    adapter._llm_client = None
    adapter._llm_client_sig = None
    adapter.init_ok = False
    adapter.last_error = "stale"

    ok, reason = adapter.check_connectivity(timeout_s=4.0)
    assert ok is True
    assert reason == ""
    assert adapter.init_ok is True


def test_check_connectivity_accepts_anthropic_raw_message(monkeypatch: pytest.MonkeyPatch):
    from brain import computer_use as cu_module

    class TextBlock:
        type = "text"
        text = "ok"

    class AnthropicMessage:
        content = [TextBlock()]

    fake_llm = MagicMock()
    fake_llm.invoke_raw = MagicMock(return_value=AnthropicMessage())

    monkeypatch.setattr(cu_module, "create_chat_llm", lambda **kwargs: fake_llm)

    adapter = cu_module.ComputerUseAdapter.__new__(cu_module.ComputerUseAdapter)
    adapter._config_manager = MagicMock()
    adapter._config_manager.get_model_api_config = MagicMock(return_value={
        "api_key": "sk-test",
        "base_url": "https://api.kimi.com/coding",
        "model": "kimi-for-coding",
    })
    adapter._llm_client = None
    adapter._llm_client_sig = None
    adapter.init_ok = False
    adapter.last_error = "stale"

    ok, reason = adapter.check_connectivity(timeout_s=4.0)
    assert ok is True
    assert reason == ""
    assert adapter.init_ok is True


def test_extract_raw_llm_text_reads_anthropic_thinking_blocks():
    from brain.computer_use import _extract_raw_llm_text

    class ThinkingBlock:
        type = "thinking"
        thinking = "inspect the screen"

    class TextBlock:
        type = "text"
        text = "done"

    class AnthropicMessage:
        content = [ThinkingBlock(), TextBlock()]

    text, reasoning = _extract_raw_llm_text(AnthropicMessage())
    assert text == "done"
    assert reasoning == "inspect the screen"


def test_call_llm_parses_anthropic_raw_message(monkeypatch: pytest.MonkeyPatch):
    from brain import computer_use as cu_module

    class TextBlock:
        type = "text"
        text = """## Observation
Ready.

## Thought
Use a special action.

## Action
Finish.

## Code
```python
computer.terminate(status="success", answer="done")
```"""

    class AnthropicMessage:
        content = [TextBlock()]

    fake_llm = MagicMock()
    fake_llm.invoke_raw = MagicMock(return_value=AnthropicMessage())

    adapter = cu_module.ComputerUseAdapter.__new__(cu_module.ComputerUseAdapter)
    adapter._cancelled = False
    adapter._llm_client = fake_llm
    adapter._agent_model_cfg = {"model": "kimi-for-coding"}
    adapter.max_completion_tokens = 128
    adapter.thinking = False
    adapter._config_manager = MagicMock()
    adapter._config_manager.consume_agent_daily_quota = MagicMock(return_value=(True, {}))
    adapter._interruptible_sleep = MagicMock()

    parsed = adapter._call_llm([{"role": "user", "content": "finish"}])
    assert parsed["action"] == "Finish."
    assert parsed["code"] == 'computer.terminate(status="success", answer="done")'


def test_scaled_pyautogui_write_accepts_keyword_aliases():
    from brain.computer_use import _ScaledPyAutoGUI

    class Backend:
        def __init__(self):
            self.calls = []

        def write(self, *args, **kwargs):
            self.calls.append((args, kwargs))

    backend = Backend()
    gui = _ScaledPyAutoGUI(backend, screen_w=1920, screen_h=1080)

    gui.write(message="notepad", interval=0.01)
    gui.typewrite(text="calc")
    gui.write(string="cmd")

    assert backend.calls == [
        (("notepad",), {"interval": 0.01}),
        (("calc",), {}),
        (("cmd",), {}),
    ]


# ---------------------------------------------------------------------------
# 3. Master switch semantics: set_agent_enabled(False) preserves sub flags
# ---------------------------------------------------------------------------


@pytest.fixture
def agent_state_isolation(monkeypatch: pytest.MonkeyPatch):
    """Snapshot/restore the relevant global state on app.agent_server.Modules
    so tests can mutate freely. Also stubs out the side-effectful calls
    inside set_agent_enabled/set_agent_flags that need full process state
    (ZMQ bridge, plugin lifecycle, openclaw probe, etc)."""
    from app.agent_server import api_routes as srv

    backup_analyzer_enabled = srv.Modules.analyzer_enabled
    backup_analyzer_profile = dict(srv.Modules.analyzer_profile)
    backup_agent_flags = dict(srv.Modules.agent_flags)
    backup_capability = {
        k: dict(v) for k, v in srv.Modules.capability_cache.items()
    }

    async def _noop_async(*args, **kwargs):
        return None

    async def _noop_admin(*args, **kwargs):
        return {"success": True}

    async def _plugin_lifecycle_started_ok(*args, **kwargs):
        # Must return True so the background _bg_plugin_enable task in
        # set_agent_flags doesn't flip ``user_plugin_enabled`` back to
        # False and create a race against the test's assertions.
        return True

    monkeypatch.setattr(srv, "_emit_agent_status_update", _noop_async)
    monkeypatch.setattr(srv, "_ensure_plugin_lifecycle_stopped", _noop_async)
    monkeypatch.setattr(srv, "_ensure_plugin_lifecycle_started", _plugin_lifecycle_started_ok)
    monkeypatch.setattr(srv, "admin_control", _noop_admin)
    monkeypatch.setattr(srv, "_cancel_openclaw_enable_probe", lambda: None)
    monkeypatch.setattr(srv, "_try_refresh_computer_use_adapter", lambda force=False: False)
    monkeypatch.setattr(srv, "_fire_agent_llm_connectivity_check", _noop_async)
    monkeypatch.setattr(srv, "_bump_state_revision", lambda: 0)

    yield srv

    srv.Modules.analyzer_enabled = backup_analyzer_enabled
    srv.Modules.analyzer_profile = backup_analyzer_profile
    srv.Modules.agent_flags = backup_agent_flags
    srv.Modules.capability_cache = backup_capability


@pytest.mark.asyncio
async def test_set_agent_enabled_off_preserves_sub_flags(
    agent_state_isolation, isolated_intent_store: Path
):
    """Master gate OFF must NOT clear sub-flag user intent. The fix lets a
    user cycle the master toggle without re-ticking every sub feature."""
    srv = agent_state_isolation

    srv.Modules.analyzer_enabled = True
    srv.Modules.agent_flags["computer_use_enabled"] = True
    srv.Modules.agent_flags["browser_use_enabled"] = True
    srv.Modules.agent_flags["user_plugin_enabled"] = True
    srv.Modules.agent_flags["openclaw_enabled"] = True
    srv.Modules.agent_flags["openfang_enabled"] = True

    await srv.agent_command({
        "command": "set_agent_enabled",
        "enabled": False,
        "_persist_intent": False,
    })

    assert srv.Modules.analyzer_enabled is False
    # Sub flags survive — this is the core semantic fix
    assert srv.Modules.agent_flags["computer_use_enabled"] is True
    assert srv.Modules.agent_flags["browser_use_enabled"] is True
    assert srv.Modules.agent_flags["user_plugin_enabled"] is True
    assert srv.Modules.agent_flags["openclaw_enabled"] is True
    assert srv.Modules.agent_flags["openfang_enabled"] is True


@pytest.mark.asyncio
async def test_set_agent_enabled_on_reprobes_openclaw_when_intent_survives(
    agent_state_isolation, isolated_intent_store: Path, monkeypatch: pytest.MonkeyPatch
):
    """Master ON must refresh OpenClaw readiness when the sub-toggle intent survived OFF."""
    srv = agent_state_isolation
    started: list[str | None] = []

    srv.Modules.agent_flags["openclaw_enabled"] = True
    srv.Modules.capability_cache["openclaw"] = {"ready": False, "reason": ""}
    monkeypatch.setattr(srv, "_start_openclaw_enable_probe", lambda lanlan_name=None: started.append(lanlan_name))

    with patch.object(srv, "_check_agent_api_gate", return_value={"ready": True, "reasons": [], "is_free_version": False}):
        await srv.agent_command({
            "command": "set_agent_enabled",
            "enabled": False,
            "_persist_intent": False,
        })

        assert srv.Modules.analyzer_enabled is False
        assert srv.Modules.agent_flags["openclaw_enabled"] is True

        started.clear()

        await srv.agent_command({
            "command": "set_agent_enabled",
            "enabled": True,
            "lanlan_name": "lanlan-test",
            "_persist_intent": False,
        })

    assert started == ["lanlan-test"]


@pytest.mark.asyncio
async def test_openclaw_availability_ready_emits_after_canceling_pending_probe(
    agent_state_isolation, monkeypatch: pytest.MonkeyPatch
):
    srv = agent_state_isolation
    emitted: list[str | None] = []
    canceled: list[bool] = []

    class _ReadyOpenClaw:
        def is_available(self):
            return {"ready": True, "reasons": []}

    async def _capture_emit(lanlan_name=None):
        emitted.append(lanlan_name)

    monkeypatch.setattr(srv.Modules, "openclaw", _ReadyOpenClaw())
    monkeypatch.setattr(srv, "_openclaw_pending", lambda: True)
    monkeypatch.setattr(srv, "_cancel_openclaw_enable_probe", lambda: canceled.append(True))
    monkeypatch.setattr(srv, "_emit_agent_status_update", _capture_emit)

    srv.Modules.agent_flags["openclaw_enabled"] = True
    srv.Modules.capability_cache["openclaw"] = {
        "ready": False,
        "reason": "AGENT_PRECHECK_PENDING",
    }

    status = await srv.openclaw_availability()

    assert status == {"ready": True, "reasons": []}
    assert srv.Modules.capability_cache["openclaw"] == {"ready": True, "reason": ""}
    assert canceled == [True]
    assert emitted == [None]


@pytest.mark.asyncio
async def test_openclaw_availability_loss_emits_after_disabling_flag(
    agent_state_isolation, monkeypatch: pytest.MonkeyPatch
):
    srv = agent_state_isolation
    emitted: list[str | None] = []

    class _UnavailableOpenClaw:
        def is_available(self):
            return {"ready": False, "reasons": ["AGENT_CONNECTIVITY_FAILED"]}

    async def _capture_emit(lanlan_name=None):
        emitted.append(lanlan_name)

    monkeypatch.setattr(srv.Modules, "openclaw", _UnavailableOpenClaw())
    monkeypatch.setattr(srv, "_openclaw_pending", lambda: False)
    monkeypatch.setattr(srv, "_emit_agent_status_update", _capture_emit)

    srv.Modules.agent_flags["openclaw_enabled"] = True
    srv.Modules.capability_cache["openclaw"] = {"ready": True, "reason": ""}

    status = await srv.openclaw_availability()

    assert status == {"ready": False, "reasons": ["AGENT_CONNECTIVITY_FAILED"]}
    assert srv.Modules.agent_flags["openclaw_enabled"] is False
    assert srv.Modules.capability_cache["openclaw"] == {
        "ready": False,
        "reason": "AGENT_CONNECTIVITY_FAILED",
    }
    assert emitted == [None]


@pytest.mark.asyncio
async def test_gate_fail_preserves_user_plugin_enabled(
    agent_state_isolation, isolated_intent_store: Path
):
    """Agent LLM gate failure (endpoint not configured) is the gate for
    CU/BU/OpenClaw/OpenFang — but NOT user_plugin, which runs on its own
    plugin lifecycle and doesn't touch the agent LLM."""
    srv = agent_state_isolation

    # Gate FAILS:
    srv.Modules.analyzer_enabled = True
    fake_gate = {"ready": False, "reasons": ["AGENT_ENDPOINT_NOT_CONFIGURED"]}

    with patch.object(srv, "_check_agent_api_gate", return_value=fake_gate):
        # User attempts to enable CU + user_plugin simultaneously. CU must
        # be rejected (LLM-coupled), user_plugin must proceed.
        await srv.set_agent_flags({
            "computer_use_enabled": True,
            "user_plugin_enabled": True,
            "_persist_intent": False,
        })

    # CU was rejected
    assert srv.Modules.agent_flags["computer_use_enabled"] is False
    # user_plugin was let through (in-memory flag set to True by handler,
    # background _bg_plugin_enable runs but we don't care about its outcome here).
    assert srv.Modules.agent_flags["user_plugin_enabled"] is True


# ---------------------------------------------------------------------------
# 4. Intent writes on explicit toggles
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_agent_enabled_writes_intent(
    agent_state_isolation, isolated_intent_store: Path
):
    """Explicit toggle (default _persist_intent=True) writes intent file."""
    from app import agent_runtime_intent as ari

    srv = agent_state_isolation

    # Stub out the LLM-side _try_refresh / probe so gate-pass branch is quiet.
    with patch.object(srv, "_check_agent_api_gate", return_value={"ready": True, "reasons": [], "is_free_version": False}):
        await srv.agent_command({
            "command": "set_agent_enabled",
            "enabled": True,
        })

    assert ari.get_intent("analyzer_enabled") is True

    with patch.object(srv, "_check_agent_api_gate", return_value={"ready": True, "reasons": [], "is_free_version": False}):
        await srv.agent_command({
            "command": "set_agent_enabled",
            "enabled": False,
        })

    assert ari.get_intent("analyzer_enabled") is False


@pytest.mark.asyncio
async def test_persist_intent_false_does_not_write(
    agent_state_isolation, isolated_intent_store: Path
):
    """Restore replay path passes _persist_intent=False; that must NOT
    write back to the intent file (which would be a self-rewriting loop)."""
    from app import agent_runtime_intent as ari

    srv = agent_state_isolation
    ari.set_intent("analyzer_enabled", True)
    # Pollute cache so we'd notice unintended overwrite.
    initial = ari.load_intent()

    with patch.object(srv, "_check_agent_api_gate", return_value={"ready": True, "reasons": [], "is_free_version": False}):
        await srv.agent_command({
            "command": "set_agent_enabled",
            "enabled": False,
            "_persist_intent": False,
        })

    # Replay with _persist_intent=False must leave intent unchanged.
    assert ari.load_intent() == initial


@pytest.mark.asyncio
async def test_capability_rejected_does_not_persist_true_intent(
    agent_state_isolation, isolated_intent_store: Path
):
    """If user requests cf=True but capability auto-rejects (in-memory
    flag stays False), intent must NOT be written True — otherwise restore
    would keep retrying a feature the user can never enable."""
    from app import agent_runtime_intent as ari

    srv = agent_state_isolation
    srv.Modules.analyzer_enabled = True
    # Force computer_use to be missing → handler will set flag=False
    srv.Modules.computer_use = None

    with patch.object(srv, "_check_agent_api_gate", return_value={"ready": True, "reasons": [], "is_free_version": False}):
        await srv.set_agent_flags({"computer_use_enabled": True})

    assert srv.Modules.agent_flags["computer_use_enabled"] is False
    # Intent not written because actual flag is False
    assert ari.get_intent("computer_use_enabled") is None


@pytest.mark.asyncio
async def test_explicit_disable_always_persists_false_intent(
    agent_state_isolation, isolated_intent_store: Path
):
    """User explicitly disabling a flag must write False intent, even
    without capability check (disable doesn't need a probe)."""
    from app import agent_runtime_intent as ari

    srv = agent_state_isolation
    srv.Modules.analyzer_enabled = True
    srv.Modules.agent_flags["openfang_enabled"] = True

    with patch.object(srv, "_check_agent_api_gate", return_value={"ready": True, "reasons": [], "is_free_version": False}):
        await srv.set_agent_flags({"openfang_enabled": False})

    assert srv.Modules.agent_flags["openfang_enabled"] is False
    assert ari.get_intent("openfang_enabled") is False


# ---------------------------------------------------------------------------
# 5. _maybe_restore_agent_intent: once flag + escape hatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_restore_once_flag_blocks_duplicate_runs(
    agent_state_isolation, isolated_intent_store: Path, monkeypatch: pytest.MonkeyPatch
):
    """Once flag must prevent duplicate restore from concurrent
    greeting_check signals from N WebSockets."""
    from app.agent_server import api_routes as srv_mod
    srv_mod._reset_intent_restore_for_testing()

    call_count = {"n": 0}

    async def _fake_do_restore():
        call_count["n"] += 1

    monkeypatch.setattr(srv_mod, "_do_restore_agent_intent", _fake_do_restore)

    await asyncio.gather(
        srv_mod._maybe_restore_agent_intent(),
        srv_mod._maybe_restore_agent_intent(),
        srv_mod._maybe_restore_agent_intent(),
    )
    assert call_count["n"] == 1


@pytest.mark.asyncio
async def test_restore_escape_hatch_via_env(
    agent_state_isolation, isolated_intent_store: Path, monkeypatch: pytest.MonkeyPatch
):
    """NEKO_DISABLE_AGENT_AUTO_RESTORE=1 must skip restore entirely."""
    from app.agent_server import api_routes as srv_mod
    srv_mod._reset_intent_restore_for_testing()

    monkeypatch.setenv("NEKO_DISABLE_AGENT_AUTO_RESTORE", "1")
    called = {"n": 0}

    async def _fake_do_restore():
        called["n"] += 1

    monkeypatch.setattr(srv_mod, "_do_restore_agent_intent", _fake_do_restore)

    await srv_mod._maybe_restore_agent_intent()
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_restore_llm_dependent_all_retries_fail_clears_intent(
    agent_state_isolation, isolated_intent_store: Path, monkeypatch: pytest.MonkeyPatch
):
    """When probe fails three times with transient error, intent should
    flip to False so the user gets a clear AGENT_AUTO_DISABLED notification
    and next restart doesn't keep banging on the same dead endpoint."""
    from app import agent_runtime_intent as ari
    from app.agent_server import api_routes as srv_mod

    ari.set_intent("computer_use_enabled", True)
    ari.set_intent("browser_use_enabled", True)

    # Mock computer_use adapter on Modules; check_connectivity always fails.
    fake_adapter = MagicMock()
    fake_adapter.check_connectivity = MagicMock(return_value=(False, "AGENT_LLM_UNREACHABLE"))
    srv_mod.Modules.computer_use = fake_adapter

    # Shrink the retry window so the test is fast (3 attempts × 0.01s spacing).
    monkeypatch.setattr(srv_mod, "_RESTORE_PING_INTERVAL_S", 0.01)

    intent = {"computer_use_enabled": True, "browser_use_enabled": True}
    await srv_mod._restore_llm_dependent_flags(intent)

    # All three attempts ran
    assert fake_adapter.check_connectivity.call_count == srv_mod._RESTORE_PING_MAX_ATTEMPTS
    # Intent cleared to False on both flags
    assert ari.get_intent("computer_use_enabled") is False
    assert ari.get_intent("browser_use_enabled") is False


@pytest.mark.asyncio
async def test_restore_llm_dependent_permanent_error_short_circuits(
    agent_state_isolation, isolated_intent_store: Path, monkeypatch: pytest.MonkeyPatch
):
    """Permanent reason (e.g. API key invalid) must bail after the first
    attempt — don't waste 15s retrying when we know the API is dead."""
    from app import agent_runtime_intent as ari
    from app.agent_server import api_routes as srv_mod

    ari.set_intent("computer_use_enabled", True)

    fake_adapter = MagicMock()
    fake_adapter.check_connectivity = MagicMock(return_value=(False, "AGENT_API_KEY_INVALID"))
    srv_mod.Modules.computer_use = fake_adapter

    monkeypatch.setattr(srv_mod, "_RESTORE_PING_INTERVAL_S", 0.01)

    intent = {"computer_use_enabled": True}
    await srv_mod._restore_llm_dependent_flags(intent)

    # Only ONE attempt — permanent reason short-circuits.
    assert fake_adapter.check_connectivity.call_count == 1
    assert ari.get_intent("computer_use_enabled") is False


@pytest.mark.asyncio
async def test_restore_llm_dependent_success_keeps_intent(
    agent_state_isolation, isolated_intent_store: Path, monkeypatch: pytest.MonkeyPatch
):
    """When probe succeeds, intent should be preserved (NOT touched —
    set_agent_flags with _persist_intent=False is called, which won't
    write intent)."""
    from app import agent_runtime_intent as ari
    from app.agent_server import api_routes as srv_mod

    ari.set_intent("computer_use_enabled", True)

    fake_adapter = MagicMock()
    fake_adapter.check_connectivity = MagicMock(return_value=(True, ""))
    fake_adapter.init_ok = True
    fake_adapter.is_available = MagicMock(return_value={"ready": True, "reasons": []})
    srv_mod.Modules.computer_use = fake_adapter

    # Master gate ON so set_agent_flags doesn't bail
    srv_mod.Modules.analyzer_enabled = True

    with patch.object(srv_mod, "_check_agent_api_gate", return_value={"ready": True, "reasons": [], "is_free_version": False}):
        intent = {"computer_use_enabled": True}
        await srv_mod._restore_llm_dependent_flags(intent)

    # Intent preserved
    assert ari.get_intent("computer_use_enabled") is True


@pytest.mark.asyncio
async def test_restore_llm_dependent_module_not_loaded_is_permanent(
    agent_state_isolation, isolated_intent_store: Path, monkeypatch: pytest.MonkeyPatch
):
    """If computer_use module is None (not loaded), restore should
    immediately clear intent — not retry, since the module won't appear."""
    from app import agent_runtime_intent as ari
    from app.agent_server import api_routes as srv_mod

    ari.set_intent("computer_use_enabled", True)
    ari.set_intent("browser_use_enabled", True)
    srv_mod.Modules.computer_use = None

    intent = {"computer_use_enabled": True, "browser_use_enabled": True}
    await srv_mod._restore_llm_dependent_flags(intent)

    assert ari.get_intent("computer_use_enabled") is False
    assert ari.get_intent("browser_use_enabled") is False


@pytest.mark.asyncio
async def test_restore_skipped_when_master_intent_off(
    agent_state_isolation, isolated_intent_store: Path, monkeypatch: pytest.MonkeyPatch
):
    """Per Codex P1: if persisted intent has ``analyzer_enabled=False`` but
    sub flags True (a legitimate state now that master OFF preserves sub
    intent), restore must NOT spin up plugin lifecycle / LLM probe / openclaw
    probe — master is the runtime gate. Sub-flag intents stay in the file
    untouched."""
    from app import agent_runtime_intent as ari
    from app.agent_server import api_routes as srv_mod

    ari.set_intent("analyzer_enabled", False)
    ari.set_intent("computer_use_enabled", True)
    ari.set_intent("user_plugin_enabled", True)
    ari.set_intent("openclaw_enabled", True)

    # If any of these get called, the master-gate skip was missed.
    agent_command_calls = []
    set_flags_calls = []
    llm_dependent_calls = []
    user_plugin_calls = []

    async def _fake_agent_command(payload):
        agent_command_calls.append(payload)
        return {"success": True}

    async def _fake_set_flags(payload):
        set_flags_calls.append(payload)
        return {"success": True}

    async def _fake_llm_dependent(intent):
        llm_dependent_calls.append(intent)

    async def _fake_user_plugin():
        user_plugin_calls.append(True)

    monkeypatch.setattr(srv_mod, "agent_command", _fake_agent_command)
    monkeypatch.setattr(srv_mod, "set_agent_flags", _fake_set_flags)
    monkeypatch.setattr(srv_mod, "_restore_llm_dependent_flags", _fake_llm_dependent)
    monkeypatch.setattr(srv_mod, "_restore_user_plugin", _fake_user_plugin)

    await srv_mod._do_restore_agent_intent()

    # Nothing should fire when master intent is False
    assert agent_command_calls == []
    assert set_flags_calls == []
    assert llm_dependent_calls == []
    assert user_plugin_calls == []

    # Sub-flag intents untouched — they stay so next master ON re-activates
    assert ari.get_intent("computer_use_enabled") is True
    assert ari.get_intent("user_plugin_enabled") is True
    assert ari.get_intent("openclaw_enabled") is True
    assert ari.get_intent("analyzer_enabled") is False


@pytest.mark.asyncio
async def test_restore_skipped_when_master_intent_never_set(
    agent_state_isolation, isolated_intent_store: Path, monkeypatch: pytest.MonkeyPatch
):
    """When master intent is None (never toggled), default-deny: don't spin
    up sub components. User can turn master on explicitly to activate them."""
    from app import agent_runtime_intent as ari
    from app.agent_server import api_routes as srv_mod

    # Master intent NOT set; sub flags set.
    ari.set_intent("user_plugin_enabled", True)
    assert ari.get_intent("analyzer_enabled") is None

    user_plugin_calls = []

    async def _fake_user_plugin():
        user_plugin_calls.append(True)

    monkeypatch.setattr(srv_mod, "_restore_user_plugin", _fake_user_plugin)

    await srv_mod._do_restore_agent_intent()
    assert user_plugin_calls == []


@pytest.mark.asyncio
async def test_restore_proceeds_when_master_intent_on(
    agent_state_isolation, isolated_intent_store: Path, monkeypatch: pytest.MonkeyPatch
):
    """Sanity counter-test: master=True + user_plugin=True must trigger
    user_plugin restore. (Pairs with the master-OFF skip test above.)"""
    from app import agent_runtime_intent as ari
    from app.agent_server import api_routes as srv_mod

    ari.set_intent("analyzer_enabled", True)
    ari.set_intent("user_plugin_enabled", True)

    user_plugin_calls = []

    async def _fake_agent_command(payload):
        return {"success": True}

    async def _fake_user_plugin():
        user_plugin_calls.append(True)

    monkeypatch.setattr(srv_mod, "agent_command", _fake_agent_command)
    monkeypatch.setattr(srv_mod, "_restore_user_plugin", _fake_user_plugin)

    await srv_mod._do_restore_agent_intent()
    # Give the parallel task a tick to schedule
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert user_plugin_calls == [True]


def test_check_connectivity_failure_classifies_reason(monkeypatch: pytest.MonkeyPatch):
    """When invoke_raw raises, the classification helper picks the bucket."""
    from brain import computer_use as cu_module

    fake_llm = MagicMock()
    fake_llm.invoke_raw = MagicMock(side_effect=Exception("Rate limit exceeded: 429"))
    monkeypatch.setattr(cu_module, "create_chat_llm", lambda **kwargs: fake_llm)

    adapter = cu_module.ComputerUseAdapter.__new__(cu_module.ComputerUseAdapter)
    adapter._config_manager = MagicMock()
    adapter._config_manager.get_model_api_config = MagicMock(return_value={
        "api_key": "sk-test",
        "base_url": "https://api.example.com",
        "model": "test-model",
    })
    adapter._llm_client = None
    adapter._llm_client_sig = None
    adapter.init_ok = True
    adapter.last_error = None

    ok, reason = adapter.check_connectivity(timeout_s=4.0)
    assert ok is False
    assert reason == "AGENT_QUOTA_EXCEEDED"
    assert adapter.init_ok is False
