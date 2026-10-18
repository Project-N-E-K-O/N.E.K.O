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

"""The settings popup's visit group (design §5 PR-13, §3.7.6; OD-09 v3).

* the group renders exactly two visit switches, ``visitEnabled`` (default
  off) and ``visitVoiceEnabled`` (default on), both persisted through the
  conversation-settings whitelist;
* ``visitMemoryEnabled`` is a hidden setting: no settings script mentions it;
* the group entry starts hidden and is only shown when ``GET /api/visit/state``
  reports the ``NEKO_VISIT_ENABLED`` switch on; the tier is read-only from
  ``default_tier``;
* 8 locales carry the same ``visit.settings`` keys and no "mute locally"
  switch; turning ``visitEnabled`` on the first time opens the visit persona
  confirmation.
"""

import json
import re
import shutil
from pathlib import Path

import pytest

from tests.node_harness import run_node_script
from utils.conversation_settings_constants import ALLOWED_CONVERSATION_SETTINGS

PROJECT_ROOT = Path(__file__).resolve().parents[2]
POPUP = PROJECT_ROOT / "static" / "avatar" / "avatar-ui-popup.js"
POPUP_CONFIG = PROJECT_ROOT / "static" / "avatar" / "avatar-ui-popup-config.js"
APP_SETTINGS = PROJECT_ROOT / "static" / "app" / "app-settings.js"
APP_STATE = PROJECT_ROOT / "static" / "app" / "app-state.js"
VISIT_SETTINGS = PROJECT_ROOT / "static" / "visit" / "visit-settings.js"
INDEX = PROJECT_ROOT / "templates" / "index.html"
LOCALES = PROJECT_ROOT / "static" / "locales"
LANGS = ("zh-CN", "zh-TW", "en", "ja", "ko", "ru", "es", "pt")
REQUIRED_KEYS = {
    "groupTitle", "enabled", "enabledTooltip", "voice", "voiceTooltip",
    "crop", "cropUpper", "cropFull", "tier", "persona",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _visit_panel_source() -> str:
    src = _read(POPUP)
    start = src.index("function createVisitSettingsSidePanel(")
    return src[start:src.index("\n}\n", start)]


def test_visit_panel_renders_exactly_the_two_visit_switches():
    panel = _visit_panel_source()
    assert re.findall(r"id: '(visit-[\w-]+)'", panel) == ["visit-enabled", "visit-voice"]
    assert re.findall(r"storageKey: '(\w+)'", panel) == ["visitEnabled", "visitVoiceEnabled"]


def test_both_switches_are_whitelisted_conversation_settings():
    assert {"visitEnabled", "visitVoiceEnabled"} <= ALLOWED_CONVERSATION_SETTINGS
    settings = _read(APP_SETTINGS)
    shared = settings[settings.index("const _SHARED_SETTINGS_KEYS = ["):]
    shared = shared[:shared.index("];")]
    assert "'visitEnabled'" in shared and "'visitVoiceEnabled'" in shared


def test_defaults_are_visit_off_and_voice_on():
    state = _read(APP_STATE)
    assert re.search(r"visitEnabled: false,", state) and re.search(r"visitVoiceEnabled: true,", state)
    settings = _read(APP_SETTINGS)
    assert "S.visitEnabled = settings.visitEnabled ?? false;" in settings
    assert "S.visitVoiceEnabled = settings.visitVoiceEnabled ?? true;" in settings
    reset = settings[settings.index("function _defaultConversationSettingsForReset()"):]
    reset = reset[:reset.index("};")]
    assert "visitEnabled: false," in reset and "visitVoiceEnabled: true," in reset


def test_visit_memory_switch_is_not_on_the_settings_page():
    for path in (POPUP, POPUP_CONFIG, APP_SETTINGS, APP_STATE, VISIT_SETTINGS):
        assert "visitMemoryEnabled" not in _read(path), path
    for lang in LANGS:
        visit = json.loads(_read(LOCALES / f"{lang}.json")).get("visit", {})
        assert "memoryEnabled" not in json.dumps(visit.get("settings", {})), lang


def test_group_entry_starts_hidden_and_follows_the_release_switch():
    src = _read(POPUP)
    block = src[src.index("const visitSettingsBtn = "):src.index("// 3. 角色设置按钮已移至分隔线下方")]
    assert "visitSettingsBtn.style.display = 'none';" in block
    assert "data-neko-visit-settings-entry" in block
    visit = _read(VISIT_SETTINGS)
    assert "var url = '/api/visit/state?catgirl=' + encodeURIComponent(catgirl);" in visit
    assert "body.enabled === true" in visit
    assert "body.default_tier" in visit
    assert "/static/visit/visit-settings.js" in _read(INDEX)


def test_locales_have_the_same_visit_settings_keys():
    key_sets = {}
    for lang in LANGS:
        data = json.loads(_read(LOCALES / f"{lang}.json"))
        settings = data["visit"]["settings"]
        assert all(isinstance(v, str) and v.strip() for v in settings.values()), lang
        key_sets[lang] = set(settings)
        assert isinstance(data["visit"]["stream"]["ttsFallback"], str), lang
    assert all(keys == key_sets["zh-CN"] for keys in key_sets.values()), key_sets
    assert REQUIRED_KEYS <= key_sets["zh-CN"]
    # The 'mute locally but retain RMS' switch was removed (OD-15 v3)
    assert not any(re.search(r"mute|silent|silence", k, re.I) for k in key_sets["zh-CN"])


def test_locale_values_use_no_owner_terms():
    banned = ("主人", "ご主人", "주인", "хозяин", "master")
    for lang in LANGS:
        data = json.loads(_read(LOCALES / f"{lang}.json"))
        text = json.dumps(data["visit"]["settings"], ensure_ascii=False).lower()
        assert not any(word.lower() in text for word in banned), lang


SETTINGS_HARNESS = r"""
'use strict';
const fs = require('node:fs');
const vm = require('node:vm');
const SRC = fs.readFileSync(__SRC__, 'utf8');

async function load(stateBody, opts) {
  opts = opts || {};
  const bodies = Array.isArray(stateBody) ? stateBody.slice() : null;
  const opened = [];
  const fetched = [];
  const refreshed = [];
  const entries = [{ style: { display: 'none' } }, { style: { display: 'none' } }];
  const tiers = [{ textContent: '' }];
  const storage = { data: {}, getItem(k) { return this.data[k] ?? null; }, setItem(k, v) { this.data[k] = String(v); } };
  const listeners = new Map();
  const win = {
    lanlan_config: { lanlan_name: '小八' },
    addEventListener(type, fn) { if (!listeners.has(type)) listeners.set(type, []); listeners.get(type).push(fn); },
    dispatchEvent(ev) { (listeners.get(ev.type) || []).forEach((fn) => fn(ev)); return true; },
    openVisitPersonaPreview(name, opts) { opened.push([name, opts]); },
    nekoLocalMutationSecurity: {
      token: opts.staleToken ? 'old' : 'tok',
      async getMutationHeaders() { return { 'X-CSRF-Token': this.token }; },
      async refreshToken() { refreshed.push(1); this.token = 'tok'; return this.token; },
    },
  };
  win.window = win;
  const document = {
    readyState: 'complete',
    addEventListener() {},
    querySelectorAll(sel) { return sel.indexOf('tier') !== -1 ? tiers : entries; },
  };
  const sandbox = {
    window: win, document, console, localStorage: storage, CustomEvent: class { constructor(t, i) { this.type = t; this.detail = (i || {}).detail; } },
    encodeURIComponent,
    fetch: async (url, init) => {
      fetched.push([url, init && init.headers]);
      if (init && init.headers && init.headers['X-CSRF-Token'] === 'old') {
        const body = { ok: false, error_code: 'csrf_validation_failed' };
        return { ok: false, status: 403, clone() { return this; }, async json() { return body; } };
      }
      const body = bodies ? bodies.shift() : stateBody;
      return { ok: true, status: 200, clone() { return this; }, async json() { return body; } };
    },
  };
  vm.createContext(sandbox);
  vm.runInContext(SRC, sandbox, { filename: 'visit-settings.js' });
  await win.nekoVisitSettings.revealEntries();
  function openPopup(buttonId) {
    win.dispatchEvent({ type: 'neko-avatar-popup-opening', detail: { buttonId, popupId: 'x', prefix: 'live2d' } });
  }
  return { win, opened, fetched, entries, tiers, storage, refreshed, openPopup };
}

(async () => {
  const report = {};
  const off = await load({ enabled: false, default_tier: 'sd600' });
  report.off_display = off.entries.map((e) => e.style.display);
  const on = await load({ enabled: true, default_tier: 'sd600' });
  report.on_display = on.entries.map((e) => e.style.display);
  report.tier = on.tiers[0].textContent;
  report.url = on.fetched[0][0];
  report.csrf = on.fetched[0][1]['X-CSRF-Token'];
  on.win.nekoVisitSettings.onVisitEnabledChanged(false);
  report.opened_on_disable = on.opened.length;
  on.win.nekoVisitSettings.onVisitEnabledChanged(true);
  on.win.nekoVisitSettings.onVisitEnabledChanged(true);
  report.opened = on.opened;
  report.crop_default = on.win.nekoVisitSettings.getCrop();
  on.win.nekoVisitSettings.setCrop('full');
  on.win.nekoVisitSettings.setCrop('bogus');
  report.crop_after = on.win.nekoVisitSettings.getCrop();
  const stale = await load({ enabled: true, default_tier: 'sd600' }, { staleToken: true });
  report.stale_display = stale.entries.map((e) => e.style.display);
  report.stale_refreshed = stale.refreshed.length;
  report.stale_fetches = stale.fetched.length;
  // 总闸在页面开着期间被关掉：下次打开设置弹窗时重新探、入口收起（CodeRabbit）
  const flip = await load([{ enabled: true, default_tier: 'sd600' }, { enabled: false, default_tier: 'sd600' }]);
  report.flip_first = flip.entries.map((e) => e.style.display);
  flip.openPopup('mic');                                  // 别的弹窗打开不探
  await new Promise((r) => setTimeout(r, 0));
  report.flip_other_fetches = flip.fetched.length;
  flip.openPopup('settings');                             // 真实页面每次打开设置弹窗走的就是这条
  for (let i = 0; i < 5; i++) await new Promise((r) => setTimeout(r, 0));
  report.flip_second = flip.entries.map((e) => e.style.display);
  report.flip_fetches = flip.fetched.length;
  process.stdout.write(JSON.stringify(report));
})().catch((e) => { console.error(e); process.exit(1); });
"""


def test_release_switch_probe_and_first_enable_persona_prompt():
    node_path = shutil.which("node")
    if not node_path:
        pytest.skip("node is required for the visit settings harness")
    result = run_node_script(
        node_path, SETTINGS_HARNESS.replace("__SRC__", json.dumps(str(VISIT_SETTINGS))),
        cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["off_display"] == ["none", "none"]
    assert out["on_display"] == ["", ""]
    assert out["tier"] == "sd600"
    assert out["url"] == "/api/visit/state?catgirl=%E5%B0%8F%E5%85%AB"
    assert out["csrf"] == "tok"
    assert out["opened_on_disable"] == 0
    # Prompt only on the first opening and require confirmation
    assert out["opened"] == [["小八", {"requireConfirm": True}]]
    assert out["crop_default"] == "upper" and out["crop_after"] == "full"
    # A backend restart invalidates cached CSRF tokens: refresh once and retry instead of treating 403 as the master switch being off (Codex P2)
    assert out["stale_display"] == ["", ""]
    assert out["stale_refreshed"] == 1 and out["stale_fetches"] == 2
    assert out["flip_first"] == ["", ""] and out["flip_second"] == ["none", "none"]
    assert out["flip_other_fetches"] == 1 and out["flip_fetches"] == 2
