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
"""Executable coverage for the session-creation body.

``_start_session_start_llm`` is the function that builds and connects the
per-session client, and both of its branches (text -> OmniOfflineClient,
voice -> OmniRealtimeClient) had no test that ever *ran* them: every existing
guard around this file asserts on its source text (AST / string matching), and
the ``start_session`` tests all return at the re-entrancy guard.  A branch that
cannot execute at all therefore stayed green through review and CI.

That is not hypothetical: a diagnostic added to the voice branch read
``realtime_config`` before its first assignment in the same scope, so every
voice session died with ``UnboundLocalError`` while the text branch — the one
the structural guards cover — kept working.  These tests drive both branches
end to end against stub collaborators, so any unbound name, missing argument,
or renamed collaborator in either branch fails here.
"""

import ast
import asyncio
import inspect
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import main_logic.core as core_facade  # noqa: E402
import main_logic.core.lifecycle as lifecycle  # noqa: E402
from main_logic.core import LLMSessionManager  # noqa: E402


class _StubClient:
    """Stands in for OmniOfflineClient / OmniRealtimeClient."""

    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.connected_with = None
        self.closed = False
        self._audio_processor = None
        type(self).instances.append(self)

    async def connect(self, initial_prompt, native_audio=True):
        self.connected_with = (initial_prompt, native_audio)

    async def close(self):
        self.closed = True

    async def set_audio_noise_reduction_enabled(self, enabled):
        self.noise_reduction = enabled

    async def handle_messages(self):
        return None


class _StubConfigManager:
    def __init__(self, core_config, model_configs):
        self._core_config = core_config
        self._model_configs = model_configs
        self.resolved_calls = 0

    async def aensure_region_resolved(self, *a, **k):
        self.resolved_calls += 1
        return True

    async def aget_core_config(self, *a, **k):
        return dict(self._core_config)

    async def aget_model_api_config(self, kind, core_config=None):
        return dict(self._model_configs[kind])


def _make_manager(monkeypatch, *, input_mode):
    """A manager positioned exactly at ``_start_session_start_llm``.

    Only the collaborators that function touches are real-ish; everything else
    is a stub, so a failure here means the function itself is broken rather
    than some unrelated dependency.
    """
    mgr = LLMSessionManager.__new__(LLMSessionManager)
    mgr.lanlan_name = "兰兰"
    mgr.master_name = "用户"
    mgr.user_language = "zh"
    mgr.memory_server_port = 48911
    mgr.use_tts = True
    mgr.core_api_type = "qwen"
    mgr.voice_id = "yui"
    mgr.session = None
    mgr.current_speech_id = ""
    mgr.lock = asyncio.Lock()
    mgr.tool_registry = types.SimpleNamespace(all=lambda: [])

    mgr._config_manager = _StubConfigManager(
        core_config={"CORE_URL": "https://www.lanlan.app", "DISABLE_TTS": False},
        model_configs={
            "realtime": {"base_url": "wss://www.lanlan.app/v1", "api_key": "k",
                         "model": "m", "api_type": "qwen"},
            "conversation": {"base_url": "https://www.lanlan.app/text/v1",
                             "api_key": "k", "model": "m"},
            "vision": {"base_url": "https://www.lanlan.app/text/v1",
                       "api_key": "k", "model": "vm"},
        },
    )

    mgr._get_text_guard_max_length = lambda: 4096
    mgr._build_initial_prompt = _async_return("PROMPT ")
    mgr._snapshot_next_session_context_messages = lambda: []
    mgr._mark_pending_context_appends_delivered_in_start_prompt = lambda *a, **k: None
    mgr._clear_pending_context_start_prompt_marks = lambda *a, **k: None
    mgr._convert_cache_to_str = lambda cache: ""
    mgr._register_builtin_tools = lambda: None
    mgr._resolve_realtime_voice = lambda cfg: "yui"
    mgr._drop_free_voice_on_route_flip = lambda old, new: False
    mgr._is_livestream_active = lambda: False
    mgr._make_thinking_active_callback = lambda session: (lambda *a, **k: None)
    mgr._bind_session_lifecycle_callbacks = lambda session: None
    mgr._sync_tools_to_active_session = _async_return(None)
    mgr.input_mode = input_mode

    _StubClient.instances = []
    monkeypatch.setattr(lifecycle, "OmniRealtimeClient", _StubClient)
    monkeypatch.setattr(lifecycle, "OmniOfflineClient", _StubClient)
    monkeypatch.setattr(
        core_facade, "aload_global_conversation_settings",
        _async_return({"noiseReductionEnabled": True}),
    )
    return mgr


def _async_return(value):
    async def _inner(*a, **k):
        return value
    return _inner


async def _new_dialog_task(text="MEMORY"):
    return text


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("input_mode", ["audio", "text"])
async def test_session_creation_runs_end_to_end(monkeypatch, input_mode):
    """Both branches build a client and connect it.

    Mutation check: reverting the voice branch's diagnostic to read
    ``realtime_config`` before it is assigned turns the ``audio`` case red with
    ``UnboundLocalError`` while ``text`` stays green — which is exactly the
    asymmetry that shipped.
    """
    mgr = _make_manager(monkeypatch, input_mode=input_mode)

    count = await LLMSessionManager._start_session_start_llm(
        mgr,
        input_mode,
        await mgr._config_manager.aget_core_config(),
        await mgr._config_manager.aget_model_api_config("realtime"),
        asyncio.create_task(_new_dialog_task()),
        0.0,
    )

    assert count == 0
    assert len(_StubClient.instances) == 1, "应当恰好构造一个 client"
    client = _StubClient.instances[0]
    assert client.connected_with is not None, "构造了 client 却没有 connect"
    assert mgr.session is client, "connect 成功后必须提升为 self.session"


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("input_mode", ["audio", "text"])
async def test_session_creation_does_not_emit_prompt_content(
    monkeypatch, capsys, input_mode
):
    sentinel = "PRIVATE_PROMPT_SENTINEL_2635"
    mgr = _make_manager(monkeypatch, input_mode=input_mode)
    mgr._build_initial_prompt = _async_return(sentinel)

    logged = []

    def _capture_log(message, *args, **kwargs):
        del kwargs
        logged.append(str(message) % args if args else str(message))

    for level in ("debug", "info", "warning", "error", "exception"):
        monkeypatch.setattr(lifecycle.logger, level, _capture_log)

    await LLMSessionManager._start_session_start_llm(
        mgr,
        input_mode,
        await mgr._config_manager.aget_core_config(),
        await mgr._config_manager.aget_model_api_config("realtime"),
        asyncio.create_task(_new_dialog_task()),
        0.0,
    )

    captured = capsys.readouterr()
    emitted = captured.out + captured.err + "\n".join(logged)
    assert sentinel not in emitted


@pytest.mark.unit
def test_lifecycle_never_emits_sensitive_prompt_objects():
    source = inspect.getsource(lifecycle)
    tree = ast.parse(source)
    sensitive_names = {"initial_prompt", "final_prime_text"}
    offenders = []

    for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
        is_output_call = (
            isinstance(call.func, ast.Name)
            and call.func.id == "print"
        ) or (
            isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "logger"
            and call.func.attr in {"debug", "info", "warning", "error", "exception"}
        )
        if not is_output_call:
            continue
        referenced_names = {
            node.id
            for argument in (*call.args, *(keyword.value for keyword in call.keywords))
            for node in ast.walk(argument)
            if isinstance(node, ast.Name)
        }
        leaked = sorted(referenced_names & sensitive_names)
        if leaked:
            offenders.append((call.lineno, leaked))

    assert offenders == []


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("input_mode", ["audio", "text"])
async def test_region_flip_between_prepare_and_connect_is_logged(monkeypatch, input_mode):
    """The route-flip diagnostic fires when the region lands mid-start.

    The whole point of the diagnostic is to make a mid-start flip visible in the
    field, so it has to survive a real call — reading the snapshot handed down
    by ``_start_session_prepare_runtime`` rather than a name that only exists in
    the caller.

    Records straight off the module logger rather than via ``caplog``: the app's
    logging setup puts ``propagate=False`` on the ``N.E.K.O`` parent, so caplog's
    root handler sees nothing once any test has pulled that setup in.
    """
    mgr = _make_manager(monkeypatch, input_mode=input_mode)
    prepare_core = {"CORE_URL": "https://www.lanlan.tech", "DISABLE_TTS": False}
    prepare_realtime = {"base_url": "wss://www.lanlan.tech/v1", "api_key": "k",
                        "model": "m", "api_type": "qwen"}

    warnings = []
    monkeypatch.setattr(
        lifecycle.logger, "warning",
        lambda msg, *a, **k: warnings.append(str(msg) % a if a else str(msg)),
    )

    await LLMSessionManager._start_session_start_llm(
        mgr, input_mode, prepare_core, prepare_realtime,
        asyncio.create_task(_new_dialog_task()), 0.0,
    )

    assert any("[GeoIP]" in w for w in warnings), (
        f"{input_mode} 分支的区域翻转诊断没有触发，实际: {warnings}"
    )
