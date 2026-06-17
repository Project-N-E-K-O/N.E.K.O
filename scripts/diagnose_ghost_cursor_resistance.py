from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = PROJECT_ROOT / "static"
SCRIPT_NAMES = (
    "tutorial/core/interaction-takeover.js",
    "tutorial/visual/highlight-controller.js",
    "tutorial-interrupt-controller.js",
    "tutorial/yui-guide/overlay.js",
    "tutorial/yui-guide/director.js",
)


def _load_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover - CLI diagnostic path
        raise RuntimeError(
            "Playwright is required. Run this with the repo venv: "
            r".\.venv\Scripts\python.exe scripts\diagnose_ghost_cursor_resistance.py"
        ) from exc
    return sync_playwright


def _bootstrap_page(page: Any, *, pc_overlay: bool) -> None:
    page.route(
        "**/ghost-cursor-diagnostic",
        lambda route: route.fulfill(
            status=200,
            content_type="text/html",
            body=(
                "<!doctype html><html><head><meta charset='utf-8'></head>"
                "<body><main id='fixture'></main></body></html>"
            ),
        ),
    )
    page.goto("http://neko.test/ghost-cursor-diagnostic")
    page.evaluate(
        """
        (pcOverlay) => {
            window.safeT = (key, fallback) => typeof fallback === 'string' ? fallback : key;
            window.showStatusToast = () => {};
            window.universalTutorialManager = {
                currentPage: 'home',
                isTutorialRunning: true,
                logPromptFlow: () => {},
            };
            window.matchMedia = window.matchMedia || (() => ({
                matches: false,
                addEventListener: () => {},
                removeEventListener: () => {},
            }));
            if (pcOverlay) {
                window.__pcOverlayPayloads = [];
                window.nekoTutorialOverlay = {
                    begin: (payload) => {
                        window.__pcOverlayPayloads.push({ type: 'begin', payload });
                        return Promise.resolve({ ok: true });
                    },
                    update: (payload) => {
                        window.__pcOverlayPayloads.push({ type: 'update', payload });
                        return Promise.resolve({ ok: true });
                    },
                    clear: (payload) => {
                        window.__pcOverlayPayloads.push({ type: 'clear', payload });
                        return Promise.resolve({ ok: true });
                    },
                    getWindowMetricsSync: () => ({
                        bounds: { x: 0, y: 0, width: 1280, height: 720 },
                        contentBounds: { x: 0, y: 0, width: 1280, height: 720 },
                        zoomFactor: 1,
                    }),
                };
                window.localStorage.setItem('yuiGuidePcOverlayRunId', 'diagnostic-run');
            }
        }
        """,
        pc_overlay,
    )
    for script_name in SCRIPT_NAMES:
        page.add_script_tag(path=str(STATIC_DIR / script_name))


def _run_dom_diagnostic(page: Any, args: argparse.Namespace) -> dict[str, Any]:
    return page.evaluate(
        """
        async ({ originX, originY, pointerX, pointerY, movementX, movementY, sampleMs, minDisplacement }) => {
            const sleepFrame = () => new Promise((resolve) => requestAnimationFrame(resolve));
            const sample = (label, startedAt) => {
                const point = window.__director.overlay.getCursorPosition();
                return {
                    label,
                    t: Math.round(performance.now() - startedAt),
                    x: point ? Number(point.x.toFixed(3)) : null,
                    y: point ? Number(point.y.toFixed(3)) : null,
                };
            };
            window.__director = window.createYuiGuideDirector({ page: 'home' });
            window.__director.platformCapabilities = { windowBoundsSource: 'electron-window-bounds' };
            window.__director.currentSceneId = 'diagnostic_scene';
            window.__director.currentStep = {
                interrupts: { threshold: 3, throttleMs: 0 },
                performance: {},
            };
            window.__director.setTutorialTakingOver(true);
            window.__director.cursor.showAt(originX, originY);
            window.__director.interruptsEnabled = true;
            await sleepFrame();
            await sleepFrame();

            const startedAt = performance.now();
            const origin = window.__director.overlay.getCursorPosition();
            const samples = [sample('before', startedAt)];
            if (!origin || !Number.isFinite(origin.x) || !Number.isFinite(origin.y)) {
                return {
                    mode: 'dom',
                    passed: false,
                    checks: {
                        cursorOriginAvailable: false,
                        movedAgainstX: false,
                        movedAgainstY: false,
                        returned: false,
                        xDisplacementVisible: false,
                        yDisplacementVisible: false,
                    },
                    reason: 'cursor-origin-unavailable',
                    samples,
                };
            }
            window.__director.handleInterrupt({
                isTrusted: true,
                type: 'mousemove',
                clientX: pointerX,
                clientY: pointerY,
                movementX,
                movementY,
            });
            while (performance.now() - startedAt < sampleMs) {
                await sleepFrame();
                samples.push(sample('frame', startedAt));
            }
            const xs = samples.map((entry) => entry.x).filter((value) => Number.isFinite(value));
            const ys = samples.map((entry) => entry.y).filter((value) => Number.isFinite(value));
            if (!xs.length || !ys.length) {
                return {
                    mode: 'dom',
                    origin,
                    passed: false,
                    checks: {
                        cursorSamplesAvailable: false,
                        movedAgainstX: false,
                        movedAgainstY: false,
                        returned: false,
                        xDisplacementVisible: false,
                        yDisplacementVisible: false,
                    },
                    reason: 'cursor-samples-unavailable',
                    samples,
                };
            }
            const minX = Math.min(...xs);
            const maxX = Math.max(...xs);
            const minY = Math.min(...ys);
            const maxY = Math.max(...ys);
            const finalPoint = samples[samples.length - 1];
            const expectedXSign = movementX > 0 ? -1 : movementX < 0 ? 1 : 0;
            const expectedYSign = movementY > 0 ? -1 : movementY < 0 ? 1 : 0;
            const xDisplacement = Math.max(Math.abs(minX - origin.x), Math.abs(maxX - origin.x));
            const yDisplacement = Math.max(Math.abs(minY - origin.y), Math.abs(maxY - origin.y));
            const movedAgainstX = expectedXSign === 0
                || (expectedXSign < 0 ? minX <= origin.x - minDisplacement : maxX >= origin.x + minDisplacement);
            const movedAgainstY = expectedYSign === 0
                || (expectedYSign < 0 ? minY <= origin.y - minDisplacement : maxY >= origin.y + minDisplacement);
            const returned = Math.hypot(finalPoint.x - origin.x, finalPoint.y - origin.y) <= 2.5;
            const xDisplacementVisible = expectedXSign === 0 || xDisplacement >= minDisplacement;
            const yDisplacementVisible = expectedYSign === 0 || yDisplacement >= minDisplacement;
            return {
                mode: 'dom',
                origin,
                movement: { x: movementX, y: movementY },
                extremes: { minX, maxX, minY, maxY },
                finalPoint,
                passed: movedAgainstX && movedAgainstY && returned && xDisplacementVisible && yDisplacementVisible,
                checks: { movedAgainstX, movedAgainstY, returned, xDisplacementVisible, yDisplacementVisible },
                displacement: { x: xDisplacement, y: yDisplacement, minRequired: minDisplacement },
                samples,
            };
        }
        """,
        {
            "originX": args.origin_x,
            "originY": args.origin_y,
            "pointerX": args.pointer_x,
            "pointerY": args.pointer_y,
            "movementX": args.movement_x,
            "movementY": args.movement_y,
            "sampleMs": args.sample_ms,
            "minDisplacement": args.min_displacement,
        },
    )


def _run_pc_payload_diagnostic(page: Any, args: argparse.Namespace) -> dict[str, Any]:
    return page.evaluate(
        """
        async ({ originX, originY, pointerX, pointerY, movementX, movementY, sampleMs, minDisplacement, minOutDurationMs }) => {
            const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
            window.__director = window.createYuiGuideDirector({ page: 'home' });
            window.__director.platformCapabilities = { windowBoundsSource: 'electron-window-bounds' };
            window.__director.currentSceneId = 'diagnostic_scene';
            window.__director.currentStep = {
                interrupts: { threshold: 3, throttleMs: 0 },
                performance: {},
            };
            window.__director.setTutorialTakingOver(true);
            window.__director.cursor.showAt(originX, originY);
            await sleep(0);
            await sleep(0);
            window.__director.interruptsEnabled = true;
            window.__director.handleInterrupt({
                isTrusted: true,
                type: 'mousemove',
                clientX: pointerX,
                clientY: pointerY,
                movementX,
                movementY,
            });
            await sleep(sampleMs);
            const payloads = (window.__pcOverlayPayloads || []).slice();
            const cursorUpdates = payloads
                .filter((entry) => entry.type === 'update')
                .map((entry) => entry.payload && entry.payload.payload && entry.payload.payload.cursor)
                .filter(Boolean);
            const visibleMoves = cursorUpdates.filter((cursor) => cursor.visible);
            const originMove = visibleMoves.find((cursor) => cursor.durationMs === 0);
            const resistanceMoves = visibleMoves.filter((cursor) => cursor.durationMs > 0);
            const hasWobble = visibleMoves.some((cursor) => cursor.effect === 'wobble');
            const outward = resistanceMoves[0] || null;
            const back = resistanceMoves[1] || null;
            const expectedXSign = movementX > 0 ? -1 : movementX < 0 ? 1 : 0;
            const expectedYSign = movementY > 0 ? -1 : movementY < 0 ? 1 : 0;
            const movedAgainstX = !outward || expectedXSign === 0
                || (expectedXSign < 0 ? outward.x <= originX - minDisplacement : outward.x >= originX + minDisplacement);
            const movedAgainstY = !outward || expectedYSign === 0
                || (expectedYSign < 0 ? outward.y <= originY - minDisplacement : outward.y >= originY + minDisplacement);
            const returned = !!back && Math.hypot(back.x - originX, back.y - originY) <= 2.5;
            const outDurationVisible = !!outward && outward.durationMs >= minOutDurationMs;
            return {
                mode: 'pc-payload',
                cursorUpdates,
                visibleMoves,
                passed: !!originMove
                    && resistanceMoves.length >= 2
                    && movedAgainstX
                    && movedAgainstY
                    && returned
                    && outDurationVisible
                    && !hasWobble,
                checks: {
                    hasInitialVisibleCursor: !!originMove,
                    hasTwoAnimatedMoves: resistanceMoves.length >= 2,
                    movedAgainstX,
                    movedAgainstY,
                    returned,
                    outDurationVisible,
                    noWobbleEffect: !hasWobble,
                },
                required: { minDisplacement, minOutDurationMs },
                resistanceMoves,
            };
        }
        """,
        {
            "originX": args.origin_x,
            "originY": args.origin_y,
            "pointerX": args.pointer_x,
            "pointerY": args.pointer_y,
            "movementX": args.movement_x,
            "movementY": args.movement_y,
            "sampleMs": args.sample_ms,
            "minDisplacement": args.min_displacement,
            "minOutDurationMs": args.min_out_duration_ms,
        },
    )


def _run_interrupt_count_diagnostic(page: Any) -> dict[str, Any]:
    return page.evaluate(
        """
        () => {
            const originalNow = Date.now;
            let now = 1000;
            Date.now = () => now;
            try {
                const director = window.createYuiGuideDirector({ page: 'home' });
                const events = [];
                director.platformCapabilities = { windowBoundsSource: 'electron-window-bounds' };
                director.currentSceneId = 'diagnostic_scene';
                director.currentStep = {
                    interrupts: { threshold: 3, throttleMs: 0 },
                    performance: {},
                };
                director.cursor.hasPosition = () => true;
                director.cursor.reactToUserMotion = () => {};
                director.playLightResistance = (x, y, options) => {
                    events.push({ type: 'light', x, y, options, interruptCount: director.interruptCount });
                };
                director.abortAsAngryExit = (source) => {
                    events.push({ type: 'angry-exit', source, interruptCount: director.interruptCount });
                };
                director.interruptsEnabled = true;

                let x = 100;
                const qualifyingGroup = () => {
                    director.lastPointerPoint = { x, y: 100, t: now, speed: 0 };
                    for (let index = 0; index < 3; index += 1) {
                        x += 40;
                        now += 1000;
                        director.handleInterrupt({
                            isTrusted: true,
                            type: 'mousemove',
                            clientX: x,
                            clientY: 100,
                            movementX: 40,
                            movementY: 0,
                        });
                    }
                };
                qualifyingGroup();
                qualifyingGroup();
                qualifyingGroup();
                return {
                    mode: 'interrupt-count',
                    passed: events.length === 3
                        && events[0].type === 'light'
                        && events[1].type === 'light'
                        && events[2].type === 'angry-exit',
                    events,
                };
            } finally {
                Date.now = originalNow;
            }
        }
        """,
    )


def _print_result(result: dict[str, Any], *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    status = "PASS" if result.get("passed") else "FAIL"
    print(f"[{status}] {result['mode']}")
    checks = result.get("checks")
    if checks:
        for name, value in checks.items():
            print(f"  - {name}: {'ok' if value else 'bad'}")
    if result["mode"] == "dom":
        print(f"  origin: {result['origin']}")
        print(f"  extremes: {result['extremes']}")
        print(f"  displacement: {result['displacement']}")
        print(f"  final: {result['finalPoint']}")
        print("  samples:")
        for entry in result["samples"][0:24]:
            print(f"    {entry['t']:>4}ms  x={entry['x']}  y={entry['y']}")
    elif result["mode"] == "pc-payload":
        print("  cursor updates:")
        for cursor in result["cursorUpdates"]:
            print(f"    {cursor}")
    elif result["mode"] == "interrupt-count":
        print("  events:")
        for event in result["events"]:
            print(f"    {event}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Diagnose avatar floating guide ghost cursor resistance movement.",
    )
    parser.add_argument("--mode", choices=("all", "dom", "pc-payload", "interrupt-count"), default="all")
    parser.add_argument("--origin-x", type=float, default=300)
    parser.add_argument("--origin-y", type=float, default=240)
    parser.add_argument("--pointer-x", type=float, default=380)
    parser.add_argument("--pointer-y", type=float, default=240)
    parser.add_argument("--movement-x", type=float, default=16)
    parser.add_argument("--movement-y", type=float, default=0)
    parser.add_argument("--sample-ms", type=int, default=420)
    parser.add_argument("--min-displacement", type=float, default=18)
    parser.add_argument("--min-out-duration-ms", type=int, default=140)
    parser.add_argument("--headed", action="store_true", help="Show the browser window while running.")
    parser.add_argument("--json", action="store_true", help="Print full JSON diagnostics.")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    sync_playwright = _load_playwright()
    results: list[dict[str, Any]] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not args.headed)
        try:
            if args.mode in ("all", "dom", "interrupt-count"):
                page = browser.new_page(viewport={"width": 1280, "height": 720})
                _bootstrap_page(page, pc_overlay=False)
                if args.mode in ("all", "dom"):
                    results.append(_run_dom_diagnostic(page, args))
                if args.mode in ("all", "interrupt-count"):
                    results.append(_run_interrupt_count_diagnostic(page))
                page.close()
            if args.mode in ("all", "pc-payload"):
                page = browser.new_page(viewport={"width": 1280, "height": 720})
                _bootstrap_page(page, pc_overlay=True)
                results.append(_run_pc_payload_diagnostic(page, args))
                page.close()
        finally:
            browser.close()

    if args.json:
        print(json.dumps(
            {
                "ok": all(result.get("passed") for result in results),
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        ))
    else:
        for index, result in enumerate(results):
            if index:
                print()
            _print_result(result, json_output=False)
    return 0 if all(result.get("passed") for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
