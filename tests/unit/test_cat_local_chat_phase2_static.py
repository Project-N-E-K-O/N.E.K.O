from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CHAT_HOST = ROOT / "static" / "app" / "app-react-chat-window"
INTERPAGE = ROOT / "static" / "app" / "app-interpage"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_cat_local_chat_scripts_are_loaded_in_both_chat_hosts():
    for template_name in ("index.html", "chat.html"):
        source = read(ROOT / "templates" / template_name)
        bootstrap = "app-react-chat-window/bootstrap-state-and-geometry.js"
        messages = "app-react-chat-window/geometry-and-messages.js"
        actions = "app-react-chat-window/message-bundle-actions-and-prompts.js"
        dock = "app-react-chat-window/minimize-and-idle-dock.js"
        lexicon = "app-react-chat-window/cat-local-chat-lexicon.js"
        manager = "app-react-chat-window/cat-local-chat.js"
        api = "app-react-chat-window/resize-drag-and-api.js"
        assert (
            source.index(bootstrap)
            < source.index(messages)
            < source.index(actions)
            < source.index(dock)
            < source.index(lexicon)
            < source.index(manager)
            < source.index(api)
        )


def test_cat_submit_is_diverted_before_normal_chat_and_uses_existing_transport():
    submit_source = read(CHAT_HOST / "message-bundle-actions-and-prompts.js")
    submit_block = submit_source.split("function handleComposerSubmit(payload)", 1)[1].split(
        "function prepareCompactHistoryDropSubmit", 1
    )[0]
    assert submit_block.index("isCatLocalChatActive") < submit_block.index("var hasAttachments")
    assert submit_block.index("submitCatLocalChatText") < submit_block.index("state.onComposerSubmit")

    interpage_source = read(INTERPAGE / "composer-voice-sync.js")
    assert "action: 'cat_local_text_submit'" in interpage_source
    assert "cat_active:" in interpage_source
    assert "cat_tier:" in interpage_source
    assert "cat_entered_at:" in interpage_source
    assert "cat_items:" in interpage_source
    assert "new BroadcastChannel" not in interpage_source


def test_cat_manager_stays_temporary_and_only_observes_accepted_local_text():
    manager_source = read(CHAT_HOST / "cat-local-chat.js")
    lexicon_source = read(CHAT_HOST / "cat-local-chat-lexicon.js")
    assert "MAX_ITEMS" in manager_source
    assert "requestId" in manager_source
    assert "enteredAt" in manager_source
    observation_block = manager_source.split("function observeAcceptedLocalText(requestId)", 1)[1].split(
        "function scheduleNextReply", 1
    )[0]
    assert "catMind.observe({" in observation_block
    assert "type: 'cat_local_text_received'" in observation_block
    assert "requestId: requestId" in observation_block
    assert "text:" not in observation_block
    assert "fetch(" not in manager_source
    assert "WebSocket" not in manager_source
    assert "喵" not in manager_source
    assert "meows:" in lexicon_source
    assert "punctuation:" in lexicon_source
    assert "kaomoji:" in lexicon_source


def test_cat1_hiss_easter_egg_reuses_the_independent_stretch_presentation():
    manager_source = read(CHAT_HOST / "cat-local-chat.js")
    lexicon_source = read(CHAT_HOST / "cat-local-chat-lexicon.js")
    actions_source = read(
        ROOT / "static" / "avatar" / "avatar-ui-buttons" / "idle-actions-and-audio.js"
    )
    core_source = read(ROOT / "static" / "avatar" / "avatar-ui-buttons" / "core.js")
    index_source = read(ROOT / "templates" / "index.html")

    assert "CAT1_HISS_STRETCH_EASTER_EGG_RATE = 0.05" in manager_source
    assert "window.NekoCatIdlePresentation" in manager_source
    assert "requestCat1HissStretch()" in manager_source
    assert "cat1-chat-angry.gif" in manager_source
    assert "cat1_stretch_done_near_chat" not in manager_source
    assert "window.NekoCatIdlePresentation = Object.freeze" in actions_source
    assert "requestCat1HissStretch: _requestNekoIdleCat1HissStretchPresentation" in actions_source
    assert "requestCat1Stretch:" not in actions_source
    assert "_NEKO_IDLE_CAT1_CHAT_HISS_SOUND_URL" in actions_source
    assert "cat1-voice-chat-angry.mp3" in core_source
    assert "appendStickerItem(hissReply.stickerUrl, reply, pending.requestId);" in manager_source
    assert "'ฅ(`ꈊ´ฅ)'" in lexicon_source
    assert "'(ฅ`ω´ฅ)'" in lexicon_source
    assert index_source.index("idle-actions-and-audio.js") < index_source.index("cat-local-chat.js")


def test_cat_text_only_prop_is_shared_and_auto_dock_is_guarded():
    schema = read(ROOT / "frontend" / "react-neko-chat" / "src" / "message-schema.ts")
    compact = read(ROOT / "frontend" / "react-neko-chat" / "src" / "App.tsx")
    full = read(ROOT / "frontend" / "react-neko-chat" / "src" / "FullChatSurface.tsx")
    dock = read(CHAT_HOST / "minimize-and-idle-dock.js")
    host = read(CHAT_HOST / "geometry-and-messages.js")
    bootstrap = read(CHAT_HOST / "bootstrap-state-and-geometry.js")
    assert "catLocalTextOnly: z.boolean().optional()" in schema
    assert "catLocalTextOnly: catLocalTextOnly" in host
    assert "catLocalTextOnly = false" in compact
    assert "catLocalTextOnly = false" in full
    effective_hidden = bootstrap.split(
        "I.getEffectiveComposerHidden = function getEffectiveComposerHidden()",
        1,
    )[1].split("I.getNekoGoodbyeModeActive", 1)[0]
    assert "!catLocalTextOnly" in effective_hidden
    assert "I.state.composerHidden || I.state.goodbyeComposerHidden" in effective_hidden
    assert dock.count("isCatLocalChatActive()) return") >= 2
    compact_restore = dock.split("I.state.chatSurfaceMode = normalized;", 1)[1].split(
        "I.renderWindow();", 1
    )[0]
    assert "if (normalized === 'compact')" in compact_restore
    assert "I.isCatLocalChatActive()" in compact_restore
    assert "I.setCompactChatState('input');" in compact_restore
