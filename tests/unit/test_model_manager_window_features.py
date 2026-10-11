import json
import re
import shutil
from pathlib import Path

import pytest

from tests.node_harness import run_node_script


def run_model_manager_node(script: str) -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for model-manager JavaScript tests")
    run_node_script(node, script, check=True)


def test_raw_vrm_config_callers_encode_local_paths_once():
    character = Path("static/app/app-character.js").read_text(encoding="utf-8")
    start = character.index("if (/^\\/(?:user_vrm")
    encode_character = character[start:character.index("// 加载 VRM 模型", start)]
    init = Path("static/vrm/vrm-init.js").read_text(encoding="utf-8")
    start = init.index("const convertedPath =")
    encode_init = init[start:init.index("// 7.", start)]
    preview = Path("static/js/character_card_manager/model-previews.js").read_text(encoding="utf-8")
    start = preview.index("const modelUrl = /^")
    encode_preview = preview[start:preview.index("const result = await localVrmManager.loadModel", start)]
    run_model_manager_node(f"""
const assert = require('node:assert/strict');
for (const raw of ['/user_vrm/猫娘 #100%.VRM', '/user_vrm/a%20b.vrm', 'https://example.com/a%20b.vrm', '/api/models/current.vrm?token=abc#part']) {{
    const expected = raw.startsWith('/user_vrm/') ? raw.split('/').map(encodeURIComponent).join('/') : raw;
    let modelUrl = raw;
    {encode_character}
    assert.equal(modelUrl, expected);
    const window = {{convertVRMModelPath: value => value}};
    const newModelPath = raw;
    {{
        {encode_init}
        assert.equal(modelUrl, expected);
    }}
    {{
        const modelPath = raw;
        {encode_preview}
        assert.equal(modelUrl, expected);
    }}
}}
""")


def test_vrm_window_return_compares_decoded_url_identities_once():
    source = Path("static/vrm/vrm-init.js").read_text(encoding="utf-8")
    helpers = source[source.index("window._vrmPathUtils ="):source.index("/**\n * 应用 VRM 打光")]
    start = source.index("const currentModelUrl = window.vrmManager.currentModel?.url;")
    comparison = source[start:source.index("// 直接使用刚刚拉取的", start)]
    run_model_manager_node(f"""
const assert = require('node:assert/strict');
const window = {{vrmManager: {{currentModel: {{}}, loadModel: async () => {{ loads++; }}}}}};
{helpers}
let loads = 0;
(async () => {{
    for (const name of ['Avatar(1)', "Avatar!'()*", '猫娘 #100%', 'a%20b']) {{
        const jsUrl = '/user_vrm/' + encodeURIComponent(name + '.vrm');
        const pythonUrl = jsUrl.replace(/[!'()*]/g, c => '%' + c.charCodeAt(0).toString(16).toUpperCase());
        window.vrmManager.currentModel.url = pythonUrl;
        const modelUrl = jsUrl;
        {{ {comparison} }}
        assert.equal(loads, 0, name + ' must not reload on window return');
    }}
    window.vrmManager.currentModel.url = '/user_vrm/a%2520b.vrm';
    const modelUrl = '/user_vrm/a%20b.vrm';
    {{ {comparison} }}
    assert.equal(loads, 1, 'literal percent and space models must remain distinct');
    for (const [previous, next] of [
        ['/api/models/a%3Fb.vrm', '/api/models/a?b.vrm'],
        ['/api/models/a%23b.vrm', '/api/models/a#b.vrm'],
        ['/user_vrm/a%3Fb.vrm', '/user_vrm/a?b.vrm'],
        ['/user_vrm/a%23b.vrm', '/user_vrm/a#b.vrm'],
        ['/user_vrm/a%2Fb.vrm', '/user_vrm/a/b.vrm'],
        ['https://one.example/a.vrm', 'https://two.example/a.vrm'],
    ]) {{
        window.vrmManager.currentModel.url = previous;
        const modelUrl = next;
        const before = loads;
        {{ {comparison} }}
        assert.equal(loads, before + 1, 'distinct URL components must reload');
        window.vrmManager.currentModel.url = next;
        {{ {comparison} }}
        assert.equal(loads, before + 1, 'an unchanged custom URL must not reload');
    }}
}})().catch(error => {{ console.error(error); process.exit(1); }});
""")


def test_live3d_switch_selects_exact_raw_vrm_path_before_filename_fallback():
    source = Path("static/js/model_manager/page-controller.js").read_text(encoding="utf-8")
    start = source.index("const tryMatchVrm = () =>")
    matching = source[start:source.index("if (activeSubType === 'mmd')", start)]
    helpers = Path("static/js/model_manager/path-request-fullscreen.js").read_text(encoding="utf-8").split("const RequestHelper", 1)[0]
    run_model_manager_node(f"""
const assert = require('node:assert/strict');
{helpers}
const name = '猫娘 Avatar.vrm';
const _vrmPathSwitch = '/user_vrm/' + name;
const option = prefix => ({{value: prefix + encodeURIComponent(name), getAttribute: key =>
    key === 'data-path' ? prefix + name : key === 'data-filename' ? name : null}});
const vrmModelSelect = {{options: [option('/static/vrm/'), option('/user_vrm/')]}};
let changed = 0;
const dispatchModelManagerChange = () => {{changed++;}};
{matching}
assert.equal(tryMatchVrm(), true);
assert.equal(vrmModelSelect.value, '/user_vrm/' + encodeURIComponent(name));
assert.equal(changed, 1);
for (const rawPath of ['/user_vrm/a%20b.vrm', 'a%20b.vrm', '/user_vrm/a b.vrm', 'a b.vrm']) {{
    const filenames = ['a b.vrm', 'a%20b.vrm'];
    const options = filenames.map(filename => ({{value: '/user_vrm/' + encodeURIComponent(filename),
        getAttribute: key => key === 'data-path' ? '/user_vrm/' + filename : key === 'data-filename' ? filename : null}}));
    const expected = rawPath.split('/').pop();
    assert.equal(ModelPathHelper.findVrmOption(options, rawPath).getAttribute('data-filename'), expected);
}}
assert.equal({source.count("ModelPathHelper.findVrmOption(vrmModelSelect.options,")}, 2, 'both selection and restoration must use the same identity rule');
""")


MODEL_MANAGER_PART_NAMES = (
    "named-window-registration.js",
    "runtime-loaders.js",
    "dropdown-manager.js",
    "page-bridge.js",
    "card-face.js",
    "path-request-fullscreen.js",
    "page-controller.js",
    "background-model-drag.js",
    "window-lifecycle.js",
)


def read_model_manager_source() -> str:
    parts_dir = Path("static/js/model_manager")
    return "".join(
        (parts_dir / part_name).read_text(encoding="utf-8")
        for part_name in MODEL_MANAGER_PART_NAMES
    )


def test_vrm_mapping_uses_original_filename_after_url_encoding():
    helper_source = Path("static/js/model_manager/path-request-fullscreen.js").read_text(
        encoding="utf-8"
    ).split("const RequestHelper", 1)[0]
    controller = Path("static/js/model_manager/page-controller.js").read_text(encoding="utf-8")
    marker = "if (vrmManager && vrmManager.expression && modelPath)"
    mapping_block = marker + controller.split(marker, 1)[1].split("\n                }", 1)[0] + "\n}"
    script = f"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const context = vm.createContext({{}});
vm.runInContext({json.dumps(helper_source)}, context);
const mappingBlock = {json.dumps(mapping_block)};
for (const [modelPath, filename, expected] of [
    ['/user_vrm/Avatar%23100%25.vrm', 'Avatar#100%.vrm', 'Avatar#100%'],
    ['/user_vrm/a%2520b.VRM', 'a%20b.VRM', 'a%20b'],
    ['/user_vrm/a%252Fb.vrm', null, 'a%2Fb'],
    ['/user_vrm/My%20Avatar.vrm', null, 'My Avatar'],
    ['/user_vrm/猫娘.vrm', null, '猫娘'],
    ['/user_vrm/Avatar100%.vrm', null, 'Avatar100%'],
]) {{
    let actual;
    context.modelPath = modelPath;
    context.filename = filename;
    context.vrmManager = {{ expression: {{ loadMoodMap(name) {{ actual = name; }} }} }};
    vm.runInContext(mappingBlock, context);
    assert.equal(actual, expected);
}}
"""
    run_model_manager_node(script)


def test_vrm_selectors_keep_raw_config_paths_separate_from_fetch_urls():
    helper = Path('static/js/model_manager/path-request-fullscreen.js').read_text(encoding='utf-8').split('const RequestHelper', 1)[0]
    controller = Path('static/js/model_manager/page-controller.js').read_text(encoding='utf-8')
    loaders = [
        'async function loadVRMModels' + controller.split('async function loadVRMModels', 1)[1].split('// 更新VRM模型下拉菜单', 1)[0],
        'async function loadLive3DModels' + controller.split('async function loadLive3DModels', 1)[1].split('// 自动选择默认 Live3D 模型', 1)[0],
    ]
    save_block = controller.split('// VRM 子类型：转换 VRM 路径', 1)[1].split('\n', 1)[1].split('if (vrmAnimationSelect)', 1)[0]
    script = f"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const filenames = ['My Avatar.vrm', '猫娘.vrm', 'Avatar#100%.vrm', 'a b.vrm', 'a%20b.vrm', 'a%2Fb.VRM'];
const models = filenames.map(filename => ({{ filename, path: '/user_vrm/' + filename, url: '/user_vrm/' + encodeURIComponent(filename) }}));
function element() {{ return {{ dataset: {{}}, attributes: {{}}, setAttribute(k, v) {{ this.attributes[k] = v; }}, getAttribute(k) {{ return this.attributes[k]; }} }}; }}
(async () => {{
for (const loader of {json.dumps(loaders)}) {{
    const options = [];
    const context = vm.createContext({{
        document: {{ createElement: element }},
        vrmModelSelect: {{ appendChild(option) {{ options.push(option); }} }},
        vrmModelSelectBtn: null, mmdModelSelect: null,
        RequestHelper: {{ async fetchJson(url) {{ return {{ success: true, models: url.includes('/vrm/') ? models : [] }}; }} }},
        t: (_, fallback) => fallback, showStatus() {{}}, updateVRMModelDropdown() {{}}, updateVRMModelSelectButtonText() {{}}, console,
    }});
    vm.runInContext({json.dumps(helper)} + loader, context);
    await vm.runInContext(loader.includes('loadLive3DModels') ? 'loadLive3DModels()' : 'loadVRMModels()', context);
    assert.equal(options.length, models.length);
    options.forEach((option, i) => {{
        assert.equal(option.value, models[i].url);
        assert.equal(option.getAttribute('data-path'), models[i].path);
        context.selectedOpt = option; context.modelData = {{}}; context.modelName = option.value; context.currentModelInfo = null;
        vm.runInContext('{{' + {json.dumps(save_block)} + '}}', context);
        assert.equal(context.modelData.vrm, models[i].path);
    }});
}}
}})().catch(error => {{ console.error(error); process.exit(1); }});
"""
    run_model_manager_node(script)


def test_vrm_preferences_match_raw_paths_without_aliasing_percent_names():
    source = Path('static/vrm/vrm-core.js').read_text(encoding='utf-8')
    path_method = source.split('class VRMCore {', 1)[1].split('constructor(', 1)[0]
    matching = 'const normalizePath =' + source.split('const normalizePath =', 1)[1].split('\n                }\n                } catch', 1)[0]
    preference_object = 'const preferences = {' + source.split('const preferences = {', 1)[1].split('\n            };', 1)[0] + '\n}; preferences;'
    script = f"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const context = vm.createContext({{ URL, window: {{location: {{origin: 'http://localhost'}}}}, position: {{x: 0, y: 0, z: 0}}, scale: {{x: 1, y: 1, z: 1}} }});
vm.runInContext('class VRMCore {{' + {json.dumps(path_method)} + '}}', context);
assert.equal(vm.runInContext("VRMCore.preferencePathFromUrl('https://external.example/user_vrm/a%20b.vrm')", context), 'https://external.example/user_vrm/a%20b.vrm');
assert.equal(vm.runInContext("VRMCore.preferencePathFromUrl('http://localhost/user_vrm/a%20b.vrm')", context), '/user_vrm/a b.vrm');
assert.equal(vm.runInContext("VRMCore.preferencePathFromUrl('https://[invalid/user_vrm/a.vrm')", context), 'https://[invalid/user_vrm/a.vrm');
for (const filename of ['My Avatar.vrm', '猫娘.vrm', 'Avatar#100%.vrm', 'a b.vrm', 'a%20b.vrm', 'a%2Fb.vrm']) {{
    const raw = '/user_vrm/' + filename;
    context.modelUrl = '/user_vrm/' + encodeURIComponent(filename);
    const expected = {{ model_path: raw }};
    context.modelsArray = [{{model_path: '/user_vrm/a%20b.vrm'}}, {{model_path: '/user_vrm/a b.vrm'}}, expected];
    if (filename === 'a b.vrm') context.modelsArray[1] = expected;
    if (filename === 'a%20b.vrm') context.modelsArray[0] = expected;
    vm.runInContext('{{' + {json.dumps(matching)} + '}}', context);
    assert.equal(context.preferences, expected);
    context.modelPath = context.modelUrl;
    const saved = vm.runInContext('{{' + {json.dumps(preference_object)} + '}}', context);
    assert.equal(saved.model_path, raw);
}}
"""
    run_model_manager_node(script)


def test_vrm_emotion_selector_has_one_entry_per_shared_stem():
    source = Path('static/js/vrm_emotion_manager.js').read_text(encoding='utf-8')
    loader = 'async function loadModelList' + source.split('async function loadModelList', 1)[1].split('// 从下拉框选择模型', 1)[0]
    script = f"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const options = [], items = [];
const models = [{{name: 'Avatar', filename: 'Avatar.vrm'}}, {{name: 'Avatar', filename: 'Avatar.VRM'}}, {{name: 'Other', filename: 'Other.vrm'}}];
const context = vm.createContext({{
    fetch: async () => ({{ok: true, json: async () => ({{success: true, models}})}}),
    document: {{createElement: () => ({{dataset: {{}}, setAttribute() {{}}, addEventListener() {{}}}})}},
    modelSelect: {{appendChild(option) {{ options.push(option); }}}}, modelSingleselectOptions: {{appendChild(item) {{ items.push(item); }}}},
    modelSingleselectText: {{}}, t: (_, fallback) => fallback, console,
    showStatus(message) {{ throw Error(message); }},
}});
vm.runInContext({json.dumps(loader)}, context);
(async () => {{
    await vm.runInContext('loadModelList()', context);
    assert.deepEqual(options.map(o => o.value), ['Avatar', 'Other']);
    assert.equal(JSON.parse(options[0].dataset.info).filename, 'Avatar.vrm');
    assert.equal(items.length, 2);
}})().catch(error => {{ console.error(error); process.exit(1); }});
"""
    run_model_manager_node(script)


def test_vrm_catalog_preview_preserves_selected_idle_and_stops_preview_rotation():
    source = Path("static/js/model_manager/page-controller.js").read_text(
        encoding="utf-8"
    )
    preview = source.split("async function playSelectedVrmAnimationOption", 1)[1].split(
        "// VRM动作选择按钮点击事件",
        1,
    )[0]
    assert re.search(
        r"vrmMotionCatalogPlayer\.setSavedRestAnimations\(\s*"
        r"getSelectedIdleAnimations\('vrm-idle-animation-multiselect'\)\s*\);",
        preview,
    )
    assert re.search(
        r"vrmMotionCatalogPlayer\.playAsset\(\s*assetId\s*,\s*\{\s*"
        r"scheduleNext:\s*false\s*\}\s*\)",
        preview,
    )
    preview_start = "isVrmAnimationPlaying = true;"
    preview_play = "const played = await vrmMotionCatalogPlayer.playAsset"
    assert preview_start in preview
    assert preview.index(preview_start) < preview.index(preview_play)

    idle_switch = source.split("async function _playIdleAnimation", 1)[1].split(
        "async function restoreVrmIdleAnimation",
        1,
    )[0]
    assert "const idlePlaybackStarted = await vrmManager.playVRMAAnimation" in idle_switch
    assert "if (idlePlaybackStarted !== true) return;" in idle_switch


def test_main_vrm_idle_rotation_ignores_cancelled_playback_completion():
    source = Path("static/vrm/vrm-init.js").read_text(encoding="utf-8")
    idle_rotation = source.split("function _startVrmIdleRotation", 1)[1].split(
        "function _stopVrmIdleRotation", 1
    )[0]

    playback = "const played = await mgr.playVRMAAnimation"
    stale_guard = "if (played !== true) return;"
    state_update = "_vrmIdleLastUrl = url;"
    assert playback in idle_rotation
    assert stale_guard in idle_rotation
    assert idle_rotation.index(playback) < idle_rotation.index(stale_guard)
    assert idle_rotation.index(stale_guard) < idle_rotation.index(state_update)


def test_vrm_catalog_preview_pause_does_not_resume_catalog_base_motion():
    source = Path("static/js/model_manager/page-controller.js").read_text(
        encoding="utf-8"
    )
    play_button_handler = source.split("if (playVrmAnimationBtn) {", 1)[1].split(
        "// ======================== MMD 模型/动画列表",
        1,
    )[0]
    pause_branch = play_button_handler.split("if (isVrmAnimationPlaying) {", 1)[
        1
    ].split("} else {", 1)[0]

    assert (
        "vrmMotionCatalogPlayer.cancel('model_manager_pause', { resume: false });"
        in pause_branch
    )
    assert "vrmManager.stopVRMAAnimation();" in pause_branch
    assert pause_branch.index("cancel('model_manager_pause'") < pause_branch.index(
        "vrmManager.stopVRMAAnimation();"
    )


def test_vrm_animation_picker_separates_catalog_and_direct_playback():
    source = Path("static/js/model_manager/page-controller.js").read_text(
        encoding="utf-8"
    )
    playback = source.split(
        "async function playSelectedVrmAnimationOption",
        1,
    )[1].split("// VRM动作选择按钮点击事件", 1)[0]

    assert "if (assetId && isCatalogMotion)" in playback
    assert "if (played !== true)" in playback
    catalog_branch = playback.split("if (assetId && isCatalogMotion)", 1)[1].split(
        "} else {", 1
    )[0]
    assert "stopIdleRotation('vrm');" in catalog_branch
    assert catalog_branch.index("stopIdleRotation('vrm');") < catalog_branch.index(
        "vrmManager.stopVRMAAnimation();"
    )
    assert "model_manager_direct_playback" in playback
    assert playback.index("model_manager_direct_playback") < playback.index(
        "vrmManager.playVRMAAnimation"
    )


def test_vrm_animation_picker_persists_official_gzip_only_from_allowed_directory():
    source = Path("static/js/model_manager/page-controller.js").read_text(
        encoding="utf-8"
    )

    assert "static\\/vrm\\/animation|user_vrm\\/animation" in source
    assert "isCatalogMotion && !isPersistableAnimation" in source


def test_vrm_catalog_options_preserve_motion_pack_urls():
    source = Path("static/js/model_manager/page-controller.js").read_text(
        encoding="utf-8"
    )
    option_build = source.split(
        "const isCatalogMotion = anim.systemMotion === true;", 1
    )[1].split("vrmAnimationSelect.appendChild(option);", 1)[0]

    assert "const finalUrl = isCatalogMotion" in option_build
    assert "? animPath" in option_build
    assert ": ModelPathHelper.vrmToUrl(animPath, 'animation');" in option_build


def test_vrm_saved_legacy_url_normalization_ignores_query_and_hash_suffixes():
    source = Path("static/js/model_manager/page-controller.js").read_text(
        encoding="utf-8"
    )
    helper = source.split("function normalizeBundledVrmAnimationUrl", 1)[1].split(
        "async function loadVrmModelWithCatalogReset", 1
    )[0]

    assert "\\.vrma(?:[?#]|$)" in helper
    assert "decodeURIComponent(assetName)" in helper
    assert "'/static/vrm/animation/' + assetName + '.vrma.gz'" in helper

    function_source = "function normalizeBundledVrmAnimationUrl" + helper
    script = f"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const context = {{}};
vm.runInNewContext({json.dumps(function_source)}, context);
const available = new Set([
  '/static/vrm/animation/比 V 手势.vrma.gz',
  '/static/vrm/animation/wait03.vrma.gz'
]);
assert.equal(
  context.normalizeBundledVrmAnimationUrl(
    '/static/vrm/animation/%E6%AF%94%20V%20%E6%89%8B%E5%8A%BF.vrma?legacy=1',
    available
  ),
  '/static/vrm/animation/比 V 手势.vrma.gz'
);
assert.equal(
  context.normalizeBundledVrmAnimationUrl(
    '/static/vrm/animation/wait03.vrma#saved',
    available
  ),
  '/static/vrm/animation/wait03.vrma.gz'
);
assert.equal(
  context.normalizeBundledVrmAnimationUrl(
    '/static/vrm/animation/custom-idle.vrma?keep=1',
    available
  ),
  '/static/vrm/animation/custom-idle.vrma?keep=1'
);
"""
    run_model_manager_node(script)


def test_vrm_catalog_player_resets_before_loading_a_new_model():
    source = Path("static/js/model_manager/page-controller.js").read_text(
        encoding="utf-8"
    )
    load_block = source.split("// 在加载新模型前，显式停止之前的动作并清理", 1)[1].split(
        "// 加载新模型后，重置播放状态", 1
    )[0]
    assert "await loadVrmModelWithCatalogReset(" in load_block

    start = source.index("async function loadVrmModelWithCatalogReset")
    end = source.index("\n    function mergeVrmAnimationLists", start)
    function_source = source[start:end]
    script = f"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const context = {{ vrmAnimationPlaybackRequestId: 7 }};
vm.runInNewContext({json.dumps(function_source)}, context);
(async function () {{
  const calls = [];
  const catalogPlayer = {{
    cancel(reason, options) {{ calls.push(['cancel', reason, options.resume]); }}
  }};
  const manager = {{
    async loadModel(url) {{ calls.push(['load', url]); return 'loaded'; }}
  }};
  const result = await context.loadVrmModelWithCatalogReset(
    catalogPlayer,
    manager,
    '/user_vrm/model.vrm',
    {{ addShadow: false }}
  );
  assert.equal(result, 'loaded');
  assert.equal(context.vrmAnimationPlaybackRequestId, 8);
  assert.equal(calls.length, 2);
  assert.deepEqual(calls[0], ['cancel', 'model_manager_model_load', false]);
  assert.deepEqual(calls[1], ['load', '/user_vrm/model.vrm']);
}})().catch(function (error) {{ console.error(error); process.exit(1); }});
"""
    run_model_manager_node(script)


def test_vrm_preview_ignores_stale_playback_completions():
    source = Path("static/js/model_manager/page-controller.js").read_text(
        encoding="utf-8"
    )
    assert "let vrmAnimationPlaybackRequestId = 0;" in source
    assert source.count("if (playbackRequestId !== vrmAnimationPlaybackRequestId) return;") >= 4
    playback = source.split(
        "async function playSelectedVrmAnimationOption", 1
    )[1].split("// VRM动作选择按钮点击事件", 1)[0]
    assert "const requestIsCurrent = () =>" in playback
    assert "if (!requestIsCurrent()) return false;" in playback
    assert playback.index("vrmManager.stopVRMAAnimation()") < playback.index(
        "await loadVrmMotionCatalog()"
    )
    assert playback.index("await loadVrmMotionCatalog()") < playback.index(
        "if (!requestIsCurrent()) return false;"
    )
    assert len(re.findall(
        r"playSelectedVrmAnimationOption\(\s*selectedOption,\s*playbackRequestId\s*\)",
        source,
    )) == 3  # function declaration plus both callers


def test_vrm_catalog_hold_pose_remains_stoppable():
    source = Path("static/js/model_manager/page-controller.js").read_text(
        encoding="utf-8"
    )
    playback = source.split(
        "async function playSelectedVrmAnimationOption", 1
    )[1].split("// VRM动作选择按钮点击事件", 1)[0]

    assert "['loop', 'hold'].includes(" in playback


def test_static_asset_version_tracks_vrm_motion_player():
    source = Path("main_routers/pages_router.py").read_text(encoding="utf-8")
    assert '_PROJECT_ROOT / "static/vrm/motion/player.js"' in source


def test_avatar_model_manager_popup_opens_fullscreen():
    source = Path("static/avatar/avatar-ui-popup.js").read_text(encoding="utf-8")

    assert "function buildAvatarFullscreenWindowFeatures()" in source
    assert "screenRef.availWidth || screenRef.width" in source
    assert "screenRef.availHeight || screenRef.height" in source
    assert "features = buildAvatarFullscreenWindowFeatures();" in source
    assert "openModelManagerWindow(finalUrl, windowName, features);" in source
    assert "window.handleHideMainUI()" not in source


def test_yui_model_manager_handoff_opens_fullscreen():
    source = Path("static/tutorial/yui-guide/page-handoff.js").read_text(encoding="utf-8")

    assert "function buildFullscreenWindowFeatures()" in source
    assert "function isModelManagerPageUrl(openUrl)" in source
    assert "if (isModelManagerPageUrl(openUrl))" in source
    assert "return buildFullscreenWindowFeatures();" in source
    start = source.index("function openModelManagerPage(")
    end = source.index("\n    function ", start + len("function openModelManagerPage("))
    model_manager_block = source[start:end]
    assert "buildFullscreenWindowFeatures()" in model_manager_block
    assert "{ keepMainUIVisible: true }" in model_manager_block


def test_model_manager_hides_main_model_only_while_fully_covered():
    model_manager_source = read_model_manager_source()
    interpage_source = Path(
        "static/app/app-interpage/bootstrap-resources-and-model-reload.js"
    ).read_text(encoding="utf-8")
    overlap_start = interpage_source.index("function refreshModelManagerWindowOverlap()")
    overlap_end = interpage_source.index(
        "function scheduleModelManagerWindowOverlapRefresh()", overlap_start
    )
    overlap_body = interpage_source[overlap_start:overlap_end]
    overlap_style_start = interpage_source.index(
        "function ensureModelManagerOverlapHiddenStyle()"
    )
    overlap_style_end = interpage_source.index(
        "function setModelManagerOverlapModelHidden(", overlap_style_start
    )
    overlap_style_body = interpage_source[overlap_style_start:overlap_style_end]
    screen_rect_start = interpage_source.index(
        "function getModelManagerActiveModelScreenRect()"
    )
    screen_rect_end = interpage_source.index(
        "function refreshModelManagerWindowOverlap()", screen_rect_start
    )
    screen_rect_body = interpage_source[screen_rect_start:screen_rect_end]
    client_rect_start = interpage_source.index(
        "function getModelManagerActiveModelClientRect("
    )
    client_rect_end = interpage_source.index(
        "function getModelManagerBrowserContentScreenOrigin()", client_rect_start
    )
    client_rect_body = interpage_source[client_rect_start:client_rect_end]
    reload_success_start = interpage_source.index("if (reloadSucceeded) {")
    reload_success_end = interpage_source.index(
        "} else {", reload_success_start
    )
    reload_success_body = interpage_source[
        reload_success_start:reload_success_end
    ]

    assert "model_manager_window_state" in model_manager_source
    assert "getModelManagerWindowScreenBounds" in model_manager_source
    assert "nekoModelManagerVisibility" in model_manager_source
    assert "document.hasFocus()" in model_manager_source
    assert "const MODEL_MANAGER_VISIBILITY_HEARTBEAT_MS = 400;" in model_manager_source
    assert "window.sendMessageToMainPage('model_manager_window_state'" in model_manager_source
    assert model_manager_source.count("if (quiet) return;") >= 1
    assert (
        "return modelManagerRectFullyCovers(state.bounds, modelBounds);"
        in overlap_body
    )
    assert "clipModelManagerClientRectToViewport" in interpage_source
    assert "getModelManagerActiveModelScreenRect" in interpage_source
    assert "modelManagerCachedModelClientBounds" in interpage_source
    assert (
        "getModelManagerActiveModelClientRect(modelManagerOverlapHidden)"
        in screen_rect_body
    )
    assert "isModelManagerActiveModelDragging" not in interpage_source
    assert "configuredModelType === 'live3d'" in client_rect_body
    assert "activeModelType === 'live2d'" in client_rect_body
    assert "activeModelType === 'vrm'" in client_rect_body
    assert "activeModelType === 'mmd'" in client_rect_body
    assert "activeModelType === 'pngtuber'" in client_rect_body
    assert "setModelManagerOverlapModelHidden(shouldHide);" in overlap_body
    assert "setModelManagerOverlapModelHidden(false);" in overlap_body
    assert "I.handleHideMainUI(" not in overlap_body
    assert "I.handleShowMainUI(" not in overlap_body
    assert "#live2d-container" in overlap_style_body
    assert "#pngtuber-container" in overlap_style_body
    assert "#react-chat-window-overlay" not in overlap_style_body
    assert "-floating-buttons" not in overlap_style_body
    assert "-lock-icon" not in overlap_style_body
    assert "display: none" not in overlap_style_body
    assert "scheduleModelManagerWindowOverlapRefresh()" in interpage_source
    assert (
        "if (_isModelHostPage()) {\n"
        "        I.yuiGuideInterpageResources.setInterval("
        "refreshModelManagerWindowOverlap, 500);\n"
        "    }"
    ) in interpage_source
    assert "function invalidateModelManagerOverlapBounds()" in interpage_source
    assert "invalidateModelManagerOverlapBounds();" in reload_success_body
    assert "mainUIHideOwners = Object.create(null)" in interpage_source
    assert "delete mainUIHideOwners[getMainUIHideOwner(options)]" in interpage_source
    assert overlap_body.index("if (!visibleModelManagerStates.length)") < overlap_body.index(
        "getModelManagerActiveModelScreenRect()"
    )


def test_model_manager_uses_one_non_focusing_window_instance():
    model_manager_source = read_model_manager_source()
    parameter_editor_source = Path(
        "static/js/live2d_parameter_editor.js"
    ).read_text(encoding="utf-8")
    common_dialogs = Path("static/common_dialogs.js").read_text(encoding="utf-8")
    character_manager = Path(
        "static/js/character_card_manager/character-data-and-transfer.js"
    ).read_text(encoding="utf-8")
    tutorial_handoff = Path("static/tutorial/yui-guide/page-handoff.js").read_text(
        encoding="utf-8"
    )
    reuse_start = character_manager.index("if (reusedModelManagerWindow)")
    reuse_end = character_manager.index(
        "window._openSettingsWindows[url] = popup;", reuse_start
    )
    reuse_body = character_manager[reuse_start:reuse_end]
    cached_reuse_start = character_manager.index(
        "if (existingWindow && !existingWindow.closed)"
    )
    cached_reuse_end = character_manager.index(
        "delete window._openSettingsWindows[url];", cached_reuse_start
    )
    cached_reuse_body = character_manager[cached_reuse_start:cached_reuse_end]
    registration_start = model_manager_source.index(
        "(function registerModelManagerNamedWindow()"
    )
    registration_end = model_manager_source.index("})();", registration_start) + len(
        "})();"
    )
    registration_body = model_manager_source[registration_start:registration_end]
    model_manager_template = Path("templates/model_manager.html").read_text(
        encoding="utf-8"
    )
    parameter_editor_template = Path(
        "templates/live2d_parameter_editor.html"
    ).read_text(encoding="utf-8")
    send_start = model_manager_source.index("function sendMessageToMainPage(")
    send_end = model_manager_source.index(
        "function isModelManagerPopupWindow()", send_start
    )
    send_body = model_manager_source[send_start:send_end]

    assert "MODEL_MANAGER_SINGLETON_WINDOW_NAME" in common_dialogs
    assert "pathname === '/model_manager' || pathname === '/l2d'" in common_dialogs
    assert "requestOpenedWindowRestoreIfMinimized(existingWindow)" in common_dialogs
    assert "if (!isModelManager) requestOpenedWindowRestore(newWindow);" in common_dialogs
    assert "neko:restore-window-if-minimized" in common_dialogs
    assert "window.open(url, '_blank'" not in character_manager
    assert "requestOpenedWindowRestoreIfMinimized(existingWindow)" in character_manager
    assert "targetWindow.document.hidden === true" in common_dialogs
    assert "if (!hasNativeRestoreBridge)" in common_dialogs
    assert "onReuse: () => { reusedModelManagerWindow = true; }" in character_manager
    assert "await rollbackAutoCreatedCatgirl(form);" in reuse_body
    assert (
        "await rollbackAutoCreatedCatgirl(form, form._autoCreatedDetachedName);"
        in cached_reuse_body
    )
    assert "form._autoCreatedDependentPopup = existingWindow" in cached_reuse_body
    assert "neko:named-window:" in registration_body
    assert "neko:named-window-focus:" in registration_body
    assert "window.localStorage.setItem(registryKey" in registration_body
    assert "setInterval(markModelManagerNamedWindowActive, 1000)" in registration_body
    assert (
        "window.opener === null || window.name !== MODEL_MANAGER_SINGLETON_WINDOW_NAME"
        in registration_body
    )
    assert "window.addEventListener('storage'" in registration_body
    assert "window.addEventListener('pageshow', () => {" in registration_body
    assert "window.addEventListener('pagehide', () => {" in registration_body
    assert "stopModelManagerVisibilityTracking();" in registration_body
    assert "stopModelManagerNamedWindowRegistration();" in registration_body
    assert "startModelManagerNamedWindowRegistration();" in registration_body
    assert "startModelManagerVisibilityTracking();" in registration_body
    assert "publishModelManagerWindowState(false);" in registration_body
    assert "window.addEventListener('unload'" not in registration_body
    assert "data.windowName !== MODEL_MANAGER_SINGLETON_WINDOW_NAME" in registration_body
    assert "api.restoreIfMinimized()" in registration_body
    assert "if (document.hidden === true) window.focus();" in registration_body
    assert "named-window-registration.js" in model_manager_template
    assert "named-window-registration.js" in parameter_editor_template
    parameter_editor_send_start = parameter_editor_source.index(
        "function sendMessageToMainPage("
    )
    parameter_editor_send_end = parameter_editor_source.index(
        "// 翻译辅助函数", parameter_editor_send_start
    )
    parameter_editor_send_body = parameter_editor_source[
        parameter_editor_send_start:parameter_editor_send_end
    ]
    assert (
        "const quiet = action === 'model_manager_window_state';"
        in parameter_editor_send_body
    )
    assert (
        parameter_editor_send_body.index("if (quiet) return;")
        < parameter_editor_send_body.index(
            "localStorage.setItem('nekopage_message'"
        )
    )
    assert "if (!quiet) {" in parameter_editor_send_body
    assert "function isModelManagerHostPageWindow(targetWindow)" in send_body
    assert (
        "if (quiet && isModelManagerHostPageWindow(window.opener)) return;"
        in send_body
    )
    assert (
        send_body.index("isModelManagerHostPageWindow(window.opener)")
        < send_body.index("localStorage.setItem('nekopage_message'")
    )
    assert "if (!isModelManagerPageUrl(targetUrl))" in tutorial_handoff
    assert "pathname === '/model_manager' || pathname === '/l2d'" in tutorial_handoff
    assert "handleHideMainUI({ owner: 'yui-page-handoff' })" in tutorial_handoff
    assert "handleShowMainUI({ owner: 'yui-page-handoff' })" in tutorial_handoff


def test_voice_clone_api_settings_uses_shared_named_window():
    source = Path("static/js/voice_clone.js").read_text(encoding="utf-8")
    common_source = Path("static/common_dialogs.js").read_text(encoding="utf-8")
    open_api_settings = source[source.index("function openApiSettings("):source.index("function openApiSettingsKeyBook(")]
    open_api_settings_key_book = source[source.index("function openApiSettingsKeyBook("):source.index("// 安全地解析 fetch 响应")]

    assert "function buildApiKeySettingsWindowFeatures(width = 1240, height = 940)" in common_source
    assert "window.buildApiKeySettingsWindowFeatures = buildApiKeySettingsWindowFeatures;" in common_source
    assert "const focusKeyBook = !!(options && options.focusKeyBook);" in open_api_settings
    assert "const url = focusKeyBook ? '/api_key?focus=key_book' : '/api_key';" in open_api_settings
    assert "const windowName = 'neko_api_key';" in open_api_settings
    assert "window.buildApiKeySettingsWindowFeatures()" in open_api_settings
    assert "window.openOrFocusWindow(url, windowName, features)" in open_api_settings
    assert "window.open(url, windowName, features)" in open_api_settings
    assert "win.focus()" in open_api_settings
    assert "function notifyApiSettingsKeyBookFocus(win)" in source
    assert "win.postMessage({ type: 'focus_api_key_book' }, window.location.origin);" in source
    assert "notifyApiSettingsKeyBookFocus(win);" in open_api_settings
    assert "openApiSettings({ focusKeyBook: true });" in open_api_settings_key_book
    assert "'apiSettings'" not in open_api_settings
    assert "width=820,height=700" not in source
