"""The i18nextLng storage follow is scoped to the avatar tool editor window.

Every template loads static/i18n-i18next.js, and several of them write
i18nextLng on their own (language selectors, the Steam language written at
startup).  Following that key everywhere would let any window live-switch every
other same-origin window and override the server-side uiLanguage choice.
"""

import shutil
import textwrap
from pathlib import Path

import pytest

from tests.node_harness import run_node_script


PROJECT_ROOT = Path(__file__).resolve().parents[2]
I18N_PATH = PROJECT_ROOT / "static" / "i18n-i18next.js"


def _slice_between(source: str, start_anchor: str, end_anchor: str, label: str) -> str:
    start = source.find(start_anchor)
    assert start >= 0, f"{label} 起始锚点已失效，请同步更新测试"
    end = source.find(end_anchor, start + len(start_anchor))
    assert end > start, f"{label} 结束锚点已失效，请同步更新测试"
    return source[start:end]


@pytest.fixture(scope="module")
def node_path():
    executable = shutil.which("node")
    if not executable:
        pytest.skip("node is required for browser runtime harnesses")
    return executable


@pytest.mark.unit
def test_storage_language_follow_is_registered_through_the_scoped_handler():
    source = I18N_PATH.read_text(encoding="utf-8")
    normal_exports = _slice_between(
        source,
        "function exportNormalFunctions() {",
        "window.changeLanguage = function (lng) {",
        "exportNormalFunctions",
    )
    assert "window.addEventListener('storage', followCrossWindowLanguage);" in normal_exports
    assert "addEventListener('storage', (" not in normal_exports

    editor_template = (PROJECT_ROOT / "templates" / "avatar_tool_editor.html").read_text(
        encoding="utf-8"
    )
    assert '<body class="avatar-tool-editor-page">' in editor_template


@pytest.mark.unit
def test_storage_language_follow_only_switches_the_editor_without_server_override(node_path):
    source = I18N_PATH.read_text(encoding="utf-8")
    initial_language_source = _slice_between(
        source,
        "let serverUiLanguageOverride = null;",
        "// 先使用同步方式获取初始语言",
        "getInitialLanguage",
    )
    follow_source = _slice_between(
        source,
        "function followCrossWindowLanguage(event) {",
        "/**",
        "followCrossWindowLanguage",
    )
    harness = textwrap.dedent(
        r"""
        const assert = require('node:assert/strict');
        const SUPPORTED_LANGUAGES = ['zh-CN', 'zh-TW', 'en', 'ja', 'ko', 'ru', 'es', 'pt'];
        const normalizeSupportedLanguageCode = value => (
          SUPPORTED_LANGUAGES.includes(value) ? value : null
        );
        const localStorage = { setItem() {}, getItem() { return null; } };
        const getLanguageFromQuery = () => null;
        const getBrowserLanguage = () => 'zh-CN';
        let serverLanguages = { uiLanguage: null, steamLanguage: null };
        const getServerLanguagePreferences = async () => serverLanguages;
        const changes = [];
        const i18next = {
          language: 'zh-CN',
          changeLanguage(language) { changes.push(language); return Promise.resolve(); },
        };
        const classes = new Set();
        const document = { body: { classList: { contains: name => classes.has(name) } } };
        const storageEvent = newValue => ({ key: 'i18nextLng', newValue });

        __INITIAL_LANGUAGE__
        __FOLLOW__

        (async () => {
          await getInitialLanguage();

          followCrossWindowLanguage(storageEvent('en'));
          assert.deepEqual(changes, [], 'ordinary pages must not follow other windows');

          classes.add('avatar-tool-editor-page');
          followCrossWindowLanguage({ key: 'other', newValue: 'en' });
          followCrossWindowLanguage(storageEvent('xx'));
          followCrossWindowLanguage(storageEvent('zh-CN'));
          assert.deepEqual(changes, []);
          followCrossWindowLanguage(storageEvent('en'));
          assert.deepEqual(changes, ['en'], 'the editor follows the main window');

          serverLanguages = { uiLanguage: 'ja', steamLanguage: null };
          assert.equal(await getInitialLanguage(), 'ja');
          followCrossWindowLanguage(storageEvent('ko'));
          assert.deepEqual(changes, ['en'], 'a server uiLanguage override wins');
          process.stdout.write('ok');
        })().catch(error => {
          console.error(error && error.stack ? error.stack : error);
          process.exitCode = 1;
        });
        """
    )
    harness = harness.replace("__INITIAL_LANGUAGE__", initial_language_source)
    harness = harness.replace("__FOLLOW__", follow_source)
    result = run_node_script(
        node_path,
        harness,
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == "ok"
