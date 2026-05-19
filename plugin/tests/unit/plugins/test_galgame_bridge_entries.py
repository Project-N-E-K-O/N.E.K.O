from __future__ import annotations

from _galgame_test_support import *


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_public_surface_preserves_phase1_entries_and_adds_phase2_entries(tmp_path: Path) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    _create_game_dir(
        bridge_root,
        game_id="demo.alpha",
        session_payload=_session(
            game_id="demo.alpha",
            session_id="sess-a",
            last_seq=1,
            state=_session_state(text="alpha"),
        ),
    )

    ctx = _Ctx(plugin_dir, _make_effective_config(bridge_root))
    plugin = GalgameBridgePlugin(ctx)
    startup = await plugin.startup()
    assert isinstance(startup, Ok)

    entry_ids = sorted(
        entry_id
        for entry_id, handler in plugin.collect_entries().items()
        if handler.meta.event_type == "plugin_entry"
    )
    assert entry_ids == [
        "galgame_agent_command",
        "galgame_apply_recommended_ocr_capture_profile",
        "galgame_auto_recalibrate_ocr_dialogue_profile",
        "galgame_bind_game",
        "galgame_build_ocr_screen_template_draft",
        "galgame_continue_auto_advance",
        "galgame_download_rapidocr_models",
        "galgame_evaluate_ocr_screen_awareness_model",
        "galgame_explain_line",
        "galgame_get_character_list",
        "galgame_get_character_profile",
        "galgame_get_history",
        "galgame_get_ocr_screen_awareness_snapshot",
        "galgame_get_push_history",
        "galgame_get_recent_lines",
        "galgame_get_scene_context",
        "galgame_get_snapshot",
        "galgame_get_status",
        "galgame_get_story_so_far",
        "galgame_import_character_data",
        "galgame_install_textractor",
        "galgame_list_memory_reader_processes",
        "galgame_list_ocr_windows",
        "galgame_open_ui",
        "galgame_rollback_ocr_capture_profile",
        "galgame_set_character_mode",
        "galgame_set_llm_vision",
        "galgame_set_memory_reader_target",
        "galgame_set_mode",
        "galgame_set_ocr_backend",
        "galgame_set_ocr_capture_profile",
        "galgame_set_ocr_screen_templates",
        "galgame_set_ocr_timing",
        "galgame_set_ocr_window_target",
        "galgame_set_rapidocr_lang",
        "galgame_suggest_choice",
        "galgame_summarize_scene",
        "galgame_train_ocr_screen_awareness_model",
        "galgame_validate_ocr_screen_templates",
    ]
    for phase1_entry in (
        "galgame_bind_game",
        "galgame_get_history",
        "galgame_get_snapshot",
        "galgame_get_status",
        "galgame_open_ui",
        "galgame_set_mode",
    ):
        assert phase1_entry in entry_ids

    assert plugin.get_list_actions() == [
        {
            "id": "open_ui",
            "kind": "ui",
            "target": "/plugin/galgame_plugin/ui/",
            "open_in": "new_tab",
        }
    ]

    static_ui = plugin.get_static_ui_config()
    assert static_ui is not None
    assert static_ui["plugin_id"] == "galgame_plugin"
    assert Path(str(static_ui["directory"])).name == "static"


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_install_textractor_entry_returns_install_result_and_refreshed_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_root = tmp_path / "TextractorInstalled"
    plugin, _, _ = _make_install_entry_plugin(
        tmp_path,
        memory_reader={
            "enabled": True,
            "install_target_dir": str(install_root),
            "textractor_proxy": "http://127.0.0.1:7890",
        },
    )
    captured_install_kwargs: dict[str, object] = {}

    async def _fake_install_textractor(**kwargs):
        captured_install_kwargs.update(kwargs)
        install_root.mkdir(parents=True, exist_ok=True)
        (install_root / "TextractorCLI.exe").write_text("", encoding="utf-8")
        return {
            "installed": True,
            "already_installed": False,
            "detected_path": str(install_root / "TextractorCLI.exe"),
            "target_dir": str(install_root),
            "expected_executable_path": str(install_root / "TextractorCLI.exe"),
            "install_supported": True,
            "can_install": False,
            "detail": "installed",
            "summary": "Textractor 安装完成",
            "release_name": "v1.0.0",
            "asset_name": "Textractor-x64.zip",
        }

    monkeypatch.setattr(
        "plugin.plugins.galgame_plugin.install_textractor",
        _fake_install_textractor,
    )

    result = await plugin.galgame_install_textractor()

    assert isinstance(result, Ok)
    assert result.value["summary"] == "Textractor 安装完成"
    assert result.value["install_result"]["installed"] is True
    assert result.value["status"]["textractor"]["installed"] is True
    assert result.value["status"]["textractor"]["detected_path"] == str(
        install_root / "TextractorCLI.exe"
    )
    assert captured_install_kwargs["textractor_proxy"] == "http://127.0.0.1:7890"


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_install_textractor_entry_uses_ctx_run_id_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_root = tmp_path / "TextractorInstalled"
    plugin, _, _ = _make_install_entry_plugin(
        tmp_path,
        memory_reader={
            "enabled": True,
            "install_target_dir": str(install_root),
        },
    )

    observed: dict[str, object] = {}

    async def _fake_install_textractor(**kwargs):
        observed.update(kwargs)
        return {
            "installed": True,
            "already_installed": False,
            "detected_path": str(install_root / "TextractorCLI.exe"),
            "target_dir": str(install_root),
            "expected_executable_path": str(install_root / "TextractorCLI.exe"),
            "install_supported": True,
            "can_install": False,
            "detail": "installed",
            "summary": "Textractor install ok",
            "release_name": "v1.0.0",
            "asset_name": "Textractor-x64.zip",
        }

    monkeypatch.setattr(
        "plugin.plugins.galgame_plugin.install_textractor",
        _fake_install_textractor,
    )

    result = await plugin.galgame_install_textractor(_ctx={"run_id": "run-123"})

    assert isinstance(result, Ok)
    assert observed["task_id"] == "run-123"


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_phase2_entries_return_structured_degraded_results_without_target_entry(tmp_path: Path) -> None:
    game_id = "demo.alpha"
    session_id = "sess-a"
    snapshot = _session_state(
        speaker="Yukino",
        text="Current line",
        scene_id="scene-a",
        line_id="line-1",
        choices=[
            {"choice_id": "choice-1", "text": "Yes", "index": 0, "enabled": True},
            {"choice_id": "choice-2", "text": "Later", "index": 1, "enabled": True},
        ],
        is_menu_open=True,
        ts="2026-04-21T08:31:00Z",
    )
    plugin = _make_phase2_entry_plugin(
        tmp_path,
        shared=_shared_state(
            game_id=game_id,
            session_id=session_id,
            last_seq=2,
            snapshot=snapshot,
            history_lines=[
                {
                    "speaker": "Yukino",
                    "text": snapshot["text"],
                    "line_id": "line-1",
                    "scene_id": "scene-a",
                    "route_id": "",
                    "ts": "2026-04-21T08:31:00Z",
                }
            ],
            history_choices=list(snapshot["choices"]),
            active_data_source=DATA_SOURCE_BRIDGE_SDK,
        ),
    )

    explain = await plugin.galgame_explain_line()
    summarize = await plugin.galgame_summarize_scene()
    suggest = await plugin.galgame_suggest_choice()
    agent_status = await plugin.galgame_agent_command(action="query_status")
    agent_reply = await plugin.galgame_agent_command(
        action="query_context",
        context_query="scene query",
    )

    assert isinstance(explain, Ok)
    assert explain.value["degraded"] is True
    assert explain.value["line_id"] == "line-1"
    assert "gateway_unavailable" in explain.value["diagnostic"]

    assert isinstance(summarize, Ok)
    assert summarize.value["degraded"] is True
    assert summarize.value["scene_id"] == "scene-a"

    assert isinstance(suggest, Ok)
    assert suggest.value["degraded"] is True
    assert suggest.value["choices"] == []

    assert isinstance(agent_status, Ok)
    assert agent_status.value["action"] == "query_status"
    assert isinstance(agent_status.value["recent_pushes"], list)

    assert isinstance(agent_reply, Ok)
    assert agent_reply.value["action"] == "query_context"
    assert "scene query" in agent_reply.value["result"]


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_get_story_so_far_uses_existing_scene_summaries(tmp_path: Path) -> None:
    plugin = _make_phase2_entry_plugin(
        tmp_path,
        shared=_shared_state(),
    )
    plugin._game_agent = SimpleNamespace(
        _scene_tracker=SimpleNamespace(
            scene_memory=[
                {
                    "scene_id": "scene-a",
                    "route_id": "",
                    "summary": "雪乃和主角确认放学后的约定。",
                    "push_seq": 7,
                },
                {
                    "scene_id": "scene-b",
                    "route_id": "",
                    "summary": "两人来到中庭，谈起接下来要调查的线索。",
                    "push_seq": 11,
                },
            ]
        )
    )

    result = await plugin.galgame_get_story_so_far()

    assert isinstance(result, Ok)
    assert result.value["available"] is True
    assert "雪乃和主角确认放学后的约定" in result.value["story_so_far"]
    assert "两人来到中庭" in result.value["story_so_far"]
    assert result.value["last_updated_seq"] == 11


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_get_story_so_far_keeps_newer_recorded_summary_when_layer1_is_stale(
    tmp_path: Path,
) -> None:
    plugin = _make_phase2_entry_plugin(
        tmp_path,
        shared=_shared_state(),
    )
    plugin._game_agent = SimpleNamespace(
        _scene_tracker=SimpleNamespace(
            scene_memory=[
                {
                    "scene_id": "scene-a",
                    "route_id": "",
                    "summary": "old layer1 scene summary",
                    "push_seq": 7,
                },
            ]
        )
    )

    plugin._record_story_progress_from_scene_summary(
        scene_id="scene-a",
        summary="new line-count progress summary",
        push_seq=12,
    )

    result = await plugin.galgame_get_story_so_far()

    assert isinstance(result, Ok)
    assert result.value["available"] is True
    assert "new line-count progress summary" in result.value["story_so_far"]
    assert "old layer1 scene summary" not in result.value["story_so_far"]
    assert result.value["last_updated_seq"] == 12


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_get_story_so_far_refreshes_zero_seq_scene_summaries(
    tmp_path: Path,
) -> None:
    plugin = _make_phase2_entry_plugin(
        tmp_path,
        shared=_shared_state(),
    )
    scene_memory: list[dict[str, object]] = [
        {
            "scene_id": "scene-a",
            "route_id": "",
            "summary": "first in-memory summary without push seq",
        },
    ]
    plugin._game_agent = SimpleNamespace(
        _scene_tracker=SimpleNamespace(scene_memory=scene_memory)
    )

    first = await plugin.galgame_get_story_so_far()

    assert isinstance(first, Ok)
    assert "first in-memory summary" in first.value["story_so_far"]
    assert first.value["last_updated_seq"] == 0
    plugin._query_rate_limits["galgame_get_story_so_far"].clear()

    scene_memory.append(
        {
            "scene_id": "scene-b",
            "route_id": "",
            "summary": "second in-memory summary without push seq",
        }
    )

    second = await plugin.galgame_get_story_so_far()

    assert isinstance(second, Ok)
    assert "first in-memory summary" in second.value["story_so_far"]
    assert "second in-memory summary" in second.value["story_so_far"]
    assert second.value["last_updated_seq"] == 0


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_galgame_continue_auto_advance_sets_choice_advisor_and_resumes_agent(tmp_path: Path) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _Ctx(plugin_dir, _make_effective_config(bridge_root))
    plugin = GalgameBridgePlugin(ctx)
    startup = await plugin.startup()
    assert isinstance(startup, Ok)

    assert plugin._game_agent is not None
    plugin._game_agent._explicit_standby = True
    plugin._game_agent._next_actuation_at = 123.0

    result = await plugin.galgame_continue_auto_advance(message="继续推进剧情")

    assert isinstance(result, Ok)
    assert plugin._state.mode == "choice_advisor"
    assert plugin._state.push_notifications is True
    assert result.value["action"] == "continue_auto_advance"
    assert result.value["mode"] == "choice_advisor"
    assert result.value["mode_result"]["success"] is True
    assert result.value["mode_result"]["mode"] == "choice_advisor"
    assert result.value["agent_result"]["action"] == "send_message"
    assert "恢复游戏 LLM" in result.value["agent_result"]["result"]
    assert result.value["status"] == result.value["agent_result"]["status"]
    assert plugin._game_agent._explicit_standby is False
    assert plugin._game_agent._next_actuation_at == 0.0


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_galgame_continue_auto_advance_preserves_mode_result_schema_when_mode_already_applied(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _Ctx(plugin_dir, _make_effective_config(bridge_root))
    plugin = GalgameBridgePlugin(ctx)
    startup = await plugin.startup()
    assert isinstance(startup, Ok)

    assert plugin._game_agent is not None
    plugin._game_agent._explicit_standby = True
    plugin._game_agent._next_actuation_at = 123.0
    with plugin._state_lock:
        plugin._state.mode = "choice_advisor"
        plugin._state.push_notifications = True
        plugin._state.advance_speed = "medium"

    result = await plugin.galgame_continue_auto_advance(message="继续推动剧情")

    assert isinstance(result, Ok)
    assert result.value["action"] == "continue_auto_advance"
    assert result.value["mode_result"]["success"] is True
    assert result.value["mode_result"]["mode"] == "choice_advisor"
    assert result.value["mode_result"]["push_notifications"] is True
    mode_payload = result.value["mode_result"]["result"]
    assert mode_payload["mode"] == "choice_advisor"
    assert mode_payload["push_notifications"] is True
    assert mode_payload["advance_speed"] == "medium"
    assert mode_payload["reader_mode"] == plugin._cfg.reader_mode
    assert mode_payload["summary"] == (
        "mode=choice_advisor "
        "push_notifications=True "
        "advance_speed=medium "
        f"reader_mode={plugin._cfg.reader_mode}"
    )
    assert mode_payload["skipped"] is True
    assert mode_payload["skip_reason"] == "already_applied"
    assert result.value["agent_result"]["action"] == "send_message"
    assert "恢复游戏 LLM" in result.value["agent_result"]["result"]
    assert plugin._game_agent._explicit_standby is False
    assert plugin._game_agent._next_actuation_at == 0.0


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_phase2_entries_mark_memory_reader_input_as_degraded_even_when_llm_succeeds(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    game_id = "mem-1a2b3c4d5e6f"
    session_id = "mem-session"
    _create_game_dir(
        bridge_root,
        game_id=game_id,
        session_payload=_memory_reader_session(
            game_id=game_id,
            session_id=session_id,
            last_seq=2,
            state=_session_state(
                speaker="雪乃",
                text="这是内存读取来的台词。",
                scene_id="mem:unknown_scene",
                line_id="mem:line-1",
                choices=[
                    {"choice_id": "mem:line-1#choice0", "text": "去教室", "index": 0, "enabled": True},
                    {"choice_id": "mem:line-1#choice1", "text": "去天台", "index": 1, "enabled": True},
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
                    "text": "这是内存读取来的台词。",
                    "line_id": "mem:line-1",
                    "scene_id": "mem:unknown_scene",
                    "route_id": "",
                },
                ts="2026-04-21T08:31:00Z",
            ),
            _event(
                seq=2,
                event_type="choices_shown",
                session_id=session_id,
                game_id=game_id,
                payload={
                    "line_id": "mem:line-1",
                    "scene_id": "mem:unknown_scene",
                    "route_id": "",
                    "choices": [
                        {"choice_id": "mem:line-1#choice0", "text": "去教室", "index": 0, "enabled": True},
                        {"choice_id": "mem:line-1#choice1", "text": "去天台", "index": 1, "enabled": True},
                    ],
                },
                ts="2026-04-21T08:31:01Z",
            ),
        ],
    )

    ctx = _Ctx(
        plugin_dir,
        _make_effective_config(
            bridge_root,
            llm={"target_entry_ref": "fake_llm:run"},
            ocr_reader={"enabled": False},
            rapidocr={"enabled": False},
        ),
    )

    async def _handler(**kwargs):
        params = kwargs.get("params") or {}
        operation = params.get("operation")
        if operation == "explain_line":
            return {"explanation": "这是对台词的解释。", "evidence": []}
        if operation == "summarize_scene":
            return {
                "summary": "这是对场景的总结。",
                "key_points": [{"type": "plot", "text": "剧情仍在推进。"}],
            }
        if operation == "suggest_choice":
            context = params.get("context") or {}
            visible_choices = context.get("visible_choices") or []
            return {
                "choices": [
                    {
                        "choice_id": visible_choices[0]["choice_id"],
                        "text": visible_choices[0]["text"],
                        "rank": 1,
                        "reason": "优先继续主线。",
                    }
                ]
            }
        raise AssertionError(f"unexpected operation: {operation}")

    ctx.entry_handler = _handler
    plugin = GalgameBridgePlugin(ctx)
    startup = await plugin.startup()
    assert isinstance(startup, Ok)
    try:
        plugin._memory_reader_manager = SimpleNamespace(
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
                        "pid": 4242,
                        "engine": "unknown",
                        "game_id": game_id,
                        "session_id": session_id,
                        "last_seq": 2,
                        "last_event_ts": "2026-04-21T08:31:01Z",
                    },
                ),
            ),
            shutdown=lambda: asyncio.sleep(0, result=None),
        )
        await plugin._poll_bridge(force=True)

        status = await plugin.galgame_get_status()
        explain = await plugin.galgame_explain_line()
        summarize = await plugin.galgame_summarize_scene()
        suggest = await plugin.galgame_suggest_choice()

        assert isinstance(status, Ok)
        assert status.value["active_data_source"] == DATA_SOURCE_MEMORY_READER

        assert isinstance(explain, Ok)
        assert explain.value["degraded"] is True
        assert "memory_reader_input" in explain.value["diagnostic"]
        assert "weaker than bridge_sdk" in explain.value["diagnostic"]
        assert explain.value["input_source"] == DATA_SOURCE_MEMORY_READER
        assert explain.value["semantic_degraded"] is True
        assert explain.value["fallback_used"] is False
        assert explain.value["explanation"] == "这是对台词的解释。"

        assert isinstance(summarize, Ok)
        assert summarize.value["degraded"] is True
        assert "memory_reader_input" in summarize.value["diagnostic"]
        assert "weaker than bridge_sdk" in summarize.value["diagnostic"]
        assert summarize.value["input_source"] == DATA_SOURCE_MEMORY_READER
        assert summarize.value["semantic_degraded"] is True
        assert summarize.value["fallback_used"] is False
        assert summarize.value["summary"] == "这是对场景的总结。"

        assert isinstance(suggest, Ok)
        assert suggest.value["degraded"] is True
        assert "memory_reader_input" in suggest.value["diagnostic"]
        assert "weaker than bridge_sdk" in suggest.value["diagnostic"]
        assert suggest.value["input_source"] == DATA_SOURCE_MEMORY_READER
        assert suggest.value["semantic_degraded"] is True
        assert suggest.value["fallback_used"] is False
        assert suggest.value["choices"][0]["choice_id"] == "mem:line-1#choice0"
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_phase2_entries_mark_ocr_reader_input_as_degraded_even_when_llm_succeeds(
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
                    {"choice_id": "ocr:line-1#choice1", "text": "去天台", "index": 1, "enabled": True},
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
            _event(
                seq=2,
                event_type="choices_shown",
                session_id=session_id,
                game_id=game_id,
                payload={
                    "line_id": "ocr:line-1",
                    "scene_id": "ocr:scene-a",
                    "route_id": "",
                    "choices": [
                        {"choice_id": "ocr:line-1#choice0", "text": "去教室", "index": 0, "enabled": True},
                        {"choice_id": "ocr:line-1#choice1", "text": "去天台", "index": 1, "enabled": True},
                    ],
                },
                ts="2026-04-21T08:31:01Z",
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
        params = kwargs.get("params") or {}
        operation = params.get("operation")
        if operation == "explain_line":
            return {"explanation": "这是对 OCR 台词的解释。", "evidence": []}
        if operation == "summarize_scene":
            return {
                "summary": "这是对 OCR 场景的总结。",
                "key_points": [{"type": "plot", "text": "OCR 主线可用。"}],
            }
        if operation == "suggest_choice":
            context = params.get("context") or {}
            visible_choices = context.get("visible_choices") or []
            return {
                "choices": [
                    {
                        "choice_id": visible_choices[0]["choice_id"],
                        "text": visible_choices[0]["text"],
                        "rank": 1,
                        "reason": "OCR 下优先继续主线。",
                    }
                ]
            }
        raise AssertionError(f"unexpected operation: {operation}")

    ctx.entry_handler = _handler
    plugin = GalgameBridgePlugin(ctx)
    startup = await plugin.startup()
    assert isinstance(startup, Ok)
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
                        "last_seq": 2,
                        "last_event_ts": "2026-04-21T08:31:01Z",
                    },
                ),
            ),
            shutdown=lambda: asyncio.sleep(0, result=None),
        )
        await plugin._poll_bridge(force=True)

        status = await plugin.galgame_get_status()
        explain = await plugin.galgame_explain_line()
        summarize = await plugin.galgame_summarize_scene()
        suggest = await plugin.galgame_suggest_choice()

        assert isinstance(status, Ok)
        assert status.value["active_data_source"] == DATA_SOURCE_OCR_READER

        assert isinstance(explain, Ok)
        assert explain.value["degraded"] is True
        assert explain.value["input_source"] == DATA_SOURCE_OCR_READER
        assert explain.value["semantic_degraded"] is True
        assert explain.value["fallback_used"] is False
        assert "ocr_reader_input" in explain.value["diagnostic"]
        assert explain.value["explanation"] == "这是对 OCR 台词的解释。"

        assert isinstance(summarize, Ok)
        assert summarize.value["degraded"] is True
        assert summarize.value["input_source"] == DATA_SOURCE_OCR_READER
        assert summarize.value["semantic_degraded"] is True
        assert summarize.value["fallback_used"] is False
        assert "ocr_reader_input" in summarize.value["diagnostic"]
        assert summarize.value["summary"] == "这是对 OCR 场景的总结。"

        assert isinstance(suggest, Ok)
        assert suggest.value["degraded"] is True
        assert suggest.value["input_source"] == DATA_SOURCE_OCR_READER
        assert suggest.value["semantic_degraded"] is True
        assert suggest.value["fallback_used"] is False
        assert "ocr_reader_input" in suggest.value["diagnostic"]
        assert suggest.value["choices"][0]["choice_id"] == "ocr:line-1#choice0"
    finally:
        await plugin.shutdown()
