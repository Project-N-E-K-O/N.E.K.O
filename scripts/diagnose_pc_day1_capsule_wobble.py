from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from yui_guide_director_parts import DIRECTOR_SCRIPT_NAMES, read_director_source


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = PROJECT_ROOT / "static"
DEFAULT_PC_REPO = PROJECT_ROOT.parent / "N.E.K.O.-PC"


@dataclass
class Check:
    name: str
    status: str
    detail: str


def _load_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover - CLI diagnostic path
        raise RuntimeError(
            "Playwright is required. Run with the repo venv, for example: "
            ".venv/bin/python scripts/diagnose_pc_day1_capsule_wobble.py"
        ) from exc
    return sync_playwright


def _read(path: Path) -> str:
    if path.is_dir():
        return "\n".join(_read(part_path) for part_path in sorted(path.glob("*.js")))
    return path.read_text(encoding="utf-8")


def _safe_extract(source: str, start_token: str, end_token: str) -> str | None:
    if start_token not in source:
        return None
    tail = source.split(start_token, 1)[1]
    if end_token not in tail:
        return None
    return tail.split(end_token, 1)[0]


def _check(condition: bool, name: str, ok_detail: str, fail_detail: str) -> Check:
    return Check(
        name=name,
        status="PASS" if condition else "FAIL",
        detail=ok_detail if condition else fail_detail,
    )


def validate_pc_repo(pc_repo: Path) -> list[Check]:
    required_files = [
        pc_repo / "src" / "preload-tutorial-global-overlay.js",
        pc_repo / "src" / "tutorial-global-overlay-service.js",
    ]
    failures: list[str] = []
    if not pc_repo.exists():
        failures.append("path does not exist")
    elif not pc_repo.is_dir():
        failures.append("path is not a directory")
    else:
        missing = [str(path.relative_to(pc_repo)) for path in required_files if not path.exists()]
        if missing:
            failures.append("missing required file(s): " + ", ".join(missing))
    if not failures:
        return [
            Check(
                "PC repo preflight",
                "PASS",
                f"PC repo is readable at {pc_repo}.",
            )
        ]
    return [
        Check(
            "PC repo preflight",
            "FAIL",
            f"Invalid --pc-repo {pc_repo}: " + "; ".join(failures),
        )
    ]


def run_static_checks(pc_repo: Path) -> list[Check]:
    checks: list[Check] = []
    try:
        sources = {
            "day1": _read(STATIC_DIR / "tutorial/yui-guide/days/day1-home-guide.js"),
            "director": read_director_source(STATIC_DIR),
            "takeover": _read(STATIC_DIR / "tutorial/core/interaction-takeover.js"),
            "interpage": _read(STATIC_DIR / "app/app-interpage"),
            "overlay": _read(STATIC_DIR / "tutorial/yui-guide/overlay.js"),
            "pc_preload": _read(pc_repo / "src" / "preload-tutorial-global-overlay.js"),
            "pc_service": _read(pc_repo / "src" / "tutorial-global-overlay-service.js"),
        }
    except Exception as exc:
        return [Check("Static source preflight", "FAIL", f"Failed to read required source: {exc}")]

    day1 = sources["day1"]
    director = sources["director"]
    takeover = sources["takeover"]
    interpage = sources["interpage"]
    overlay = sources["overlay"]
    pc_preload = sources["pc_preload"]
    pc_service = sources["pc_service"]

    greeting_block = _safe_extract(day1, "id: 'day1_intro_greeting'", "id: 'day1_capsule_drag_hint'")
    capsule_block = _safe_extract(day1, "id: 'day1_capsule_drag_hint'", "id: 'day1_history_handle'")
    legacy_externalized_intro_block = _safe_extract(
        director,
        "async runChatIntroPreludeExternalized",
        "const introText = this.resolvePerformanceBubbleText",
    )
    greeting_play_block = _safe_extract(
        director,
        "async playDay1IntroGreetingRoundScene",
        "await this.playIntroGreetingReply();",
    )
    missing_blocks = [
        name
        for name, block in [
            ("day1_intro_greeting", greeting_block),
            ("day1_capsule_drag_hint", capsule_block),
            ("runChatIntroPreludeExternalized", legacy_externalized_intro_block),
            ("playDay1IntroGreetingRoundScene", greeting_play_block),
        ]
        if block is None
    ]
    if missing_blocks:
        checks.append(Check("Static block extraction", "FAIL", "Missing expected block(s): " + ", ".join(missing_blocks)))
    greeting_block = greeting_block or ""
    capsule_block = capsule_block or ""
    legacy_externalized_intro_block = legacy_externalized_intro_block or ""
    greeting_play_block = greeting_play_block or ""
    checks.extend([
        _check(
            "cursorAction: 'wobble'" not in greeting_block
            and "setExternalizedChatCursor('input'" not in legacy_externalized_intro_block
            and "effect: 'wobble'" not in legacy_externalized_intro_block
            and "setExternalizedChatCursor('');" in greeting_play_block
            and "this.cursor.hide();" in greeting_play_block
            and "setExternalizedChatCursor('');" in legacy_externalized_intro_block
            and "this.cursor.hide();" in legacy_externalized_intro_block,
            "Day1 intro greeting clears any previous input wobble",
            "day1_intro_greeting clears externalized and PC/home cursors before only spotlighting the input.",
            "day1_intro_greeting can still inherit a previous ghost cursor wobble into the first greeting line.",
        ),
        _check(
            "target: 'chat-input'" in capsule_block
            and "cursorAction: 'wobble'" in capsule_block
            and "cursorWobbleDurationMs: 2000" in capsule_block,
            "Day1 capsule scene requests a 2000ms input wobble",
            "day1_capsule_drag_hint targets chat-input with cursorAction=wobble and cursorWobbleDurationMs=2000.",
            "day1_capsule_drag_hint is not configured to wobble the chat input for 2000ms.",
        ),
        _check(
            "effectDurationMs" in director
            and "externalizedCursorOptions.effectDurationMs" in director
            and "this.cursor.wobble(effectDurationMs)" in director,
            "Home director carries effectDurationMs through externalized chat anchors",
            "director sends and replays effectDurationMs for externalized chat cursor anchors.",
            "director does not consistently carry effectDurationMs through the externalized cursor path.",
        ),
        _check(
            "effectDurationMs" in takeover and "yui_guide_set_chat_cursor" in takeover,
            "Tutorial takeover forwards effectDurationMs to the chat window",
            "tutorial-interaction-takeover posts effectDurationMs with yui_guide_set_chat_cursor.",
            "tutorial-interaction-takeover does not forward effectDurationMs.",
        ),
        _check(
            "effectDurationMs" in interpage
            and "reportYuiGuideChatCursorAnchor" in interpage
            and "rememberYuiGuideChatCursorScreenPoint" in interpage,
            "External chat returns effectDurationMs with cursor anchors",
            "app-interpage stores and reports effectDurationMs from the external chat cursor target.",
            "app-interpage does not report effectDurationMs back to the home window.",
        ),
        _check(
            "effectDurationMs" in overlay
            and "wobbleCursor(effectDurationMs)" in overlay
            and "moveCursorTo(this.cursorPosition.x, this.cursorPosition.y, 0, 'wobble', effectDurationMs)" in overlay,
            "Home overlay bridge sends wobble duration to PC overlay",
            "yui-guide-overlay forwards effectDurationMs to the PC overlay bridge.",
            "yui-guide-overlay does not forward effectDurationMs to the PC overlay bridge.",
        ),
        _check(
            "effectDurationMs" in pc_preload
            and "--cursor-effect-duration" in pc_preload
            and "shouldReplayEffect" in pc_preload,
            "PC renderer receives duration and replays repeated wobble effects",
            "preload-tutorial-global-overlay applies --cursor-effect-duration and does not dedupe wobble replay.",
            "PC renderer may still swallow or shorten repeated wobble effects.",
        ),
        _check(
            "translateX(-14px)" in pc_service
            and "translateX(14px)" in pc_service
            and "var(--cursor-effect-duration,2000ms)" in pc_service,
            "PC renderer CSS uses visible 2000ms horizontal wobble",
            "tutorial-global-overlay-service defines visible horizontal wobble with a 2000ms fallback.",
            "PC renderer CSS still lacks a visible 2000ms horizontal wobble.",
        ),
    ])
    return checks


def run_browser_bridge_probe() -> tuple[list[Check], dict[str, Any]]:
    sync_playwright = _load_playwright()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()

        home = browser.new_page(viewport={"width": 1280, "height": 720})
        home.route(
            "**/day1-wobble-home",
            lambda route: route.fulfill(
                status=200,
                content_type="text/html",
                body="<!doctype html><html><head><meta charset='utf-8'></head><body></body></html>",
            ),
        )
        home.goto("http://neko.test/day1-wobble-home")
        home.evaluate(
            """
            () => {
                window.history.pushState({}, '', '/');
                window.__NEKO_MULTI_WINDOW__ = true;
                window.__pcOverlayUpdates = [];
                window.safeT = (key, fallback) => typeof fallback === 'string' ? fallback : key;
                window.nekoTutorialOverlay = {
                    getWindowMetricsSync: () => ({
                        bounds: { x: 100, y: 50, width: 1200, height: 800 },
                        contentBounds: { x: 100, y: 50, width: 1200, height: 800 },
                        zoomFactor: 1,
                    }),
                    begin: () => Promise.resolve({ ok: true }),
                    update: (payload) => {
                        window.__pcOverlayUpdates.push(payload);
                        return Promise.resolve({ ok: true });
                    },
                    clear: () => Promise.resolve({ ok: true }),
                };
            }
            """
        )
        for script in (
            "tutorial/visual/highlight-controller.js",
            "tutorial-interrupt-controller.js",
            "tutorial/core/interaction-takeover.js",
            "tutorial/yui-guide/overlay.js",
            *DIRECTOR_SCRIPT_NAMES,
        ):
            home.add_script_tag(path=str(STATIC_DIR / script))

        greeting_result = home.evaluate(
            """
            async () => {
                window.__pcOverlayUpdates = [];
                const director = window.createYuiGuideDirector({ page: 'home' });
                const calls = [];
                director.interactionTakeover = {
                    setExternalizedChatSpotlight: (kind) => calls.push({ type: 'spotlight', kind }),
                    setExternalizedChatCursor: (kind, options) => calls.push({
                        type: 'cursor',
                        kind,
                        effect: options && options.effect,
                        effectDurationMs: options && options.effectDurationMs,
                    }),
                };
                director.isHomeChatExternalized = () => true;
                director.getStep = () => ({ performance: {} });
                director.waitForSceneDelay = async () => true;
                director.enableInterrupts = () => {};
                director.playIntroGreetingReply = async () => {};
                director.introFlowStarted = true;
                director.sceneRunId = 41;
                director.cursor.showAt(320, 280);
                director.cursor.wobble(2000);
                await new Promise((resolve) => setTimeout(resolve, 0));
                const beforeGreetingUpdateCount = window.__pcOverlayUpdates.length;
                const ok = await director.playDay1IntroGreetingRoundScene(41);
                await new Promise((resolve) => setTimeout(resolve, 0));
                return {
                    ok,
                    calls,
                    beforeGreetingUpdateCount,
                    updates: window.__pcOverlayUpdates,
                };
            }
            """
        )

        director_result = home.evaluate(
            """
            async () => {
                const director = window.createYuiGuideDirector({ page: 'home' });
                const calls = [];
                director.interactionTakeover = {
                    setExternalizedChatSpotlight: (kind) => calls.push({ type: 'spotlight', kind }),
                    setExternalizedChatCursor: (kind, options) => calls.push({
                        type: 'cursor',
                        kind,
                        effect: options && options.effect,
                        effectDurationMs: options && options.effectDurationMs,
                    }),
                };
                director.isHomeChatExternalized = () => true;
                director.currentSceneId = 'day1_intro_greeting';
                director.prepareAvatarFloatingScene = async () => true;
                director.speakGuideLine = async () => null;
                director.waitForSceneDelay = async () => true;
                director.appendGuideChatMessage = () => {};
                director.applyGuideEmotion = () => {};
                director.enableInterrupts = () => {};
                await director.playAvatarFloatingScene({
                    id: 'day1_capsule_drag_hint',
                    text: '把鼠标移到这里，长按就可以拉着聊天框到处跑啦~ 双击两下就能随时发消息给我哦！',
                    voiceKey: 'day1_capsule_drag_hint',
                    target: 'chat-input',
                    cursorAction: 'wobble',
                    cursorWobbleDurationMs: 2000,
                }, 1, 2, 9);
                return calls;
            }
            """
        )

        anchor_result = home.evaluate(
            """
            async () => {
                const director = window.createYuiGuideDirector({ page: 'home' });
                director.currentSceneId = 'day1_capsule_drag_hint';
                window.dispatchEvent(new CustomEvent('neko:yui-guide:external-chat-cursor-anchor', {
                    detail: {
                        x: 640,
                        y: 430,
                        kind: 'input',
                        effect: 'wobble',
                        effectDurationMs: 2000,
                        source: 'external-chat',
                        timestamp: Date.now(),
                    },
                }));
                await new Promise((resolve) => setTimeout(resolve, 0));
                return {
                    updates: window.__pcOverlayUpdates,
                    latestAnchor: director.latestExternalizedChatCursorAnchorPoint,
                    cursorPosition: director.overlay.getCursorPosition(),
                };
            }
            """
        )

        chat = browser.new_page(viewport={"width": 1280, "height": 720})
        chat.route(
            "**/day1-wobble-chat",
            lambda route: route.fulfill(
                status=200,
                content_type="text/html",
                body="<!doctype html><html><head><meta charset='utf-8'></head><body></body></html>",
            ),
        )
        chat.goto("http://neko.test/day1-wobble-chat")
        chat.evaluate(
            """
            () => {
                window.history.pushState({}, '', '/chat');
                window.__relays = [];
                window.__updates = [];
                window.nekoTutorialOverlay = {
                    getWindowMetricsSync: () => ({
                        bounds: { x: 100, y: 50, width: 1200, height: 800 },
                        contentBounds: { x: 100, y: 50, width: 1200, height: 800 },
                        zoomFactor: 1,
                    }),
                    begin: () => Promise.resolve({ ok: true }),
                    update: (payload) => {
                        window.__updates.push(payload);
                        return Promise.resolve({ ok: true });
                    },
                    clear: () => Promise.resolve({ ok: true }),
                    relayToPet: (payload) => window.__relays.push(payload),
                };
                window.localStorage.setItem('yuiGuidePcOverlayRunId', 'diagnostic-run');
                document.body.innerHTML = `
                    <div id="react-chat-window-shell" style="position:fixed;left:550px;top:390px;width:470px;height:88px;">
                        <div id="react-chat-window-root">
                            <div class="compact-chat-surface-frame"
                                data-compact-geometry-owner="surface"
                                data-compact-geometry-item="input"
                                style="position:fixed;left:600px;top:420px;width:390px;height:54px;border-radius:999px;"></div>
                        </div>
                    </div>
                `;
            }
            """
        )
        for part_path in sorted((STATIC_DIR / "app/app-interpage").glob("*.js")):
            chat.add_script_tag(path=str(part_path))
        chat_result = chat.evaluate(
            """
            async () => {
                window.postMessage({
                    __nekoTutorialOverlayRelay: true,
                    payload: {
                        action: 'yui_guide_set_chat_cursor',
                        kind: 'input',
                        effect: 'wobble',
                        effectDurationMs: 2000,
                        timestamp: Date.now(),
                        tutorialRunId: 'diagnostic-run',
                    },
                }, '*');
                await new Promise((resolve) => setTimeout(resolve, 180));
                const raw = window.localStorage.getItem('neko_yui_guide_external_chat_cursor_screen_point_v1');
                return {
                    relays: window.__relays,
                    updates: window.__updates,
                    stored: raw ? JSON.parse(raw) : null,
                    hasLocalCursor: !!document.getElementById('yui-guide-chat-cursor'),
                };
            }
            """
        )
        browser.close()

    pc_cursor_updates = [
        update.get("payload", {}).get("cursor")
        for update in anchor_result.get("updates", [])
        if update.get("payload", {}).get("cursor")
    ]
    latest_pc_cursor = pc_cursor_updates[-1] if pc_cursor_updates else {}
    relays = [
        relay
        for relay in chat_result.get("relays", [])
        if relay.get("action") == "yui_guide_chat_cursor_anchor"
    ]
    latest_relay = relays[-1] if relays else {}
    cursor_calls = [
        call for call in director_result
        if call.get("type") == "cursor" and call.get("kind") == "input"
    ]
    latest_director_call = cursor_calls[-1] if cursor_calls else {}
    greeting_updates = greeting_result.get("updates", [])
    greeting_cursor_updates = [
        update.get("payload", {}).get("cursor")
        for update in greeting_updates
        if update.get("payload", {}).get("cursor")
    ]
    greeting_start_index = int(greeting_result.get("beforeGreetingUpdateCount") or 0)
    greeting_updates_after_start = greeting_updates[greeting_start_index:]
    greeting_cursor_updates_after_start = [
        update.get("payload", {}).get("cursor")
        for update in greeting_updates_after_start
        if update.get("payload", {}).get("cursor")
    ]
    greeting_calls = greeting_result.get("calls", [])

    checks = [
        _check(
            any(
                cursor.get("visible") is False
                for cursor in greeting_cursor_updates_after_start
                if isinstance(cursor, dict)
            )
            and not any(
                cursor.get("effect") == "wobble"
                for cursor in greeting_cursor_updates_after_start
                if isinstance(cursor, dict)
            )
            and any(
                call.get("type") == "cursor" and call.get("kind") == ""
                for call in greeting_calls
            ),
            "Day1 intro greeting clears residual PC cursor wobble before narration",
            f"greeting cursor updates after start: {json.dumps(greeting_cursor_updates_after_start, ensure_ascii=False, sort_keys=True)}",
            f"greeting did not clear residual cursor wobble: {json.dumps(greeting_result, ensure_ascii=False, sort_keys=True)}",
        ),
        _check(
            latest_director_call.get("effect") == "wobble"
            and latest_director_call.get("effectDurationMs") == 2000,
            "Home director sends input wobble=2000ms to external chat",
            f"director cursor call: {json.dumps(latest_director_call, ensure_ascii=False, sort_keys=True)}",
            f"director did not send wobble=2000ms: {json.dumps(director_result, ensure_ascii=False, sort_keys=True)}",
        ),
        _check(
            latest_relay.get("effect") == "wobble"
            and latest_relay.get("effectDurationMs") == 2000,
            "External chat returns wobble=2000ms cursor anchor",
            f"external chat relay: {json.dumps(latest_relay, ensure_ascii=False, sort_keys=True)}",
            f"external chat did not relay wobble=2000ms: {json.dumps(chat_result, ensure_ascii=False, sort_keys=True)}",
        ),
        _check(
            latest_pc_cursor.get("effect") == "wobble"
            and latest_pc_cursor.get("effectDurationMs") == 2000
            and latest_pc_cursor.get("visible") is True,
            "Home director forwards wobble=2000ms to PC overlay",
            f"PC cursor payload: {json.dumps(latest_pc_cursor, ensure_ascii=False, sort_keys=True)}",
            f"PC cursor payload missing wobble=2000ms: {json.dumps(anchor_result, ensure_ascii=False, sort_keys=True)}",
        ),
    ]
    return checks, {
        "greetingResult": greeting_result,
        "directorCalls": director_result,
        "chatResult": chat_result,
        "anchorResult": anchor_result,
    }


def run_pc_renderer_probe(pc_repo: Path) -> tuple[list[Check], dict[str, Any]]:
    if not pc_repo.exists():
        result = {"pcRepo": str(pc_repo), "reason": "missing_pc_repo"}
        return [
            Check(
                "PC repo preflight",
                "FAIL",
                f"N.E.K.O.-PC repo was not found at {pc_repo}.",
            )
        ], result
    if shutil.which("node") is None:
        result = {"reason": "missing_node"}
        return [
            Check(
                "Node runtime preflight",
                "FAIL",
                "Node.js was not found on PATH, so the PC renderer probe could not run.",
            )
        ], result

    node_script = f"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const repoRoot = {json.dumps(str(pc_repo))};
function read(relativePath) {{
  return fs.readFileSync(path.join(repoRoot, relativePath), 'utf8');
}}
function createStyle() {{
  return {{
    _props: {{}},
    setProperty(name, value) {{ this._props[name] = value; }},
  }};
}}
function syncClassName(element, classes) {{
  element.className = Array.from(classes).join(' ');
}}
function createClassList(element, addLog) {{
  const readClasses = () => new Set(String(element.className || '').split(/\\s+/).filter(Boolean));
  const writeClasses = classes => syncClassName(element, classes);
  return {{
    add(...names) {{
      names.forEach(name => addLog.push({{ element: element.id || element.tagName, name }}));
      const classes = readClasses();
      names.forEach(name => classes.add(name));
      writeClasses(classes);
    }},
    remove(...names) {{
      const classes = readClasses();
      names.forEach(name => classes.delete(name));
      writeClasses(classes);
    }},
    contains(name) {{ return readClasses().has(name); }},
    toggle(name, force) {{
      const classes = readClasses();
      const shouldAdd = typeof force === 'boolean' ? force : !classes.has(name);
      if (shouldAdd) classes.add(name);
      else classes.delete(name);
      writeClasses(classes);
      return shouldAdd;
    }},
  }};
}}
function createFakeElement(tagName, addLog) {{
  const element = {{
    tagName,
    id: '',
    children: [],
    parentNode: null,
    hidden: false,
    style: createStyle(),
    className: '',
    classList: null,
    appendChild(child) {{ child.parentNode = this; this.children.push(child); return child; }},
    removeChild(child) {{ const i = this.children.indexOf(child); if (i >= 0) this.children.splice(i, 1); child.parentNode = null; return child; }},
    setAttribute(name, value) {{ this[name] = value; }},
    querySelector(selector) {{
      const className = selector.startsWith('.') ? selector.slice(1) : '';
      const stack = [...this.children];
      while (stack.length) {{
        const current = stack.shift();
        if (className && String(current.className || '').split(/\\s+/).includes(className)) return current;
        stack.push(...current.children);
      }}
      return null;
    }},
    get offsetWidth() {{ return 1; }},
  }};
  element.classList = createClassList(element, addLog);
  return element;
}}
const listeners = new Map();
const addLog = [];
const timeoutLog = [];
const stage = createFakeElement('div', addLog);
stage.id = 'stage';
const cursor = createFakeElement('div', addLog);
cursor.id = 'cursor';
const cursorVisual = createFakeElement('div', addLog);
cursorVisual.className = 'cursor-visual';
cursor.appendChild(cursorVisual);
const document = {{
  getElementById(id) {{ if (id === 'stage') return stage; if (id === 'cursor') return cursor; return null; }},
  createElement: (tagName) => createFakeElement(tagName, addLog),
}};
const window = {{
  innerWidth: 800,
  innerHeight: 600,
  addEventListener() {{}},
  requestAnimationFrame(callback) {{ callback(); return 1; }},
  setTimeout(callback, ms) {{ timeoutLog.push(ms); return 1; }},
}};
const context = vm.createContext({{
  require(moduleName) {{
    if (moduleName === 'electron') {{
      return {{ ipcRenderer: {{ on(channel, callback) {{ listeners.set(channel, callback); }} }} }};
    }}
    throw new Error('Unexpected require: ' + moduleName);
  }},
  document,
  window,
}});
vm.runInContext(read('src/preload-tutorial-global-overlay.js'), context);
const callback = listeners.get('neko:tutorial-overlay-state');
assert.equal(typeof callback, 'function');
const state = {{
  active: true,
  spotlights: [],
  cursor: {{ visible: true, x: 100, y: 120, durationMs: 0, effect: 'wobble', effectDurationMs: 2000 }},
  petal: null,
  assets: {{}},
}};
callback({{}}, state);
callback({{}}, state);
const serviceSource = read('src/tutorial-global-overlay-service.js');
const result = {{
  wobbleAdds: addLog.filter(entry => entry.name === 'is-wobbling').length,
  cursorClassName: cursor.className,
  effectDuration: cursorVisual.style._props['--cursor-effect-duration'] || '',
  timeoutLog,
  hasHorizontalWobbleCss: serviceSource.includes('translateX(-14px)') && serviceSource.includes('translateX(14px)'),
  hasDurationCss: serviceSource.includes('var(--cursor-effect-duration,2000ms)'),
}};
console.log(JSON.stringify(result));
"""
    with tempfile.NamedTemporaryFile("w", suffix=".cjs", encoding="utf-8", delete=False) as handle:
        handle.write(node_script)
        temp_path = Path(handle.name)
    try:
        try:
            completed = subprocess.run(
                ["node", str(temp_path)],
                cwd=str(pc_repo),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=30,
            )
        except subprocess.TimeoutExpired as exc:
            result = {"pcRepo": str(pc_repo), "reason": "node_probe_timed_out", "timeout": exc.timeout}
            return [
                Check(
                    "PC renderer probe executed",
                    "FAIL",
                    f"Node VM timed out after {exc.timeout} seconds.",
                )
            ], result
        except Exception as exc:
            result = {"pcRepo": str(pc_repo), "reason": "node_probe_failed_to_start", "error": str(exc)}
            return [
                Check(
                    "PC renderer probe executed",
                    "FAIL",
                    f"Node VM could not start: {exc}",
                )
            ], result
    finally:
        try:
            temp_path.unlink()
        except OSError:
            pass

    if completed.returncode != 0:
        result = {"stdout": completed.stdout, "stderr": completed.stderr, "returncode": completed.returncode}
    else:
        result = json.loads(completed.stdout.strip() or "{}")

    checks = [
        _check(
            completed.returncode == 0,
            "PC renderer probe executed",
            "Node VM loaded preload-tutorial-global-overlay.js successfully.",
            f"Node VM failed: {json.dumps(result, ensure_ascii=False, sort_keys=True)}",
        ),
        _check(
            result.get("wobbleAdds") == 2,
            "PC renderer replays repeated wobble payloads",
            f"is-wobbling was added {result.get('wobbleAdds')} times for two identical payloads.",
            f"is-wobbling was not replayed: {json.dumps(result, ensure_ascii=False, sort_keys=True)}",
        ),
        _check(
            result.get("effectDuration") == "2000ms",
            "PC renderer applies 2000ms effect duration",
            f"cursorVisual --cursor-effect-duration = {result.get('effectDuration')}",
            f"cursorVisual effect duration is wrong: {json.dumps(result, ensure_ascii=False, sort_keys=True)}",
        ),
        _check(
            result.get("hasHorizontalWobbleCss") is True and result.get("hasDurationCss") is True,
            "PC renderer CSS contains visible horizontal wobble",
            "PC overlay CSS contains translateX wobble and the 2000ms CSS variable fallback.",
            f"PC overlay CSS missing visible wobble pieces: {json.dumps(result, ensure_ascii=False, sort_keys=True)}",
        ),
    ]
    return checks, result


def print_report(checks: list[Check], raw: dict[str, Any]) -> int:
    failures = [check for check in checks if check.status == "FAIL"]
    print("PC Day1 capsule ghost cursor wobble diagnostic")
    print("=" * 52)
    for check in checks:
        print(f"[{check.status}] {check.name}")
        print(f"       {check.detail}")
    print("\nRaw probe result")
    print(json.dumps(raw, ensure_ascii=False, indent=2, sort_keys=True))
    print("\nSummary")
    print(f"PASS={sum(1 for check in checks if check.status == 'PASS')} FAIL={len(failures)}")
    if failures:
        print("\nLikely breakpoints")
        for check in failures:
            print(f"- {check.name}: {check.detail}")
    else:
        print("\nAll local bridge and renderer probes pass. If the live PC app still does not wobble, the running N.E.K.O.-PC process is likely using an older bundle/process and needs to be restarted or updated to the latest main.")
    return 1 if failures else 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Diagnose Day1 capsule ghost cursor wobble in N.E.K.O.-PC.")
    parser.add_argument("--pc-repo", type=Path, default=DEFAULT_PC_REPO, help="Path to the N.E.K.O.-PC repo.")
    parser.add_argument("--skip-browser", action="store_true", help="Skip Playwright bridge probes.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args(argv)

    checks: list[Check] = []
    raw: dict[str, Any] = {}
    pc_repo_checks = validate_pc_repo(args.pc_repo)
    checks.extend(pc_repo_checks)
    pc_repo_ok = all(check.status == "PASS" for check in pc_repo_checks)
    if pc_repo_ok:
        checks.extend(run_static_checks(args.pc_repo))
    else:
        raw["pcRepoPreflight"] = {
            "pcRepo": str(args.pc_repo),
            "ok": False,
            "errors": [check.detail for check in pc_repo_checks if check.status == "FAIL"],
        }
    if not args.skip_browser:
        browser_checks, browser_raw = run_browser_bridge_probe()
        checks.extend(browser_checks)
        raw["browser"] = browser_raw
    if pc_repo_ok:
        pc_checks, pc_raw = run_pc_renderer_probe(args.pc_repo)
        checks.extend(pc_checks)
        raw["pcRenderer"] = pc_raw

    if args.json:
        print(json.dumps(
            {
                "ok": all(check.status == "PASS" for check in checks),
                "checks": [check.__dict__ for check in checks],
                "raw": raw,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ))
        return 1 if any(check.status == "FAIL" for check in checks) else 0
    return print_report(checks, raw)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
