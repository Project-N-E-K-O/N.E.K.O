from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest


pytestmark = pytest.mark.unit

PLUGIN_DIR = Path(__file__).resolve().parents[3] / "plugins" / "study_companion"
STATIC_DIR = PLUGIN_DIR / "static"
LOCALES = ["zh-CN", "zh-TW", "en", "es", "ja", "ko", "pt", "ru"]
COMMUNICATION_KEYS = {
    "ui.settings.communication.title",
    "ui.settings.communication.enabled.label",
    "ui.settings.communication.enabled.help",
    "ui.settings.solution_narration_enabled.label",
    "ui.settings.solution_narration_enabled.help",
    "ui.settings.communication.requires_enabled",
    "ui.settings.communication.runtime_ready",
    "ui.settings.communication.runtime_unavailable",
    "ui.settings.communication.commands_unavailable",
}


def test_advanced_settings_exposes_communication_parent_and_runtime_status() -> None:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    assert 'id="settingsCommunicationEnabled"' in html
    assert 'id="settingsSolutionNarrationEnabled"' in html
    assert 'id="settingsGeneralNarrationEnabled"' in html
    assert 'id="settingsCommunicationRuntime"' in html
    assert (
        'data-i18n="ui.settings.communication.enabled.label">'
        "Enable Study Companion communication with N.E.K.O"
        in html
    )
    assert (
        'data-i18n="ui.settings.communication.enabled.help">'
        "Controls proactive Study Companion messages and N.E.K.O command communication."
        in html
    )
    assert (
        'data-i18n="ui.settings.solution_narration_enabled.help">'
        "Narrates only the problem analysis, answer, and transfer practice."
        in html
    )


def test_advanced_settings_exposes_independent_general_narration_control() -> None:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    source = (STATIC_DIR / "main.js").read_text(encoding="utf-8")

    checkbox = re.search(
        r'<input(?=[^>]*\bid="settingsGeneralNarrationEnabled")'
        r'(?=[^>]*\btype="checkbox")[^>]*>',
        html,
    )

    assert checkbox is not None
    assert "const settingsGeneralNarrationEnabled = $id('settingsGeneralNarrationEnabled');" in source
    assert re.search(
        r"settingsGeneralNarrationEnabled\.checked\s*=\s*"
        r"communication\.general_narration_enabled\s*!==\s*false",
        source,
    )
    assert re.search(
        r"communication\.general_narration_enabled\s*=\s*"
        r"settingsGeneralNarrationEnabled\s*\?\s*"
        r"settingsGeneralNarrationEnabled\.checked\s*:\s*true",
        source,
    )
    assert "settingsGeneralNarrationEnabled.disabled = saving || !enabled;" in source


def test_communication_parent_control_uses_formal_settings_response_contract() -> None:
    source = (STATIC_DIR / "main.js").read_text(encoding="utf-8")

    assert "const settingsCommunicationEnabled = $id('settingsCommunicationEnabled');" in source
    assert "const settingsCommunicationRuntime = $id('settingsCommunicationRuntime');" in source
    assert "communication.enabled = settingsCommunicationEnabled" in source
    assert "payload.communication_status || {}" in source
    assert "settingsSolutionNarrationEnabled.disabled =" in source
    assert "settingsGeneralNarrationEnabled.disabled =" in source
    assert "settingsCommunicationEnabled.addEventListener('change'" in source


def test_communication_copy_is_complete_for_all_locales_and_never_mentions_yui() -> None:
    bundles = {
        locale: json.loads((PLUGIN_DIR / "i18n" / f"{locale}.json").read_text(encoding="utf-8"))
        for locale in LOCALES
    }

    assert {path.stem for path in (PLUGIN_DIR / "i18n").glob("*.json")} == set(LOCALES)
    baseline_keys = set(bundles["en"])
    for locale, bundle in bundles.items():
        assert set(bundle) == baseline_keys, locale
        assert COMMUNICATION_KEYS <= set(bundle), locale
        for key in COMMUNICATION_KEYS:
            copy = bundle[key]
            assert copy.strip(), f"{locale}: {key}"
            assert "yui" not in copy.lower(), f"{locale}: {key}"
        assert "N.E.K.O" in bundle["ui.settings.communication.enabled.label"], locale
        assert "N.E.K.O" in bundle["ui.settings.communication.enabled.help"], locale


def test_parent_switch_disables_child_and_failed_save_restores_server_config() -> None:
    if shutil.which("node") is None:
        pytest.skip("node is not installed")

    frontend_dir = Path(__file__).resolve().parents[4] / "frontend" / "plugin-manager"
    if not (frontend_dir / "node_modules" / "happy-dom").is_dir():
        pytest.skip("frontend/plugin-manager node_modules with happy-dom is not installed")

    script = r"""
import { Window } from 'happy-dom';
import fs from 'node:fs';
import path from 'node:path';

const staticDir = process.env.STUDY_COMPANION_STATIC_DIR;
const i18nDir = process.env.STUDY_COMPANION_I18N_DIR;
const html = fs.readFileSync(path.join(staticDir, 'index.html'), 'utf8');
const mainJs = fs.readFileSync(path.join(staticDir, 'main.js'), 'utf8');
const documentControllerJs = fs.readFileSync(path.join(staticDir, 'document-controller.js'), 'utf8');
const outcomeFormattersJs = fs.readFileSync(path.join(staticDir, 'outcome-formatters.js'), 'utf8');
const surfacePanelsJs = fs.readFileSync(path.join(staticDir, 'surface-panels.js'), 'utf8');
const knowledgeMapJs = fs.readFileSync(path.join(staticDir, 'knowledge-map.js'), 'utf8');
const i18nJs = fs.readFileSync(path.join(staticDir, 'i18n.js'), 'utf8');
const enBundle = JSON.parse(fs.readFileSync(path.join(i18nDir, 'en.json'), 'utf8'));

const window = new Window({ url: 'http://testserver/plugin/study_companion/ui/?locale=en' });
const { document } = window;
Object.defineProperty(window, 'parent', { value: { postMessage: () => {} } });
document.write(html);
document.close();

let failUpdate = true;
let runtimeAvailable = false;
const runs = new Map();
let runNumber = 0;
const confirmed = {
  config: {
    study: { default_mode: 'companion' },
    ocr_reader: { enabled: true, languages: 'eng' },
    llm: { llm_call_timeout_seconds: 30, llm_vision_enabled: false },
    communication: { enabled: false, solution_narration_enabled: true, general_narration_enabled: true },
  },
  communication_status: {
    configured_enabled: false,
    solution_narration_enabled: true,
    available: false,
    command_subscription_active: false,
    command_worker_active: false,
    events_emitted: 0,
    events_blocked: 0,
  },
};

window.fetch = async (rawUrl, options = {}) => {
  const url = String(rawUrl);
  if (url === '/plugin/study_companion/ui-api/i18n/en.json') return Response.json(enBundle);
  if (url === '/runs' && options.method === 'POST') {
    const body = JSON.parse(String(options.body || '{}'));
    const runId = `run-${++runNumber}`;
    runs.set(runId, body);
    return Response.json({ run_id: runId, status: 'queued' });
  }
  if (/^\/runs\/run-\d+$/.test(url)) return Response.json({ status: 'succeeded' });
  if (/^\/runs\/run-\d+\/export$/.test(url)) {
    const runId = url.match(/^\/runs\/(run-\d+)\/export$/)[1];
    const run = runs.get(runId) || {};
    if (run.entry_id === 'study_update_settings_config') {
      if (failUpdate) {
        return Response.json({ items: [{ type: 'json', json: { success: false, error: { message: 'save failed' } } }] });
      }
      confirmed.config = run.args.config;
      confirmed.communication_status = {
        ...confirmed.communication_status,
        configured_enabled: true,
        available: runtimeAvailable,
        command_subscription_active: false,
        command_worker_active: false,
      };
      return Response.json({ items: [{ type: 'json', json: { success: true, data: confirmed } }] });
    }
    const data = run.entry_id === 'study_get_settings_config'
      ? confirmed
      : { status: 'ready', active_mode: 'companion', is_first_run: false };
    return Response.json({ items: [{ type: 'json', json: { success: true, data } }] });
  }
  throw new Error(`Unexpected fetch: ${url}`);
};

window.eval(i18nJs);
window.eval(surfacePanelsJs);
window.eval(documentControllerJs);
window.eval(outcomeFormattersJs);
window.eval(`${knowledgeMapJs}\n${mainJs}`);

async function waitFor(predicate, label) {
  const deadline = Date.now() + 3000;
  while (Date.now() < deadline) {
    if (predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
  throw new Error(`timed out waiting for ${label}`);
}

await waitFor(
  () => Array.from(runs.values()).some((run) => run.entry_id === 'study_status'),
  'frontend bootstrap',
);
document.getElementById('advancedToggleBtn').click();
await waitFor(() => document.getElementById('settingsConfigStatus').textContent.includes('loaded'), 'settings load');

const parent = document.getElementById('settingsCommunicationEnabled');
const child = document.getElementById('settingsSolutionNarrationEnabled');
const generalChild = document.getElementById('settingsGeneralNarrationEnabled');
const runtime = document.getElementById('settingsCommunicationRuntime');
if (parent.checked || !child.checked || !child.disabled || !generalChild.checked || !generalChild.disabled) {
  throw new Error(`disabled parent state mismatch: parent=${parent.checked} child=${child.checked}/${child.disabled} general=${generalChild.checked}/${generalChild.disabled}`);
}
if (!runtime.textContent.includes('disabled')) {
  throw new Error(`disabled runtime summary missing: ${runtime.textContent}`);
}
window.eval(`renderCommunicationRuntime({ configured_enabled: false, available: true })`);
if (!runtime.textContent.includes('do not match')) {
  throw new Error(`inverse configured/runtime mismatch summary missing: ${runtime.textContent}`);
}
window.eval(`renderCommunicationRuntime({ configured_enabled: false, available: false })`);

parent.checked = true;
parent.dispatchEvent(new window.Event('change', { bubbles: true }));
if (child.disabled || generalChild.disabled) throw new Error('child remained disabled after enabling parent');
document.getElementById('settingsSaveBtn').click();
await waitFor(() => document.getElementById('settingsConfigStatus').textContent.includes('Could not save'), 'failed save');
if (parent.checked || !child.checked || !child.disabled || !generalChild.checked || !generalChild.disabled) {
  throw new Error(`failed save did not restore confirmed config: parent=${parent.checked} child=${child.checked}/${child.disabled} general=${generalChild.checked}/${generalChild.disabled}`);
}

failUpdate = false;
parent.checked = true;
parent.dispatchEvent(new window.Event('change', { bubbles: true }));
document.getElementById('settingsSaveBtn').click();
await waitFor(() => document.getElementById('settingsConfigStatus').textContent.includes('Saved'), 'successful save');
if (!parent.checked || child.disabled || generalChild.disabled) throw new Error('successful save did not keep enabled state');
if (!runtime.textContent.includes('do not match')) {
  throw new Error(`configured/runtime mismatch summary missing: ${runtime.textContent}`);
}

runtimeAvailable = true;
const updateCount = Array.from(runs.values()).filter((run) => run.entry_id === 'study_update_settings_config').length;
document.getElementById('settingsSaveBtn').click();
await waitFor(
  () => Array.from(runs.values()).filter((run) => run.entry_id === 'study_update_settings_config').length > updateCount,
  'runtime-ready save',
);
await waitFor(() => runtime.textContent.includes('command communication is unavailable'), 'partial runtime summary');
if (!runtime.textContent.includes('command communication is unavailable')) {
  throw new Error(`partial runtime summary missing: ${runtime.textContent}`);
}
"""
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=frontend_dir,
        env={
            **os.environ,
            "STUDY_COMPANION_STATIC_DIR": str(STATIC_DIR),
            "STUDY_COMPANION_I18N_DIR": str(PLUGIN_DIR / "i18n"),
        },
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_browser_can_enable_communication_and_save_parent_child_values() -> None:
    playwright_sync_api = pytest.importorskip("playwright.sync_api")
    try:
        with playwright_sync_api.sync_playwright() as playwright:
            if not Path(playwright.chromium.executable_path).exists():
                pytest.skip("Playwright chromium is not installed")
    except Exception as exc:  # pragma: no cover - environment-dependent browser probe
        pytest.skip(f"Playwright chromium is unavailable: {exc}")

    static_files = {
        name: (content_type, (STATIC_DIR / name).read_text(encoding="utf-8"))
        for name, content_type in {
            "index.html": "text/html",
            "style.css": "text/css",
            "i18n.js": "text/javascript",
            "surface-panels.js": "text/javascript",
            "knowledge-map.js": "text/javascript",
            "document-controller.js": "text/javascript",
            "outcome-formatters.js": "text/javascript",
            "main.js": "text/javascript",
            "katex.min.js": "text/javascript",
            "katex-render.js": "text/javascript",
            "katex.min.css": "text/css",
        }.items()
    }
    en_bundle = json.loads((PLUGIN_DIR / "i18n" / "en.json").read_text(encoding="utf-8"))
    runs: dict[str, dict] = {}
    update_payloads: list[dict] = []

    with playwright_sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()

        def route_handler(route) -> None:
            request = route.request
            path = request.url.split("://", 1)[1].split("/", 1)[1].split("?", 1)[0]
            if path == "plugin/study_companion/ui":
                path += "/"
            if path == "plugin/study_companion/ui/":
                content_type, body = static_files["index.html"]
                route.fulfill(status=200, content_type=content_type, body=body)
                return
            if path.startswith("plugin/study_companion/ui/"):
                name = path.rsplit("/", 1)[-1]
                if name in static_files:
                    content_type, body = static_files[name]
                    route.fulfill(status=200, content_type=content_type, body=body)
                    return
            if path == "plugin/study_companion/ui-api/i18n/en.json":
                route.fulfill(status=200, content_type="application/json", body=json.dumps(en_bundle))
                return
            if path == "runs" and request.method == "POST":
                run_id = f"run-{len(runs) + 1}"
                runs[run_id] = request.post_data_json
                route.fulfill(status=200, content_type="application/json", body=json.dumps({"run_id": run_id, "status": "queued"}))
                return
            match = re.fullmatch(r"runs/(run-\d+)(/export)?", path)
            if match and not match.group(2):
                route.fulfill(status=200, content_type="application/json", body=json.dumps({"status": "succeeded"}))
                return
            if match and match.group(2):
                run = runs[match.group(1)]
                if run["entry_id"] == "study_get_settings_config":
                    data = {
                        "config": {
                            "study": {"default_mode": "companion"},
                            "ocr_reader": {"enabled": True, "languages": "eng"},
                            "llm": {"llm_call_timeout_seconds": 30},
                            "communication": {"enabled": False, "solution_narration_enabled": True, "general_narration_enabled": True},
                        },
                        "communication_status": {
                            "configured_enabled": False,
                            "solution_narration_enabled": True,
                            "available": False,
                            "command_subscription_active": False,
                            "command_worker_active": False,
                        },
                    }
                elif run["entry_id"] == "study_update_settings_config":
                    update_payloads.append(run["args"]["config"])
                    data = {
                        "config": run["args"]["config"],
                        "communication_status": {
                            "configured_enabled": True,
                            "solution_narration_enabled": True,
                            "available": True,
                            "command_subscription_active": False,
                            "command_worker_active": False,
                        },
                    }
                else:
                    data = {"status": "ready", "active_mode": "companion", "is_first_run": False}
                body = {"items": [{"type": "json", "json": {"success": True, "data": data}}]}
                route.fulfill(status=200, content_type="application/json", body=json.dumps(body))
                return
            route.fulfill(status=404, body=f"Unhandled test route: {path}")

        page.route("**/*", route_handler)
        page.goto("http://neko.test/plugin/study_companion/ui/?locale=en", wait_until="networkidle")
        playwright_sync_api.expect(page.locator("#modeSwitch")).to_have_attribute("data-ready", "true")
        playwright_sync_api.expect(page.locator("#learningProfileModal")).to_be_visible()
        page.locator("#learningProfileModal .button-secondary").click()
        playwright_sync_api.expect(page.locator("#learningProfileModal")).to_be_hidden()
        page.locator("#advancedToggleBtn").click()
        playwright_sync_api.expect(page.locator("#settingsConfigStatus")).to_contain_text("loaded")

        parent = page.locator("#settingsCommunicationEnabled")
        child = page.locator("#settingsSolutionNarrationEnabled")
        general_child = page.locator("#settingsGeneralNarrationEnabled")
        playwright_sync_api.expect(parent).not_to_be_checked()
        playwright_sync_api.expect(child).to_be_checked()
        playwright_sync_api.expect(child).to_be_disabled()
        playwright_sync_api.expect(general_child).to_be_checked()
        playwright_sync_api.expect(general_child).to_be_disabled()

        parent.check()
        playwright_sync_api.expect(child).to_be_enabled()
        playwright_sync_api.expect(general_child).to_be_enabled()
        page.locator("#settingsSaveBtn").click()
        playwright_sync_api.expect(page.locator("#settingsConfigStatus")).to_contain_text("Saved")
        playwright_sync_api.expect(page.locator("#settingsCommunicationRuntime")).to_contain_text(
            "command communication is unavailable"
        )
        assert update_payloads[-1]["communication"] == {
            "enabled": True,
            "solution_narration_enabled": True,
            "general_narration_enabled": True,
        }
        browser.close()
