import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LIVE2D_MODEL_PATH = PROJECT_ROOT / "static" / "live2d" / "live2d-model.js"
LIVE2D_EMOTION_PATH = PROJECT_ROOT / "static" / "live2d" / "live2d-emotion.js"
PARAMETER_EDITOR_PATH = PROJECT_ROOT / "static" / "js" / "live2d_parameter_editor.js"
APP_INTERPAGE_PATH = PROJECT_ROOT / "static" / "app" / "app-interpage.js"
MAO_PRO_MODEL_PATH = PROJECT_ROOT / "static" / "mao_pro" / "mao_pro.model3.json"


def _run_node_harness(script: str) -> subprocess.CompletedProcess[str]:
    node_executable = shutil.which("node")
    if node_executable is None:
        pytest.skip("node not found")
    return subprocess.run(
        [node_executable, "-"],
        input=script,
        text=True,
        capture_output=True,
        cwd=PROJECT_ROOT,
        timeout=10,
        check=False,
    )


def _manager_harness(body: str) -> str:
    return textwrap.dedent(
        f"""
        const assert = require('node:assert');
        const fs = require('node:fs');
        const vm = require('node:vm');
        const context = {{
          console: {{ log() {{}}, warn() {{}}, error() {{}}, groupCollapsed() {{}}, groupEnd() {{}} }},
          window: {{ LIPSYNC_PARAMS: ['ParamMouthOpenY', 'ParamMouthForm'] }},
          Live2DManager: function Live2DManager() {{}},
          fetch: async () => {{ throw new Error('unexpected fetch'); }},
          performance: {{ now() {{ return 0; }} }},
          setTimeout, clearTimeout, setInterval, clearInterval,
        }};
        context.global = context;
        context.window.Live2DManager = context.Live2DManager;
        vm.createContext(context);
        vm.runInContext(fs.readFileSync({json.dumps(str(LIVE2D_MODEL_PATH))}, 'utf8'), context);
        vm.runInContext(fs.readFileSync({json.dumps(str(LIVE2D_EMOTION_PATH))}, 'utf8'), context);
        {body}
        """
    )


def _extract_js_function(source: str, name: str) -> str:
    start_candidates = [
        source.find(f"function {name}"),
        source.find(f"async function {name}"),
    ]
    start = min(candidate for candidate in start_candidates if candidate >= 0)
    signature_end = source.index(")", start)
    brace_start = source.index("{", signature_end)
    depth = 0
    for index in range(brace_start, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError(f"unterminated JavaScript function: {name}")


def test_parameter_editor_mode_suppresses_idle_and_saved_parameter_overlay():
    model_source = LIVE2D_MODEL_PATH.read_text(encoding="utf-8")
    editor_source = PARAMETER_EDITOR_PATH.read_text(encoding="utf-8")
    configure_start = model_source.index("Live2DManager.prototype._configureLoadedModel")
    configure_end = model_source.index("Live2DManager.prototype._applyTextureQuality", configure_start)
    configure_source = model_source[configure_start:configure_end]

    assert "parameterEditingMode: true" in editor_source
    assert "dragEnabled: true" in editor_source
    assert "wheelEnabled: true" in editor_source
    assert "options.suppressInitialIdle === true || this._parameterEditingMode" in model_source
    assert "hasEffectiveParameters && this._parameterEditingMode !== true" in model_source
    assert "shouldApplyPersistentExpressions = this._parameterEditingMode !== true" in model_source
    assert "if (!suppressInitialIdle)" in model_source
    assert "motionManager.stopAllMotions()" in configure_source
    assert "expressionManager.stopAllExpressions()" in configure_source
    assert configure_source.count("this._applyEffectiveModelParameters(") == 1


def test_parameter_editor_saves_draft_instead_of_runtime_core_values():
    source = PARAMETER_EDITOR_PATH.read_text(encoding="utf-8")
    save_start = source.index("if (saveBtn) {\n    saveBtn.addEventListener")
    save_source = source[save_start : source.index("// 初始化", save_start)]

    assert "currentParameters" not in source
    assert "draftParameters[paramId] = clampedValue" in source
    assert "draftParameters[parameter.key] = resetValue" in source
    assert "buildParametersFromDraft(" in save_source
    assert "draftParameters," in save_source
    assert "coreModel.getParameterValueByIndex(i)" not in save_source

    build_function = _extract_js_function(source, "buildParametersFromDraft")
    script = textwrap.dedent(
        f"""
        const assert = require('node:assert');
        {build_function}
        const draft = {{ ParamAllColor1: 0.8, ParamAllColor2: 0.6 }};
        const runtimeCoreValues = {{ ParamAllColor1: 0.8, ParamAllColor2: 0.6 }};
        for (let frame = 0; frame < 30; frame += 1) {{
          runtimeCoreValues.ParamAllColor1 = frame / 100;
          runtimeCoreValues.ParamAllColor2 = 1 - frame / 100;
        }}
        const saved = buildParametersFromDraft(draft, {{ ParamAllColor1: 0, ParamAllColor2: 1 }}, {{
          _isEyeBlinkParamId() {{ return false; }},
        }});
        assert.deepStrictEqual(saved, {{ ParamAllColor1: 0.8, ParamAllColor2: 0.6 }});
        assert.notStrictEqual(saved.ParamAllColor1, runtimeCoreValues.ParamAllColor1);
        """
    )
    result = _run_node_harness(script)
    assert result.returncode == 0, result.stderr


def test_user_preference_parameters_override_model_directory_parameters():
    script = _manager_harness(
        """
        const manager = new context.Live2DManager();
        const effective = manager._mergeEffectiveModelParameters(
          { ParamAllColor1: 0.1, ParamAllColor2: 0.2, ParamHair: 0.3 },
          { ParamAllColor1: 0.8, ParamAllColor2: 0.9 },
        );
        assert.deepStrictEqual(
          JSON.parse(JSON.stringify(effective)),
          { ParamAllColor1: 0.8, ParamAllColor2: 0.9, ParamHair: 0.3 },
        );
        """
    )
    result = _run_node_harness(script)
    assert result.returncode == 0, result.stderr


def test_cubism4_parameter_catalog_uses_official_ids_and_model_defaults():
    script = _manager_harness(
        """
        const manager = new context.Live2DManager();
        const ids = [
          { getString() { return { s: 'ParamHair_Color1' }; } },
          'ParamClothes_Color1',
        ];
        const coreModel = {
          _parameterIds: ids,
          parameters: { defaultValues: [0.25, 0.5] },
          getParameterCount() { return ids.length; },
          getParameterIndex(id) {
            return ['ParamHair_Color1', 'ParamClothes_Color1'].indexOf(id);
          },
        };

        const catalog = manager._buildModelParameterCatalog(coreModel);
        assert.deepStrictEqual(
          JSON.parse(JSON.stringify(catalog)),
          [
            { index: 0, id: 'ParamHair_Color1', key: 'ParamHair_Color1', defaultValue: 0.25 },
            { index: 1, id: 'ParamClothes_Color1', key: 'ParamClothes_Color1', defaultValue: 0.5 },
          ],
        );
        """
    )
    result = _run_node_harness(script)
    assert result.returncode == 0, result.stderr


def test_parameter_catalog_reuses_all_supported_default_value_sources():
    script = _manager_harness(
        """
        const manager = new context.Live2DManager();
        const ids = ['ParamFromGetter', 'ParamFromArray'];
        const coreModel = {
          _parameterIds: ids,
          defaults: [undefined, '0.75'],
          getParameterCount() { return ids.length; },
          getParameterIndex(id) { return ids.indexOf(id); },
          getParamDefault(index) { return index === 0 ? '0.25' : Number.NaN; },
        };

        const catalog = manager._buildModelParameterCatalog(coreModel);
        assert.deepStrictEqual(
          JSON.parse(JSON.stringify(catalog)),
          [
            { index: 0, id: 'ParamFromGetter', key: 'ParamFromGetter', defaultValue: 0.25 },
            { index: 1, id: 'ParamFromArray', key: 'ParamFromArray', defaultValue: 0.75 },
          ],
        );
        """
    )
    result = _run_node_harness(script)
    assert result.returncode == 0, result.stderr


def test_legacy_index_aliases_are_normalized_and_user_preferences_remain_authoritative():
    script = _manager_harness(
        """
        const manager = new context.Live2DManager();
        const ids = ['ParamHair_Color1', 'ParamClothes_Color1', 'ParamBangs_Hairstyle'];
        const coreModel = {
          _model: { parameters: { ids, defaultValues: [0, 0, 0] } },
          getParameterCount() { return ids.length; },
          getParameterIndex(id) { return ids.indexOf(id); },
        };

        const legacy = {
          param_0: 5.42,
          param_1: 4.57,
          ParamHair_Color1: 0,
          ParamClothes_Color1: 0,
        };
        const normalized = manager._normalizeModelParameters(coreModel, legacy);
        assert.deepStrictEqual(
          JSON.parse(JSON.stringify(normalized)),
          { ParamHair_Color1: 0, ParamClothes_Color1: 0 },
        );
        assert.strictEqual(Object.keys(normalized).some((key) => key.startsWith('param_')), false);

        // Official IDs are authoritative regardless of JSON insertion order.
        const reverseOrdered = manager._normalizeModelParameters(
          coreModel,
          { ParamHair_Color1: 0, param_0: 5.42 },
        );
        assert.deepStrictEqual(
          JSON.parse(JSON.stringify(reverseOrdered)),
          { ParamHair_Color1: 0 },
        );

        // Normalize each source before merging so aliases and official IDs
        // cannot both survive in the effective parameter dictionary.
        const effective = manager._mergeEffectiveModelParameters(
          { ParamHair_Color1: 0.1, param_0: 0.2, ParamClothes_Color1: 0.3 },
          {},
          coreModel,
        );
        assert.deepStrictEqual(
          JSON.parse(JSON.stringify(effective)),
          { ParamHair_Color1: 0.1, ParamClothes_Color1: 0.3 },
        );

        const preferenceOverride = manager._mergeEffectiveModelParameters(
          { ParamHair_Color1: 0.1, ParamClothes_Color1: 0.3 },
          { param_0: 0.7, ParamHair_Color1: 0.8 },
          coreModel,
        );
        assert.deepStrictEqual(
          JSON.parse(JSON.stringify(preferenceOverride)),
          { ParamHair_Color1: 0.8, ParamClothes_Color1: 0.3 },
        );

        // Editing one unrelated parameter must preserve the normalized
        // appearance values and must not re-emit legacy aliases.
        const draft = { ...normalized, ParamBangs_Hairstyle: 1 };
        const saved = manager._normalizeModelParameters(coreModel, draft);
        assert.deepStrictEqual(
          JSON.parse(JSON.stringify(saved)),
          {
            ParamHair_Color1: 0,
            ParamClothes_Color1: 0,
            ParamBangs_Hairstyle: 1,
          },
        );
        """
    )
    result = _run_node_harness(script)
    assert result.returncode == 0, result.stderr


def test_parameter_catalog_keeps_stable_index_fallback_when_model_ids_are_unavailable():
    script = _manager_harness(
        """
        const manager = new context.Live2DManager();
        const coreModel = {
          parameters: { defaultValues: [0.2, -0.5] },
          getParameterCount() { return 2; },
          getParameterIndex() { return -1; },
        };

        const catalog = manager._buildModelParameterCatalog(coreModel);
        assert.deepStrictEqual(
          JSON.parse(JSON.stringify(catalog)),
          [
            { index: 0, id: '', key: 'param_0', defaultValue: 0.2 },
            { index: 1, id: '', key: 'param_1', defaultValue: -0.5 },
          ],
        );
        assert.deepStrictEqual(
          JSON.parse(JSON.stringify(manager._normalizeModelParameters(coreModel, { param_0: 0.7, param_1: -0.1 }))),
          { param_0: 0.7, param_1: -0.1 },
        );
        """
    )
    result = _run_node_harness(script)
    assert result.returncode == 0, result.stderr


def test_parameter_editor_initializes_and_resets_through_canonical_catalog():
    source = PARAMETER_EDITOR_PATH.read_text(encoding="utf-8")
    record_start = source.index("function recordInitialParameters()")
    record_end = source.index("// 获取参数的范围和默认值", record_start)
    record_source = source[record_start:record_end]
    reset_start = source.index("if (resetAllBtn) {")
    reset_end = source.index("function buildParametersFromDraft", reset_start)
    reset_source = source[reset_start:reset_end]

    assert "manager._buildModelParameterCatalog(coreModel)" in record_source
    assert "manager._normalizeModelParameters(coreModel, rawEffectiveParameters)" in record_source
    assert "coreModel.getParameterId(i)" not in record_source
    assert "draftParameters = {};" in reset_source
    assert "resetValue = parameter.defaultValue" in reset_source
    assert "initialParameters[paramId]" not in reset_source


def test_effective_parameter_refresh_reinstalls_overlay_with_new_values():
    script = _manager_harness(
        """
        const manager = new context.Live2DManager();
        manager._parameterEditingMode = false;
        manager.applyModelParameters = (_model, parameters) => {
          manager.lastApplied = { ...parameters };
        };
        manager.installMouthOverride = () => {
          manager.installedSnapshot = { ...(manager.savedModelParameters || {}) };
          manager.installCount = (manager.installCount || 0) + 1;
        };
        const ids = ['ParamAllColor1'];
        const model = { internalModel: { coreModel: {
          _parameterIds: ids,
          getParameterCount() { return ids.length; },
          getParameterIndex(id) { return ids.indexOf(id); },
        } } };

        manager._applyEffectiveModelParameters(model, { ParamAllColor1: 0.2 }, {});
        manager._applyEffectiveModelParameters(model, { ParamAllColor1: 0.2 }, { ParamAllColor1: 0.8 });

        assert.strictEqual(manager.lastApplied.ParamAllColor1, 0.8);
        assert.strictEqual(manager.installedSnapshot.ParamAllColor1, 0.8);
        assert.strictEqual(manager.installCount, 2);
        assert.strictEqual(manager._shouldApplySavedParams, true);
        """
    )
    result = _run_node_harness(script)
    assert result.returncode == 0, result.stderr


def test_editing_mode_effective_parameters_override_stopped_persistent_expression_values():
    script = _manager_harness(
        """
        const ids = ['ParamAllColor1'];
        const values = [0.2];
        const coreModel = {
          getParameterCount() { return 1; },
          getParameterId(index) { return ids[index]; },
          getParameterIndex(id) { return ids.indexOf(id); },
          setParameterValueByIndex(index, value) { values[index] = value; },
        };
        const manager = new context.Live2DManager();
        manager._parameterEditingMode = true;
        manager.initialParameters = { ParamAllColor1: 0 };
        manager.appearanceBaselineParameters = {};
        manager.persistentExpressionParamsByName = {
          resident: [{ Id: 'ParamAllColor1', Value: 0.2 }],
        };
        manager.installMouthOverride = () => {};
        manager._applyEffectiveModelParameters(
          { internalModel: { coreModel } },
          { ParamAllColor1: 0.1 },
          { ParamAllColor1: 0.8 },
        );
        assert.strictEqual(values[0], 0.8);
        assert.strictEqual(manager.appearanceBaselineParameters.ParamAllColor1, 0.8);
        assert.strictEqual(manager._shouldApplySavedParams, false);
        """
    )
    result = _run_node_harness(script)
    assert result.returncode == 0, result.stderr


def test_editing_mode_overlay_does_not_overwrite_slider_value_across_frames():
    script = _manager_harness(
        """
        const ids = ['ParamAllColor1'];
        const values = [0.1];
        const coreModel = {
          getParameterCount() { return ids.length; },
          getParameterId(index) { return ids[index]; },
          getParameterIndex(id) { return ids.indexOf(id); },
          getParameterValueByIndex(index) { return values[index]; },
          getParameterDefaultValueByIndex() { return 0; },
          setParameterValueByIndex(index, value) { values[index] = value; },
          setParameterValueById(id, value) { values[ids.indexOf(id)] = value; },
          update() {},
        };
        const motionManager = { state: { currentPriority: 0 }, update() {} };
        const manager = new context.Live2DManager();
        manager.currentModel = { deltaTime: 16.66, internalModel: { coreModel, motionManager } };
        manager._parameterEditingMode = true;
        manager.savedModelParameters = { ParamAllColor1: 0.1 };
        manager._shouldApplySavedParams = false;
        manager._mouseTrackingEnabled = true;
        manager._autoEyeBlinkEnabled = false;
        manager.mouthValue = 0;
        manager.persistentExpressionParamsByName = {
          resident: [{ Id: 'ParamAllColor1', Value: 0.2 }],
        };

        manager.installMouthOverride();
        values[0] = 0.8;
        for (let frame = 0; frame < 30; frame += 1) {
          motionManager.update(0.016);
          coreModel.update();
        }
        assert.strictEqual(values[0], 0.8);
        """
    )
    result = _run_node_harness(script)
    assert result.returncode == 0, result.stderr


def test_clear_expression_restores_only_active_ids_to_appearance_baseline():
    script = _manager_harness(
        """
        const ids = ['ParamAllColor1', 'ParamUnrelated'];
        const values = [0.1, 0.7];
        const coreModel = {
          getParameterIndex(id) { return ids.indexOf(id); },
          getParameterId(index) { return ids[index]; },
          setParameterValueById(id, value) { values[ids.indexOf(id)] = value; },
          setParameterValueByIndex(index, value) { values[index] = value; },
        };
        let stopped = 0;
        let persistentReplayed = 0;
        const manager = new context.Live2DManager();
        manager.currentModel = { internalModel: { coreModel, motionManager: {
          expressionManager: { stopAllExpressions() { stopped += 1; } },
        } } };
        manager.initialParameters = { ParamAllColor1: 0, ParamUnrelated: 0 };
        manager.motionBaselineParameters = {};
        manager.appearanceBaselineParameters = { ParamAllColor1: 0.8, ParamUnrelated: 0.4 };
        manager._activeExpressionParamIds = new Set(['ParamAllColor1']);
        manager._cancelSmoothReset = () => {};
        manager._removeManualExpressionOverride = () => {};
        manager.applyPersistentExpressionsNative = () => { persistentReplayed += 1; };

        manager.clearExpression();

        assert.strictEqual(values[0], 0.8);
        assert.strictEqual(values[1], 0.7);
        assert.strictEqual(stopped, 1);
        assert.strictEqual(persistentReplayed, 1);
        """
    )
    result = _run_node_harness(script)
    assert result.returncode == 0, result.stderr


def test_parameter_save_treats_preferences_as_authoritative_and_sends_one_refresh():
    source = PARAMETER_EDITOR_PATH.read_text(encoding="utf-8")
    save_start = source.index("if (saveBtn) {\n    saveBtn.addEventListener")
    save_source = source[save_start : source.index("// 初始化", save_start)]

    assert "if (prefSuccess)" in save_source
    assert "if (fileSuccess)" in save_source
    assert save_source.count("sendMessageToMainPage('reload_model_parameters'") == 1
    assert "lanlan_name: getParameterEditorLanlanName()" in save_source
    assert "model_name: currentModelInfo.name" in save_source
    assert "model_path: currentModelInfo.path" in save_source


def test_stale_load_token_is_checked_after_directory_parameter_failure():
    source = LIVE2D_MODEL_PATH.read_text(encoding="utf-8")
    configure_start = source.index("Live2DManager.prototype._configureLoadedModel")
    configure_end = source.index("Live2DManager.prototype._applyTextureQuality", configure_start)
    configure_source = source[configure_start:configure_end]

    load_index = configure_source.index("await this._loadModelDirectoryParameters(this.modelName)")
    catch_index = configure_source.index("console.error('加载模型参数失败:'", load_index)
    token_index = configure_source.index("if (!this._isLoadTokenActive(loadToken)) return;", load_index)
    apply_index = configure_source.index("this._applyEffectiveModelParameters(", load_index)

    assert load_index < catch_index < token_index < apply_index
    assert "模型目录参数 > 用户偏好参数" not in configure_source


def test_file_save_failure_with_preference_success_survives_parameter_reload():
    editor_source = PARAMETER_EDITOR_PATH.read_text(encoding="utf-8")
    persist_function = _extract_js_function(editor_source, "persistParameterDraft")
    script = _manager_harness(
        f"""
        vm.runInContext({json.dumps(persist_function)}, context);
        (async () => {{
          const modelPath = '/static/mao_pro/mao_pro.model3.json';
          let storedPreference = null;
          const values = [0];
          const ids = ['ParamAllColor1'];
          const coreModel = {{
            getParameterCount() {{ return 1; }},
            getParameterId(index) {{ return ids[index]; }},
            getParameterIndex(id) {{ return ids.indexOf(id); }},
            getParameterValueByIndex(index) {{ return values[index]; }},
            setParameterValueByIndex(index, value) {{ values[index] = value; }},
          }};
          const model = {{ x: 10, y: 20, scale: {{ x: 1, y: 1 }}, internalModel: {{ coreModel }} }};
          const manager = new context.Live2DManager();
          manager.currentModel = model;
          manager.modelName = 'mao_pro';
          manager._lastLoadedModelPath = modelPath;
          manager.initialParameters = {{ ParamAllColor1: 0 }};
          manager.appearanceBaselineParameters = {{}};
          manager.motionBaselineParameters = {{}};
          manager._parameterEditingMode = false;
          manager.getPersistentExpressionParamIds = () => new Set();
          manager.saveUserPreferences = async (path, position, scale, parameters) => {{
            storedPreference = {{ model_path: path, position, scale, parameters: {{ ...parameters }} }};
            return true;
          }};
          manager._loadModelDirectoryParameters = async () => ({{ ParamAllColor1: 0.1 }});
          manager.loadUserPreferences = async () => [storedPreference];
          manager.installMouthOverride = () => {{
            manager.installedSnapshot = {{ ...(manager.savedModelParameters || {{}}) }};
          }};

          const saveResult = await context.persistParameterDraft(
            {{ name: 'mao_pro', path: modelPath }},
            model,
            {{ ParamAllColor1: 0.8 }},
            {{
              manager,
              fetchImpl: async () => ({{
                ok: false,
                status: 403,
                async json() {{ return {{ success: false, error: 'read-only' }}; }},
              }}),
            }},
          );
          assert.strictEqual(saveResult.fileSuccess, false);
          assert.strictEqual(saveResult.prefSuccess, true);
          assert.strictEqual(storedPreference.parameters.ParamAllColor1, 0.8);

          const totalFailure = await context.persistParameterDraft(
            {{ name: 'mao_pro', path: modelPath }},
            model,
            {{ ParamAllColor1: 0.9 }},
            {{
              manager: {{ saveUserPreferences: async () => false }},
              fetchImpl: async () => {{ throw new Error('read-only'); }},
            }},
          );
          assert.strictEqual(totalFailure.fileSuccess, false);
          assert.strictEqual(totalFailure.prefSuccess, false);

          values[0] = 0;
          const reloadResult = await manager.reloadModelParameters({{
            model_name: 'mao_pro',
            model_path: modelPath,
          }});
          assert.strictEqual(reloadResult.applied, true);
          assert.strictEqual(values[0], 0.8);
          assert.strictEqual(manager.savedModelParameters.ParamAllColor1, 0.8);
          assert.strictEqual(manager.appearanceBaselineParameters.ParamAllColor1, 0.8);
          assert.strictEqual(manager.installedSnapshot.ParamAllColor1, 0.8);
        }})().catch((error) => {{ console.error(error); process.exitCode = 1; }});
        """
    )
    result = _run_node_harness(script)
    assert result.returncode == 0, result.stderr


def test_reload_model_parameters_is_received_on_all_cross_page_channels():
    source = APP_INTERPAGE_PATH.read_text(encoding="utf-8")

    assert "async function handleReloadModelParametersMessage" in source
    assert "case 'reload_model_parameters':" in source
    assert "event.data.action === 'reload_model_parameters'" in source
    assert "event.key !== 'nekopage_message'" in source
    assert "参数轻量热刷新失败，降级为完整模型重载" in source


def test_all_mao_pro_motions_restore_saved_hair_colors_from_appearance_baseline():
    model_config = json.loads(MAO_PRO_MODEL_PATH.read_text(encoding="utf-8"))
    motion_files = {
        motion["File"]
        for motions in model_config["FileReferences"]["Motions"].values()
        for motion in motions
    }
    assert len(motion_files) == 7

    motion_paths = [MAO_PRO_MODEL_PATH.parent / relative_path for relative_path in sorted(motion_files)]
    for motion_path in motion_paths:
        motion = json.loads(motion_path.read_text(encoding="utf-8"))
        parameter_ids = {
            curve["Id"]
            for curve in motion.get("Curves", [])
            if curve.get("Target") == "Parameter"
        }
        assert {"ParamAllColor1", "ParamAllColor2"} <= parameter_ids, motion_path.name

    script = _manager_harness(
        f"""
        const motionPaths = {json.dumps([str(path) for path in motion_paths])};
        const ids = ['ParamAllColor1', 'ParamAllColor2'];
        const values = [0, 0];
        const coreModel = {{
          getParameterIndex(id) {{ return ids.indexOf(id); }},
          setParameterValueById(id, value) {{ values[ids.indexOf(id)] = value; }},
        }};
        const manager = new context.Live2DManager();
        manager.currentModel = {{ internalModel: {{ coreModel }} }};
        manager.initialParameters = {{ ParamAllColor1: 0, ParamAllColor2: 0 }};
        manager.motionBaselineParameters = {{}};
        manager.appearanceBaselineParameters = {{ ParamAllColor1: 0.8, ParamAllColor2: 0.6 }};

        for (const motionPath of motionPaths) {{
          const motion = JSON.parse(fs.readFileSync(motionPath, 'utf8'));
          manager._trackActiveMotionParametersFromData(motion);
          values[0] = 0;
          values[1] = 0;
          manager._resetActiveMotionParameters({{ preserveExpression: false }});
          assert.strictEqual(values[0], 0.8, motionPath);
          assert.strictEqual(values[1], 0.6, motionPath);
        }}
        """
    )
    result = _run_node_harness(script)
    assert result.returncode == 0, result.stderr
