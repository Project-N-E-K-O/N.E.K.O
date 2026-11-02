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

"""Static contract of the visit chat surface (design §5 PR-12, §4.5, §3.6.6).

What the PR CI can check without a browser: the display socket wiring in
``app-websocket.js`` (bind first, every downlink type, every status code
spelled as the wire constant, the Blob branch untouched), the plain-text
rendering of visit lines on both layers (React schema + branch, the adapter,
the local bubble), the endpoints the chat surface and the persona panel call,
and the 8 locales.
"""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DESIGN = ROOT / "docs" / "design" / "visit-infrastructure.md"
WS = ROOT / "static" / "app" / "app-websocket.js"
CHAT = ROOT / "static" / "app" / "app-react-chat-window" / "visit-chat.js"
DISPLAY_RELAY = ROOT / "static" / "visit" / "display-relay.js"
PERSONA = ROOT / "static" / "app" / "visit-persona-panel.js"
ADAPTER = ROOT / "static" / "app" / "app-chat-adapter.js"
SUBMIT = ROOT / "static" / "app" / "app-react-chat-window" / "message-bundle-actions-and-prompts.js"
SCHEMA = ROOT / "frontend" / "react-neko-chat" / "src" / "message-schema.ts"
BLOCK_VIEW = ROOT / "frontend" / "react-neko-chat" / "src" / "MessageBlockView.tsx"
INDEX_CSS = ROOT / "static" / "css" / "index.css"
PERSONA_WINDOW_CSS = ROOT / "static" / "css" / "visit_persona_window.css"
PERSONA_TEMPLATE = ROOT / "templates" / "visit_persona.html"
TEMPLATES = (ROOT / "templates" / "index.html", ROOT / "templates" / "chat.html")
LOCALES = ("zh-CN", "zh-TW", "en", "ja", "ko", "ru", "es", "pt")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _function_body(source: str, name: str) -> str:
    """Body of ``function name(...) {...}`` (brace matched; good enough for these files)."""
    match = re.search(r"function\s+" + re.escape(name) + r"\s*\([^)]*\)\s*\{", source)
    assert match, name
    depth, i = 1, match.end()
    while depth:
        ch = source[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        i += 1
    return source[match.end():i - 1]


def _design_section(title: str) -> str:
    text = _read(DESIGN)
    start = text.index(title)
    end = text.index("\n#### ", start + len(title))
    return text[start:end]


def _design_state_actions() -> set[str]:
    section = _design_section("#### visit_state_change")
    match = re.search(r"action:((?:'[a-z_]+'\|?)+)", section)
    assert match
    return set(re.findall(r"'([a-z_]+)'", match.group(1)))


def _design_status_codes() -> set[str]:
    section = _design_section("#### status（串门码）")
    match = re.search(r"code ∈ \{([^}]*)\}", section)
    assert match
    return {code.strip() for code in match.group(1).split(",")}


# ── display socket（app-websocket.js）────────────────────────────────


def test_onopen_sends_visit_bind_with_the_csrf_token():
    source = _read(WS)
    onopen = source[source.index("S.socket.onopen = function"):source.index("S.socket.onmessage = function")]
    assert "_sendVisitBind(_thisSocket)" in onopen
    bind = _function_body(source, "_sendVisitBind")
    assert "action: 'visit_bind'" in bind and "csrf_token: token" in bind
    assert "window.nekoDesktopRuntime" in bind and "desktop_run_id: desktopRunId" in bind
    # Token uses the same source as local HTTP mutation endpoints; nekoLocalMutationSecurity exposes only getMutationHeaders / refreshToken
    token = _function_body(source, "_visitCsrfToken")
    assert "security.getMutationHeaders()" in token and "security.refreshToken()" in token
    assert "getToken" not in token


def test_every_visit_downlink_type_is_dispatched():
    source = _read(WS)
    downlink = {"visit_line", "visit_line_delta", "visit_line_abort", "visit_typing",
                "visit_state_change", "visit_invite", "visit_debrief"}
    block = source[source.index("// 串门帧（§4.5 全部 visit_* 下行）"):]
    block = block[:block.index("return;\n                }") + 1]
    found = set(re.findall(r"response\.type === '(visit_[a-z_]+)'", block))
    assert downlink <= found
    assert "nekoVisitChat.handleFrame(response)" in block


def test_chat_surface_handles_every_state_change_action_of_the_design():
    body = _function_body(_read(CHAT), "onStateChange")
    cases = set(re.findall(r"case '([a-z_]+)':", body))
    assert cases == _design_state_actions()
    # invite_ready is the only path delivering invite codes to the host frontend
    assert "visit.inviteCode = frame.invite_code" in body


def test_status_toast_map_keys_are_exactly_the_design_codes():
    source = _read(WS)
    block = source[source.index("var VISIT_STATUS_TOAST_KEYS = {"):]
    block = block[:block.index("};")]
    keys = set(re.findall(r"^\s+([A-Za-z_]+):", block, re.M))
    assert keys == _design_status_codes()
    for value in re.findall(r":\s*'([^']+)'", block):
        assert value.startswith("visit.")


def test_blob_branch_is_untouched():
    source = _read(WS)
    pinned = (
        "            // Binary audio data\n"
        "            if (event.data instanceof Blob) {\n"
        "                if (window.DEBUG_AUDIO) {\n"
        "                    console.log(window.t('console.audioBinaryReceived'), event.data.size, "
        "window.t('console.audioBinaryBytes'));\n"
        "                }\n"
        "                if (typeof window.enqueueIncomingAudioBlob === 'function') {\n"
        "                    window.enqueueIncomingAudioBlob(event.data);\n"
        "                }\n"
        "                return;\n"
        "            }\n"
    )
    assert pinned in source


def test_chat_window_skips_the_iframe_but_still_renders_lines():
    source = _read(WS)
    block = source[source.index("// 串门帧（§4.5 全部 visit_* 下行）"):]
    block = block[:block.index("return;\n                }") + 1]
    bridge = block.index("nekoVisitParentBridge.onStateChange")
    guard = block.rfind("_isVisitChatFollowerWindow()", 0, bridge)
    assert guard != -1 and "!_isVisitChatFollowerWindow()" in block
    # Route dialogue to the chat surface regardless of window
    handle = block.index("nekoVisitChat.handleFrame(response)")
    assert "_isVisitChatFollowerWindow" not in block[block.index("if (window.nekoVisitChat"):handle]
    follower = _function_body(source, "_isVisitChatFollowerWindow")
    assert "__NEKO_MULTI_WINDOW__ === true" in follower and "chat(?:_full)?" in follower


def test_chat_window_relays_visit_lifecycle_to_the_pet_window():
    chat = _read(CHAT)
    state = _function_body(chat, "onStateChange")
    assert "window.nekoRelayVisitStateToDisplay(frame)" in state
    snapshot = _function_body(chat, "applyState")
    assert "relayStateSnapshotToDisplay(state, snapshotVisit)" in snapshot

    relay = _read(DISPLAY_RELAY)
    assert "new BroadcastChannel(CHANNEL_NAME)" in relay
    assert "neko_visit_display_v1" in relay
    assert "bridge.onStateChange(frame)" in relay
    # The transport bridge needs lifecycle metadata, not the host's invite code.
    assert "'action', 'side', 'visit_id'" in relay
    assert "invite_code" not in relay

    index = _read(ROOT / "templates" / "index.html")
    chat_template = _read(ROOT / "templates" / "chat.html")
    assert index.index("/static/visit/parent-bridge.js") < index.index("/static/visit/display-relay.js")
    assert chat_template.index("/static/visit/display-relay.js") < chat_template.index(
        "/static/app/app-react-chat-window/visit-chat.js"
    )


# ── Plain-text dialogue (§3.6.6, two layers) ──────────────────────────────────────


def test_schema_declares_plain_text():
    schema = _read(SCHEMA)
    block = schema[schema.index("const textBlockSchema = z.object({"):]
    block = block[:block.index("});")]
    assert "plain_text: z.boolean().optional()" in block


def test_plain_text_branch_comes_before_smart_text_block():
    view = _read(BLOCK_VIEW)
    branch = view[view.index("if (block.type === 'text') {"):]
    plain = branch.index("if (block.plain_text)")
    smart = branch.index("<SmartTextBlock")
    assert plain < smart
    line = branch[plain:branch.index("\n", plain)]
    assert "SmartTextBlock" not in line and "{block.text}" in line


def test_adapter_and_local_bubble_mark_visit_lines_plain_text():
    adapter = _read(ADAPTER)
    render = _function_body(adapter, "renderVisitLine")
    assert "plain_text: true" in render
    role = _function_body(adapter, "visitLineRole")
    # Peers (visiting catgirl / the other household's human) always use role 'tool'
    assert "if (!speaker || speaker.self !== true) return 'tool';" in role
    assert set(re.findall(r"'(user|assistant|system|tool|[a-z]+)'", role)) <= {"tool", "user", "assistant", "human"}
    chat = _read(CHAT)
    assert "plain_text: true" in _function_body(chat, "localBubbleMessage")


def test_index_css_has_the_tool_rules():
    css = _read(INDEX_CSS)
    assert re.search(r"\.message-bubble-tool\s*\{", css)
    assert re.search(r"\.avatar-tool\s*\{", css)


# ── Loading and disabling capabilities ───────────────────────────────────────────────


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
def test_both_templates_load_the_chat_surface_before_the_host_api(template):
    html = _read(template)
    chat = html.index("/static/app/app-react-chat-window/visit-chat.js")
    persona = html.index("/static/app/visit-persona-panel.js")
    host_api = html.index("/static/app/app-react-chat-window/resize-drag-and-api.js")
    # resize-drag-and-api.js deletes __appReactChatWindowParts at its end; this must precede it
    assert chat < host_api and persona > 0


def test_chat_surface_has_no_raf_and_no_websocket():
    for path in (CHAT, PERSONA):
        source = _read(path)
        assert "requestAnimationFrame(" not in source
        assert "new WebSocket(" not in source


def test_composer_submit_goes_through_the_visit_gate_first():
    submit = _read(SUBMIT)
    gate = submit.index("I.isVisitChatActive()")
    assert gate < submit.index("I.state.onComposerSubmit(detail)")
    assert gate < submit.index("window.appButtons.sendTextPayload(detail.text")


# ── Confirmation dialogs contain no technical numbers; endpoint literals ─────────────────────────────────


def test_confirm_dialogs_show_no_technical_numbers():
    chat = _read(CHAT)
    for name in ("previewThenConfirm", "onInvite"):
        body = _function_body(chat, name).lower()
        for banned in ("usage", "token", "tts", "llm_"):
            assert banned not in body, (name, banned)
    for lang in LOCALES:
        visit = json.loads(_read(ROOT / "static" / "locales" / f"{lang}.json"))["visit"]
        for group in ("join", "invite"):
            for text in visit[group].values():
                low = text.lower()
                assert "token" not in low and "tts" not in low, (lang, group, text)


def test_details_calls_only_the_details_literal_and_passes_next_cursor_back():
    chat = _read(CHAT)
    url = _function_body(chat, "detailsUrl")
    assert "'/api/visit/details/' + encodeURIComponent(visitId) + '?catgirl='" in url
    assert "'&cursor=' + encodeURIComponent(cursor)" in url
    details = _function_body(chat, "openDetails")
    assert "data.next_cursor" in details and "loadPage(next)" in details
    assert "/api/visit/details/'" not in chat.replace("'/api/visit/details/' + encodeURIComponent", "")


def test_join_previews_first_and_never_joins_after_a_failed_preview():
    chat = _read(CHAT)
    preview = _function_body(chat, "previewThenConfirm")
    assert "'/api/visit/invites/' + encodeURIComponent(inviteCode) + '/preview'" in preview
    assert "/join" not in preview
    # The preview-failure branch returns first without opening a dialog
    fail = preview.index("if (preview.status !== 200")
    assert preview.index("return null;", fail) < preview.index("openDialog(")
    join = _function_body(chat, "joinWithCode")
    assert join.index("previewThenConfirm(inviteCode, catgirl)") < join.index("'/join'")
    assert "if (!data) return null;" in join


def test_invite_recovers_an_existing_room_before_creating_and_polls_after_creation():
    chat = _read(CHAT)
    url = _function_body(chat, "currentHostRoomUrl")
    assert "'/api/visit/rooms/current?catgirl=' + encodeURIComponent(catgirl)" in url
    start = _function_body(chat, "startInvite")
    assert start.index("currentHostRoomUrl(catgirl)") < start.index("'/api/visit/rooms'")
    assert "current.status !== 404" in start
    assert "scheduleInviteRecovery(catgirl" in start
    recovery = _function_body(chat, "scheduleInviteRecovery")
    assert "api('GET', currentHostRoomUrl(catgirl))" in recovery


def test_invite_creation_fails_immediately_when_start_does_not_succeed():
    chat = _read(CHAT)
    start = _function_body(chat, "startInvite")
    switched = "return { ok: false, error: t('visit.error.characterSwitched'"
    assert switched in start
    for function_name in ("createInviteCode", "createDirectInvitation"):
        body = _function_body(chat, function_name)
        assert "if (!started || started.ok !== true)" in body


def test_persona_409_opens_the_panel_before_any_retry():
    body = _function_body(_read(CHAT), "withPersonaRetry")
    first = body.index("await request()")
    gate = body.index("VISIT_PERSONA_UNREVIEWED")
    panel = body.index("window.openVisitPersonaPreview(")
    retry = body.index("await request()", first + 1)
    assert first < gate < panel < retry
    assert "if (confirmed !== true) return result;" in body[panel:retry]


def test_route_end_body_matches_the_design():
    body = _function_body(_read(CHAT), "endRoute")
    assert "'/api/visit/route/end'" in body
    assert "lanlan_name: currentCatgirl(), visit_id: visitId, reason: reason" in body


def test_debrief_choice_posts_the_button_payload_and_the_page_name():
    chat = _read(CHAT)
    body = _function_body(chat, "postDebriefChoice")
    assert "Object.assign({}, payload, { lanlan_name: currentCatgirl() })" in body
    assert "'/api/visit/debrief/choice'" in body
    blocks = _function_body(chat, "handleChatBlocks")
    assert "action: 'visit_debrief_chip_ack'" in blocks


def test_persona_panel_endpoints_and_review_flag():
    panel = _read(PERSONA)
    urls = set(re.findall(r"'(/api/visit/[^'?]*)", panel))
    assert urls == {"/api/visit/persona", "/api/visit/persona/regenerate"}
    assert "getMutationHeaders" in panel
    assert "var body = { reviewed: true };" in _function_body(panel, "save")
    assert "window.openVisitPersonaPreview = function" in panel


def test_no_visit_url_has_a_trailing_slash_before_the_query():
    for path in (CHAT, PERSONA):
        for url in re.findall(r"'(/api/visit/[^']*)'", _read(path)):
            assert not url.endswith("/") or url in (
                "/api/visit/details/", "/api/visit/rooms/", "/api/visit/invites/", "/api/visit/direct/"
            ), url


# ── Eight languages ─────────────────────────────────────────────────────────


def _flatten(prefix: str, node) -> set[str]:
    if not isinstance(node, dict):
        return {prefix}
    keys = set()
    for key, value in node.items():
        keys |= _flatten(f"{prefix}.{key}" if prefix else key, value)
    return keys


def _locale(lang: str) -> dict:
    return json.loads(_read(ROOT / "static" / "locales" / f"{lang}.json"))


def test_visit_keys_match_across_the_eight_locales():
    sets = {lang: _flatten("visit", _locale(lang)["visit"]) for lang in LOCALES}
    reference = sets["zh-CN"]
    for lang, keys in sets.items():
        assert keys == reference, (lang, sorted(keys ^ reference))
    for lang in LOCALES:
        assert _locale(lang)["chat"]["visitBarAriaLabel"]


def test_every_key_the_chat_surface_uses_exists_in_every_locale():
    used = set()
    for path in (CHAT, PERSONA):
        used |= set(re.findall(r"\bt\('((?:visit|chat)\.[A-Za-z0-9_.]+[A-Za-z0-9_])'", _read(path)))
    ws = _read(WS)
    block = ws[ws.index("var VISIT_STATUS_TOAST_KEYS = {"):]
    used |= set(re.findall(r":\s*'(visit\.[^']+)'", block[:block.index("};")]))
    for lang in LOCALES:
        flat = _flatten("", _locale(lang))
        missing = sorted(key for key in used if key not in flat)
        assert not missing, (lang, missing)


def test_no_dehumanizing_address_in_visit_copy():
    banned = ("主人", "ご主人", "주인", "хозяин", "master")
    for lang in LOCALES:
        for key in _flatten("visit", _locale(lang)["visit"]):
            node = _locale(lang)
            for part in key.split("."):
                node = node[part]
            low = str(node).lower()
            for word in banned:
                assert word.lower() not in low, (lang, key, node)


def test_persona_panel_warns_about_a_stale_private_section_list():
    panel = _read(PERSONA)
    assert "data.sections_outdated === true" in _function_body(panel, "renderPrivate")


def test_persona_panel_escape_and_failed_first_generation():
    panel = _read(PERSONA)
    assert "document.addEventListener('keydown', onKeydown, true)" in panel
    assert "document.removeEventListener('keydown', onKeydown, true)" in panel
    load = _function_body(panel, "load")
    assert "started.status !== 202 && codeOf(started) !== 'persona_generating'" in load


def test_refused_bind_refreshes_the_token_and_retries_once_per_socket():
    source = _read(WS)
    status = _function_body(source, "_showVisitStatus")
    # Recognize only rejection of bind itself (backend reason:bind_refused), and retry using the connection that received this status
    assert "code === 'VISIT_E_UNAUTHORIZED' && details && details.reason === 'bind_refused'" in status
    assert "_retryVisitBindAfterRefusal(socket || S.socket)" in status
    assert "_showVisitStatus(statusCode, statusDetails || {}, _thisSocket);" in source
    retry = _function_body(source, "_retryVisitBindAfterRefusal")
    assert "_visitBindRefreshed.has(socket)" in retry and "_sendVisitBind(socket, true)" in retry
    assert "forceRefresh === true || attempt > 0" in _function_body(source, "_sendVisitBind")


def test_visit_line_render_carries_the_lamport_sort_key():
    # Pass visit-chat's (lp, side_rank) sorting key to the host with each message, or dialogue still sorts by arrival order
    render = _function_body(_read(ADAPTER), "renderVisitLine")
    assert "patch.sortKey = spec.sortKey" in render
    assert "sortKey: entry.rendered ? undefined : lineSortKey(entry)" in _function_body(_read(CHAT), "renderLine")


def test_galgame_choices_are_hidden_while_the_visit_owns_the_composer():
    # GalGame choices are generated for normal chat; clicking during visit ownership sends them to the guest as visit dialogue
    props = _function_body(_read(ROOT / "static" / "app" / "app-react-chat-window" / "geometry-and-messages.js"), "buildRenderProps")
    assert "var visitOwnsComposer = typeof I.isVisitChatActive === 'function' && I.isVisitChatActive();" in props
    assert "galgameOptions: !visitOwnsComposer &&" in props
    assert "galgameOptionsLoading: !visitOwnsComposer &&" in props


def test_refused_ordinary_submission_is_closed_by_request_id():
    buttons = _read(ROOT / "static" / "app" / "app-buttons.js")
    assert "rememberSentTextSubmission(requestId, reactOptimisticMessageAppended !== null ? reactOptimisticMessageId : '', !isVisitLine, startedTextSessionHere);" in buttons
    body = _function_body(buttons, "markTextSubmissionRefused")
    assert "updateMessage(record.messageId, { status: 'failed' })" in body
    assert "window._lastSubmittedRequestId === id" in body and "S.pendingTextTurnSubmitAt = 0;" in body
    # Identify in-flight replies by the sentence that started the turn, not _lastSubmittedRequestId (avatar drops do not write it)
    assert "if (lastTurnRequestId === id) {" in body


def test_choice_prompts_are_hidden_while_the_visit_owns_the_composer():
    # Both rendering and hit areas fetch choices through getRevealedChoicePrompt, which returns null while visits own the input
    reveal = _function_body(_read(SUBMIT), "getRevealedChoicePrompt")
    assert "if (typeof I.isVisitChatActive === 'function' && I.isVisitChatActive()) return null;" in reveal


def test_persona_panel_refreshes_compact_hit_geometry_on_open_and_close():
    source = _read(PERSONA)
    assert source.count("refreshCompactGeometry();") >= 2
    assert "new CustomEvent('neko:compact-interaction-geometry-refresh')" in source


def test_persona_panel_refreshes_compact_hit_geometry_after_async_content_changes():
    source = _read(PERSONA)
    for name in ("setNotice", "setButtons", "renderPrivate"):
        assert "refreshCompactGeometry();" in _function_body(source, name), name


def test_visit_dialog_owns_compact_input_until_it_closes():
    source = _read(CHAT)
    body = _function_body(source, "openDialog")
    assert "var releaseInteractive = acquireForceInteractiveLease();" in body
    close = body[body.index("close: function (value)"):body.index("setLines: function (lines)")]
    assert "releaseInteractive();" in close
    lease = _function_body(source, "acquireForceInteractiveLease")
    assert "window.__nekoAcquireForceInteractiveLease" in lease
    assert "window.__nekoForceInteractiveLeaseState" in lease
    assert "state.count += 1;" in lease and "state.count = Math.max(0, state.count - 1);" in lease
    assert "if (state.count === 0)" in lease

    css = _read(INDEX_CSS)
    overlay = re.search(r"\.visit-dialog-overlay\s*\{([^}]*)\}", css, re.S)
    dialog = re.search(r"\.visit-dialog\s*\{([^}]*)\}", css, re.S)
    assert overlay and "background: transparent" in overlay.group(1)
    assert overlay and "pointer-events: none" in overlay.group(1)
    assert dialog and "pointer-events: auto" in dialog.group(1)
    assert dialog and "-webkit-app-region: no-drag" in dialog.group(1)


def test_persona_panel_keeps_actions_outside_its_scrollable_body():
    source = _read(PERSONA)
    assert "? 'visit-persona-content'" in source
    assert "? 'visit-persona-actions'" in source
    assert "? 'visit-persona-editor'" in source
    assert "var header = el('div', 'modal-header');" in source
    assert "btnClass = 'visit-persona-action';" in source
    assert "body.appendChild(privateBox);" in source
    assert source.index("dialog.appendChild(body);") < source.index("dialog.appendChild(actions);")

    css = _read(PERSONA_WINDOW_CSS)
    root = re.search(r"#visit-persona-window-root\s*\{([^}]*)\}", css, re.S)
    actions = re.search(r"\.visit-persona-actions\s*\{([^}]*)\}", css, re.S)
    assert root and "overflow-y: auto" in root.group(1)
    assert "#visit-persona-window-root::-webkit-scrollbar" in css
    assert "width: 8px" in css and "rgba(64, 197, 241, 0.55)" in css
    assert actions and "flex: 0 0 auto" in actions.group(1)
    assert actions and "position: sticky" in actions.group(1) and "bottom: 0" in actions.group(1)


def test_persona_hides_private_card_sections_in_both_modes_and_standalone_close():
    source = _read(PERSONA)
    assert "return standalone ? [] : [closeButton()];" in _function_body(source, "passiveButtons")
    private = _function_body(source, "renderPrivate")
    assert "data.private_sections" not in private
    assert "visit-persona-private-list" not in private
    assert "data.sections_outdated === true" in private
    assert "data.scan_complete === false" in private


def test_persona_panel_owns_compact_input_and_has_no_dim_backdrop():
    source = _read(PERSONA)
    assert "modal-overlay visit-persona-overlay" in source
    assert "modal-dialog visit-persona" in source
    assert "modal-input visit-persona-text" in source
    assert "modal-btn-primary" in source and "modal-btn-secondary" in source
    assert "standalone ? function () {} : acquireForceInteractiveLease();" in source
    assert "if (!standalone)" in source
    assert "releaseInteractive();" in _function_body(source, "close")
    lease = _function_body(source, "acquireForceInteractiveLease")
    assert "window.__nekoAcquireForceInteractiveLease" in lease
    assert "window.__nekoForceInteractiveLeaseState" in lease

    css = _read(INDEX_CSS)
    overlay = re.search(r"\.modal-overlay\.visit-persona-overlay\s*\{([^}]*)\}", css, re.S)
    dialog = re.search(r"\.visit-persona-overlay \.visit-persona\s*\{([^}]*)\}", css, re.S)
    assert overlay and "background: transparent" in overlay.group(1)
    assert overlay and "pointer-events: none" in overlay.group(1)
    assert dialog and "pointer-events: auto" in dialog.group(1)


def test_visit_lines_do_not_toast_text_chatting():
    buttons = _read(ROOT / "static" / "app" / "app-buttons.js")
    assert """                    if (!isVisitLine) {
                        window.showStatusToast(window.t ? window.t('app.textChattingShort')""" in buttons


def test_persona_panel_is_reused_only_for_the_same_character():
    # Another window switched characters: confirmation in the old character's panel must not admit a visit for the new character
    source = _read(PERSONA)
    assert "if (openPanel.catgirl === catgirl) return openPanel.result;" in source
    assert "openPanel.close(false);" in source
    assert "panel.close = close;" in source


def test_persona_panel_displays_the_real_character_name_instead_of_the_placeholder():
    source = _read(PERSONA)
    render = _function_body(source, "renderPersonaText")
    assert "value.split('{LANLAN_NAME}').join(catgirl)" in render
    assert "loadedText = renderPersonaText(data.text, catgirl);" in source


def test_persona_preview_opens_as_a_standalone_neko_tool_window():
    source = _read(PERSONA)
    template = _read(PERSONA_TEMPLATE)
    css = _read(PERSONA_WINDOW_CSS)
    assert "window.open('/visit_persona?' + params.toString()" in source
    assert "window.buildCenteredPopupFeatures(820, 720)" in source
    assert "type: 'neko:visit-persona-result'" in source
    assert "window.__NEKO_VISIT_PERSONA_STANDALONE__ === true" in source
    assert "return openStandaloneVisitPersonaPreview(catgirl, options);" in source
    assert "visit-persona-page-surface" in source
    assert "data-compact-geometry-owner" in source and "if (!standalone)" in source
    assert "/static/common_dialogs.js" not in template
    assert "/static/css/index.css" not in template
    assert "modal-overlay" not in css and "modal-dialog" not in css
    assert "-webkit-app-region: no-drag !important" in css


def test_refused_ordinary_submission_releases_an_ack_only_text_session():
    # A normal submission also requests session startup, while visits return only ack; undo 'text session started' on rejection
    buttons = _read(ROOT / "static" / "app" / "app-buttons.js")
    assert "startedTextSessionHere = true;" in buttons
    assert "!isVisitLine, startedTextSessionHere);" in buttons
    body = _function_body(buttons, "markTextSubmissionRefused")
    assert "if (record.startedSession && lastSessionStartRequestId === id && S.isTextSessionActive === true) {" in body
    # Do not undo if a later sentence now owns the current session
    assert "if (startedSession === true) lastSessionStartRequestId = String(requestId);" in buttons
    assert "S.isTextSessionActive = false;" in body


def test_visit_lines_send_their_visit_id_on_the_wire():
    buttons = _read(ROOT / "static" / "app" / "app-buttons.js")
    assert "if (isVisitLine && typeof options.visitId === 'string' && options.visitId) {" in buttons
    assert "textMessage.visit_id = options.visitId;" in buttons


def test_persona_panel_exposes_a_close_hook_for_character_switches():
    source = _read(PERSONA)
    assert "window.closeVisitPersonaPreview = function closeVisitPersonaPreview()" in source
    assert "openPanel.close(false)" in _function_body(source, "closeVisitPersonaPreview")
