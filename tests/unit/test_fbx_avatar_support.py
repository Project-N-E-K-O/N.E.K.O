import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INDEX_HTML = ROOT / "templates" / "index.html"
FBX_MANAGER = ROOT / "static" / "fbx" / "fbx-manager.js"
FBX_INIT = ROOT / "static" / "fbx" / "fbx-init.js"
TRANSPORT_JS = ROOT / "static" / "jukebox" / "jukebox" / "transport.js"
LIVE2D_MODELS = ROOT / "main_routers" / "characters_router" / "live2d_models.py"
PAGE_CONFIG = ROOT / "main_routers" / "config_router" / "page_config.py"
WEB_APP = ROOT / "app" / "main_server" / "web_app.py"
STORAGE_ROOTS = ROOT / "utils" / "config_manager" / "storage_roots.py"
THREE_DIR = ROOT / "static" / "libs"

VENDORED_LOADER_FILES = (
    "three/addons/loaders/FBXLoader.js",
    "three/addons/curves/NURBSCurve.js",
    "three/addons/curves/NURBSUtils.js",
    "three/addons/libs/fflate.module.js",
)


def _node_binary():
    return shutil.which("node")


def test_fbx_loader_chain_is_vendored_under_the_addons_mount():
    """The importmap maps ``three/addons/`` at ``/static/libs/three/addons/``.

    FBXLoader only works if its own imports resolve through that same mount, so
    the two files it pulls in have to sit beside it at the paths it asks for.
    """
    for relative in VENDORED_LOADER_FILES:
        assert (ROOT / "static" / "libs" / relative).is_file(), relative

    loader = (THREE_DIR / "three" / "addons" / "loaders" / "FBXLoader.js").read_text(
        encoding="utf-8"
    )
    assert "from '../libs/fflate.module.js'" in loader
    assert "from '../curves/NURBSCurve.js'" in loader


def test_main_page_provides_the_fbx_container_and_loads_the_manager():
    source = INDEX_HTML.read_text(encoding="utf-8")
    assert 'id="fbx-container"' in source
    assert 'id="fbx-canvas"' in source
    assert "/static/fbx/fbx-manager.js" in source
    assert "/static/fbx/fbx-init.js" in source


def test_fbx_init_activates_only_for_live3d_fbx_and_reads_a_model_path():
    source = FBX_INIT.read_text(encoding="utf-8")
    assert "modelType !== 'live3d'" in source
    assert "subType !== 'fbx'" in source
    assert "fbxModel" in source
    assert "new window.FBXManager()" in source
    assert "manager.loadModel(modelPath" in source


def test_fbx_manager_exposes_the_animation_contract_jukebox_drives():
    source = FBX_MANAGER.read_text(encoding="utf-8")
    for member in (
        "async loadModel(",
        "async loadAnimation(",
        "playAnimation(",
        "pauseAnimation(",
        "stopAnimation(",
        "currentAnimationUrl",
    ):
        assert member in source, member
    assert "window.FBXManager = FBXManager" in source


def test_jukebox_routes_live3d_fbx_to_the_fbx_renderer():
    """Without this the fbx subtype falls through to the MMD branch."""
    source = TRANSPORT_JS.read_text(encoding="utf-8")
    assert "if (sub === 'fbx') return 'fbx';" in source


def test_backend_accepts_and_persists_an_fbx_avatar():
    source = LIVE2D_MODELS.read_text(encoding="utf-8")
    assert "data.get('fbx')" in source
    assert "data.get('fbx_animation')" in source
    assert "'/user_fbx/', '/static/fbx/', '/workshop/'" in source
    assert "'live3d_sub_type', 'fbx'" in source
    assert "'fbx', 'model_path', fbx_model" in source


def test_page_config_resolves_fbx_and_web_app_serves_it():
    page_config = PAGE_CONFIG.read_text(encoding="utf-8")
    assert "== 'fbx'" in page_config
    assert "def _resolve_fbx_path(" in page_config
    assert "FBX_STATIC_PATH" in page_config

    web_app = WEB_APP.read_text(encoding="utf-8")
    assert '"/user_fbx"' in web_app
    assert '"/user_fbx/animation"' in web_app
    assert "ensure_fbx_directory()" in web_app

    storage = STORAGE_ROOTS.read_text(encoding="utf-8")
    assert "self.fbx_dir = " in storage
    assert "def ensure_fbx_directory(" in storage


def test_resolve_fbx_path_accepts_real_files_and_rejects_traversal():
    sys.path.insert(0, str(ROOT))
    from main_routers.config_router import page_config
    from utils.config_manager import get_config_manager

    config_manager = get_config_manager()
    config_manager.ensure_fbx_directory()

    probe = ROOT / "static" / "fbx" / "unit_probe.fbx"
    probe.parent.mkdir(parents=True, exist_ok=True)
    probe.write_bytes(b"probe")
    user_probe = config_manager.fbx_dir / "unit_probe.fbx"
    user_probe.write_bytes(b"probe")
    try:
        assert page_config._resolve_fbx_path("/static/fbx/unit_probe.fbx", config_manager, "t") == "/static/fbx/unit_probe.fbx"
        assert page_config._resolve_fbx_path("/user_fbx/unit_probe.fbx", config_manager, "t") == "/user_fbx/unit_probe.fbx"
        assert page_config._resolve_fbx_path("/static/fbx/nope.fbx", config_manager, "t") == ""
        assert page_config._resolve_fbx_path("../etc/passwd", config_manager, "t") == ""
        # An absolute path outside the two known roots is passed through
        # untouched, exactly like _resolve_mmd_path and _resolve_vrm_path do:
        # those prefixes are served by other routes (workshop, imported packs).
        # Pinned so the three resolvers cannot drift apart silently.
        outside = "/workshop/some-pack/model.fbx"
        assert page_config._resolve_fbx_path(outside, config_manager, "t") == outside
        assert page_config._resolve_fbx_path(outside, config_manager, "t") == page_config._resolve_mmd_path(
            outside, config_manager, "t"
        )
    finally:
        probe.unlink(missing_ok=True)
        user_probe.unlink(missing_ok=True)


FBX_MANAGER_SCRIPT = r"""
import * as THREE from 'three';
import { readFileSync } from 'node:fs';

globalThis.window = globalThis;
globalThis.THREE = THREE;
globalThis.requestAnimationFrame = (fn) => setTimeout(fn, 0);
globalThis.cancelAnimationFrame = (id) => clearTimeout(id);

const container = { clientWidth: 800, clientHeight: 600, style: {}, classList: { add() {}, remove() {} } };
const canvas = { style: { setProperty() {} } };
globalThis.document = {
  getElementById: (id) => (id === 'fbx-container' ? container : id === 'fbx-canvas' ? canvas : null),
};

const source = readFileSync(process.env.FBX_MANAGER_PATH, 'utf8');
new Function('window', 'document', 'requestAnimationFrame', 'cancelAnimationFrame',
  source + '\nreturn window.FBXManager;')(
  globalThis, globalThis.document, globalThis.requestAnimationFrame, globalThis.cancelAnimationFrame
);

const results = [];
const check = (name, cond, extra) => results.push([name, !!cond, extra]);

const FBXManager = globalThis.FBXManager;
check('exported', typeof FBXManager === 'function');

const manager = new FBXManager();
manager.scene = new THREE.Scene();
manager.camera = new THREE.PerspectiveCamera(30, 800 / 600, 0.1, 100000);
manager.camera.position.set(0, 100, 300);
manager.renderer = { setSize() {}, setClearColor() {}, render() {}, dispose() {} };
manager.container = container;
manager.canvas = canvas;

const geometry = new THREE.BufferGeometry();
geometry.setAttribute('position', new THREE.Float32BufferAttribute([0, 0, 0, 0, 1, 0, 1, 1, 0], 3));
geometry.setAttribute('normal', new THREE.Float32BufferAttribute([0, 0, 1, 0, 0, 1, 0, 0, 1], 3));
const mesh = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial());
const bone = new THREE.Bone();
mesh.add(bone);

const clip = new THREE.AnimationClip('probe', 1.5, [
  new THREE.VectorKeyframeTrack('.position', [0, 1], [0, 0, 0, 1, 1, 1]),
]);

const buildObject = () => {
  const root = new THREE.Group();
  root.add(mesh);
  root.animations = [clip];
  return root;
};

manager._getLoader = async () => ({
  loadAsync: async () => buildObject(),
});

const model = await manager.loadModel('/static/fbx/probe.fbx');
check('loadModel returns object', !!model && model.isObject3D === true);
check('model added to scene', manager.scene.children.includes(model));
check('mixer created', !!manager.mixer);
check('actions bound', manager._actions.length === 1, manager._actions.length + ' actions');
check('url recorded', manager.currentAnimationUrl === '/static/fbx/probe.fbx');
check('camera fitted', manager.camera.position.z > 0);
check('model grounded', Math.abs(new THREE.Box3().setFromObject(model).min.y) < 1e-6);

check('play returns true', manager.playAnimation('idle') === true);
check('actions running', manager._actions.every((a) => a.isRunning()));
manager.pauseAnimation();
check('pause pauses', manager._actions.every((a) => a.paused === true));
manager.playAnimation('dance');
check('resume unpauses', manager._actions.every((a) => a.paused === false));

const before = manager.mixer.time;
manager.mixer.update(0.25);
check('mixer advances', manager.mixer.time > before);

const loaded = await manager.loadAnimation('/static/fbx/dance.fbx');
check('loadAnimation returns clips', loaded.clips.length === 1);
check('loadAnimation keeps mixer', loaded.mixer === manager.mixer);
check('loadAnimation updates url', manager.currentAnimationUrl === '/static/fbx/dance.fbx');

manager.stopAnimation();
check('stop clears actions', manager._actions.every((a) => !a.isRunning()));
check('stop clears url', manager.currentAnimationUrl === null);

let threw = false;
try {
  manager.currentModel = null;
  await manager.loadAnimation('/static/fbx/x.fbx');
} catch (e) {
  threw = true;
}
check('loadAnimation without model throws', threw);

manager.pauseRendering();
check('pauseRendering stops frames', manager._shouldRender === false);
manager.resumeRendering();
check('resumeRendering resumes', manager._shouldRender === true);

manager.dispose();
check('dispose marks disposed', manager._isDisposed === true);
check('dispose releases scene', manager.scene === null);

let pass = 0;
let fail = 0;
for (const [name, ok, extra] of results) {
  if (ok) { pass++; } else { fail++; console.log('  FAIL ' + name + (extra ? ' (' + extra + ')' : '')); }
}
console.log('RESULT ' + pass + ' passed ' + fail + ' failed');
process.exit(fail === 0 ? 0 : 1);
"""


@pytest.mark.plugin_unit
def test_fbx_manager_animation_flow_against_real_three(tmp_path):
    """Drive FBXManager through real three.js classes with a stubbed loader.

    The loader is the only fake: every Object3D, AnimationMixer, action and
    Box3 the manager touches is the real implementation, so this covers the
    manager's own state machine (bind, play, pause, stop, dispose) rather than
    three's parser, which the vendored file brings with it.
    """
    node = _node_binary()
    if node is None:
        pytest.skip("node is not available")

    modules = tmp_path / "node_modules" / "three"
    modules.mkdir(parents=True)
    for name in ("three.module.js", "three.core.js"):
        shutil.copyfile(THREE_DIR / name, modules / name)
    (modules / "package.json").write_text(
        json.dumps(
            {
                "name": "three",
                "version": "0.180.0",
                "type": "module",
                "main": "three.module.js",
                "exports": {".": "./three.module.js"},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "package.json").write_text('{"type": "module"}', encoding="utf-8")
    script = tmp_path / "fbx_manager_probe.mjs"
    script.write_text(FBX_MANAGER_SCRIPT, encoding="utf-8")

    completed = subprocess.run(
        [node, str(script)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        env={**__import__("os").environ, "FBX_MANAGER_PATH": str(FBX_MANAGER)},
    )
    import re
    match = re.search(r"RESULT (\d+) passed (\d+) failed", completed.stdout)
    assert match, completed.stdout
    passed, failed = int(match.group(1)), int(match.group(2))
    assert failed == 0, completed.stdout
    assert passed >= 20, completed.stdout
