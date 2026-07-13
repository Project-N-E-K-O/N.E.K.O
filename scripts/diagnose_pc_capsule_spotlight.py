from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


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
            "Playwright is required. Run with the repo environment, for example: "
            "uv run python scripts/diagnose_pc_capsule_spotlight.py"
        ) from exc
    return sync_playwright


def _check(condition: bool, name: str, ok_detail: str, fail_detail: str) -> Check:
    return Check(name=name, status="PASS" if condition else "FAIL", detail=ok_detail if condition else fail_detail)


def _read(path: Path) -> str:
    if path.is_dir():
        return "\n".join(_read(part_path) for part_path in sorted(path.glob("*.js")))
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8-sig")


def _extract_function(source: str, signature: str, next_signature: str | None = None) -> str:
    if signature not in source:
        return ""
    tail = source.split(signature, 1)[1]
    if next_signature and next_signature in tail:
        return tail.split(next_signature, 1)[0]
    return tail


def run_neko_static_checks() -> list[Check]:
    source_path = STATIC_DIR / "app/app-interpage"
    source = _read(source_path)
    render_block = _extract_function(source, "function renderYuiGuideChatSpotlight(spotlight, kind, rect)", "function updateYuiGuideChatSpotlight(kind)")
    suppress_block = _extract_function(source, "function shouldSuppressYuiGuideChatLocalFx(kind)", "function getYuiGuideChatCircleSpotlightPadding")
    update_block = _extract_function(source, "function updateYuiGuideChatSpotlight(kind)", "function applyYuiGuideChatSpotlight(kind)")
    radius_formula = "Math.min(34, Math.max(18, Math.round((rect.height + padding * 2) / 2)))"

    checks = [
        _check(
            radius_formula in render_block,
            "N.E.K.O local chat spotlight uses rounded-rect radius",
            "renderYuiGuideChatSpotlight uses the height-clamped 18..34 radius formula.",
            "renderYuiGuideChatSpotlight still does not use the rounded-rect radius formula.",
        ),
        _check(
            "if (kind === 'input')" in suppress_block and "return false;" in suppress_block,
            "N.E.K.O keeps local input spotlight visible while PC overlay is active",
            "input spotlight is not suppressed by the PC overlay bridge.",
            "input spotlight can still be hidden when the PC overlay bridge is active.",
        ),
        _check(
            "if (kind !== 'input' && isYuiGuidePcOverlayAvailable()) {" in update_block,
            "N.E.K.O routes chat input spotlight to local generic highlight",
            "input spotlight skips the PC overlay path and uses the local rounded-rect highlight.",
            "input spotlight still enters the PC overlay path.",
        ),
        _check(
            "is-plain-capsule" not in update_block,
            "N.E.K.O does not add a custom plain-capsule class",
            "input spotlight uses the generic rounded-rect classes/chrome.",
            "input spotlight still adds a custom is-plain-capsule class.",
        ),
    ]
    return checks


def run_browser_probe() -> tuple[list[Check], dict[str, Any]]:
    sync_playwright = _load_playwright()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 720})
        page.route(
            "**/pc-capsule-spotlight-diagnostic",
            lambda route: route.fulfill(
                status=200,
                content_type="text/html",
                body="<!doctype html><html><head><meta charset='utf-8'></head><body></body></html>",
            ),
        )
        page.goto("http://neko.test/pc-capsule-spotlight-diagnostic")
        page.evaluate(
            """
            () => {
                window.history.pushState({}, '', '/chat');
                window.__pcOverlayBegins = [];
                window.__pcOverlayUpdates = [];
                window.nekoTutorialOverlay = {
                    getWindowMetricsSync: () => ({
                        bounds: { x: 100, y: 50, width: 1200, height: 800 },
                        contentBounds: { x: 100, y: 50, width: 1200, height: 800 },
                        zoomFactor: 1,
                    }),
                    begin: (payload) => {
                        window.__pcOverlayBegins.push(payload);
                        return Promise.resolve({ ok: true });
                    },
                    update: (payload) => {
                        window.__pcOverlayUpdates.push(payload);
                        return Promise.resolve({ ok: true });
                    },
                    clear: () => Promise.resolve({ ok: true }),
                };
                document.body.innerHTML = `
                    <div id="react-chat-window-shell" style="position:fixed; left:560px; top:360px; width:480px; height:90px;">
                        <div id="react-chat-window-root">
                            <div
                                class="compact-chat-surface-frame"
                                data-compact-geometry-owner="surface"
                                data-compact-geometry-item="capsule"
                                data-compact-drag-surface="true"
                                style="position:fixed; left:600px; top:400px; width:430px; height:54px; border-radius:999px;"
                            ></div>
                        </div>
                    </div>
                    <div id="yui-guide-chat-spotlight" hidden>
                        <div class="yui-guide-chat-spotlight-chrome"></div>
                        <span class="yui-guide-chat-spotlight-sweep"></span>
                        <div class="yui-guide-chat-spotlight-ear-left"></div>
                        <div class="yui-guide-chat-spotlight-ear-right"></div>
                        <div class="yui-guide-chat-spotlight-paw"></div>
                    </div>
                `;
            }
            """
        )
        for part_path in sorted((STATIC_DIR / "app/app-interpage").glob("*.js")):
            page.add_script_tag(path=str(part_path))
        result = page.evaluate(
            """
            async () => {
                window.postMessage({
                    __nekoTutorialOverlayRelay: true,
                    payload: {
                        action: 'yui_guide_set_chat_spotlight',
                        kind: 'input',
                        timestamp: Date.now(),
                        tutorialRunId: 'diagnostic-run',
                    },
                }, '*');
                await new Promise((resolve) => setTimeout(resolve, 220));
                const spotlight = document.getElementById('yui-guide-chat-spotlight');
                const updates = window.__pcOverlayUpdates || [];
                return {
                    begins: window.__pcOverlayBegins,
                    updates,
                    local: spotlight ? {
                        hidden: spotlight.hidden,
                        visible: spotlight.classList.contains('is-visible'),
                        input: spotlight.classList.contains('is-input'),
                        plainCapsule: spotlight.classList.contains('is-plain-capsule'),
                        radius: spotlight.style.borderRadius,
                        border: spotlight.style.border,
                        boxShadow: spotlight.style.boxShadow,
                        chromeDisplay: getComputedStyle(document.querySelector('.yui-guide-chat-spotlight-chrome')).display,
                        sweepDisplay: getComputedStyle(document.querySelector('.yui-guide-chat-spotlight-sweep')).display,
                    } : null,
                };
            }
            """
        )
        browser.close()

    local = result.get("local") or {}
    checks = [
        _check(
            not result.get("begins") and not result.get("updates"),
            "Browser probe keeps input out of the PC overlay path",
            "input spotlight did not call window.nekoTutorialOverlay.begin/update.",
            "input spotlight still called window.nekoTutorialOverlay.begin/update.",
        ),
        _check(
            local.get("hidden") is False and local.get("visible") is True and local.get("input") is True and local.get("radius") == "34px",
            "Browser probe renders local generic rounded-rect spotlight",
            f"local spotlight = {json.dumps(local, ensure_ascii=False, sort_keys=True)}",
            f"local spotlight is unexpected: {json.dumps(local, ensure_ascii=False, sort_keys=True)}",
        ),
        _check(
            local.get("plainCapsule") is False and local.get("chromeDisplay") != "none" and local.get("sweepDisplay") != "none",
            "Browser probe keeps generic chrome and sweep visible",
            f"local spotlight chrome = {json.dumps(local, ensure_ascii=False, sort_keys=True)}",
            f"local spotlight chrome is unexpected: {json.dumps(local, ensure_ascii=False, sort_keys=True)}",
        ),
    ]
    return checks, result


def run_pc_static_checks(pc_repo: Path) -> list[Check]:
    pc_repo = pc_repo.expanduser()
    preload_path = pc_repo / "src" / "preload-common.js"
    preload_source = _read(preload_path) if preload_path.exists() else ""

    checks = [
        _check(
            pc_repo.exists(),
            "N.E.K.O.-PC repository exists for static checks",
            f"PC repository found at {pc_repo}.",
            f"PC repository was not found at {pc_repo}.",
        ),
        _check(
            preload_path.exists(),
            "N.E.K.O.-PC preload bridge file exists",
            f"preload-common.js found at {preload_path}.",
            f"preload-common.js was not found at {preload_path}.",
        ),
    ]
    if not pc_repo.exists():
        return checks
    checks.append(
        _check(
            "setupTutorialOverlayBridge" in preload_source,
            "N.E.K.O.-PC preload exposes the tutorial overlay bridge",
            "setupTutorialOverlayBridge is present in src/preload-common.js.",
            "setupTutorialOverlayBridge was not found in src/preload-common.js.",
        )
    )
    return checks


def print_report(checks: list[Check], *, raw_browser_result: dict[str, Any] | None = None) -> int:
    failures = [check for check in checks if check.status == "FAIL"]
    print("PC capsule spotlight diagnostic")
    print("=" * 36)
    for check in checks:
        print(f"[{check.status}] {check.name}")
        print(f"       {check.detail}")
    if raw_browser_result is not None:
        print("\nBrowser probe raw result")
        print(json.dumps(raw_browser_result, ensure_ascii=False, indent=2, sort_keys=True))
    print("\nSummary")
    print(f"PASS={sum(1 for check in checks if check.status == 'PASS')} FAIL={len(failures)}")
    return 1 if failures else 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Diagnose PC tutorial capsule chat spotlight contract.")
    parser.add_argument("--pc-repo", type=Path, default=DEFAULT_PC_REPO, help="Path to the N.E.K.O.-PC repo.")
    parser.add_argument("--skip-browser", action="store_true", help="Only run static contract checks.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args(argv)

    checks = []
    browser_result: dict[str, Any] | None = None
    checks.extend(run_neko_static_checks())
    if not args.skip_browser:
        browser_checks, browser_result = run_browser_probe()
        checks.extend(browser_checks)
    checks.extend(run_pc_static_checks(args.pc_repo))

    if args.json:
        print(json.dumps(
            {
                "ok": all(check.status == "PASS" for check in checks),
                "checks": [check.__dict__ for check in checks],
                "browserResult": browser_result,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ))
        return 1 if any(check.status == "FAIL" for check in checks) else 0

    return print_report(checks, raw_browser_result=browser_result)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
