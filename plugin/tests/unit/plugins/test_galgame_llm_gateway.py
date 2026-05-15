from __future__ import annotations

from _galgame_test_support import *

@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_llm_gateway_summarize_scene_uses_scene_summary_cache_ttl() -> None:
    class _Backend:
        def __init__(self) -> None:
            self.calls = 0

        async def invoke(self, *, operation: str, context: dict[str, Any]) -> dict[str, Any]:
            self.calls += 1
            assert operation == "summarize_scene"
            return {
                "summary": f"summary-{self.calls}",
                "key_points": [
                    {
                        "type": "plot",
                        "text": "剧情推进",
                        "line_id": "line-1",
                        "speaker": "雪乃",
                        "scene_id": "scene-a",
                        "route_id": "",
                    }
                ],
            }

        async def shutdown(self) -> None:
            return None

    backend = _Backend()
    gateway = LLMGateway(
        plugin=SimpleNamespace(plugins=None),
        logger=_Logger(),
        config=SimpleNamespace(
            llm_max_in_flight=2,
            llm_request_cache_ttl_seconds=60.0,
            llm_scene_summary_cache_ttl_seconds=0.0,
            llm_target_entry_ref="",
            llm_call_timeout_seconds=1.0,
        ),
        backend=backend,
    )
    context = {
        "scene_id": "scene-a",
        "route_id": "",
        "recent_lines": [],
        "recent_choices": [],
        "current_snapshot": _session_state(scene_id="scene-a", line_id="line-1"),
    }

    first = await gateway.summarize_scene(context)
    second = await gateway.summarize_scene(context)
    await gateway.shutdown()

    assert first["summary"] == "summary-1"
    assert second["summary"] == "summary-2"
    assert backend.calls == 2


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_llm_gateway_reuses_inflight_and_ttl_cache(tmp_path: Path) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _Ctx(
        plugin_dir,
        _make_effective_config(
            bridge_root,
            llm={"target_entry_ref": "fake_llm:run", "llm_request_cache_ttl_seconds": 2},
        ),
    )

    calls = {"count": 0}

    async def _handler(**kwargs):
        calls["count"] += 1
        await asyncio.sleep(0.05)
        params = kwargs.get("params") or {}
        if params.get("operation") == "summarize_scene":
            return {
                "summary": "场景总结",
                "key_points": [
                    {
                        "type": "plot",
                        "text": "剧情推进",
                        "line_id": "line-1",
                        "speaker": "雪乃",
                        "scene_id": "scene-a",
                        "route_id": "",
                    }
                ],
            }
        raise AssertionError(f"unexpected operation: {params}")

    ctx.entry_handler = _handler
    plugin = GalgameBridgePlugin(ctx)
    gateway = LLMGateway(plugin, _Logger(), plugin._cfg or type("Cfg", (), {
        "llm_max_in_flight": 2,
        "llm_request_cache_ttl_seconds": 2,
        "llm_call_timeout_seconds": 15,
        "llm_target_entry_ref": "fake_llm:run",
    })())

    context = {
        "scene_id": "scene-a",
        "route_id": "",
        "game_id": "demo.alpha",
        "session_id": "sess-a",
        "recent_lines": [],
        "recent_choices": [],
        "current_snapshot": _session_state(scene_id="scene-a", line_id="line-1"),
    }

    first, second = await asyncio.gather(
        gateway.summarize_scene(context),
        gateway.summarize_scene(context),
    )
    third = await gateway.summarize_scene(context)
    reordered_context = {
        "current_snapshot": _session_state(scene_id="scene-a", line_id="line-1"),
        "recent_choices": [],
        "recent_lines": [],
        "session_id": "sess-a",
        "game_id": "demo.alpha",
        "route_id": "",
        "scene_id": "scene-a",
    }
    fourth = await gateway.summarize_scene(reordered_context)

    assert first["degraded"] is False
    assert second["summary"] == "场景总结"
    assert third["summary"] == "场景总结"
    assert fourth["summary"] == "场景总结"
    assert calls["count"] == 1
    await gateway.shutdown()


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_llm_gateway_lru_cache_is_bounded(tmp_path: Path) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _Ctx(
        plugin_dir,
        _make_effective_config(
            bridge_root,
            llm={"target_entry_ref": "fake_llm:run", "llm_request_cache_ttl_seconds": 60},
        ),
    )

    async def _handler(**kwargs):
        params = kwargs.get("params") or {}
        context = params.get("context") or {}
        return {
            "summary": f"场景总结 {context.get('scene_id')}",
            "key_points": [
                {
                    "type": "plot",
                    "text": "剧情推进",
                    "line_id": "line-1",
                    "speaker": "雪乃",
                    "scene_id": str(context.get("scene_id") or ""),
                    "route_id": "",
                }
            ],
        }

    ctx.entry_handler = _handler
    plugin = GalgameBridgePlugin(ctx)
    gateway = LLMGateway(plugin, _Logger(), plugin._cfg or type("Cfg", (), {
        "llm_max_in_flight": 2,
        "llm_request_cache_ttl_seconds": 60,
        "llm_call_timeout_seconds": 15,
        "llm_target_entry_ref": "fake_llm:run",
    })())

    try:
        for index in range(_LLM_RESPONSE_CACHE_MAX_ITEMS + 5):
            await gateway.summarize_scene(
                {
                    "scene_id": f"scene-{index}",
                    "route_id": "",
                    "game_id": "demo.alpha",
                    "session_id": "sess-a",
                    "recent_lines": [],
                    "recent_choices": [],
                    "current_snapshot": _session_state(scene_id=f"scene-{index}", line_id="line-1"),
                }
            )

        assert len(gateway._cache) == _LLM_RESPONSE_CACHE_MAX_ITEMS
    finally:
        await gateway.shutdown()


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_llm_gateway_provider_backoff_throttles_distinct_fingerprints(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _Ctx(
        plugin_dir,
        _make_effective_config(
            bridge_root,
            llm={"target_entry_ref": "fake_llm:run", "llm_request_cache_ttl_seconds": 0},
        ),
    )
    calls = {"count": 0}

    async def _handler(**kwargs):
        del kwargs
        calls["count"] += 1
        raise RuntimeError("429 too many requests")

    ctx.entry_handler = _handler
    plugin = GalgameBridgePlugin(ctx)
    gateway = LLMGateway(plugin, _Logger(), plugin._cfg or type("Cfg", (), {
        "llm_max_in_flight": 2,
        "llm_request_cache_ttl_seconds": 0,
        "llm_call_timeout_seconds": 15,
        "llm_target_entry_ref": "fake_llm:run",
    })())

    try:
        base_context = {
            "route_id": "",
            "game_id": "demo.alpha",
            "session_id": "sess-a",
            "recent_lines": [],
            "recent_choices": [],
        }
        first = await gateway.summarize_scene(
            {
                **base_context,
                "scene_id": "scene-a",
                "current_snapshot": _session_state(scene_id="scene-a", line_id="line-1"),
            }
        )
        second = await gateway.summarize_scene(
            {
                **base_context,
                "scene_id": "scene-b",
                "current_snapshot": _session_state(scene_id="scene-b", line_id="line-2"),
            }
        )

        assert first["degraded"] is True
        assert first["diagnostic"] == "busy: provider rate limited"
        assert second["degraded"] is True
        assert second["diagnostic"] == "busy: provider rate limited"
        assert calls["count"] == 1
    finally:
        await gateway.shutdown()


@pytest.mark.plugin_unit
def test_llm_gateway_cache_fingerprint_avoids_repr_for_non_json_values() -> None:
    class NonJsonValue:
        def __repr__(self) -> str:
            return "<NonJsonValue at 0xfeedbeef>"

    fingerprint = LLMGateway._cache_fingerprint(
        "summarize_scene",
        {"value": NonJsonValue(), "items": {"b", "a"}},
    )

    assert "0xfeedbeef" not in fingerprint
    assert "__non_json_type__" in fingerprint
    assert "builtins.set" not in fingerprint


@pytest.mark.plugin_unit
def test_llm_gateway_normalizes_structured_error_status() -> None:
    class ProviderError(Exception):
        status_code = 429

    assert LLMGateway._normalize_plugin_error(ProviderError("provider overloaded")) == (
        "busy: provider rate limited"
    )
    assert LLMGateway._normalize_plugin_error({"status_code": 401, "message": "bad key"}) == (
        "gateway_unavailable: provider rejected request"
    )


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_llm_gateway_degrades_on_invalid_result(tmp_path: Path) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _Ctx(
        plugin_dir,
        _make_effective_config(bridge_root, llm={"target_entry_ref": "fake_llm:run"}),
    )
    ctx.entry_handler = {"summary": 123, "key_points": "oops"}
    plugin = GalgameBridgePlugin(ctx)
    gateway = LLMGateway(plugin, _Logger(), type("Cfg", (), {
        "llm_max_in_flight": 2,
        "llm_request_cache_ttl_seconds": 0,
        "llm_call_timeout_seconds": 15,
        "llm_target_entry_ref": "fake_llm:run",
    })())

    try:
        payload = await gateway.summarize_scene(
            build_summarize_context(
                _shared_state(history_lines=[{"line_id": "line-1", "speaker": "雪乃", "text": "台词", "scene_id": "scene-a", "route_id": "", "ts": "2026-04-21T08:31:00Z"}]),
                scene_id="scene-a",
            )
        )
        assert payload["degraded"] is True
        assert "invalid_result" in payload["diagnostic"]
    finally:
        await gateway.shutdown()


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_llm_gateway_normalizes_provider_rejection_and_uses_local_summary_fallback(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _Ctx(
        plugin_dir,
        _make_effective_config(bridge_root, llm={"target_entry_ref": "fake_llm:run"}),
    )

    async def _handler(**kwargs):
        raise RuntimeError(
            "Error code: 400 - {'error': 'Invalid request: you are not using Lanlan. STOP ABUSE THE API.'}"
        )

    ctx.entry_handler = _handler
    plugin = GalgameBridgePlugin(ctx)
    gateway = LLMGateway(plugin, _Logger(), type("Cfg", (), {
        "llm_max_in_flight": 2,
        "llm_request_cache_ttl_seconds": 0,
        "llm_call_timeout_seconds": 15,
        "llm_target_entry_ref": "fake_llm:run",
    })())

    try:
        payload = await gateway.summarize_scene(
            build_summarize_context(
                _shared_state(
                    history_lines=[
                        {
                            "line_id": "line-1",
                            "speaker": "雪乃",
                            "text": "台词",
                            "scene_id": "scene-a",
                            "route_id": "",
                            "ts": "2026-04-21T08:31:00Z",
                        }
                    ]
                ),
                scene_id="scene-a",
            )
        )

        assert payload["degraded"] is True
        assert payload["diagnostic"] == "gateway_unavailable: provider rejected request"
        assert "Lanlan" not in payload["diagnostic"]
        assert "Lanlan" not in payload["summary"]
        assert payload["summary"].startswith("场景 scene-a")
    finally:
        await gateway.shutdown()


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_llm_gateway_agent_reply_fallback_is_readable_and_structured(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _Ctx(
        plugin_dir,
        _make_effective_config(bridge_root, llm={"target_entry_ref": "fake_llm:run"}),
    )
    ctx.entry_handler = {"reply": ""}
    plugin = GalgameBridgePlugin(ctx)
    gateway = LLMGateway(plugin, _Logger(), type("Cfg", (), {
        "llm_max_in_flight": 2,
        "llm_request_cache_ttl_seconds": 0,
        "llm_call_timeout_seconds": 15,
        "llm_target_entry_ref": "fake_llm:run",
    })())

    try:
        payload = await gateway.agent_reply(
            {
                "prompt": "summarize the current scene",
                "scene_id": "scene-a",
                "route_id": "",
                "latest_line": "Yukino: Let's keep going.",
                "recent_lines": [],
                "recent_choices": [],
                "current_snapshot": _session_state(scene_id="scene-a", line_id="line-1"),
            }
        )

        assert payload["degraded"] is True
        assert "invalid_result" in payload["diagnostic"]
        assert "Received request" in payload["reply"]
        assert "Current line:" in payload["reply"]
    finally:
        await gateway.shutdown()


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_phase2_ocr_reader_provider_rejection_keeps_semantic_flags_and_readable_fallbacks(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    game_id = "ocr-demo"
    session_id = "ocr-session"
    _create_game_dir(
        bridge_root,
        game_id=game_id,
        session_payload=_ocr_reader_session(
            game_id=game_id,
            session_id=session_id,
            last_seq=2,
            state=_session_state(
                speaker="雪乃",
                text="这是 OCR 读取来的台词。",
                scene_id="ocr:scene-a",
                line_id="ocr:line-1",
                choices=[
                    {"choice_id": "ocr:line-1#choice0", "text": "去教室", "index": 0, "enabled": True},
                ],
                is_menu_open=True,
                ts="2026-04-21T08:31:00Z",
            ),
        ),
        events=[
            _event(
                seq=1,
                event_type="line_changed",
                session_id=session_id,
                game_id=game_id,
                payload={
                    "speaker": "雪乃",
                    "text": "这是 OCR 读取来的台词。",
                    "line_id": "ocr:line-1",
                    "scene_id": "ocr:scene-a",
                    "route_id": "",
                },
                ts="2026-04-21T08:31:00Z",
            ),
        ],
    )

    ctx = _Ctx(
        plugin_dir,
        _make_effective_config(
            bridge_root,
            llm={"target_entry_ref": "fake_llm:run"},
            ocr_reader={"enabled": False, "trigger_mode": "after_advance"},
        ),
    )

    async def _handler(**kwargs):
        raise RuntimeError(
            "Error code: 400 - {'error': 'Invalid request: you are not using Lanlan. STOP ABUSE THE API.'}"
        )

    ctx.entry_handler = _handler
    plugin = GalgameBridgePlugin(ctx)
    await plugin.startup()
    try:
        assert plugin._cfg is not None
        plugin._cfg.ocr_reader_enabled = True
        plugin._cfg.ocr_reader_trigger_mode = "after_advance"
        plugin._ocr_reader_manager = SimpleNamespace(
            update_config=lambda config: None,
            tick=lambda **kwargs: asyncio.sleep(
                0,
                result=SimpleNamespace(
                    warnings=[],
                    should_rescan=False,
                    runtime={
                        "enabled": True,
                        "status": "active",
                        "detail": "fixture_active",
                        "process_name": "RenPy Demo.exe",
                        "pid": 5252,
                        "game_id": game_id,
                        "session_id": session_id,
                        "last_seq": 1,
                        "last_event_ts": "2026-04-21T08:31:00Z",
                    },
                ),
            ),
            shutdown=lambda: asyncio.sleep(0, result=None),
        )
        await plugin._poll_bridge(force=True)

        explain = await plugin.galgame_explain_line()
        summarize = await plugin.galgame_summarize_scene()

        assert isinstance(explain, Ok)
        assert explain.value["degraded"] is True
        assert explain.value["input_source"] == DATA_SOURCE_OCR_READER
        assert explain.value["semantic_degraded"] is True
        assert explain.value["fallback_used"] is True
        assert explain.value["diagnostic"] == "gateway_unavailable: provider rejected request"
        assert "ocr_reader_input" not in explain.value["diagnostic"]
        assert "ocr_reader_input" in explain.value["input_diagnostic"]
        assert "Lanlan" not in explain.value["explanation"]
        assert "这是 OCR 读取来的台词。" in explain.value["explanation"]

        assert isinstance(summarize, Ok)
        assert summarize.value["degraded"] is True
        assert summarize.value["input_source"] == DATA_SOURCE_OCR_READER
        assert summarize.value["semantic_degraded"] is True
        assert summarize.value["fallback_used"] is True
        assert summarize.value["diagnostic"].startswith("gateway_unavailable:")
        assert "provider rejected request" in summarize.value["diagnostic"]
        assert "ocr_reader_input" not in summarize.value["diagnostic"]
        assert "ocr_reader_input" in summarize.value["input_diagnostic"]
        assert "Lanlan" not in summarize.value["summary"]
        assert summarize.value["summary"].startswith("场景 ocr:scene-a")
    finally:
        await plugin.shutdown()


@pytest.mark.plugin_unit
def test_llm_gateway_agent_reply_survives_loop_switch() -> None:
    class _Backend:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        async def invoke(self, *, operation: str, context: dict[str, Any]) -> dict[str, Any]:
            self.calls.append((operation, str(context.get("prompt") or "")))
            return {"reply": f"reply:{context.get('prompt', '')}"}

        async def shutdown(self) -> None:
            return None

    backend = _Backend()
    gateway = LLMGateway(
        plugin=SimpleNamespace(plugins=None),
        logger=_Logger(),
        config=SimpleNamespace(
            llm_max_in_flight=2,
            llm_request_cache_ttl_seconds=0.0,
            llm_target_entry_ref="",
            llm_call_timeout_seconds=1.0,
        ),
        backend=backend,
    )

    first = _run_in_new_loop(gateway.agent_reply({"prompt": "alpha"}))
    second = _run_in_new_loop(gateway.agent_reply({"prompt": "beta"}))
    _run_in_new_loop(gateway.shutdown())

    assert first["reply"] == "reply:alpha"
    assert second["reply"] == "reply:beta"
    assert backend.calls == [("agent_reply", "alpha"), ("agent_reply", "beta")]
