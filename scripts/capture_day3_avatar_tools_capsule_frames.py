from __future__ import annotations

import argparse
import base64
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from monitor_pc_day3_avatar_tools import (
    DAY3_AVATAR_PROPS_TEXT,
    PROJECT_ROOT,
    STATIC_DIR,
    _install_static_routes,
    _load_playwright,
    _load_scripts,
)


DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "artifacts" / "day3_avatar_tools_capsule_frames"


def _safe_int(value: Any, fallback: int = 0) -> int:
    try:
        if value is None:
            return fallback
        number = float(value)
        if not math.isfinite(number):
            return fallback
        return int(round(number))
    except Exception:
        return fallback


def _dispatch_tutorial_message(page: Any, message: dict[str, Any]) -> None:
    page.evaluate(
        """
        async (message) => {
            const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
            window.__capturedRelayMessages.push(Object.assign({ at: Math.round(performance.now()) }, message || {}));
            window.dispatchEvent(new CustomEvent('neko:tutorial-overlay-relay', { detail: message || {} }));
            window.postMessage({ __nekoTutorialOverlayRelay: true, payload: message || {} }, '*');
            await wait(20);
        }
        """,
        message,
    )


def _prepare_chat_page(page: Any) -> None:
    page.goto("http://neko.test/chat")
    page.evaluate(
        """
        () => {
            window.__NEKO_MULTI_WINDOW__ = true;
            window.__capturedRelayMessages = [];
            window.__capsuleFrameSamples = [];
            window.__stopCapsuleFrameSampler = null;
            window.nekoChatWindow = { ensureExpandedForTutorial: () => true };
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
                begin: () => Promise.resolve({ ok: true }),
                update: () => Promise.resolve({ ok: true }),
                clear: () => Promise.resolve({ ok: true }),
                relayToPet: () => Promise.resolve({ ok: true }),
                relayToChat: () => Promise.resolve({ ok: true }),
            };
            try {
                window.localStorage.setItem('yuiGuidePcOverlayRunId', 'capture-day3-' + Date.now());
            } catch (_) {}
        }
        """
    )
    page.add_style_tag(content="""
        html, body { width: 100% !important; min-height: 1200px !important; overflow: auto !important; }
        #react-chat-window-shell { height: 1040px !important; overflow: visible !important; }
        #react-chat-window-root { min-height: 1040px !important; overflow: visible !important; }
    """)
    page.add_script_tag(path=str(STATIC_DIR / "react" / "neko-chat" / "neko-chat-window.iife.js"))
    _load_scripts(page, ["app/app-react-chat-window.js", "app-interpage.js"])
    page.evaluate(
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
            window.__capsuleDomEvents = [];
            const visible = (node) => {
                if (!node) return false;
                const style = window.getComputedStyle(node);
                const rect = node.getBoundingClientRect();
                return style.display !== 'none'
                    && style.visibility !== 'hidden'
                    && Number(style.opacity || 1) > 0.01
                    && rect.width > 1
                    && rect.height > 1;
            };
            const snapshot = () => {
                const toggle = document.querySelector('.send-button-circle.compact-input-tool-toggle');
                const fan = document.querySelector('.compact-input-tool-fan');
                const avatar = document.querySelector('.compact-input-tool-item-avatar .composer-emoji-btn');
                const tools = Array.from(document.querySelectorAll(
                    '#composer-tool-popover-compact .composer-icon-button[data-avatar-tool-id], #composer-tool-popover .composer-icon-button[data-avatar-tool-id]'
                ));
                return {
                    at: Math.round(performance.now()),
                    toggleOpen: !!(toggle && (toggle.classList.contains('is-open') || toggle.getAttribute('aria-expanded') === 'true')),
                    fanOpen: fan ? fan.getAttribute('data-compact-input-tool-fan-open') : '',
                    fanInteractive: fan ? fan.getAttribute('data-compact-input-tool-fan-interactive') : '',
                    avatarVisible: visible(avatar),
                    avatarExpanded: avatar ? avatar.getAttribute('aria-expanded') : '',
                    toolIds: tools.map((node) => node.getAttribute('data-avatar-tool-id')),
                    visibleToolIds: tools.filter(visible).map((node) => node.getAttribute('data-avatar-tool-id')),
                };
            };
            const patch = () => {
                const toggle = document.querySelector('.send-button-circle.compact-input-tool-toggle');
                const avatar = document.querySelector('.compact-input-tool-item-avatar .composer-emoji-btn');
                if (toggle && !toggle.dataset.capsuleCapturePatched) {
                    toggle.dataset.capsuleCapturePatched = 'true';
                    toggle.addEventListener('click', () => window.__capsuleDomEvents.push({
                        type: 'tool-toggle-dom-click',
                        snapshot: snapshot(),
                    }), true);
                }
                if (avatar && !avatar.dataset.capsuleCapturePatched) {
                    avatar.dataset.capsuleCapturePatched = 'true';
                    avatar.addEventListener('click', () => window.__capsuleDomEvents.push({
                        type: 'avatar-button-dom-click',
                        snapshot: snapshot(),
                    }), true);
                }
            };
            patch();
            const root = document.getElementById('react-chat-window-root');
            if (root) {
                const observer = new MutationObserver(() => {
                    patch();
                    window.__capsuleDomEvents.push({ type: 'chat-dom-mutated', snapshot: snapshot() });
                });
                observer.observe(root, { attributes: true, childList: true, subtree: true });
            }
        }
        """
    )


def _install_frame_sampler(page: Any) -> None:
    page.evaluate(
        """
        () => {
            const rectOf = (node) => {
                if (!node) return null;
                const rect = node.getBoundingClientRect();
                return {
                    x: Math.round(rect.x),
                    y: Math.round(rect.y),
                    width: Math.round(rect.width),
                    height: Math.round(rect.height),
                    left: Math.round(rect.left),
                    top: Math.round(rect.top),
                    right: Math.round(rect.right),
                    bottom: Math.round(rect.bottom),
                };
            };
            const visible = (node) => {
                if (!node) return false;
                const style = window.getComputedStyle(node);
                const rect = node.getBoundingClientRect();
                return style.display !== 'none'
                    && style.visibility !== 'hidden'
                    && Number(style.opacity || 1) > 0.01
                    && rect.width > 1
                    && rect.height > 1;
            };
            const snapshot = (at) => {
                const surface = document.querySelector('.compact-chat-surface-frame[data-compact-chat-state="input"]')
                    || document.querySelector('.compact-chat-surface-frame');
                const fan = document.querySelector('.compact-input-tool-fan');
                const avatar = document.querySelector('.compact-input-tool-item-avatar .composer-emoji-btn');
                const popover = document.getElementById('composer-tool-popover-compact')
                    || document.getElementById('composer-tool-popover');
                const tools = Array.from(document.querySelectorAll(
                    '#composer-tool-popover-compact .composer-icon-button[data-avatar-tool-id], #composer-tool-popover .composer-icon-button[data-avatar-tool-id]'
                ));
                return {
                    at: Math.round(at),
                    surfaceRect: rectOf(surface),
                    fanRect: rectOf(fan),
                    avatarRect: rectOf(avatar),
                    popoverRect: rectOf(popover),
                    fanOpen: fan ? fan.getAttribute('data-compact-input-tool-fan-open') : '',
                    fanInteractive: fan ? fan.getAttribute('data-compact-input-tool-fan-interactive') : '',
                    avatarExpanded: avatar ? avatar.getAttribute('aria-expanded') : '',
                    avatarActive: !!(avatar && avatar.classList.contains('is-active')),
                    popoverExists: !!popover,
                    toolIds: tools.map((node) => node.getAttribute('data-avatar-tool-id')),
                    visibleToolIds: tools.filter(visible).map((node) => node.getAttribute('data-avatar-tool-id')),
                    toolCount: tools.length,
                    visibleToolCount: tools.filter(visible).length,
                    toolRects: tools.map((node) => ({
                        id: node.getAttribute('data-avatar-tool-id'),
                        visible: visible(node),
                        rect: rectOf(node),
                    })),
                };
            };
            let active = true;
            window.__capsuleFrameSamples = [];
            const tick = (at) => {
                if (!active) return;
                window.__capsuleFrameSamples.push(snapshot(at));
                window.requestAnimationFrame(tick);
            };
            window.__stopCapsuleFrameSampler = () => { active = false; };
            window.requestAnimationFrame(tick);
        }
        """
    )


def _measure_crop_rect(page: Any) -> dict[str, int]:
    page.evaluate(
        """
        () => {
            const surface = document.querySelector('.compact-chat-surface-frame[data-compact-chat-state="input"]')
                || document.querySelector('.compact-chat-surface-frame');
            if (surface && typeof surface.scrollIntoView === 'function') {
                surface.scrollIntoView({ block: 'center', inline: 'center' });
            }
        }
        """
    )
    page.wait_for_timeout(80)
    rect = page.evaluate(
        """
        () => {
            const candidates = [
                document.querySelector('.compact-chat-surface-frame[data-compact-chat-state="input"]'),
                document.querySelector('.compact-input-tool-fan'),
                document.getElementById('composer-tool-popover-compact'),
                document.getElementById('composer-tool-popover'),
            ].filter(Boolean);
            const base = document.querySelector('.compact-chat-surface-frame[data-compact-chat-state="input"]')
                || document.querySelector('.compact-chat-surface-frame')
                || document.getElementById('react-chat-window-root');
            if (base && !candidates.includes(base)) candidates.push(base);
            const rects = candidates.map((node) => node.getBoundingClientRect()).filter((rect) => (
                rect && rect.width > 0 && rect.height > 0
            ));
            if (!rects.length) return { x: 0, y: 0, width: window.innerWidth, height: window.innerHeight };
            const left = Math.min(...rects.map((rect) => rect.left));
            const top = Math.min(...rects.map((rect) => rect.top));
            const right = Math.max(...rects.map((rect) => rect.right));
            const bottom = Math.max(...rects.map((rect) => rect.bottom));
            const padX = 54;
            const padTop = 140;
            const padBottom = 34;
            const x = Math.max(0, Math.floor(left - padX));
            const y = Math.max(0, Math.floor(top - padTop));
            const width = Math.min(window.innerWidth - x, Math.ceil((right - left) + padX * 2));
            const height = Math.min(window.innerHeight - y, Math.ceil((bottom - top) + padTop + padBottom));
            return { x, y, width, height };
        }
        """
    )
    return {
        "x": max(0, _safe_int(rect.get("x"))),
        "y": max(0, _safe_int(rect.get("y"))),
        "width": max(1, _safe_int(rect.get("width"), 1)),
        "height": max(1, _safe_int(rect.get("height"), 1)),
    }


def _save_cropped_frames(frames: list[dict[str, Any]], frame_dir: Path, crop_rect: dict[str, int]) -> list[dict[str, Any]]:
    frame_dir.mkdir(parents=True, exist_ok=True)
    saved: list[dict[str, Any]] = []
    crop_box = (
        crop_rect["x"],
        crop_rect["y"],
        crop_rect["x"] + crop_rect["width"],
        crop_rect["y"] + crop_rect["height"],
    )
    for index, frame in enumerate(frames, start=1):
        try:
            raw = base64.b64decode(frame.get("data") or "", validate=True)
        except Exception:
            continue
        if not raw:
            continue
        raw_path = frame_dir / f"frame_{index:04d}_full.png"
        raw_path.write_bytes(raw)
        try:
            with Image.open(raw_path) as image:
                cropped = image.crop(crop_box)
                cropped_path = frame_dir / f"frame_{index:04d}.png"
                cropped.save(cropped_path)
        except Exception:
            raw_path.unlink(missing_ok=True)
            continue
        raw_path.unlink(missing_ok=True)
        saved.append({
            "index": index,
            "file": str(cropped_path),
            "metadata": frame.get("metadata") or {},
        })
    return saved


def _make_contact_sheet(saved_frames: list[dict[str, Any]], output_path: Path, max_frames: int = 30) -> str | None:
    if not saved_frames:
        return None
    chosen = saved_frames
    if len(chosen) > max_frames:
        step = max(1, math.floor(len(chosen) / max_frames))
        chosen = chosen[::step][:max_frames]
    thumbs = []
    for frame in chosen:
        image = Image.open(frame["file"]).convert("RGB")
        image.thumbnail((220, 150))
        thumbs.append((frame["index"], image.copy()))
        image.close()
    cols = min(5, len(thumbs))
    rows = math.ceil(len(thumbs) / cols)
    cell_w = 240
    cell_h = 180
    sheet = Image.new("RGB", (cols * cell_w, rows * cell_h), "white")
    draw = ImageDraw.Draw(sheet)
    for offset, (index, image) in enumerate(thumbs):
        col = offset % cols
        row = offset // cols
        x = col * cell_w + 10
        y = row * cell_h + 24
        draw.text((col * cell_w + 10, row * cell_h + 6), f"frame {index:04d}", fill=(24, 32, 44))
        sheet.paste(image, (x, y))
    sheet.save(output_path)
    return str(output_path)


def capture_capsule_frames(output_dir: Path, duration_ms: int) -> dict[str, Any]:
    sync_playwright = _load_playwright()
    output_dir.mkdir(parents=True, exist_ok=True)
    frame_dir = output_dir / "frames"

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 900, "height": 1200}, device_scale_factor=1)
        page = context.new_page()
        _install_static_routes(page)
        _prepare_chat_page(page)

        # Bring the tool fan into the same prepared state that the target Day 3 line uses.
        now = lambda: int(time.time() * 1000)
        _dispatch_tutorial_message(page, {
            "action": "yui_guide_set_compact_tool_fan_open",
            "open": True,
            "reason": "avatar-floating-guide-prepare-tool-fan",
            "timestamp": now(),
        })
        _dispatch_tutorial_message(page, {
            "action": "yui_guide_set_chat_spotlight",
            "kind": "tool-toggle",
            "timestamp": now(),
        })
        _dispatch_tutorial_message(page, {
            "action": "yui_guide_set_chat_cursor",
            "kind": "avatar-tools",
            "effect": "move",
            "effectDurationMs": 0,
            "targetIndex": 0,
            "timestamp": now(),
        })
        page.wait_for_function(
            """
            () => {
                const fan = document.querySelector('.compact-input-tool-fan');
                const avatar = document.querySelector('.compact-input-tool-item-avatar .composer-emoji-btn');
                if (!fan || !avatar) return false;
                const style = window.getComputedStyle(avatar);
                const rect = avatar.getBoundingClientRect();
                return fan.getAttribute('data-compact-input-tool-fan-open') === 'true'
                    && fan.getAttribute('data-compact-input-tool-fan-interactive') === 'true'
                    && style.display !== 'none'
                    && style.visibility !== 'hidden'
                    && Number(style.opacity || 1) > 0.01
                    && rect.width > 1
                    && rect.height > 1;
            }
            """,
            timeout=3000,
        )

        crop_rect = _measure_crop_rect(page)
        _install_frame_sampler(page)

        session = context.new_cdp_session(page)
        frames: list[dict[str, Any]] = []

        def on_screencast_frame(params: dict[str, Any]) -> None:
            data = params.get("data")
            if isinstance(data, str) and data:
                frames.append({
                    "data": data,
                    "metadata": params.get("metadata") or {},
                })
            session.send("Page.screencastFrameAck", {"sessionId": params["sessionId"]})

        session.on("Page.screencastFrame", on_screencast_frame)
        session.send("Page.startScreencast", {
            "format": "png",
            "quality": 100,
            "everyNthFrame": 1,
        })

        capture_started_at = time.time()
        _dispatch_tutorial_message(page, {
            "action": "yui_guide_set_chat_cursor",
            "kind": "avatar-tools",
            "effect": "click",
            "effectDurationMs": 420,
            "targetIndex": 0,
            "timestamp": now(),
        })
        _dispatch_tutorial_message(page, {
            "action": "yui_guide_click_avatar_tool_button",
            "reason": "avatar-floating-guide-open-avatar-tool-menu",
            "timestamp": now(),
        })
        page.wait_for_timeout(120)
        _dispatch_tutorial_message(page, {
            "action": "yui_guide_set_avatar_tool_menu_open",
            "open": True,
            "reason": "avatar-floating-guide-open-avatar-tool-menu",
            "timestamp": now(),
        })
        page.wait_for_timeout(300)
        page.wait_for_timeout(max(250, duration_ms))
        session.send("Page.stopScreencast")
        page.evaluate("() => { if (window.__stopCapsuleFrameSampler) window.__stopCapsuleFrameSampler(); }")

        samples = page.evaluate("() => window.__capsuleFrameSamples || []")
        relay_messages = page.evaluate("() => window.__capturedRelayMessages || []")
        dom_events = page.evaluate("() => window.__capsuleDomEvents || []")
        final_state = page.evaluate(
            """
            () => {
                const tools = Array.from(document.querySelectorAll(
                    '#composer-tool-popover-compact .composer-icon-button[data-avatar-tool-id], #composer-tool-popover .composer-icon-button[data-avatar-tool-id]'
                ));
                const visible = (node) => {
                    const style = window.getComputedStyle(node);
                    const rect = node.getBoundingClientRect();
                    return style.display !== 'none'
                        && style.visibility !== 'hidden'
                        && Number(style.opacity || 1) > 0.01
                        && rect.width > 1
                        && rect.height > 1;
                };
                return {
                    toolIds: tools.map((node) => node.getAttribute('data-avatar-tool-id')),
                    visibleToolIds: tools.filter(visible).map((node) => node.getAttribute('data-avatar-tool-id')),
                    toolCount: tools.length,
                    visibleToolCount: tools.filter(visible).length,
                    html: (document.getElementById('react-chat-window-root') || document.body).innerHTML.slice(0, 3000),
                };
            }
            """
        )
        browser.close()

    saved_frames = _save_cropped_frames(frames, frame_dir, crop_rect)
    max_tool_count = max([_safe_int(sample.get("toolCount")) for sample in samples] or [0])
    max_visible_tool_count = max([_safe_int(sample.get("visibleToolCount")) for sample in samples] or [0])
    first_three_tool_sample = next((sample for sample in samples if _safe_int(sample.get("visibleToolCount")) >= 3), None)
    visible_tool_ids = []
    for sample in samples:
        ids = sample.get("visibleToolIds") or []
        if len(ids) >= len(visible_tool_ids):
            visible_tool_ids = ids

    contact_sheet = _make_contact_sheet(saved_frames, output_dir / "contact_sheet.png")
    dom_event_counts: dict[str, int] = {}
    for event in dom_events:
        event_type = event.get("type") or "unknown"
        dom_event_counts[event_type] = dom_event_counts.get(event_type, 0) + 1
    fan_interactive_changes = [
        {
            "type": event.get("type"),
            "at": ((event.get("snapshot") or {}).get("at")),
            "fanOpen": ((event.get("snapshot") or {}).get("fanOpen")),
            "fanInteractive": ((event.get("snapshot") or {}).get("fanInteractive")),
            "avatarVisible": ((event.get("snapshot") or {}).get("avatarVisible")),
            "visibleToolIds": ((event.get("snapshot") or {}).get("visibleToolIds")),
        }
        for event in dom_events
        if event.get("type") in {"tool-toggle-dom-click", "avatar-button-dom-click", "chat-dom-mutated"}
    ]
    summary = {
        "ok": max_visible_tool_count >= 3,
        "targetLine": DAY3_AVATAR_PROPS_TEXT,
        "outputDir": str(output_dir),
        "frameDir": str(frame_dir),
        "contactSheet": contact_sheet,
        "durationMs": duration_ms,
        "captureElapsedMs": round((time.time() - capture_started_at) * 1000),
        "screencastFrameCount": len(saved_frames),
        "samplerFrameCount": len(samples),
        "cropRect": crop_rect,
        "maxToolCount": max_tool_count,
        "maxVisibleToolCount": max_visible_tool_count,
        "visibleToolIds": visible_tool_ids,
        "firstThreeVisibleToolSample": first_three_tool_sample,
        "finalState": final_state,
        "relayMessages": relay_messages,
        "domEventCounts": dom_event_counts,
        "avatarButtonDomClickCount": dom_event_counts.get("avatar-button-dom-click", 0),
        "fanInteractiveChanges": fan_interactive_changes,
        "domEvents": dom_events,
        "frames": saved_frames,
        "samples": samples,
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Capture every screencast frame of the Day 3 Avatar props line capsule input area."
    )
    parser.add_argument("--output-dir", type=Path, default=None, help="Directory for PNG frames and summary.json.")
    parser.add_argument("--duration-ms", type=int, default=2600, help="Capture duration after the Avatar click opens tools.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON summary.")
    args = parser.parse_args(argv)

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = args.output_dir or (DEFAULT_OUTPUT_ROOT / timestamp)
    summary = capture_capsule_frames(output_dir=output_dir, duration_ms=max(args.duration_ms, 250))
    if args.json:
        printable = {key: value for key, value in summary.items() if key not in {"frames", "samples"}}
        print(json.dumps(printable, ensure_ascii=False, indent=2))
    else:
        print("Day3 Avatar props capsule frame capture")
        print("=" * 48)
        print(f"Output: {summary['outputDir']}")
        print(f"Frames: {summary['screencastFrameCount']} PNGs")
        print(f"Contact sheet: {summary['contactSheet']}")
        print(f"Max visible tools: {summary['maxVisibleToolCount']} {summary['visibleToolIds']}")
        print("Result:", "SHOWED 3 TOOLS" if summary["ok"] else "DID NOT SHOW 3 TOOLS")
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
