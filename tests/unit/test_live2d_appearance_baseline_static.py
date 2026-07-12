import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LIVE2D_CORE_PATH = PROJECT_ROOT / "static" / "live2d" / "live2d-core.js"
LIVE2D_MODEL_PATH = PROJECT_ROOT / "static" / "live2d" / "live2d-model.js"
LIVE2D_EMOTION_PATH = PROJECT_ROOT / "static" / "live2d" / "live2d-emotion.js"


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


def test_saved_live2d_parameters_feed_appearance_baseline():
    core_source = LIVE2D_CORE_PATH.read_text(encoding="utf-8")
    model_source = LIVE2D_MODEL_PATH.read_text(encoding="utf-8")

    assert "this.appearanceBaselineParameters = {};" in core_source
    assert "Live2DManager.prototype.mergeAppearanceBaselineParameters" in model_source
    assert "this.mergeAppearanceBaselineParameters(model, parameters);" in model_source
    assert "this._isRuntimeManagedAppearanceParam" in model_source


def test_full_live2d_reset_prefers_saved_appearance_baseline():
    source = LIVE2D_EMOTION_PATH.read_text(encoding="utf-8")

    assert "this._isRuntimeManagedAppearanceParam(paramId, resolvedParamId, coreModel)" in source
    assert "? [this.appearanceBaselineParameters, this.motionBaselineParameters, this.initialParameters]" in source
    assert "? [this.appearanceBaselineParameters, this.savedModelParameters, this.motionBaselineParameters" not in source
    assert "const resetValue = baseline.found ? baseline.value : initialValue;" in source
    assert "coreModel.setParameterValueByIndex(paramIndex, resetValue);" in source
    assert "coreModel.setParameterValueById(paramId, resetValue);" in source


def test_live2d_appearance_baseline_filters_runtime_parameters():
    script = textwrap.dedent(
        f"""
        const assert = require('node:assert');
        const fs = require('node:fs');
        const vm = require('node:vm');

        const context = {{
          console: {{
            log() {{}},
            warn() {{}},
            error() {{}},
            groupCollapsed() {{}},
            groupEnd() {{}},
          }},
          window: {{ LIPSYNC_PARAMS: ['ParamMouthOpenY', 'ParamMouthForm'] }},
          Live2DManager: function Live2DManager() {{}},
          setTimeout,
          clearTimeout,
          setInterval,
          clearInterval,
        }};
        context.global = context;
        context.window.Live2DManager = context.Live2DManager;

        vm.createContext(context);
        vm.runInContext(fs.readFileSync({json.dumps(str(LIVE2D_MODEL_PATH))}, 'utf8'), context);
        vm.runInContext(fs.readFileSync({json.dumps(str(LIVE2D_EMOTION_PATH))}, 'utf8'), context);

        const ids = [
          'ParamAngleX',
          'ParamBodyAngleX',
          'ParamBreath',
          'ParamLookAtX',
          'ParamAllColor1',
          'ParamHairFront',
        ];
        const values = [10, 20, 0.4, 0.5, 1, 0.25];
        const coreModel = {{
          getParameterCount() {{ return ids.length; }},
          getParameterId(index) {{ return ids[index]; }},
          getParameterIndex(id) {{ return ids.indexOf(id); }},
          getParameterValueByIndex(index) {{ return values[index]; }},
        }};
        const manager = new context.Live2DManager();
        manager.currentModel = {{ internalModel: {{ coreModel }} }};

        manager.recordInitialParameters();

        assert.strictEqual(manager.appearanceBaselineParameters.ParamAllColor1, 1);
        assert.strictEqual(manager.appearanceBaselineParameters.ParamHairFront, 0.25);
        assert.strictEqual(
          Object.prototype.hasOwnProperty.call(manager.appearanceBaselineParameters, 'ParamBodyAngleX'),
          false,
        );
        assert.strictEqual(
          Object.prototype.hasOwnProperty.call(manager.appearanceBaselineParameters, 'ParamBreath'),
          false,
        );
        assert.strictEqual(
          Object.prototype.hasOwnProperty.call(manager.appearanceBaselineParameters, 'ParamLookAtX'),
          false,
        );
        """
    )

    result = _run_node_harness(script)
    assert result.returncode == 0, result.stderr


def test_live2d_appearance_baseline_skips_unresolved_numeric_keys():
    script = textwrap.dedent(
        f"""
        const assert = require('node:assert');
        const fs = require('node:fs');
        const vm = require('node:vm');

        const context = {{
          console: {{
            log() {{}},
            warn() {{}},
            error() {{}},
            groupCollapsed() {{}},
            groupEnd() {{}},
          }},
          window: {{ LIPSYNC_PARAMS: ['ParamMouthOpenY', 'ParamMouthForm'] }},
          Live2DManager: function Live2DManager() {{}},
          setTimeout,
          clearTimeout,
          setInterval,
          clearInterval,
        }};
        context.global = context;
        context.window.Live2DManager = context.Live2DManager;

        vm.createContext(context);
        vm.runInContext(fs.readFileSync({json.dumps(str(LIVE2D_MODEL_PATH))}, 'utf8'), context);

        const coreModel = {{
          getParameterCount() {{ return 3; }},
          getParameterIndex(id) {{
            const match = String(id).match(/^param_(\\d+)$/);
            return match ? Number(match[1]) : -1;
          }},
        }};
        const manager = new context.Live2DManager();
        manager.initialParameters = {{ param_0: 0, param_1: 0, param_2: 0 }};
        manager.appearanceBaselineParameters = {{}};

        manager.mergeAppearanceBaselineParameters(
          {{ internalModel: {{ coreModel }} }},
          {{ '1': 0.8, param_2: 0.7 }},
        );

        assert.deepStrictEqual(Object.keys(manager.appearanceBaselineParameters), []);
        """
    )

    result = _run_node_harness(script)
    assert result.returncode == 0, result.stderr


def test_live2d_reset_ignores_unfiltered_saved_runtime_baseline():
    script = textwrap.dedent(
        f"""
        const assert = require('node:assert');
        const fs = require('node:fs');
        const vm = require('node:vm');

        const context = {{
          console: {{
            log() {{}},
            warn() {{}},
            error() {{}},
            groupCollapsed() {{}},
            groupEnd() {{}},
          }},
          window: {{ LIPSYNC_PARAMS: ['ParamMouthOpenY', 'ParamMouthForm'] }},
          Live2DManager: function Live2DManager() {{}},
          setTimeout,
          clearTimeout,
          setInterval,
          clearInterval,
        }};
        context.global = context;
        context.window.Live2DManager = context.Live2DManager;

        vm.createContext(context);
        vm.runInContext(fs.readFileSync({json.dumps(str(LIVE2D_MODEL_PATH))}, 'utf8'), context);
        vm.runInContext(fs.readFileSync({json.dumps(str(LIVE2D_EMOTION_PATH))}, 'utf8'), context);

        const ids = ['ParamAllColor1', 'ParamBodyAngleX'];
        const values = [0, 0];
        const coreModel = {{
          getParameterCount() {{ return ids.length; }},
          getParameterId(index) {{ return ids[index]; }},
          getParameterIndex(id) {{ return ids.indexOf(id); }},
          getParameterValueByIndex(index) {{ return values[index]; }},
          setParameterValueByIndex(index, value) {{ values[index] = value; }},
          setParameterValueById(id, value) {{ values[ids.indexOf(id)] = value; }},
        }};

        const manager = new context.Live2DManager();
        manager.currentModel = {{ internalModel: {{ coreModel }} }};
        manager.initialParameters = {{
          ParamAllColor1: 0,
          ParamBodyAngleX: 0,
        }};
        manager.motionBaselineParameters = {{
          ParamBodyAngleX: 5,
          param_1: 0,
        }};
        manager.appearanceBaselineParameters = {{
          ParamAllColor1: 1,
          param_0: 1,
        }};
        manager.savedModelParameters = {{
          ParamBodyAngleX: 30,
          param_1: 30,
        }};

        manager._resetParametersToInitialState({{ preserveExpression: false }});

        assert.strictEqual(values[0], 1);
        assert.strictEqual(values[1], 5);
        """
    )

    result = _run_node_harness(script)
    assert result.returncode == 0, result.stderr
