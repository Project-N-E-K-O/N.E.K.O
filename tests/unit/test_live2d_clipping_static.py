import shutil
from pathlib import Path

import pytest

from tests.node_harness import run_node_stdin


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("mask_count", [0, 1, 3, 35, 96])
def test_clipping_configuration_preserves_mappings_and_allocates_buffers(mask_count):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not found")

    script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

// Use the shipped manager and context implementations, not a mock of initialize.
// These private class names intentionally pin the test to the bundled SDK;
// a bundle upgrade must recheck this integration.
const bundle = fs.readFileSync('static/libs/index.min.js', 'utf8');
const start = bundle.indexOf('class fe{');
const end = bundle.indexOf('class Me{', start);
assert.ok(start >= 0 && end > start, 'locate bundled clipping classes');
const ClippingManager = new Function('ge', 'Ct', 'bt', 'Bt', 'Ot',
    'let _e = null; ' + bundle.slice(start, end) + '; return fe;')(
        class {}, class {}, class {}, () => {}, () => {});

const sandbox = { Live2DManager: function () {}, console: { log() {}, warn() {} } };
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync('static/live2d/live2d-model.js', 'utf8'), sandbox);

async function check(n) {
    // Two targets share each context. Alternate single and combined masks;
    // the trailing source drawables have no masks themselves.
    const masks = Array.from({ length: n * 3 }, (_, i) => i < n * 2
        ? (n > 1 && i % n % 2 ? [n * 2 + i % n, n * 2] : [n * 2 + i % n])
        : []);
    const counts = masks.map(ids => ids.length);
    const core = {
        getDrawableCount: () => masks.length,
        getDrawableMasks: () => masks,
        getDrawableMaskCounts: () => counts,
    };
    const manager = new ClippingManager();
    manager.initialize(core, masks.length, masks, counts, 1);
    const contexts = manager._clippingContextListForMask.slice();
    const mapping = manager._clippingContextListForDraw.slice();
    const targets = contexts.map(c => c._clippedDrawableIndexList.slice());
    assert.equal(contexts.length, n);

    const model = { internalModel: { renderer: { _clippingManager: manager }, coreModel: core } };
    const boundary = new Error('stop before unrelated model positioning');
    // Execute the real application function through clipping configuration.
    const host = {
        _isLoadTokenActive: () => true,
        applyModelSettings() { throw boundary; },
    };
    await assert.rejects(
        sandbox.Live2DManager.prototype._configureLoadedModel.call(
            host, model, '/user_live2d/test/test.model3.json', {}, 1),
        error => error === boundary);

    assert.equal(manager.getRenderTextureCount(), 3);
    assert.deepEqual(manager._clippingContextListForDraw, mapping);
    contexts.forEach((context, i) => {
        assert.equal(manager._clippingContextListForMask[i], context);
        assert.deepEqual(context._clippedDrawableIndexList, targets[i]);
    });

    // A fresh three-buffer initialization is the reference for layout semantics.
    const reference = new ClippingManager();
    reference.initialize(core, masks.length, masks, counts, 3);
    manager.setupLayoutBounds(n);
    reference.setupLayoutBounds(n);
    const layout = m => m._clippingContextListForMask.map(c =>
        [c._bufferIndex, c._layoutChannelNo, c._layoutBounds]);
    assert.deepEqual(layout(manager), layout(reference));
    if (n === 3) {
        assert.deepEqual(contexts.map(c => [c._bufferIndex, c._layoutChannelNo]),
            [[0, 0], [1, 0], [2, 0]]);
    }

    // Verify lazy resource allocation with a GL call recorder. This checks
    // resource wiring, not GPU pixels or shader output.
    const textures = [], framebuffers = [], attachments = [];
    let bound = null;
    manager.setGL({
        createTexture() { const t = {}; textures.push(t); return t; },
        createFramebuffer() { const f = {}; framebuffers.push(f); return f; },
        bindFramebuffer(target, framebuffer) { bound = framebuffer; },
        framebufferTexture2D(target, attachment, type, texture) {
            attachments.push([bound, texture]);
        },
        bindTexture() {}, texImage2D() {}, texParameteri() {},
    });
    assert.equal(manager._maskTexture, undefined);
    const allocated = manager.getMaskRenderTexture();
    assert.equal(allocated.length, 3);
    assert.equal(textures.length, 3);
    assert.equal(framebuffers.length, 3);
    attachments.forEach(([framebuffer, texture], i) => {
        assert.equal(framebuffer, framebuffers[i]);
        assert.equal(texture, textures[i]);
    });
    assert.equal(attachments.length, 3);
    assert.equal(manager.getMaskRenderTexture(), allocated);
    assert.equal(textures.length, 3, 'reuse allocated mask textures');
}
check(MASK_COUNT).catch(error => { console.error(error); process.exitCode = 1; });
""".replace("MASK_COUNT", str(mask_count))
    result = run_node_stdin(
        node, script, cwd=PROJECT_ROOT, capture_output=True, timeout=10, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
