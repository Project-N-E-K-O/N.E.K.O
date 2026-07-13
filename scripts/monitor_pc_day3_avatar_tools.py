from __future__ import annotations

import argparse
import json
import mimetypes
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from yui_guide_director_parts import DIRECTOR_SCRIPT_NAMES


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = PROJECT_ROOT / "static"
DAY3_TOOL_TOGGLE_TEXT = "在这个小按钮里，有许多可以和人家互动的小道具呢。"
DAY3_AVATAR_PROPS_TEXT = (
    "你可以随时来摸摸我的头，或者给我吃一根甜甜的棒棒糖。"
    "如果有时候我不小心做错事了，你也可以用小锤子敲敲我，不过……一定要轻轻的，不能太用力哦。"
)


@dataclass
class Finding:
    name: str
    status: str
    detail: str


def _load_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover - CLI diagnostic path
        raise RuntimeError(
            "Playwright is required. Run with the repo venv, for example: "
            ".venv/bin/python scripts/monitor_pc_day3_avatar_tools.py"
        ) from exc
    return sync_playwright


def _finding(condition: bool, name: str, ok_detail: str, fail_detail: str) -> Finding:
    return Finding(
        name=name,
        status="PASS" if condition else "FAIL",
        detail=ok_detail if condition else fail_detail,
    )


def _fulfill_static(route: Any) -> None:
    request_url = route.request.url
    path_part = request_url.split("://", 1)[-1].split("/", 1)
    request_path = "/" + (path_part[1].split("?", 1)[0] if len(path_part) > 1 else "")
    if request_path in ("/", "/day3-home-monitor"):
        route.fulfill(
            status=200,
            content_type="text/html",
            body="<!doctype html><html><head><meta charset='utf-8'></head><body></body></html>",
        )
        return
    if request_path in ("/chat", "/day3-chat-monitor"):
        body = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <link rel="stylesheet" href="/static/react/neko-chat/neko-chat-window.css">
  <style>
    html, body { margin: 0; width: 100%; height: 100%; overflow: hidden; }
    #react-chat-window-overlay { position: fixed; inset: 0; }
    #react-chat-window-shell { position: fixed; left: 64px; top: 70px; width: 520px; height: 360px; overflow: visible; }
    #react-chat-window-root { width: 100%; height: 100%; overflow: visible; }
  </style>
</head>
<body class="electron-chat-window">
  <div id="react-chat-window-overlay" hidden>
    <div id="react-chat-window-backdrop"></div>
    <div id="react-chat-window-shell" role="dialog" aria-modal="true">
      <div id="react-chat-window-drag-handle" aria-hidden="true"></div>
      <div id="react-chat-window-header-actions">
        <button id="avatarPreviewHeaderButton" type="button" aria-label="avatar"></button>
        <button id="exportConversationButton" type="button" aria-label="export"></button>
        <button id="reactChatWindowMinimizeButton" type="button" aria-label="minimize">
          <img id="reactChatWindowMinimizeIcon" src="/static/icons/expand_icon_off.png" alt="">
        </button>
        <button id="reactChatWindowCloseButton" type="button" aria-label="close">x</button>
      </div>
      <div id="react-chat-window-root"></div>
    </div>
  </div>
</body>
</html>
"""
        route.fulfill(status=200, content_type="text/html", body=body)
        return
    if request_path.startswith("/static/"):
        file_path = PROJECT_ROOT / request_path.lstrip("/")
        if file_path.exists() and file_path.is_file():
            content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
            route.fulfill(status=200, content_type=content_type, body=file_path.read_bytes())
            return
    route.fulfill(status=404, content_type="text/plain", body="not found")


def _install_static_routes(page: Any) -> None:
    page.route("**/*", _fulfill_static)


def _load_scripts(page: Any, scripts: list[str]) -> None:
    for script in scripts:
        page.add_script_tag(path=str(STATIC_DIR / script))


def run_monitor() -> dict[str, Any]:
    sync_playwright = _load_playwright()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        home = browser.new_page(viewport={"width": 1280, "height": 720})
        chat = browser.new_page(viewport={"width": 900, "height": 620})
        _install_static_routes(home)
        _install_static_routes(chat)

        home.goto("http://neko.test/day3-home-monitor")
        chat.goto("http://neko.test/chat")

        timeline: list[dict[str, Any]] = []
        started_at = {"home": 0}

        def mark(source: str, event_type: str, detail: Any | None = None) -> None:
            timeline.append({
                "source": source,
                "type": event_type,
                "detail": detail or {},
            })

        home.evaluate(
            """
            () => {
                window.__NEKO_MULTI_WINDOW__ = true;
                window.__monitorEvents = [];
                window.__relayToChatMessages = [];
                window.__markLocal = (type, detail = {}) => {
                    window.__monitorEvents.push({ at: Math.round(performance.now()), source: 'home', type, detail });
                };
                window.safeT = (key, fallback) => typeof fallback === 'string' ? fallback : key;
                window.nekoTutorialOverlay = {
                    getCapabilities: () => ({ petalTransition: true }),
                    getWindowMetricsSync: () => ({
                        bounds: { x: 30, y: 40, width: 1280, height: 720 },
                        contentBounds: { x: 30, y: 40, width: 1280, height: 720 },
                        zoomFactor: 1,
                    }),
                    begin: (payload) => {
                        window.__markLocal('pc-overlay-begin', payload || {});
                        return Promise.resolve({ ok: true });
                    },
                    update: (payload) => {
                        window.__markLocal('pc-overlay-update', payload || {});
                        return Promise.resolve({ ok: true });
                    },
                    clear: (payload) => {
                        window.__markLocal('pc-overlay-clear', payload || {});
                        return Promise.resolve({ ok: true });
                    },
                    relayToChat: (message) => {
                        const payload = message || {};
                        window.__relayToChatMessages.push(payload);
                        window.__markLocal('native-relay-to-chat', payload);
                        return Promise.resolve({ ok: true });
                    },
                    relayToPet: (message) => Promise.resolve(window.__markLocal('relay-to-pet-local', message || {})),
                };
                try {
                    window.localStorage.setItem('yuiGuidePcOverlayRunId', 'monitor-day3-' + Date.now());
                } catch (_) {}
            }
            """
        )
        _load_scripts(home, [
            "tutorial/visual/highlight-controller.js",
            "tutorial-interrupt-controller.js",
            "tutorial/core/interaction-takeover.js",
            "tutorial/yui-guide/overlay.js",
            *DIRECTOR_SCRIPT_NAMES,
            "tutorial/yui-guide/days/day3-interaction-guide.js",
        ])

        chat.evaluate(
            """
            () => {
                window.__NEKO_MULTI_WINDOW__ = true;
                window.nekoChatWindow = { ensureExpandedForTutorial: () => true };
                window.__monitorEvents = [];
                window.__markLocal = (type, detail = {}) => {
                    window.__monitorEvents.push({ at: Math.round(performance.now()), source: 'chat', type, detail });
                };
                window.safeT = (key, fallback) => typeof fallback === 'string' ? fallback : key;
                window.t = window.safeT;
                window.showStatusToast = () => {};
                window.nekoTutorialOverlay = {
                    getCapabilities: () => ({ petalTransition: true }),
                    getWindowMetricsSync: () => ({
                        bounds: { x: 120, y: 90, width: 900, height: 620 },
                        contentBounds: { x: 120, y: 90, width: 900, height: 620 },
                        zoomFactor: 1,
                    }),
                    begin: (payload) => {
                        window.__markLocal('pc-overlay-begin', payload || {});
                        return Promise.resolve({ ok: true });
                    },
                    update: (payload) => {
                        window.__markLocal('pc-overlay-update', payload || {});
                        return Promise.resolve({ ok: true });
                    },
                    clear: (payload) => {
                        window.__markLocal('pc-overlay-clear', payload || {});
                        return Promise.resolve({ ok: true });
                    },
                    relayToPet: (message) => Promise.resolve(window.__markLocal('relay-to-pet-local', message || {})),
                    relayToChat: (message) => Promise.resolve(window.__markLocal('relay-to-chat-local', message || {})),
                };
                try {
                    window.localStorage.setItem('yuiGuidePcOverlayRunId', 'monitor-day3-' + Date.now());
                } catch (_) {}
            }
            """
        )
        chat.add_script_tag(path=str(STATIC_DIR / "react" / "neko-chat" / "neko-chat-window.iife.js"))
        _load_scripts(chat, ["app/app-react-chat-window.js", "app/app-interpage.js"])

        chat.evaluate(
            """
            async () => {
                const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
                window.reactChatWindowHost.openWindow();
                await wait(260);
                window.reactChatWindowHost.setViewProps({
                    compactChatState: 'input',
                    composerDisabled: false,
                    composerHidden: false,
                });
                window.reactChatWindowHost.setChatSurfaceMode('compact');
                window.reactChatWindowHost.setCompactChatState('input');
                await wait(260);

                const patchButton = () => {
                    const toggle = document.querySelector('.send-button-circle.compact-input-tool-toggle');
                    const avatar = document.querySelector('.compact-input-tool-item-avatar .composer-emoji-btn');
                    if (toggle && !toggle.dataset.monitorPatched) {
                        toggle.dataset.monitorPatched = 'true';
                        toggle.addEventListener('click', () => window.__markLocal('tool-toggle-dom-click', snapshot()), true);
                    }
                    if (avatar && !avatar.dataset.monitorPatched) {
                        avatar.dataset.monitorPatched = 'true';
                        avatar.addEventListener('click', () => window.__markLocal('avatar-button-dom-click', snapshot()), true);
                    }
                };
                window.__snapshotDay3Chat = () => snapshot();
                function snapshot() {
                    const toggle = document.querySelector('.send-button-circle.compact-input-tool-toggle');
                    const fan = document.querySelector('.compact-input-tool-fan');
                    const avatar = document.querySelector('.compact-input-tool-item-avatar .composer-emoji-btn');
                    const popover = document.getElementById('composer-tool-popover-compact')
                        || document.getElementById('composer-tool-popover');
                    const toolIds = Array.from(document.querySelectorAll(
                        '#composer-tool-popover-compact .composer-icon-button[data-avatar-tool-id], #composer-tool-popover .composer-icon-button[data-avatar-tool-id]'
                    )).map((node) => node.getAttribute('data-avatar-tool-id'));
                    const isVisible = (node) => {
                        if (!node) return false;
                        const rect = node.getBoundingClientRect();
                        const style = window.getComputedStyle(node);
                        return rect.width > 0
                            && rect.height > 0
                            && style.visibility !== 'hidden'
                            && style.display !== 'none'
                            && Number(style.opacity || '1') > 0.01;
                    };
                    const visibleToolIds = Array.from(document.querySelectorAll(
                        '#composer-tool-popover-compact .composer-icon-button[data-avatar-tool-id], #composer-tool-popover .composer-icon-button[data-avatar-tool-id]'
                    ))
                        .filter(isVisible)
                        .map((node) => node.getAttribute('data-avatar-tool-id'));
                    return {
                        toggleExists: !!toggle,
                        toggleOpen: !!(toggle && (toggle.classList.contains('is-open') || toggle.getAttribute('aria-expanded') === 'true')),
                        fanExists: !!fan,
                        fanOpen: fan ? fan.getAttribute('data-compact-input-tool-fan-open') : '',
                        fanInteractive: fan ? fan.getAttribute('data-compact-input-tool-fan-interactive') : '',
                        avatarExists: !!avatar,
                        avatarDisabled: !!(avatar && avatar.disabled),
                        avatarExpanded: avatar ? avatar.getAttribute('aria-expanded') : '',
                        avatarActive: !!(avatar && avatar.classList.contains('is-active')),
                        popoverExists: !!popover,
                        toolIds,
                        toolCount: toolIds.length,
                        visibleToolIds,
                        visibleToolCount: visibleToolIds.length,
                    };
                }
                patchButton();
                window.__markLocal('chat-ready', snapshot());
                const observer = new MutationObserver(() => {
                    patchButton();
                    window.__markLocal('chat-dom-mutated', snapshot());
                });
                observer.observe(document.getElementById('react-chat-window-root'), {
                    attributes: true,
                    childList: true,
                    subtree: true,
                });
            }
            """
        )

        result = home.evaluate(
            """
            async () => {
                const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
                const director = window.createYuiGuideDirector({ page: 'home' });
                const originalWait = director.waitForSceneDelay.bind(director);
                director.speakGuideLine = async (text, options) => {
                    window.__markLocal('speech-start', { text, voiceKey: options && options.voiceKey });
                    await wait(120);
                    window.__markLocal('speech-end', { text, voiceKey: options && options.voiceKey });
                    return null;
                };
                director.waitForSceneDelay = async (durationMs) => {
                    window.__markLocal('director-wait', { durationMs });
                    return originalWait(Math.min(Math.max(Number(durationMs) || 0, 0), 240));
                };
                director.appendGuideChatMessage = (text, options) => {
                    window.__markLocal('append-guide-chat-message', { text, voiceKey: options && options.voiceKey });
                };
                director.applyGuideEmotion = (emotion) => window.__markLocal('emotion', { emotion });
                director.enableInterrupts = () => window.__markLocal('enable-interrupts');
                director.ensureAvatarFloatingGuideSurfaceReady = async () => true;
                director.ensureGuideIdleSwayPerformance = async () => true;
                director.ensurePersistentGhostCursorLookAtPerformance = async () => null;
                director.setTutorialTakingOver = (active) => window.__markLocal('taking-over', { active });
                director.isHomeChatExternalized = () => true;
                director.interactionTakeover = {
                    setExternalizedChatSpotlight: (kind) => {
                        const message = {
                            action: 'yui_guide_set_chat_spotlight',
                            kind,
                            timestamp: Date.now(),
                        };
                        window.__relayToChatMessages.push(message);
                        window.__markLocal('relay-to-chat', message);
                    },
                    setExternalizedChatCursor: (kind, options = {}) => {
                        const message = {
                            action: 'yui_guide_set_chat_cursor',
                            kind,
                            effect: typeof options.effect === 'string' ? options.effect : '',
                            effectDurationMs: Number.isFinite(options.effectDurationMs) ? options.effectDurationMs : 0,
                            targetIndex: Number.isFinite(options.targetIndex) ? options.targetIndex : 0,
                            timestamp: Date.now(),
                        };
                        if (Number.isFinite(options.durationMs)) {
                            message.durationMs = options.durationMs;
                        }
                        window.__relayToChatMessages.push(message);
                        window.__markLocal('relay-to-chat', message);
                    },
                    clearExternalizedChatSpotlight: () => {
                        const message = {
                            action: 'yui_guide_set_chat_spotlight',
                            kind: '',
                            timestamp: Date.now(),
                        };
                        window.__relayToChatMessages.push(message);
                        window.__markLocal('relay-to-chat', message);
                    },
                    clearExternalizedChatCursor: () => {
                        const message = {
                            action: 'yui_guide_set_chat_cursor',
                            kind: '',
                            effect: '',
                            timestamp: Date.now(),
                        };
                        window.__relayToChatMessages.push(message);
                        window.__markLocal('relay-to-chat', message);
                    },
                    setExternalizedChatCompactToolFanOpen: (open, reason) => {
                        const message = {
                            action: 'yui_guide_set_compact_tool_fan_open',
                            open: open === true,
                            reason: reason || '',
                            timestamp: Date.now(),
                        };
                        window.__relayToChatMessages.push(message);
                        window.__markLocal('relay-to-chat', message);
                    },
                    setExternalizedChatAvatarToolMenuOpen: (open, reason) => {
                        const message = {
                            action: 'yui_guide_set_avatar_tool_menu_open',
                            open: open === true,
                            reason: reason || '',
                            timestamp: Date.now(),
                        };
                        window.__relayToChatMessages.push(message);
                        window.__markLocal('relay-to-chat', message);
                    },
                    clickExternalizedChatAvatarToolButton: (reason) => {
                        const message = {
                            action: 'yui_guide_click_avatar_tool_button',
                            reason: reason || '',
                            timestamp: Date.now(),
                        };
                        window.__relayToChatMessages.push(message);
                        window.__markLocal('relay-to-chat', message);
                    },
                };
                const originalClickCursorAndWait = director.clickCursorAndWait.bind(director);
                director.clickCursorAndWait = async (holdMs) => {
                    window.__markLocal('home-clickCursorAndWait-start', { holdMs });
                    const value = await originalClickCursorAndWait(holdMs);
                    window.__markLocal('home-clickCursorAndWait-end', { holdMs, value });
                    return value;
                };
                const originalSetCompactToolFanOpen = director.setCompactToolFanOpen.bind(director);
                director.setCompactToolFanOpen = (open, reason) => {
                    window.__markLocal('director-setCompactToolFanOpen', { open, reason });
                    return originalSetCompactToolFanOpen(open, reason);
                };
                const originalClickChatAvatarToolButton = director.clickChatAvatarToolButton.bind(director);
                director.clickChatAvatarToolButton = (reason) => {
                    window.__markLocal('director-clickChatAvatarToolButton', { reason });
                    return originalClickChatAvatarToolButton(reason);
                };
                const originalRunOperation = director.runAvatarFloatingSceneOperation.bind(director);
                director.runAvatarFloatingSceneOperation = async (scene, primaryTarget, narrationStartedAt) => {
                    window.__markLocal('operation-start', { sceneId: scene && scene.id, operation: scene && scene.operation });
                    const value = await originalRunOperation(scene, primaryTarget, narrationStartedAt);
                    window.__markLocal('operation-end', { sceneId: scene && scene.id, operation: scene && scene.operation, value });
                    return value;
                };
                const originalRunExternalizedClick = director.runExternalizedChatCursorClickScene.bind(director);
                director.runExternalizedChatCursorClickScene = async (scene, startSceneOperation) => {
                    window.__markLocal('externalized-click-scene-start', { sceneId: scene && scene.id, cursorMoveDurationMs: scene && scene.cursorMoveDurationMs });
                    const value = await originalRunExternalizedClick(scene, startSceneOperation);
                    window.__markLocal('externalized-click-scene-end', { sceneId: scene && scene.id, value });
                    return value;
                };
                const originalSetExternalizedChatCursorEffect = director.setExternalizedChatCursorEffect.bind(director);
                director.setExternalizedChatCursorEffect = (kind, effect, options) => {
                    window.__markLocal('director-setExternalizedChatCursorEffect', { kind, effect, options: options || {} });
                    return originalSetExternalizedChatCursorEffect(kind, effect, options);
                };
                window.__director = director;

                const scenes = window.YuiGuideDailyGuides[3].round.scenes.filter((scene) => (
                    scene.id === 'day3_tool_toggle_intro'
                    || scene.id === 'day3_avatar_tools'
                    || scene.id === 'day3_avatar_tools_props'
                ));
                const results = [];
                for (let index = 0; index < scenes.length; index += 1) {
                    window.__markLocal('scene-start', { sceneId: scenes[index].id });
                    const ok = await director.playAvatarFloatingScene(scenes[index], 3, index, 7);
                    window.__markLocal('scene-end', { sceneId: scenes[index].id, ok });
                    results.push({ sceneId: scenes[index].id, ok });
                    await wait(120);
                }
                return {
                    results,
                    homeEvents: window.__monitorEvents,
                    relayToChatMessages: window.__relayToChatMessages,
                };
            }
            """
        )

        for message in result.get("relayToChatMessages") or []:
            mark("home", "relay-to-chat-replay", message)
            chat.evaluate(
                """
                async (message) => {
                    const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
                    window.__markLocal('relay-from-home', message || {});
                    window.dispatchEvent(new CustomEvent('neko:tutorial-overlay-relay', { detail: message || {} }));
                    window.postMessage({ __nekoTutorialOverlayRelay: true, payload: message || {} }, '*');
                    await wait(300);
                }
                """,
                message or {},
            )

        chat_state = chat.evaluate("() => window.__snapshotDay3Chat ? window.__snapshotDay3Chat() : null")
        chat_events = chat.evaluate("() => window.__monitorEvents || []")
        home_events = home.evaluate("() => window.__monitorEvents || []")
        chat.evaluate(
            """
            async () => {
                const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
                const clearMessages = [
                    { action: 'yui_guide_set_chat_spotlight', kind: '', timestamp: Date.now() },
                    { action: 'yui_guide_set_chat_cursor', kind: '', effect: '', timestamp: Date.now() },
                ];
                clearMessages.forEach((message) => {
                    window.dispatchEvent(new CustomEvent('neko:tutorial-overlay-relay', { detail: message }));
                    window.postMessage({ __nekoTutorialOverlayRelay: true, payload: message }, '*');
                });
                await wait(80);
            }
            """
        )
        browser.close()

        return {
            "result": result,
            "chatState": chat_state,
            "timeline": timeline,
            "homeEvents": home_events,
            "chatEvents": chat_events,
        }


def analyze(raw: dict[str, Any]) -> tuple[list[Finding], dict[str, Any]]:
    timeline = raw.get("timeline") or []
    home_events = raw.get("homeEvents") or []
    chat_events = raw.get("chatEvents") or []
    all_events = timeline + home_events + chat_events

    def count(event_type: str, source: str | None = None, predicate=None) -> int:
        total = 0
        for event in all_events:
            if event.get("type") != event_type:
                continue
            if source and event.get("source") != source:
                continue
            if predicate and not predicate(event):
                continue
            total += 1
        return total

    def has_pc_cursor_effect(effect: str) -> bool:
        for event in all_events:
            if event.get("type") != "pc-overlay-update":
                continue
            payload = event.get("detail") or {}
            cursor = ((payload.get("payload") or {}).get("cursor") or {})
            if cursor.get("effect") == effect:
                return True
        return False

    tool_toggle_open_before_home_click = False
    saw_home_click = False
    for event in all_events:
        if event.get("type") == "home-clickCursorAndWait-start":
            saw_home_click = True
        if event.get("type") == "director-setCompactToolFanOpen" and not saw_home_click:
            detail = event.get("detail") or {}
            if detail.get("open") is True:
                tool_toggle_open_before_home_click = True

    chat_state = raw.get("chatState") or {}
    snapshot_events = [
        event
        for event in all_events
        if isinstance(event.get("detail"), dict) and "toolCount" in (event.get("detail") or {})
    ]
    snapshots = [event.get("detail") or {} for event in snapshot_events]

    def visible_count(snapshot: dict[str, Any]) -> int:
        value = snapshot.get("visibleToolCount")
        if value is None:
            value = snapshot.get("toolCount")
        return int(value or 0)

    max_tool_count = max([visible_count(snapshot) for snapshot in snapshots] or [0])

    def closest_snapshot_to(event_types: set[str], source: str | None = None) -> dict[str, Any]:
        targets = [
            event
            for event in all_events
            if event.get("type") in event_types
            and (source is None or event.get("source") == source)
            and isinstance(event.get("at"), (int, float))
        ]
        if not targets:
            return {}
        best: tuple[float, dict[str, Any]] | None = None
        for snapshot_event in snapshot_events:
            if not isinstance(snapshot_event.get("at"), (int, float)):
                continue
            detail = snapshot_event.get("detail") or {}
            distance = min(abs(snapshot_event["at"] - target["at"]) for target in targets)
            if best is None or distance < best[0]:
                best = (distance, detail)
        return best[1] if best else {}

    avatar_click_snapshot = closest_snapshot_to({"avatar-button-dom-click"}, "chat") or closest_snapshot_to({"home-clickCursorAndWait-start"})
    visible_tool_ids = []
    visible_tool_at = None
    for event in all_events:
        detail = event.get("detail") or {}
        if not isinstance(detail, dict):
            continue
        if visible_count(detail) >= 3:
            visible_tool_ids = detail.get("visibleToolIds") or []
            if detail.get("visibleToolCount") is None and not visible_tool_ids:
                visible_tool_ids = detail.get("toolIds") or []
            visible_tool_at = event.get("at")
            break
    close_clicks = count(
        "relay-from-home",
        "chat",
        lambda event: (event.get("detail") or {}).get("action") == "yui_guide_click_avatar_tool_button"
        and "close" in ((event.get("detail") or {}).get("reason") or "")
    )
    findings = [
        _finding(
            count("home-clickCursorAndWait-start") >= 2,
            "Director starts visible click animation for both Day 3 click scenes",
            "工具总按钮和 Avatar 工具按钮两个 click scene 都调用了 clickCursorAndWait。",
            "至少有一个 Day 3 click scene 没有调用 clickCursorAndWait。",
        ),
        _finding(
            not tool_toggle_open_before_home_click,
            "Tool fan open is not requested before click start",
            "弧形菜单打开请求发生在 clickCursorAndWait 之后。",
            "弧形菜单打开请求早于 clickCursorAndWait，解释了“没点击动画就打开”。",
        ),
        _finding(
            has_pc_cursor_effect("click"),
            "PC overlay receives a cursor click effect",
            "PC overlay timeline 中出现 cursor.effect=click。",
            "PC overlay 没收到 cursor.effect=click；首页 clickCursorAndWait 不等于 PC 可见点击动画。",
        ),
        _finding(
            count("avatar-button-dom-click", "chat") >= 1,
            "Chat Avatar button receives a real DOM click",
            "外置聊天窗 Avatar 按钮收到真实 click。",
            "外置聊天窗 Avatar 按钮没有收到真实 click；道具菜单不会由 React onClick 展开。",
        ),
        _finding(
            max_tool_count >= 3,
            "Three avatar tools become visible during Avatar click",
            f"过程里检测到 {max_tool_count} 个道具: {visible_tool_ids}",
            f"过程里最多只检测到 {max_tool_count} 个道具；最终状态: {chat_state.get('toolIds')}",
        ),
        _finding(
            avatar_click_snapshot.get("fanInteractive") == "true",
            "Compact tool fan is interactive when Avatar click runs",
            "弧形菜单已进入 interactive=true 状态。",
            f"弧形菜单 interactive={avatar_click_snapshot.get('fanInteractive')} avatarExpanded={avatar_click_snapshot.get('avatarExpanded')}；过早 click 会被 React shouldSuppressCompactToolClick 吃掉。",
        ),
        _finding(
            close_clicks >= 1,
            "Tutorial sends a close click after narration",
            "旁白结束后教程会再次点击 Avatar 按钮关闭道具菜单；最终 0 个道具是可解释的关闭状态。",
            "没有检测到关闭点击；如果最终仍为 0，需要继续查 DOM 或状态同步。",
        ),
    ]

    summary = {
        "eventCounts": {
            "pcOverlayClickUpdates": count("pc-overlay-update", predicate=lambda event: (((event.get("detail") or {}).get("payload") or {}).get("cursor") or {}).get("effect") == "click"),
            "homeClickCursorAndWait": count("home-clickCursorAndWait-start"),
            "directorSetCompactToolFanOpen": count("director-setCompactToolFanOpen"),
            "directorClickChatAvatarToolButton": count("director-clickChatAvatarToolButton"),
            "relayToChat": count("relay-to-chat"),
            "avatarButtonDomClick": count("avatar-button-dom-click", "chat"),
            "toolToggleDomClick": count("tool-toggle-dom-click", "chat"),
            "chatDomMutations": count("chat-dom-mutated", "chat"),
        },
        "maxToolCountDuringRun": max_tool_count,
        "visibleToolIdsDuringRun": visible_tool_ids,
        "visibleToolAtMs": visible_tool_at,
        "avatarClickSnapshot": avatar_click_snapshot,
        "finalChatState": chat_state,
    }
    return findings, summary


def _is_key_event(event: dict[str, Any]) -> bool:
    event_type = event.get("type")
    detail = event.get("detail") or {}
    if event_type in {
        "home-clickCursorAndWait-start",
        "director-setCompactToolFanOpen",
        "director-clickChatAvatarToolButton",
        "avatar-button-dom-click",
        "tool-toggle-dom-click",
    }:
        return True
    if event_type in {"relay-to-chat", "relay-to-chat-replay", "relay-from-home"}:
        return (detail.get("action") or "") in {
            "yui_guide_set_chat_cursor",
            "yui_guide_set_compact_tool_fan_open",
            "yui_guide_click_avatar_tool_button",
        }
    if event_type == "pc-overlay-update":
        cursor = ((detail.get("payload") or {}).get("cursor") or {})
        return bool(cursor.get("effect"))
    if isinstance(detail, dict) and int(detail.get("toolCount") or 0) >= 3:
        return True
    return False


def print_report(raw: dict[str, Any], findings: list[Finding], summary: dict[str, Any], verbose: bool = False) -> int:
    failures = [finding for finding in findings if finding.status == "FAIL"]
    print("PC Day3 Avatar tools monitor")
    print("=" * 48)
    print("Target lines:")
    print(f"- {DAY3_TOOL_TOGGLE_TEXT}")
    print(f"- {DAY3_AVATAR_PROPS_TEXT}")
    print("\nFindings")
    for finding in findings:
        print(f"[{finding.status}] {finding.name}")
        print(f"       {finding.detail}")
    print("\nEvent counts")
    print(json.dumps(summary.get("eventCounts", {}), ensure_ascii=False, indent=2, sort_keys=True))
    print("\nFinal chat state")
    print(json.dumps(summary.get("finalChatState", {}), ensure_ascii=False, indent=2, sort_keys=True))
    key_events = [
        event
        for event in (
            (raw.get("timeline") or [])
            + (raw.get("homeEvents") or [])
            + (raw.get("chatEvents") or [])
        )
        if _is_key_event(event)
    ]
    print("\nKey events")
    print(json.dumps(key_events, ensure_ascii=False, indent=2, sort_keys=True))
    if verbose:
        print("\nTimeline")
        print(json.dumps(raw.get("timeline", []), ensure_ascii=False, indent=2, sort_keys=True))
        print("\nHome events")
        print(json.dumps(raw.get("homeEvents", []), ensure_ascii=False, indent=2, sort_keys=True))
        print("\nChat events")
        print(json.dumps(raw.get("chatEvents", []), ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if failures else 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Monitor why PC Day3 Avatar tools do not show three props.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument("--verbose", action="store_true", help="Print the full raw event log.")
    args = parser.parse_args(argv)

    raw = run_monitor()
    findings, summary = analyze(raw)
    if args.json:
        print(json.dumps(
            {
                "ok": all(finding.status == "PASS" for finding in findings),
                "findings": [finding.__dict__ for finding in findings],
                "summary": summary,
                "raw": raw,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ))
        return 1 if any(finding.status == "FAIL" for finding in findings) else 0
    return print_report(raw, findings, summary, verbose=args.verbose)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
