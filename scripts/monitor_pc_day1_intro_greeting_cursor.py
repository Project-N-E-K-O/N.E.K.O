from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = PROJECT_ROOT / "static"
GREETING_TEXT = (
    "微风、阳光，还有刚刚好出现的你。初次见面，我是林悠怡，未来的日子请多关照喵！"
    "我把关于这里的一切都写进新手指南里啦！就当作是我们相遇的第一份小礼物，请查收吧！"
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
            ".venv/bin/python scripts/monitor_pc_day1_intro_greeting_cursor.py"
        ) from exc
    return sync_playwright


def _finding(condition: bool, name: str, ok_detail: str, fail_detail: str) -> Finding:
    return Finding(
        name=name,
        status="PASS" if condition else "FAIL",
        detail=ok_detail if condition else fail_detail,
    )


def run_monitor() -> dict[str, Any]:
    sync_playwright = _load_playwright()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 720})
        page.route(
            "**/day1-intro-greeting-monitor",
            lambda route: route.fulfill(
                status=200,
                content_type="text/html",
                body="<!doctype html><html><head><meta charset='utf-8'></head><body></body></html>",
            ),
        )
        page.goto("http://neko.test/day1-intro-greeting-monitor")
        page.evaluate(
            """
            () => {
                window.history.pushState({}, '', '/');
                window.__NEKO_MULTI_WINDOW__ = true;
                window.__phase = 'setup';
                window.__timeline = [];
                window.safeT = (key, fallback) => typeof fallback === 'string' ? fallback : key;
                window.__mark = (type, detail = {}) => {
                    window.__timeline.push({
                        at: Math.round(performance.now()),
                        phase: window.__phase,
                        type,
                        detail,
                    });
                };
                window.nekoTutorialOverlay = {
                    getWindowMetricsSync: () => ({
                        bounds: { x: 100, y: 50, width: 1200, height: 800 },
                        contentBounds: { x: 100, y: 50, width: 1200, height: 800 },
                        zoomFactor: 1,
                    }),
                    begin: () => {
                        window.__mark('pc-overlay-begin');
                        return Promise.resolve({ ok: true });
                    },
                    update: (payload) => {
                        window.__mark('pc-overlay-update', payload || {});
                        return Promise.resolve({ ok: true });
                    },
                    clear: () => {
                        window.__mark('pc-overlay-clear');
                        return Promise.resolve({ ok: true });
                    },
                };
            }
            """
        )
        for script in (
            "tutorial/visual/highlight-controller.js",
            "tutorial-interrupt-controller.js",
            "tutorial/core/interaction-takeover.js",
            "tutorial/yui-guide/overlay.js",
            "tutorial/yui-guide/director.js",
        ):
            page.add_script_tag(path=str(STATIC_DIR / script))

        result = page.evaluate(
            """
            async (greetingText) => {
                const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
                const director = window.createYuiGuideDirector({ page: 'home' });
                director.isHomeChatExternalized = () => true;
                director.getStep = () => ({ performance: { voiceKey: 'intro_greeting_reply' } });
                director.waitForSceneDelay = async () => true;
                director.enableInterrupts = () => window.__mark('director-enable-interrupts');
                director.runIntroGreetingHugPerformance = async () => null;
                director.runIntroGiftHeartPerformance = async () => null;
                director.appendGuideChatMessage = (text, options) => {
                    window.__mark('append-guide-chat-message', {
                        text,
                        textMatchesGreeting: text === greetingText,
                        voiceKey: options && options.voiceKey,
                    });
                };
                director.speakGuideLine = async (text, options) => {
                    window.__phase = 'during-greeting-speech';
                    window.__mark('speech-start', {
                        text,
                        textMatchesGreeting: text === greetingText,
                        voiceKey: options && options.voiceKey,
                    });
                    await wait(280);
                    window.__mark('speech-end', {
                        textMatchesGreeting: text === greetingText,
                        voiceKey: options && options.voiceKey,
                    });
                    window.__phase = 'after-greeting-speech';
                    return null;
                };
                director.interactionTakeover = {
                    setExternalizedChatSpotlight: (kind) => window.__mark('external-chat-spotlight', { kind }),
                    setExternalizedChatCursor: (kind, options) => window.__mark('external-chat-cursor', {
                        kind,
                        effect: options && options.effect,
                        effectDurationMs: options && options.effectDurationMs,
                    }),
                };

                const originalCursorWobble = director.cursor.wobble.bind(director.cursor);
                director.cursor.wobble = (durationMs) => {
                    window.__mark('director-cursor-wobble', { durationMs: Number(durationMs) || 0 });
                    return originalCursorWobble(durationMs);
                };
                const originalCursorHide = director.cursor.hide.bind(director.cursor);
                director.cursor.hide = () => {
                    window.__mark('director-cursor-hide');
                    return originalCursorHide();
                };
                const originalOverlayWobble = director.overlay.wobbleCursor.bind(director.overlay);
                director.overlay.wobbleCursor = (durationMs) => {
                    window.__mark('overlay-wobble-cursor', { durationMs: Number(durationMs) || 0 });
                    return originalOverlayWobble(durationMs);
                };
                const originalOverlayHide = director.overlay.hideCursor.bind(director.overlay);
                director.overlay.hideCursor = () => {
                    window.__mark('overlay-hide-cursor');
                    return originalOverlayHide();
                };

                director.introFlowStarted = true;
                director.sceneRunId = 77;

                window.__phase = 'seed-previous-stage-wobble';
                director.cursor.showAt(320, 280);
                director.cursor.wobble(2000);
                await wait(0);

                const beforeGreetingIndex = window.__timeline.length;
                window.__phase = 'before-greeting-call';
                const ok = await director.playDay1IntroGreetingRoundScene(77);
                await wait(0);

                return {
                    ok,
                    beforeGreetingIndex,
                    timeline: window.__timeline,
                };
            }
            """,
            GREETING_TEXT,
        )
        chat = browser.new_page(viewport={"width": 1280, "height": 720})
        chat.route(
            "**/day1-intro-greeting-chat-monitor",
            lambda route: route.fulfill(
                status=200,
                content_type="text/html",
                body="<!doctype html><html><head><meta charset='utf-8'></head><body></body></html>",
            ),
        )
        chat.goto("http://neko.test/day1-intro-greeting-chat-monitor")
        chat.evaluate(
            """
            () => {
                window.history.pushState({}, '', '/chat');
                window.__NEKO_MULTI_WINDOW__ = true;
                window.localStorage.setItem('yuiGuidePcOverlayRunId', 'monitor-run');
                window.__chatTimeline = [];
                window.__chatPhase = 'setup';
                window.__markChat = (type, detail = {}) => {
                    window.__chatTimeline.push({
                        at: Math.round(performance.now()),
                        phase: window.__chatPhase,
                        type,
                        detail,
                    });
                };
                window.nekoTutorialOverlay = {
                    getWindowMetricsSync: () => ({
                        bounds: { x: 100, y: 50, width: 1200, height: 800 },
                        contentBounds: { x: 100, y: 50, width: 1200, height: 800 },
                        zoomFactor: 1,
                    }),
                    begin: (payload) => {
                        window.__markChat('chat-pc-overlay-begin', payload || {});
                        return Promise.resolve({ ok: true });
                    },
                    update: (payload) => {
                        window.__markChat('chat-pc-overlay-update', payload || {});
                        return Promise.resolve({ ok: true });
                    },
                    clear: () => Promise.resolve({ ok: true }),
                };
                const originalAnimate = Element.prototype.animate;
                Element.prototype.animate = function(keyframes, options) {
                    window.__markChat('element-animate', {
                        id: this.id || '',
                        className: this.className || '',
                        duration: options && options.duration,
                        keyframes: Array.isArray(keyframes) ? keyframes.length : 0,
                    });
                    return originalAnimate.call(this, keyframes, options);
                };
                document.body.innerHTML = `
                    <div id="react-chat-window-shell" class="neko-e-collapsed" style="position:fixed;left:550px;top:390px;width:470px;height:88px;">
                        <div id="react-chat-window-root">
                            <div class="compact-chat-surface-frame"
                                data-compact-geometry-owner="surface"
                                data-compact-geometry-item="input"
                                style="position:fixed;left:600px;top:420px;width:390px;height:54px;border-radius:999px;"></div>
                        </div>
                    </div>
                `;
                window.nekoChatWindow = {
                    ensureExpandedForTutorial() {
                        window.__markChat('ensure-expanded-for-tutorial');
                        document.getElementById('react-chat-window-shell').classList.remove('neko-e-collapsed');
                        return true;
                    },
                };
            }
            """
        )
        for part_path in sorted((STATIC_DIR / "app/app-interpage").glob("*.js")):
            chat.add_script_tag(path=str(part_path))
        chat_result = chat.evaluate(
            """
            async () => {
                const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
                window.__chatPhase = 'seed-chat-local-wobble';
                window.postMessage({
                    __nekoTutorialOverlayRelay: true,
                    payload: {
                        action: 'yui_guide_set_chat_cursor',
                        kind: 'input',
                        effect: 'wobble',
                        effectDurationMs: 2000,
                        timestamp: Date.now(),
                    },
                }, '*');
                await wait(40);
                window.__chatPhase = 'clear-for-greeting';
                window.postMessage({
                    __nekoTutorialOverlayRelay: true,
                    payload: {
                        action: 'yui_guide_set_chat_cursor',
                        kind: '',
                        effect: '',
                        timestamp: Date.now(),
                    },
                }, '*');
                await wait(820);
                return {
                    hasLocalCursor: !!document.getElementById('yui-guide-chat-cursor'),
                    timeline: window.__chatTimeline,
                };
            }
            """
        )
        browser.close()
        result["chatResult"] = chat_result
        return result


def analyze(result: dict[str, Any]) -> tuple[list[Finding], dict[str, Any]]:
    timeline = result.get("timeline") if isinstance(result, dict) else []
    if not isinstance(timeline, list):
        timeline = []
    chat_result = result.get("chatResult") if isinstance(result, dict) else {}
    chat_timeline = chat_result.get("timeline") if isinstance(chat_result, dict) else []
    if not isinstance(chat_timeline, list):
        chat_timeline = []

    before_greeting_index = int(result.get("beforeGreetingIndex") or 0)
    greeting_events = timeline[before_greeting_index:]
    during_speech_events = [
        event for event in greeting_events
        if event.get("phase") == "during-greeting-speech"
    ]
    speech_started = any(
        event.get("type") == "speech-start"
        and event.get("detail", {}).get("textMatchesGreeting") is True
        for event in greeting_events
    )
    director_wobble_during_greeting = [
        event for event in greeting_events
        if event.get("type") in {"director-cursor-wobble", "overlay-wobble-cursor"}
    ]
    director_wobble_during_speech = [
        event for event in during_speech_events
        if event.get("type") in {"director-cursor-wobble", "overlay-wobble-cursor"}
    ]
    external_cursor_wobble = [
        event for event in greeting_events
        if event.get("type") == "external-chat-cursor"
        and event.get("detail", {}).get("effect") == "wobble"
    ]
    pc_cursor_wobble = [
        event for event in greeting_events
        if event.get("type") == "pc-overlay-update"
        and event.get("detail", {}).get("payload", {}).get("cursor", {}).get("effect") == "wobble"
    ]
    pc_cursor_hide = [
        event for event in greeting_events
        if event.get("type") == "pc-overlay-update"
        and event.get("detail", {}).get("payload", {}).get("cursor", {}).get("visible") is False
    ]
    external_cursor_clear = [
        event for event in greeting_events
        if event.get("type") == "external-chat-cursor"
        and not event.get("detail", {}).get("kind")
    ]
    spotlight_input = [
        event for event in greeting_events
        if event.get("type") == "external-chat-spotlight"
        and event.get("detail", {}).get("kind") == "input"
    ]
    chat_wobble_after_clear = [
        event for event in chat_timeline
        if event.get("phase") == "clear-for-greeting"
        and event.get("type") == "element-animate"
        and event.get("detail", {}).get("duration") == 620
    ]
    chat_local_animations = [
        event for event in chat_timeline
        if event.get("type") == "element-animate"
    ]
    chat_pc_cursor_updates = [
        event for event in chat_timeline
        if event.get("type") == "chat-pc-overlay-update"
        and event.get("detail", {}).get("payload", {}).get("cursor")
    ]

    if pc_cursor_wobble:
        likely_reason = "第一句播放路径仍向 PC 全局 overlay 发送了 effect=wobble。"
    elif external_cursor_wobble:
        likely_reason = "第一句播放路径仍向外置胶囊聊天窗发送了 effect=wobble。"
    elif director_wobble_during_speech:
        likely_reason = "第一句台词播放期间 Director/Overlay 直接调用了 cursor.wobble()。"
    elif director_wobble_during_greeting and not pc_cursor_hide:
        likely_reason = "第一句开始前存在上一阶段 wobble，且进入台词前没有向 PC overlay 发送 hide。"
    elif not pc_cursor_hide:
        likely_reason = "没有监测到新的 wobble，但第一句开始前也没有清 PC cursor；真实界面可能看到上一阶段动画残留。"
    elif chat_wobble_after_clear:
        likely_reason = "外置聊天窗的旧 720ms cursor 重试在第一句 clear 之后重新触发了本地 ghost wobble。"
    else:
        likely_reason = "本地监控未发现第一句播放期间的新 wobble；若真机仍晃，多半是运行中的 PC/前端进程未加载当前静态代码。"

    findings = [
        _finding(
            speech_started,
            "Greeting line playback was observed",
            "监控到“微风、阳光...”这句台词开始播放。",
            "没有监控到“微风、阳光...”这句台词播放，脚本没有覆盖到目标路径。",
        ),
        _finding(
            not director_wobble_during_speech,
            "No Director cursor.wobble during greeting speech",
            "台词播放期间没有 Director/Overlay 直接调用 wobble。",
            f"台词播放期间仍有 wobble 调用: {json.dumps(director_wobble_during_speech, ensure_ascii=False)}",
        ),
        _finding(
            not external_cursor_wobble,
            "No external chat cursor wobble request during greeting",
            "第一句流程没有向外置胶囊聊天窗发送 effect=wobble。",
            f"外置聊天窗收到 wobble: {json.dumps(external_cursor_wobble, ensure_ascii=False)}",
        ),
        _finding(
            not pc_cursor_wobble,
            "No PC overlay wobble payload during greeting",
            "第一句流程没有向 PC 全局 overlay 发送 effect=wobble。",
            f"PC overlay 收到 wobble: {json.dumps(pc_cursor_wobble, ensure_ascii=False)}",
        ),
        _finding(
            bool(pc_cursor_hide),
            "Residual PC cursor is hidden before greeting speech",
            f"进入第一句后监控到 PC cursor hide: {json.dumps(pc_cursor_hide, ensure_ascii=False)}",
            "进入第一句后没有监控到 PC cursor hide，上一阶段 wobble 可能残留到台词播放期间。",
        ),
        _finding(
            bool(external_cursor_clear),
            "External chat cursor is cleared before greeting speech",
            f"进入第一句后监控到外置聊天窗 cursor clear: {json.dumps(external_cursor_clear, ensure_ascii=False)}",
            "进入第一句后没有监控到外置聊天窗 cursor clear。",
        ),
        _finding(
            bool(spotlight_input),
            "Input spotlight remains enabled for greeting",
            "第一句仍会打开胶囊输入框高亮。",
            "第一句没有监控到胶囊输入框高亮。",
        ),
        _finding(
            not chat_wobble_after_clear and chat_result.get("hasLocalCursor") is False,
            "External chat local cursor retry stays cleared after greeting clear",
            "清 cursor 后等待 720ms，没有旧重试重新触发聊天窗本地 ghost wobble。",
            "清 cursor 后本地状态异常: "
            f"chat_wobble_after_clear={json.dumps(chat_wobble_after_clear, ensure_ascii=False)}, "
            f"hasLocalCursor={chat_result.get('hasLocalCursor')}",
        ),
        _finding(
            not chat_local_animations and bool(chat_pc_cursor_updates),
            "External chat ghost cursor is rendered by PC global overlay only",
            f"外置聊天窗没有本地 ghost 动画，PC overlay 收到 cursor 更新: {json.dumps(chat_pc_cursor_updates, ensure_ascii=False)}",
            f"外置聊天窗仍在本地画 ghost，或没有投递到 PC overlay: {json.dumps(chat_timeline, ensure_ascii=False)}",
        ),
    ]

    summary = {
        "likelyReason": likely_reason,
        "eventCounts": {
            "greetingEvents": len(greeting_events),
            "directorWobbleDuringGreeting": len(director_wobble_during_greeting),
            "directorWobbleDuringSpeech": len(director_wobble_during_speech),
            "externalCursorWobble": len(external_cursor_wobble),
            "pcCursorWobble": len(pc_cursor_wobble),
            "pcCursorHide": len(pc_cursor_hide),
            "externalCursorClear": len(external_cursor_clear),
            "inputSpotlight": len(spotlight_input),
            "chatLocalWobbleAfterClear": len(chat_wobble_after_clear),
            "chatLocalAnimations": len(chat_local_animations),
            "chatPcCursorUpdates": len(chat_pc_cursor_updates),
        },
    }
    return findings, summary


def print_report(result: dict[str, Any], findings: list[Finding], summary: dict[str, Any]) -> int:
    failures = [finding for finding in findings if finding.status == "FAIL"]
    print("PC Day1 intro greeting ghost cursor monitor")
    print("=" * 56)
    print("Target line:")
    print(GREETING_TEXT)
    print("\nFindings")
    for finding in findings:
        print(f"[{finding.status}] {finding.name}")
        print(f"       {finding.detail}")
    print("\nConclusion")
    print(summary.get("likelyReason", "未能判断原因。"))
    print("\nEvent counts")
    print(json.dumps(summary.get("eventCounts", {}), ensure_ascii=False, indent=2, sort_keys=True))
    print("\nTimeline")
    print(json.dumps(result.get("timeline", []), ensure_ascii=False, indent=2, sort_keys=True))
    print("\nExternal Chat Timeline")
    print(json.dumps(result.get("chatResult", {}).get("timeline", []), ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if failures else 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Monitor why the Day1 intro greeting line makes the PC ghost cursor wobble."
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args(argv)

    result = run_monitor()
    findings, summary = analyze(result)
    if args.json:
        print(json.dumps(
            {
                "ok": all(finding.status == "PASS" for finding in findings),
                "targetLine": GREETING_TEXT,
                "findings": [finding.__dict__ for finding in findings],
                "summary": summary,
                "raw": result,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ))
        return 1 if any(finding.status == "FAIL" for finding in findings) else 0
    return print_report(result, findings, summary)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
