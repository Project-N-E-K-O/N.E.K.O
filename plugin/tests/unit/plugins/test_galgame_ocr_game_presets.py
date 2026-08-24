from __future__ import annotations

import html
import json
import re
import shutil
from pathlib import Path

import pytest

from _galgame_test_support import (
    OCR_CAPTURE_PROFILE_STAGE_DIALOGUE,
    OCR_CAPTURE_PROFILE_STAGE_MENU,
    DetectedGameWindow,
    OcrReaderManager,
    _FakeCaptureBackend,
    _FakeOcrBackend,
    _Logger,
    _make_effective_config,
    _make_plugin_dirs,
    build_config,
)
from tests.node_harness import run_node_script


def _make_ocr_manager(tmp_path: Path) -> OcrReaderManager:
    _plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    return OcrReaderManager(
        logger=_Logger(),
        config=build_config(
            _make_effective_config(bridge_root, ocr_reader={"enabled": True})
        ),
        platform_fn=lambda: True,
        window_scanner=lambda: [],
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
    )


@pytest.mark.plugin_unit
def test_senren_banka_uses_builtin_dialogue_profile_until_manually_overridden(
    tmp_path: Path,
) -> None:
    manager = _make_ocr_manager(tmp_path)
    target = DetectedGameWindow(
        hwnd=1821,
        title="Senren Banka",
        process_name="SenrenBanka.exe",
        pid=1821,
        width=1920,
        height=1080,
    )

    builtin = manager._capture_profile_selection_for_target(
        target,
        stage=OCR_CAPTURE_PROFILE_STAGE_DIALOGUE,
    )

    assert builtin.match_source == "builtin_preset"
    assert builtin.profile.to_dict() == pytest.approx(
        {
            "left_inset_ratio": 0.25,
            "right_inset_ratio": 0.05,
            "top_ratio": 0.62,
            "bottom_inset_ratio": 0.08,
        }
    )

    manager.update_capture_profiles(
        {
            "SenrenBanka.exe": {
                OCR_CAPTURE_PROFILE_STAGE_DIALOGUE: {
                    "left_inset_ratio": 0.30,
                    "right_inset_ratio": 0.04,
                    "top_ratio": 0.60,
                    "bottom_inset_ratio": 0.10,
                }
            }
        }
    )
    manual = manager._capture_profile_selection_for_target(
        target,
        stage=OCR_CAPTURE_PROFILE_STAGE_DIALOGUE,
    )

    assert manual.match_source == "process_fallback"
    assert manual.profile.to_dict() == pytest.approx(
        {
            "left_inset_ratio": 0.30,
            "right_inset_ratio": 0.04,
            "top_ratio": 0.60,
            "bottom_inset_ratio": 0.10,
        }
    )


@pytest.mark.plugin_unit
def test_senren_banka_profile_is_dialogue_only_and_does_not_affect_other_games(
    tmp_path: Path,
) -> None:
    manager = _make_ocr_manager(tmp_path)
    senren_target = DetectedGameWindow(
        hwnd=1822,
        title="Senren Banka",
        process_name="SenrenBanka.exe",
        pid=1822,
    )
    other_target = DetectedGameWindow(
        hwnd=1823,
        title="Other Game",
        process_name="OtherGame.exe",
        pid=1823,
    )

    senren_menu = manager._capture_profile_selection_for_target(
        senren_target,
        stage=OCR_CAPTURE_PROFILE_STAGE_MENU,
    )
    other_dialogue = manager._capture_profile_selection_for_target(
        other_target,
        stage=OCR_CAPTURE_PROFILE_STAGE_DIALOGUE,
    )

    assert senren_menu.match_source == "config_default"
    assert other_dialogue.match_source == "config_default"


@pytest.mark.plugin_unit
def test_senren_banka_frontend_profile_matches_backend_preset() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for Galgame UI JavaScript tests")

    source = (
        Path(__file__).resolve().parents[3]
        / "plugins"
        / "galgame_plugin"
        / "static"
        / "main.js"
    ).read_text(encoding="utf-8")
    constants = (
        source[
            source.index("const DEFAULT_CAPTURE_PROFILE") : source.index(
                "const OCR_PROFILE_STAGE_LABELS_ZH"
            )
        ]
        + source[
            source.index("const AIHONG_CAPTURE_PRESETS") : source.index(
                "const AUTO_REFRESH_IDLE_INTERVAL_MS"
            )
        ]
    )
    helpers = source[
        source.index("function normalizeProcessName") : source.index(
            "function setInputValueIfIdle"
        )
    ]
    script = f"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const context = {{}};
vm.runInNewContext({json.dumps(constants + helpers)}, context);

const builtin = context.resolveEditableCaptureProfile(
  {{}},
  'SenrenBanka.exe',
  'dialogue_stage',
  'process_fallback'
);
assert.deepEqual(JSON.parse(JSON.stringify(builtin)), {{
  left_inset_ratio: 0.25,
  right_inset_ratio: 0.05,
  top_ratio: 0.62,
  bottom_inset_ratio: 0.08,
}});

const manual = context.resolveEditableCaptureProfile(
  {{
    ocr_capture_profiles: {{
      'SenrenBanka.exe': {{
        dialogue_stage: {{
          left_inset_ratio: 0.31,
          right_inset_ratio: 0.04,
          top_ratio: 0.59,
          bottom_inset_ratio: 0.11,
        }},
      }},
    }},
  }},
  'SenrenBanka.exe',
  'dialogue_stage',
  'process_fallback'
);
assert.equal(manual.left_inset_ratio, 0.31);
assert.equal(manual.top_ratio, 0.59);
"""
    run_node_script(node, script, check=True)


@pytest.mark.plugin_unit
def test_senren_banka_frontend_profile_can_be_selected_manually() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for Galgame UI JavaScript tests")

    plugin_root = Path(__file__).resolve().parents[3] / "plugins" / "galgame_plugin"
    source = (plugin_root / "static" / "main.js").read_text(encoding="utf-8")
    index = (plugin_root / "static" / "index.html").read_text(encoding="utf-8")
    constants = (
        source[
            source.index("const DEFAULT_CAPTURE_PROFILE") : source.index(
                "const OCR_PROFILE_STAGE_LABELS_ZH"
            )
        ]
        + source[
            source.index("const AIHONG_CAPTURE_PRESETS") : source.index(
                "const AUTO_REFRESH_IDLE_INTERVAL_MS"
            )
        ]
    )
    helpers = source[
        source.index("function normalizeProcessName") : source.index(
            "function setInputValueIfIdle"
        )
    ]
    selector = source[
        source.index("function renderOcrProfile") : source.index(
            "function renderHistory"
        )
    ]
    render_helpers = source[
        source.index("function setInputValueIfIdle") : source.index(
            "function renderInstallTaskState"
        )
    ]
    script = f"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const context = {{}};
vm.runInNewContext({json.dumps(constants + helpers)}, context);

const status = {{
  ocr_capture_profiles: {{
    'RenamedGame.exe': {{
      dialogue_stage: {{
        left_inset_ratio: 0.10,
        right_inset_ratio: 0.10,
        top_ratio: 0.40,
        bottom_inset_ratio: 0.20,
      }},
    }},
  }},
}};
const manualPreset = context.resolveEditableCaptureProfile(
  status,
  'RenamedGame.exe',
  'dialogue_stage',
  'process_fallback',
  'senren_banka'
);
assert.deepEqual(JSON.parse(JSON.stringify(manualPreset)), {{
  left_inset_ratio: 0.25,
  right_inset_ratio: 0.05,
  top_ratio: 0.62,
  bottom_inset_ratio: 0.08,
}});
const automatic = context.resolveEditableCaptureProfile(
  status,
  'RenamedGame.exe',
  'dialogue_stage',
  'process_fallback',
  'auto'
);
assert.equal(automatic.left_inset_ratio, 0.10);
assert.equal(automatic.top_ratio, 0.40);

const elements = {{
  ocrProfileGamePresetSelect: {{ value: 'senren_banka' }},
  ocrProfileProcessInput: {{ value: 'OtherGame.exe' }},
  ocrProfileStageSelect: {{ value: 'default' }},
  ocrProfileSaveScopeSelect: {{ value: 'window_bucket' }},
  ocrProfileLeftInput: {{ value: '' }},
  ocrProfileRightInput: {{ value: '' }},
  ocrProfileTopInput: {{ value: '' }},
  ocrProfileBottomInput: {{ value: '' }},
  ocrProfileRuntimeHint: {{ textContent: '' }},
  ocrProfileAutoRecalibrateBtn: {{ disabled: false, title: '' }},
  ocrProfileApplyRecommendedBtn: {{ disabled: false, title: '' }},
  ocrProfileRollbackBtn: {{ disabled: false, title: '' }},
  ocrProfileAutoApplyRecommendedInput: {{ checked: false }},
}};
const selectorContext = {{
  document: {{
    activeElement: elements.ocrProfileGamePresetSelect,
    getElementById(id) {{
      return elements[id] || null;
    }},
  }},
  latestStatus: {{
    ocr_reader_runtime: {{
      process_name: 'OtherGame.exe',
      width: 1280,
      height: 720,
    }},
  }},
  uiT(_key, fallback) {{ return fallback; }},
  uiTf(_key, fallback) {{ return fallback; }},
  ocrProfileStageLabel(_stage, fallback) {{ return fallback; }},
  ocrCaptureMatchSourceLabel(_source, fallback) {{ return fallback; }},
  formatCaptureProfile() {{ return ''; }},
  formatFixedNumber(value) {{ return String(value); }},
}};
vm.runInNewContext({json.dumps(constants + helpers + render_helpers + selector)}, selectorContext);
selectorContext.selectOcrGameCapturePreset();
assert.equal(elements.ocrProfileProcessInput.value, 'SenrenBanka.exe');
assert.equal(elements.ocrProfileStageSelect.value, 'dialogue_stage');
assert.equal(elements.ocrProfileSaveScopeSelect.value, 'process_fallback');
assert.equal(elements.ocrProfileLeftInput.value, '0.25');
assert.equal(elements.ocrProfileTopInput.value, '0.62');

elements.ocrProfileSaveScopeSelect.value = 'window_bucket';
selectorContext.latestStatus = {{
  ocr_reader_runtime: {{
    process_name: 'SenrenBanka.exe',
    width: 1920,
    height: 1080,
  }},
}};
selectorContext.selectOcrGameCapturePreset();
assert.equal(elements.ocrProfileSaveScopeSelect.value, 'window_bucket');
"""
    run_node_script(node, script, check=True)

    assert 'id="ocrProfileGamePresetSelect"' in index
    assert '<option value="auto"' in index
    assert '<option value="senren_banka">' in index
    assert "千恋＊万花" in html.unescape(index)
    assert re.search(
        r"getElementById\(\s*['\"]ocrProfileGamePresetSelect['\"]\s*\)"
        r"\s*\.addEventListener\(\s*['\"]change['\"]\s*,"
        r"\s*selectOcrGameCapturePreset\s*\)",
        source,
    )


@pytest.mark.plugin_unit
def test_saving_selected_game_preset_returns_editor_to_stored_profile() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for Galgame UI JavaScript tests")

    source = (
        Path(__file__).resolve().parents[3]
        / "plugins"
        / "galgame_plugin"
        / "static"
        / "main.js"
    ).read_text(encoding="utf-8")
    save_functions = source[
        source.index("function readProfileNumber") : source.index(
            "async function clearOcrCaptureProfile"
        )
    ]
    script = f"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const elements = {{
  ocrProfileProcessInput: {{ value: 'SenrenBanka.exe' }},
  ocrProfileStageSelect: {{ value: 'dialogue_stage' }},
  ocrProfileSaveScopeSelect: {{ value: 'process_fallback' }},
  ocrProfileLeftInput: {{ value: '0.31' }},
  ocrProfileRightInput: {{ value: '0.04' }},
  ocrProfileTopInput: {{ value: '0.59' }},
  ocrProfileBottomInput: {{ value: '0.11' }},
  ocrProfileGamePresetSelect: {{ value: 'senren_banka' }},
}};
let refreshCount = 0;
const context = {{
  document: {{ getElementById(id) {{ return elements[id] || null; }} }},
  normalizeCaptureProfileSaveScope(value) {{ return value; }},
  async callPlugin() {{ return {{ summary: 'saved' }}; }},
  setFlash() {{}},
  uiT(_key, fallback) {{ return fallback; }},
  async refreshAll() {{ refreshCount += 1; }},
}};
vm.runInNewContext({json.dumps(save_functions)}, context);
(async () => {{
  await context.saveOcrCaptureProfile();
  assert.equal(elements.ocrProfileGamePresetSelect.value, 'auto');
  assert.equal(refreshCount, 1);
}})().catch((error) => {{
  console.error(error);
  process.exitCode = 1;
}});
"""
    run_node_script(node, script, check=True)
