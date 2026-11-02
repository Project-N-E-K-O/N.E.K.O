from __future__ import annotations

import shutil
import textwrap
from pathlib import Path

import pytest

from tests.node_harness import run_node_script


ROOT = Path(__file__).resolve().parents[2]
CAPTURE = ROOT / "static" / "app" / "app-audio-capture.js"
POPUP = ROOT / "static" / "avatar" / "avatar-popup-common.js"


def _helpers() -> str:
    source = CAPTURE.read_text(encoding="utf-8")
    start = source.index("var MIC_POPUP_IDS")
    end = source.index("// 时域采样 buffer", start)
    return source[start:end]


def test_volume_meter_resolves_the_open_popup_including_pngtuber() -> None:
    source = CAPTURE.read_text(encoding="utf-8")
    visualizer = source.split("function startMicVolumeVisualization()", 1)[1].split(
        "function stopMicVolumeVisualization()", 1
    )[0]
    render = source.split("window.renderFloatingMicList = async function (popupArg)", 1)[1].split(
        "if (!micPopup) return false;", 1
    )[0]

    assert "pngtuber-popup-mic" in _helpers()
    assert "findMicPopupForVolume(cachedBarFill)" in visualizer
    assert "document.getElementById('live2d-popup-mic') ||" not in visualizer
    assert "findDisplayedMicPopup()" in render


def test_pngtuber_side_panel_uses_its_own_button_zone() -> None:
    source = POPUP.read_text(encoding="utf-8")
    assert "/^(live2d|vrm|mmd|pngtuber)-/i" in source
    assert "const ownerPrefixMatch = popupId.match(/^(live2d|vrm|mmd|pngtuber)-/);" in source
    assert '[id^="pngtuber-btn-"]' in source


def test_open_pngtuber_popup_owns_the_volume_meter() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required for the microphone popup lookup")

    script = textwrap.dedent(
        """
        const assert = require('assert');
        function element(id, display, open, parent) {
          return {
            id,
            isConnected: true,
            style: { display },
            offsetParent: open ? { id: 'offset-parent' } : null,
            parentNode: parent || null,
            closest(selector) {
              let current = this;
              while (current) {
                if (selector === '[id$="-popup-mic"]' && current.id && current.id.endsWith('-popup-mic')) {
                  return current;
                }
                current = current.parentNode;
              }
              return null;
            }
          };
        }
        const nodes = new Map();
        global.document = {
          getElementById(id) { return nodes.get(id) || null; }
        };
        %s
        const hiddenLive2d = element('live2d-popup-mic', 'none', false, null);
        const openPngtuber = element('pngtuber-popup-mic', 'flex', true, null);
        const bar = element('mic-volume-bar-fill', 'block', true, openPngtuber);
        nodes.set(hiddenLive2d.id, hiddenLive2d);
        nodes.set(openPngtuber.id, openPngtuber);
        assert.strictEqual(findMicPopupForVolume(bar), openPngtuber);

        const openLive2d = element('live2d-popup-mic', 'flex', true, null);
        const liveBar = element('mic-volume-bar-fill', 'block', true, openLive2d);
        nodes.set(openLive2d.id, openLive2d);
        nodes.delete(openPngtuber.id);
        assert.strictEqual(findMicPopupForVolume(liveBar), openLive2d);

        nodes.set(hiddenLive2d.id, hiddenLive2d);
        nodes.set('vrm-popup-mic', element('vrm-popup-mic', 'none', false, null));
        nodes.set(openPngtuber.id, openPngtuber);
        assert.strictEqual(findDisplayedMicPopup(), openPngtuber);
        assert.strictEqual(findOpenMicPopup(), openPngtuber);

        openPngtuber.style.display = 'none';
        openPngtuber.offsetParent = null;
        assert.strictEqual(findMicPopupForVolume(bar), null);
        assert.strictEqual(findDisplayedMicPopup(), null);
        """
    ) % _helpers()
    result = run_node_script(
        node,
        script,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
