"""Static contracts of the memory browser "visit memories" panel (visit-infrastructure §5 PR-15)."""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
JS_PATH = ROOT / "static" / "js" / "memory_browser.js"
HTML_PATH = ROOT / "templates" / "memory_browser.html"
LOCALES_DIR = ROOT / "static" / "locales"
LOCALES = ("zh-CN", "zh-TW", "en", "ja", "ko", "ru", "es", "pt")
SECTION_START = "// ── 串门记忆面板"
SECTION_END = "    function interpolateText(text, options) {"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def visit_section() -> str:
    source = read(JS_PATH)
    start = source.index(SECTION_START)
    end = source.index(SECTION_END, start)
    return source[start:end]


def function_body(name: str, source: str | None = None) -> str:
    """Source of a top-level (4-space indented) function inside the IIFE, up to the next one."""
    source = visit_section() if source is None else source
    match = re.search(r"\n    (?:async )?function " + re.escape(name) + r"\(", source)
    assert match is not None, f"function {name} not found"
    rest = source[match.end():]
    nxt = re.search(r"\n    (?:async )?function \w+\(|\n    // ── ", rest)
    return source[match.start(): match.end() + (nxt.start() if nxt else len(rest))]


def function_names(source: str) -> list[str]:
    return re.findall(r"\n    (?:async )?function (\w+)\(", source)


def enclosing_function(source: str, index: int) -> str:
    names = [(m.start(), m.group(1)) for m in re.finditer(r"\n    (?:async )?function (\w+)\(", source)]
    owner = ""
    for start, name in names:
        if start < index:
            owner = name
    return owner


def code_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.strip() and not line.strip().startswith("//")]


def visit_locale(lang: str) -> dict:
    return json.loads(read(LOCALES_DIR / f"{lang}.json"))["memory"]["visit"]


def flatten(obj: dict, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in obj.items():
        if isinstance(value, dict):
            out.update(flatten(value, f"{prefix}{key}."))
        else:
            out[f"{prefix}{key}"] = value
    return out


# ── 接线 ──────────────────────────────────────────────────────────────


def test_visit_panel_is_an_auxiliary_panel_of_the_memory_browser():
    html = read(HTML_PATH)
    source = read(JS_PATH)
    assert 'id="memory-visit-panel"' in html
    assert re.search(r'id="memory-visit-trigger"[^>]*data-memory-panel-trigger="visit"', html)
    assert "/static/js/memory_browser.js" in html
    names = re.search(r"const MEMORY_AUXILIARY_PANEL_NAMES = \[([^\]]*)\]", source).group(1)
    assert "'visit'" in names
    assert "if (name === 'visit') openVisitMemoryPanel();" in source
    assert "initVisitMemoryPanel();" in source
    assert "renderVisitMemoryPanel();" in source


# ── 端点字面量 ────────────────────────────────────────────────────────


def test_visit_endpoint_literals_have_no_trailing_slash():
    section = visit_section()
    literals = re.findall(r"""(['"`])(/api/visit/[^'"`]*)\1(\s*\+)?""", section)
    found = {lit for _q, lit, _plus in literals}
    assert found == {
        "/api/visit/memory/peers",
        "/api/visit/memory/forget",
        "/api/visit/memory/forget_all",
        "/api/visit/memory/forget/discard_unreadable",
        "/api/visit/contacts/block",
        "/api/visit/history",
        "/api/visit/report",
        "/api/visit/details/",
    }
    for _q, lit, plus in literals:
        if lit.endswith("/"):
            # 只有拼接前缀可以以 / 结尾（后面紧跟 + visit_id）
            assert plus, f"trailing-slash endpoint literal {lit!r}"
    assert "const VISIT_HISTORY_URL = '/api/visit/history';" in section


# ── 每场记录：只取本机代理 /api/visit/history，不从本机 spool 拼 ─────────


def test_history_list_only_comes_from_the_history_endpoint():
    section = visit_section()
    load = function_body("loadVisitHistory")
    assert "visitRequest(VISIT_HISTORY_URL + (cursor ? '?cursor=' + encodeURIComponent(cursor) : ''))" in load
    assert "result.payload.items" in load
    assert "next_cursor" in load
    # 列表状态只在 loadVisitHistory 里由 history 响应写入
    for match in re.finditer(r"visitMemoryState\.history = ", section):
        assert enclosing_function(section, match.start()) == "loadVisitHistory"
    assert "visitMemoryState.history.concat(items)" in load
    render = function_body("renderVisitHistory")
    assert "visitMemoryState.history.forEach" in render
    # 不读本机副本拼列表
    for forbidden in ("/api/visit/transcript", "spool", "localStorage", "sessionStorage", "indexedDB"):
        assert forbidden not in "\n".join(code_lines(section)), forbidden


def test_details_endpoint_is_only_called_after_clicking_view_details():
    source = read(JS_PATH)
    section = visit_section()
    assert source.count("'/api/visit/details/'") == 1
    load = function_body("loadVisitDetailsPage")
    assert "visitRequest('/api/visit/details/' + encodeURIComponent(details.visitId)" in load
    # 分页：带 next_cursor 才有下一页，下一页带 cursor= 再调同一端点
    assert "'?cursor=' + encodeURIComponent(cursor)" in load
    assert "result.payload.next_cursor" in load
    step = function_body("stepVisitDetailsPage")
    assert "details.cursors[details.page + 1] = details.nextCursor" in step
    # loadVisitDetailsPage 只由 openVisitDetails / 翻页调用
    callers = {enclosing_function(section, m.start()) for m in re.finditer(r"(?<!function )\bloadVisitDetailsPage\(\)", section)}
    assert callers == {"openVisitDetails", "stepVisitDetailsPage"}
    # openVisitDetails 只在「查看详情」按钮的点击回调里调
    calls = [m.start() for m in re.finditer(r"(?<!function )\bopenVisitDetails\(visitId\)", section)]
    assert len(calls) == 1
    assert enclosing_function(section, calls[0]) == "renderVisitHistoryRow"
    before = section[: calls[0]]
    assert before.rstrip().endswith("detailsButton.addEventListener('click', function () {")
    # 打开面板 / 拉列表时不预取详情
    for name in ("openVisitMemoryPanel", "loadVisitHistory", "renderVisitHistory"):
        assert "details/" not in function_body(name)


def test_transcript_text_is_written_with_text_content_only():
    section = visit_section()
    for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
        assert forbidden not in section, forbidden
    half = function_body("renderVisitTranscriptHalf")
    assert "createVisitElement('span', 'memory-visit-line-body', typeof half.text === 'string' ? half.text : '')" in half
    helper = function_body("createVisitElement")
    assert "el.textContent = String(text)" in helper


# ── 举报 ─────────────────────────────────────────────────────────────


def test_report_202_queued_is_shown_as_recorded_not_as_error():
    report = function_body("submitVisitReport")
    branch = report.index("result.status === 202 && result.payload.queued === true")
    queued_key = report.index("visitText('reportQueued', '举报已记录，网络恢复后自动提交。')", branch)
    error_branch = report.index("visitErrorText(result)")
    assert branch < queued_key < error_branch
    queued_block = report[branch:report.index("} else if", branch)]
    assert "tone: 'success'" in queued_block
    assert "reportStates.set(visitId, 'queued')" in queued_block
    for lang in LOCALES:
        assert visit_locale(lang)["reportQueued"].strip()


def test_failed_report_keeps_the_draft_and_only_settled_outcomes_drop_it():
    # 提交失败（report_persist_failed / 网络错等）后重建表单要回填用户填过的内容；
    # 成功、排队（含已排队但登录失效）、已在排队、取消时才丢掉草稿
    form = function_body("renderVisitReportForm")
    assert "const draft = visitMemoryState.reportDrafts.get(visitId);" in form
    for line in ("reason.value = draft.reason;", "note.value = draft.note;",
                 "include.checked = draft.include_transcript;"):
        assert line in form, line
    submit_handler = form[form.index("form.addEventListener('submit'"):]
    assert submit_handler.index("saveDraft();") < submit_handler.index("submitVisitReport(visitId")
    for listener in ("reason.addEventListener('change', saveDraft);", "note.addEventListener('input', saveDraft);",
                     "include.addEventListener('change', saveDraft);"):
        assert listener in form, listener
    cancel = form[form.index("cancel.addEventListener('click'"):form.index("const submit = ")]
    assert "visitMemoryState.reportDrafts.delete(visitId);" in cancel
    submit = function_body("submitVisitReport")
    branches = re.split(r"\n        \} else ", submit[submit.index("const code = visitResultCode(result);"):])
    drops = ["reportDrafts.delete(visitId)" in b for b in branches]
    # 202 排队 / 200 已提交 / 已排队但登录失效 / already_queued 丢草稿；persist_failed 与其余错误保留
    assert drops == [True, True, True, True, False, False], drops


def test_report_submission_is_guarded_while_in_flight():
    # 请求在途时表单可能被重建（刷新列表等）：同一场不能再提交一次（CodeRabbit）
    submit = function_body("submitVisitReport")
    guard = submit.index("if (visitMemoryState.reportInFlight.has(visitId)) return;")
    assert guard < submit.index("visitMemoryState.reportInFlight.add(visitId);") < submit.index("visitRequest(")
    assert "visitMemoryState.reportInFlight.delete(visitId);" in submit[submit.index("} finally {"):]
    form = function_body("renderVisitReportForm")
    assert "submit.disabled = visitMemoryState.reportInFlight.has(visitId);" in form


def test_queued_report_with_an_expired_login_is_shown_as_queued():
    submit = function_body("submitVisitReport")
    branch = submit[submit.index("code === 'VISIT_LOGIN_REQUIRED' && result.payload.queued === true"):]
    branch = branch[:branch.index("} else if (code === 'already_queued')")]
    assert "visitMemoryState.reportStates.set(visitId, 'queued');" in branch
    assert "visitText('reportQueuedLogin'" in branch


def test_report_request_body_matches_the_local_report_endpoint():
    report = function_body("submitVisitReport")
    assert "visitRequest(VISIT_REPORT_URL, {" in report
    for field in ("visit_id: visitId", "reason: fields.reason", "note: fields.note",
                  "include_transcript: fields.include_transcript"):
        assert field in report
    assert "peer_uid" not in report
    section = visit_section()
    assert "const VISIT_REPORT_REASONS = ['harassment', 'sexual', 'privacy', 'spam', 'other'];" in section


# ── 完整 peer_uid 永不进可见文本 ────────────────────────────────────────


def test_rendering_never_interpolates_peer_uid_only_short_id():
    section = visit_section()
    for name in function_names(section):
        if name.startswith("render") or name in ("visitPeerLabel", "visitPeerCatGroups", "visitPeerCatCount",
                                                 "openVisitConfirm", "createVisitElement"):
            assert "peer_uid" not in function_body(name), name
    label = function_body("visitPeerLabel")
    assert "peer.short_id" in label
    assert "visitPeerLabel(peer)" in function_body("renderVisitPeerRow")
    assert "visitPeerLabel(peer)" in function_body("renderVisitBlockRow")
    assert "item.peer_short_code" in function_body("renderVisitHistoryRow")
    # peer_uid 只出现在：列表过滤、清除这个人 / 拉黑 的请求体
    allowed = {"loadVisitPeers", "submitVisitForget", "submitVisitBlock"}
    for match in re.finditer(r"peer_uid", section):
        line_start = section.rfind("\n", 0, match.start()) + 1
        line = section[line_start: section.find("\n", match.start())]
        if line.strip().startswith("//"):
            continue
        assert enclosing_function(section, match.start()) in allowed, line
        for sink in ("textContent", "setAttribute", "title", "aria", "dataset", "createVisitElement", "console"):
            assert sink not in line, line


def test_forget_and_block_request_bodies_take_the_row_peer_uid():
    forget = function_body("submitVisitForget")
    assert "const peer = action.peer;" in forget
    assert "body: { catgirl: action.catgirl, peer_uid: peer.peer_uid }" in forget
    assert "visitRequest(VISIT_FORGET_URL, {" in forget
    block = function_body("submitVisitBlock")
    assert "body: { peer_uid: peer.peer_uid, blocked: blocked }" in block
    assert "visitRequest(VISIT_BLOCK_URL, {" in block
    row = function_body("renderVisitPeerRow")
    assert "requestVisitForgetPerson(peer);" in row
    block_row = function_body("renderVisitBlockRow")
    assert "requestVisitBlock(peer);" in block_row
    assert "submitVisitBlock(peer, false);" in block_row


def test_forget_body_carries_the_current_catgirl_of_the_panel():
    load = function_body("loadVisitPeers")
    assert "const catgirl = currentCatName || '';" in load
    assert "visitMemoryState.catgirl = catgirl;" in load
    assert "VISIT_PEERS_URL + '?catgirl=' + encodeURIComponent(catgirl)" in load
    request = function_body("requestVisitForgetPerson")
    assert "catgirl: visitMemoryState.catgirl" in request
    forget = function_body("submitVisitForget")
    # 确认期间切了角色：不按旧角色发
    assert "action.catgirl !== visitMemoryState.catgirl" in forget
    assert forget.index("action.catgirl !== visitMemoryState.catgirl") < forget.index("visitRequest(")


# ── 确认之后才发清除请求 ───────────────────────────────────────────────


def test_forget_requests_are_only_sent_after_confirmation():
    section = visit_section()
    source = read(JS_PATH)
    assert source.count("'/api/visit/memory/forget_all'") == 1
    uses = [m.start() for m in re.finditer(r"\bVISIT_FORGET_ALL_URL\b", section)]
    assert len(uses) == 2  # 定义 + submitVisitForgetAll 里的一次请求
    assert enclosing_function(section, uses[1]) == "submitVisitForgetAll"
    for submit in ("submitVisitForgetAll", "submitVisitForget"):
        callers = {enclosing_function(section, m.start())
                   for m in re.finditer(r"\b" + submit + r"\(", section)} - {submit}
        assert callers == {"confirmVisitAction"}, (submit, callers)
    confirm = function_body("confirmVisitAction")
    assert "await submitVisitForgetAll(onlyCurrent ? action.catgirl : '');" in confirm
    assert "await submitVisitForget(action);" in confirm
    assert "await submitVisitBlock(action.peer, true);" in confirm
    # 底部按钮与「清除这个人」只打开确认框，不发请求
    for name in ("requestVisitForgetAll", "requestVisitForgetPerson", "requestVisitBlock"):
        body = function_body(name)
        assert "openVisitConfirm(" in body
        assert "visitRequest(" not in body and "fetch(" not in body
    init = function_body("initVisitMemoryPanel")
    assert "bind('memory-visit-forget-all', requestVisitForgetAll);" in init
    assert "bind('memory-visit-confirm-ok', function () {\n            confirmVisitAction();" in init
    forget_all = function_body("submitVisitForgetAll")
    assert "body: catgirl ? { catgirl: catgirl } : {}" in forget_all
    html = read(HTML_PATH)
    assert 'id="memory-visit-confirm-scope-checkbox"' in html


def test_unreadable_forget_record_offers_the_confirmed_discard_entry():
    # 读不出的清除记录挡住清除时（503 reason:'forget_record_unreadable'）不提示「稍后重试」，
    # 而是给出丢弃入口；丢弃请求只在二次确认之后发，带 confirm:true
    section = visit_section()
    assert "const VISIT_FORGET_RECORD_UNREADABLE = 'forget_record_unreadable';" in section
    errors = function_body("visitErrorText")
    assert errors.index("isVisitForgetRecordUnreadable(result)") < errors.index("result.status === 503")
    assert "visitText('errorForgetRecordUnreadable'" in errors
    for submit in ("submitVisitForget", "submitVisitForgetAll"):
        assert "showVisitDiscardEntry(isVisitForgetRecordUnreadable(result));" in function_body(submit), submit
    uses = [m.start() for m in re.finditer(r"\bVISIT_DISCARD_UNREADABLE_URL\b", section)]
    assert len(uses) == 2 and enclosing_function(section, uses[1]) == "submitVisitDiscardUnreadable"
    callers = {enclosing_function(section, m.start())
               for m in re.finditer(r"\bsubmitVisitDiscardUnreadable\(", section)} - {"submitVisitDiscardUnreadable"}
    assert callers == {"confirmVisitAction"}
    discard = function_body("submitVisitDiscardUnreadable")
    assert "body: { confirm: true }" in discard
    assert "visitResultCode(result) === 'discard_failed'" in discard
    request = function_body("requestVisitDiscardUnreadable")
    assert "openVisitConfirm({ kind: 'discard_unreadable' });" in request
    assert "visitRequest(" not in request
    assert "bind('memory-visit-discard-unreadable', requestVisitDiscardUnreadable);" in function_body(
        "initVisitMemoryPanel")
    html = read(HTML_PATH)
    assert re.search(r'<button[^>]*id="memory-visit-discard-unreadable"[^>]*\bhidden\b', html)


# ── CSRF 与错误码 ─────────────────────────────────────────────────────


def test_every_visit_request_carries_the_csrf_header_from_page_config():
    section = visit_section()
    fetch_owners = {enclosing_function(section, m.start()) for m in re.finditer(r"\bfetch\(", section)}
    assert fetch_owners == {"loadVisitCsrfToken", "visitRequest"}  # sendOnce 是 visitRequest 的内部函数
    token = function_body("loadVisitCsrfToken")
    assert "fetch('/api/config/page_config'" in token
    assert "payload.autostart_csrf_token" in token
    request = function_body("visitRequest")
    assert "const headers = { 'X-CSRF-Token': visitCsrfToken };" in request
    assert "result.payload.error_code === 'csrf_validation_failed'" in request


def test_error_codes_get_their_own_copy():
    errors = function_body("visitErrorText")
    for needle in ("'visit_active'", "'VISIT_LOGIN_REQUIRED'", "'forget_pending'", "'blocklist_unavailable'",
                   "'servers_unreachable'", "'not_participant'", "'unknown_visit'",
                   "result.status === 503", "result.status === 403"):
        assert needle in errors, needle
    peers = function_body("renderVisitPeers")
    assert "visitText('peersEmpty'" in peers
    history = function_body("loadVisitHistory")
    assert "visitResultCode(result) === 'VISIT_LOGIN_REQUIRED'" in history


# ── 8 locale ─────────────────────────────────────────────────────────


def test_eight_locales_have_the_same_visit_keys_and_placeholders():
    flat = {lang: flatten(visit_locale(lang)) for lang in LOCALES}
    base = flat["zh-CN"]
    for lang in LOCALES:
        assert set(flat[lang]) == set(base), (lang, set(flat[lang]) ^ set(base))
        for key, value in flat[lang].items():
            assert isinstance(value, str) and value.strip(), (lang, key)
            assert set(re.findall(r"\{\{\s*(\w+)\s*\}\}", value)) == set(
                re.findall(r"\{\{\s*(\w+)\s*\}\}", base[key])), (lang, key)
    assert set(visit_locale("en")["reportReasons"]) == {"harassment", "sexual", "privacy", "spam", "other"}


def test_every_visit_key_used_by_the_page_exists():
    section = visit_section()
    html = read(HTML_PATH)
    used = set(re.findall(r"visitText\('([A-Za-z]+)'", section))
    used |= set(re.findall(r'data-i18n="memory\.visit\.([A-Za-z]+)"', html))
    keys = set(visit_locale("zh-CN"))
    assert used <= keys, used - keys
    assert "visitText('reportReasons.' + value, value)" in section


@pytest.mark.parametrize("lang,marker", [
    ("zh-CN", "当前角色"),
    ("zh-TW", "目前角色"),
    ("en", "current character"),
    ("ja", "現在のキャラクター"),
    ("ko", "현재 캐릭터"),
    ("ru", "текущий персонаж"),
    ("es", "personaje actual"),
    ("pt", "personagem atual"),
])
def test_forget_person_confirmation_says_only_the_current_character(lang, marker):
    text = visit_locale(lang)["forgetPersonConfirm"]
    assert marker in text
    assert "{{catgirl}}" in text
    assert "{{code}}" in text


def test_visit_copy_has_no_dehumanizing_terms():
    for lang in LOCALES:
        for key, value in flatten(visit_locale(lang)).items():
            lowered = value.lower()
            for term in ("主人", "master", "ご主人", "주인", "хозяин"):
                assert term not in lowered, (lang, key, term)


def test_current_catgirl_fallback_syncs_the_panel_triggers():
    # 没有私聊记忆文件、只靠 current_catgirl 兜底认出的角色：赋值后要同步「导入记忆」与「串门记忆」入口，
    # 否则两个入口整页都保持禁用（Codex P2）
    source = read(JS_PATH)
    start = source.index("const response = await fetch('/api/characters/current_catgirl');")
    branch = source[start:source.index("} catch (error) {", start)]
    assert branch.index("currentCatName = current.current_catgirl || '';") < branch.index("updateExternalImportButton();")
    sync = source[source.index("function updateExternalImportButton() {"):]
    assert "syncVisitMemoryTrigger();" in sync[:sync.index("\n    }\n")]
