"""Static-contract tests for the VRM formant (five-vowel) lip-sync.

Locks the structural wiring so later refactors cannot silently break it:
  - vrm-lipsync-formant.js must load before vrm-animation.js (runtime dep);
  - _updateLipSync prefers the formant path and falls back to the legacy
    single-channel volume driver when the analyzer is unavailable;
  - the formant path writes all five vowels every frame (including 0),
    which overrides idle-VRMA mouth-track residue.
"""
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _read(rel):
    return (PROJECT_ROOT / rel).read_text(encoding="utf-8")


def test_formant_module_loaded_before_animation():
    """In vrm-init.js the formant module is listed before vrm-animation.js."""
    init_source = _read("static/vrm/vrm-init.js")
    formant_idx = init_source.index("/static/vrm/vrm-lipsync-formant.js")
    animation_idx = init_source.index("/static/vrm/vrm-animation.js")
    assert formant_idx < animation_idx


def test_formant_module_exposes_global_and_no_esm():
    """Classic-script architecture: attach to window, no ESM import/export."""
    source = _read("static/vrm/vrm-lipsync-formant.js")
    assert "window.FormantLipSyncAnalyzer = FormantLipSyncAnalyzer;" in source
    assert "\nexport " not in source
    assert "\nimport " not in source


def test_animation_lazy_instantiates_analyzer_with_fallback():
    """startLipSync instantiates lazily; the constructor must not touch window."""
    source = _read("static/vrm/vrm-animation.js")
    assert "new window.FormantLipSyncAnalyzer(analyser)" in source
    # Constructor only declares the field; it must not reference the global
    # class at load time because parallel module load order is not guaranteed.
    constructor = source.split("constructor(", 1)[1].split("startLipSync(", 1)[0]
    assert "new window.FormantLipSyncAnalyzer" not in constructor


def test_update_lipsync_prefers_formant_then_falls_back():
    """_updateLipSync tries the formant path first, then falls back to volume."""
    source = _read("static/vrm/vrm-animation.js")
    method = source.split("_updateLipSync(delta) {", 1)[1]
    formant_branch = method.index("this._updateLipSyncFormant(expressionManager, delta);")
    fallback_branch = method.index("getByteFrequencyData(this.frequencyData)")
    assert formant_branch < fallback_branch


def test_formant_path_writes_all_five_vowels():
    """Formant path iterates the whole mouthExpressions table, writing 0s too."""
    source = _read("static/vrm/vrm-animation.js")
    formant_method = source.split("_updateLipSyncFormant(expressionManager, delta) {", 1)[1]
    assert "Object.entries(this.mouthExpressions)" in formant_method
    assert "const target = weights[vowel] ?? 0;" in formant_method
    assert "expressionManager.setValue(name, target);" in formant_method


def test_fallback_path_still_clears_other_vowels_before_aa():
    """Fallback single-channel path keeps the clear-others-then-write-aa guard."""
    source = _read("static/vrm/vrm-animation.js")
    method = source.split("_updateLipSync(delta) {", 1)[1].split(
        "_updateLipSyncFormant(expressionManager, delta) {", 1
    )[0]
    assert "if (!name || vowel === 'aa') continue;" in method
    assert "expressionManager.setValue(name, 0);" in method


# ─────────────────────── MMD 侧（共享同一分析器）───────────────────────


def test_mmd_animation_lazy_instantiates_formant_analyzer():
    """mmd-animation.startLipSync lazily builds the analyzer, with fallback."""
    source = _read("static/mmd/mmd-animation.js")
    assert "new window.FormantLipSyncAnalyzer(analyser)" in source
    assert "this._formantAnalyzer = null;" in source  # 失败/缺失回退
    # 构造期不引用全局类：实例化必须发生在 startLipSync 内而非 constructor
    assert "startLipSync(analyser) {" in source


def test_mmd_expression_prefers_formant_then_falls_back():
    """mmd-expression.update tries formant path first, then legacy setMouth."""
    source = _read("static/mmd/mmd-expression.js")
    method = source.split("update(delta) {", 1)[1]
    formant_branch = method.index("anim._formantAnalyzer")
    fallback_branch = method.index("anim.getLipSyncValue()")
    assert formant_branch < fallback_branch


def test_mmd_expression_formant_maps_all_five_vowels():
    """Formant path maps analyzer keys (aa/ee/ih/oh/ou) to MMD vowels and writes
    every vowel each frame (including 0), overriding idle-VMD mouth residue."""
    source = _read("static/mmd/mmd-expression.js")
    # 键映射表完整覆盖五元音
    assert "aa: 'a'" in source
    assert "ih: 'i'" in source
    assert "ou: 'u'" in source
    assert "ee: 'e'" in source
    assert "oh: 'o'" in source
    # formant 路径按映射全写 morph（含 0 目标值）
    formant_method = source.split("if (anim._formantAnalyzer) {", 1)[1].split(
        "anim.getLipSyncValue()", 1
    )[0]
    assert "weights[formantKey] ?? 0" in formant_method
    assert "this.setMorphWeight(name, target);" in formant_method


def test_mmd_init_loads_shared_formant_module():
    """mmd-init.js must load the shared formant analyzer, otherwise MMD-only
    mode never defines window.FormantLipSyncAnalyzer and silently degrades."""
    source = _read("static/mmd/mmd-init.js")
    assert "/static/vrm/vrm-lipsync-formant.js" in source


def test_mmd_expression_has_reset_all_lip_morphs():
    """resetAllLipMorphs() must exist and iterate all five vowel keys in
    lipMorphNames, writing 0 to every morph."""
    source = _read("static/mmd/mmd-expression.js")
    assert "resetAllLipMorphs()" in source
    method = source.split("resetAllLipMorphs()", 1)[1].split("\n    }", 1)[0]
    assert "Object.keys(this.lipMorphNames)" in method
    assert "this.setMorphWeight(name, 0)" in method


def test_mmd_stop_lip_sync_calls_reset_all():
    """stopLipSync must call resetAllLipMorphs() (not setMouth(0)) so that
    formant-driven i/u/e morphs are also cleared."""
    source = _read("static/mmd/mmd-animation.js")
    stop_method = source.split("stopLipSync()", 1)[1].split("\n    }", 1)[0]
    assert "resetAllLipMorphs()" in stop_method
    assert "setMouth(0)" not in stop_method
