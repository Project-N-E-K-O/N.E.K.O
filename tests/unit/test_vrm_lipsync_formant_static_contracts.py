"""Static-contract tests for the VRM/MMD formant (five-vowel) lip-sync.

Two layers guard this feature:

* The behaviour suite ``tests/frontend/vrm_lipsync_formant.test.cjs`` runs the
  real analyzer under node and is launched by ``test_formant_behaviour_suite``
  below.  It used to live at ``tests/unit/vrm_lipsync_formant.test.js``, where
  no runner in this repo collects that suffix, so none of its assertions had
  ever executed in CI.
* The string-level contracts here lock structural wiring that the behaviour
  suite cannot see: which loader chains ship the shared analyzer, that the
  analyzer stays lazily constructed behind a fallback, and that both avatar
  pipelines write every vowel each frame.
"""
import re
import shutil
from pathlib import Path

import pytest

from tests.node_harness import run_node_script


PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Every loader chain that can end up owning a VRM or MMD avatar. The shared
# analyzer attaches to window, so a chain that omits it silently degrades to
# the legacy single-channel driver with no error anywhere.
LOADER_CHAINS = (
    "static/vrm/vrm-init.js",
    "static/mmd/mmd-init.js",
    "static/js/model_manager/runtime-loaders.js",
    "static/js/character_card_manager/model-previews.js",
)

FORMANT_MODULE = "/static/vrm/vrm-lipsync-formant.js"

# Mirrors VOWEL_KEYS in static/vrm/vrm-lipsync-formant.js.
EXPECTED_VOWEL_KEYS = ("aa", "ee", "ih", "oh", "ou")


def _read(rel):
    return (PROJECT_ROOT / rel).read_text(encoding="utf-8")


# ─────────────────────────── node behaviour suite ───────────────────────────


def test_formant_behaviour_suite():
    """The node suite for the analyzer passes."""
    node_path = shutil.which("node")
    if not node_path:
        pytest.skip("node not found")

    test_path = PROJECT_ROOT / "tests" / "frontend" / "vrm_lipsync_formant.test.cjs"
    # Goes through the shared launcher rather than a hand-rolled subprocess call:
    # it pins the temp-file form (Windows' 32767-char command line) and UTF-8 in
    # both directions, which this suite needs because it names its cases in
    # Chinese. tests/unit/test_node_harness_contract.py enforces that.
    #
    # The launcher runs `node <file>` with no --test flag; node:test still
    # executes every case and still exits non-zero on failure, and the suite
    # resolves the repo from cwd when __dirname points at the temp copy.
    result = run_node_script(
        node_path,
        test_path.read_text(encoding="utf-8"),
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


# ─────────────────────────────── loader chains ───────────────────────────────


# The array names each loader chain is expected to expose. Pinned so that a
# regex that silently stops matching one of them fails loudly instead of
# vacuously reporting "nothing to check". An earlier version of the pattern
# allowed `[` to be followed by anything, so `const failedModules = [];` matched
# lazily all the way to the *next* array's closing bracket and swallowed
# `mmdModules` whole -- the MMD chain was then never checked on its own.
EXPECTED_CHAIN_ARRAYS = {
    "static/vrm/vrm-init.js": {"parallelModules"},
    "static/mmd/mmd-init.js": {"parallelModules"},
    "static/js/model_manager/runtime-loaders.js": {"vrmModules", "mmdModules"},
    "static/js/character_card_manager/model-previews.js": {"vrmModules", "mmdModules"},
}


def _module_arrays(source):
    """Yield ``(name, body)`` for every multi-line JS array of /static/ paths.

    Requiring a newline right after ``[`` is what keeps empty arrays such as
    ``const failedModules = [];`` from matching and eating a later array.
    """
    for match in re.finditer(r"const\s+(\w+)\s*=\s*\[[ \t]*\n(.*?)\n\s*\];", source, re.S):
        name, body = match.group(1), match.group(2)
        if "/static/" in body:
            yield name, body


@pytest.mark.parametrize("chain", LOADER_CHAINS)
def test_chains_that_load_animation_also_load_the_analyzer(chain):
    """Every module list that pulls in an animation module also pulls the analyzer.

    Asserted per array rather than per file on purpose. runtime-loaders.js holds
    two independent chains (VRM and MMD) in one file, so a file-level substring
    check stays green when only one of them carries the entry -- a mutation of
    exactly that shape survived the first version of this test.

    The predicate is semantic, not a hand-kept list: vrm-animation /
    mmd-animation are what construct FormantLipSyncAnalyzer, so any chain that
    loads one of them and omits the analyzer degrades to the legacy driver.
    The first version of this feature wired only vrm-init.js and mmd-init.js,
    leaving the model-manager page and the card-maker preview behind; both of
    those set the ``_vrmModulesLoading`` / ``_mmdModulesLoading`` flags that make
    the init IIFEs return early, so they cannot inherit the entry from vrm-init.
    """
    source = _read(chain)
    arrays = list(_module_arrays(source))
    names = {name for name, _ in arrays}

    # Parser-drift guard. Without pinning the names, a pattern that stops
    # matching one array turns this test into a no-op that still reports green.
    missing = EXPECTED_CHAIN_ARRAYS[chain] - names
    assert not missing, (
        f"{chain}: expected module arrays {sorted(missing)} were not parsed "
        f"(got {sorted(names)}) -- the matcher has drifted"
    )

    consumers = [
        (name, body)
        for name, body in arrays
        if "vrm-animation.js" in body or "mmd-animation.js" in body
    ]
    assert consumers, (
        f"{chain}: no array loads an animation module -- the matcher has drifted"
    )

    for name, body in consumers:
        assert FORMANT_MODULE in body, (
            f"{chain}: array `{name}` loads an animation module but not "
            f"{FORMANT_MODULE}; lip-sync there silently falls back to the "
            f"legacy single-channel driver"
        )


def test_shared_analyzer_is_reentrant():
    """The module tolerates being loaded twice.

    runtime-loaders.js lists the analyzer in both its VRM and its MMD chain,
    and those two IIFEs run concurrently. A classic script whose top level
    declares ``const`` throws "already been declared" on the second execution
    and takes the whole chain down with it, so the implementation must sit
    inside a guarded IIFE.
    """
    source = _read("static/vrm/vrm-lipsync-formant.js")
    assert "(function initFormantLipSync() {" in source
    guard = source.split("(function initFormantLipSync() {", 1)[1].split("const VOWEL_FORMANTS", 1)[0]
    assert "typeof window.FormantLipSyncAnalyzer === 'function'" in guard
    assert "return;" in guard


def test_formant_module_exposes_global_and_no_esm():
    """Classic-script architecture: attach to window, no ESM import/export."""
    source = _read("static/vrm/vrm-lipsync-formant.js")
    assert "window.FormantLipSyncAnalyzer = FormantLipSyncAnalyzer;" in source
    assert "\nexport " not in source
    assert "\nimport " not in source


# ──────────────────────────── shared vowel key table ────────────────────────


def test_vowel_key_tables_agree():
    """The three vowel-key tables in the codebase stay in sync.

    Without pinning the literal here, a reviewer changing the analyzer's
    VOWEL_KEYS would change this test's own expectation at the same time and
    the derived comparison below would keep passing vacuously.
    """
    analyzer = _read("static/vrm/vrm-lipsync-formant.js")
    animation = _read("static/vrm/vrm-animation.js")
    expression = _read("static/mmd/mmd-expression.js")

    analyzer_keys = analyzer.split("const VOWEL_KEYS = Object.freeze([", 1)[1].split("]", 1)[0]
    fallback_keys = animation.split("static FALLBACK_VOWEL_KEYS = Object.freeze([", 1)[1].split("]", 1)[0]

    def parsed(chunk):
        return tuple(part.strip().strip("'\"") for part in chunk.split(",") if part.strip())

    assert parsed(analyzer_keys) == EXPECTED_VOWEL_KEYS
    assert parsed(fallback_keys) == EXPECTED_VOWEL_KEYS

    # vrm-animation reads the shared table at runtime and only falls back to
    # its own copy when the analyzer script has not executed yet.
    assert "window.VRM_LIPSYNC_VOWEL_KEYS" in animation
    # The MMD map is keyed by the analyzer's vowel keys.
    mmd_map = expression.split("const FORMANT_TO_MMD_VOWEL = Object.freeze(", 1)[1].split(")", 1)[0]
    for vowel in EXPECTED_VOWEL_KEYS:
        assert f"{vowel}:" in mmd_map, f"MMD map is missing analyzer key {vowel}"


# ───────────────────────────── host analyser ownership ───────────────────────


def test_f2_search_floor_is_not_lowered():
    """F2 must keep searching from 1000Hz up, and from a fixed floor.

    This guards a change that was tried and reverted. Dropping the floor to
    700Hz so the rounded vowels' reference centres (ou 800, oh 900) fall inside
    the window looks right on the parameter table but is a net regression: a
    voiced spectrum falls monotonically past F1, and a real F2 peak sits
    10-25dB below F1, so at a 700Hz floor the F1 skirt is usually the loudest
    bin in the window and the peak-pick returns the floor itself. Measured on
    225 frames of real formant data (Peterson-Barney / Hillenbrand values,
    5 f0 x 3 F1 bandwidths), top-1 accuracy went 96.9% -> 84.4%, with ee->oh
    and aa->oh flips, and the rounded vowels gained nothing -- the max
    normalisation in sample() means an unreachable centre does not stop a vowel
    from winning, so the motivating premise was false to begin with.

    A relative floor (max(F2_MIN_HZ, f1 * ratio)) was also tried; it is inert
    for four of the five reference vowels and far too small to clear F1's skirt
    where it does bind.
    """
    source = _read("static/vrm/vrm-lipsync-formant.js")
    f2_min = int(source.split("const F2_MIN_HZ = ", 1)[1].split(";", 1)[0])
    assert f2_min >= 1000, (
        f"F2 search floor is {f2_min}Hz; below 1000Hz the F1 skirt wins the "
        f"peak-pick and front vowels collapse onto the rounded ones"
    )
    sample = source.split("sample() {", 1)[1].split("\n        }", 1)[0]
    assert "this._peakBetween(F2_MIN_HZ, F2_MAX_HZ)" in sample, (
        "F2 window must be the fixed [F2_MIN_HZ, F2_MAX_HZ]; a floor derived "
        "from the measured f1 tracks F1's skirt instead of rejecting it"
    )


def test_peak_frequency_has_a_floor():
    """_peakBetween must never return 0Hz.

    Bin width follows the host fftSize. A small host FFT leaves only a couple
    of bins below 1kHz, and bin 0's centre frequency is 0Hz; that reaches
    sample() as Math.log2(0 / v.f1) = -Infinity and zeroes all five vowel
    weights at once, so the winner degenerates to a constant.
    """
    source = _read("static/vrm/vrm-lipsync-formant.js")
    assert "const MIN_PEAK_HZ = " in source
    peak_method = source.split("_peakBetween(minHz, maxHz) {", 1)[1].split("\n        }", 1)[0]
    assert "Math.max(MIN_PEAK_HZ," in peak_method


def test_analyzer_never_writes_host_analyser_config():
    """The analyzer must not reconfigure the AnalyserNode it is handed.

    The node it receives is app-audio-playback's shared S.globalAnalyser, which
    Live2D and PNGTuber also read; both size their time-domain buffer once from
    ``analyser.fftSize`` at start, so rewriting it changes their sampling
    window with no restore point.
    """
    source = _read("static/vrm/vrm-lipsync-formant.js")
    assert "analyser.fftSize =" not in source
    assert "analyser.smoothingTimeConstant =" not in source
    # It adapts to whatever the host provides instead.
    assert "analyser.frequencyBinCount" in source


def test_host_owns_the_shared_analyser_format():
    """The owner of S.globalAnalyser configures it, once, at creation.

    Both knobs have to be set there rather than by a consumer: the default
    smoothingTimeConstant of 0.8 is too sluggish for vowel discrimination
    (vowel switches take 4-8 frames instead of 2-3 at 60fps), and moving the
    setting into the analyzer is what made it trample a shared node.
    """
    source = _read("static/app/app-audio-playback.js")
    creation = source.split("S.globalAnalyser = S.audioPlayerContext.createAnalyser();", 1)[1]
    creation = creation.split("S.spatialPannerNode", 1)[0]
    assert "S.globalAnalyser.fftSize = 2048;" in creation
    assert "smoothingTimeConstant" in creation, (
        "the shared analyser must pin its frequency smoothing explicitly; "
        "relying on the 0.8 default costs ~42ms of vowel-switch latency"
    )
    value = float(
        creation.split("S.globalAnalyser.smoothingTimeConstant = ", 1)[1].split(";", 1)[0]
    )
    assert 0.0 <= value <= 0.6, f"smoothing {value} is too sluggish for vowel discrimination"


# ───────────────────────────────── VRM wiring ────────────────────────────────


def test_animation_lazy_instantiates_analyzer_with_fallback():
    """startLipSync instantiates lazily; the constructor must not touch window."""
    source = _read("static/vrm/vrm-animation.js")
    assert "new window.FormantLipSyncAnalyzer(analyser)" in source
    # Constructor only declares the field; it must not reference the global
    # class at load time because parallel module load order is not guaranteed.
    constructor = source.split("constructor(", 1)[1].split("startLipSync(", 1)[0]
    assert "new window.FormantLipSyncAnalyzer" not in constructor


def test_vrm_analyzer_construction_is_guarded():
    """A throwing analyzer must degrade to the legacy path, not escape.

    startLipSync is called from scheduleAudioChunks; an exception there aborts
    the rest of that chunk's scheduling bookkeeping. mmd-animation already
    wraps the same construction, so VRM has to match.
    """
    source = _read("static/vrm/vrm-animation.js")
    start = source.split("startLipSync(analyser) {", 1)[1].split("stopLipSync()", 1)[0]
    construct_at = start.index("new window.FormantLipSyncAnalyzer(analyser)")
    try_at = start.index("try {")
    catch_at = start.index("} catch (e) {")
    assert try_at < construct_at < catch_at
    assert "this._lipSyncAnalyzer = null;" in start[catch_at:]


def test_update_lipsync_prefers_formant_then_falls_back():
    """_updateLipSync tries the formant path first, and the branches are exclusive.

    Ordering alone is not enough to assert: without the ``return`` the formant
    path falls through into the legacy single-channel driver, which then
    overwrites all five vowels with its volume-only 'aa' write every frame --
    the feature is off but every ordering assertion still holds.
    """
    source = _read("static/vrm/vrm-animation.js")
    method = source.split("_updateLipSync(delta) {", 1)[1]
    formant_branch = method.index("this._updateLipSyncFormant(expressionManager, delta);")
    fallback_branch = method.index("getByteFrequencyData(this.frequencyData)")
    assert formant_branch < fallback_branch

    between = method[formant_branch:fallback_branch]
    assert "return;" in between, (
        "the formant branch must return; otherwise it falls through into the "
        "legacy path and the vowel weights are immediately overwritten"
    )


def test_mmd_formant_branch_is_exclusive():
    """Same exclusivity requirement on the MMD side of update()."""
    source = _read("static/mmd/mmd-expression.js")
    method = source.split("update(delta) {", 1)[1]
    formant_branch = method.index("anim._formantAnalyzer")
    fallback_branch = method.index("anim.getLipSyncValue()")
    between = method[formant_branch:fallback_branch]
    assert "return;" in between, (
        "the formant branch must return; otherwise setMouth() overwrites the "
        "vowel morphs in the same frame"
    )


def test_formant_path_writes_all_five_vowels_with_preset_fallback():
    """Formant path writes every vowel each frame, via the shared name resolver.

    The legacy path drove ``this.mouthExpressions.aa || 'aa'``, so a model whose
    expression list could not be enumerated still moved its mouth. Skipping
    unmapped vowels instead would freeze the mouth shut without even logging,
    because setValue would never be called.
    """
    source = _read("static/vrm/vrm-animation.js")
    formant_method = source.split("_updateLipSyncFormant(expressionManager, delta) {", 1)[1]
    formant_method = formant_method.split("\n    }", 1)[0]
    assert "VRMAnimation.VOWEL_KEYS" in formant_method
    assert "this._mouthExpressionName(vowel)" in formant_method
    assert "expressionManager.setValue(name, target);" in formant_method
    assert "continue;" not in formant_method, "unmapped vowels must not be skipped"


def test_mouth_write_set_equals_reset_set():
    """Whatever lip-sync writes, stopLipSync must be able to clear.

    The preset-name fallback only fires when the expression mapping came up
    empty -- exactly the case where a reset loop keyed on "skip if the mapping
    is empty" clears nothing. The two sides would then disagree precisely when
    the fallback is load-bearing, and the mouth stays frozen at its last frame
    after speech ends. Both sides therefore go through _mouthExpressionName.
    """
    source = _read("static/vrm/vrm-animation.js")
    assert "_mouthExpressionName(vowel) {" in source
    resolver = source.split("_mouthExpressionName(vowel) {", 1)[1].split("\n    }", 1)[0]
    assert "this.mouthExpressions[vowel] || vowel" in resolver

    reset = source.split("resetMouthExpressions() {", 1)[1].split("\n    }", 1)[0]
    assert "VRMAnimation.VOWEL_KEYS" in reset, "reset must cover every vowel key"
    assert "this._mouthExpressionName(vowel)" in reset, (
        "reset must resolve names the same way the write paths do"
    )
    assert "if (name)" not in reset, (
        "skipping unmapped vowels on reset is what leaves fallback-driven "
        "expressions stuck after stopLipSync"
    )

    # Neither write path may re-derive the name locally.
    for marker in (
        "_updateLipSyncFormant(expressionManager, delta) {",
        "_updateLipSync(delta) {",
    ):
        body = source.split(marker, 1)[1].split("\n    }", 1)[0]
        assert "mouthExpressions.aa || 'aa'" not in body
        assert "this.mouthExpressions[vowel] || vowel" not in body


def test_mapping_is_rebuilt_not_merged_on_model_switch():
    """updateMouthExpressionMapping clears before it enumerates.

    VRMManager only constructs VRMAnimation when it has none, so switching
    models reuses the instance and re-runs this method, while the match loop
    only assigns on a hit. Left uncleared, a vowel the new model does not match
    keeps the previous model's expression name -- and a stale name is worse
    than an empty one, because _mouthExpressionName only falls back to the VRM
    preset when the mapping is empty. setValue then targets an expression the
    new model does not have, is silently ignored, and that vowel stops moving.
    """
    source = _read("static/vrm/vrm-animation.js")
    method = source.split("updateMouthExpressionMapping() {", 1)[1].split("\n    }", 1)[0]
    clear_at = method.index("this.mouthExpressions[vowel] = null;")
    assign_at = method.index("if (match) this.mouthExpressions[vowel] = match;")
    assert clear_at < assign_at, "the clear must happen before the match loop"


def test_fallback_path_still_clears_other_vowels_before_aa():
    """Fallback single-channel path keeps the clear-others-then-write-aa guard."""
    source = _read("static/vrm/vrm-animation.js")
    method = source.split("_updateLipSync(delta) {", 1)[1].split(
        "_updateLipSyncFormant(expressionManager, delta) {", 1
    )[0]
    assert "if (vowel === 'aa') continue;" in method
    assert "expressionManager.setValue(name, 0);" in method


# ─────────────────────── MMD side (shares the same analyzer) ─────────────────


def test_mmd_animation_lazy_instantiates_formant_analyzer():
    """mmd-animation.startLipSync lazily builds the analyzer, with fallback."""
    source = _read("static/mmd/mmd-animation.js")
    assert "new window.FormantLipSyncAnalyzer(analyser)" in source
    assert "this._formantAnalyzer = null;" in source
    assert "startLipSync(analyser) {" in source


def test_mmd_expression_prefers_formant_then_falls_back():
    """mmd-expression.update tries formant path first, then legacy setMouth."""
    source = _read("static/mmd/mmd-expression.js")
    method = source.split("update(delta) {", 1)[1]
    formant_branch = method.index("anim._formantAnalyzer")
    fallback_branch = method.index("anim.getLipSyncValue()")
    assert formant_branch < fallback_branch


def test_mmd_expression_formant_maps_all_five_vowels():
    """Formant path maps analyzer keys to MMD vowels and writes every one."""
    source = _read("static/mmd/mmd-expression.js")
    for analyzer_key, mmd_key in (("aa", "a"), ("ih", "i"), ("ou", "u"), ("ee", "e"), ("oh", "o")):
        assert f"{analyzer_key}: '{mmd_key}'" in source

    formant_method = source.split("if (anim._formantAnalyzer) {", 1)[1].split(
        "anim.getLipSyncValue()", 1
    )[0]
    assert "weights[formantKey] ?? 0" in formant_method
    assert "this.setMorphWeight(name, target);" in formant_method


def test_mmd_silent_frames_leave_iue_to_the_idle_motion():
    """On silent frames only あ/お are zeroed; い/う/え are left alone.

    Deliberate, and the same trade-off the legacy path makes: its clear-loop for
    い/う/え runs only inside the ``lipValue > 0.05`` branch, so non-speech frames
    keep playing whatever mouth track the idle VMD motion drives. Writing all
    five every frame would hold those three morphs flat for as long as
    _lipSyncEnabled is set.
    """
    source = _read("static/mmd/mmd-expression.js")
    formant_method = source.split("if (anim._formantAnalyzer) {", 1)[1].split(
        "anim.getLipSyncValue()", 1
    )[0]
    assert "const speaking = FORMANT_KEYS.some(" in formant_method
    assert "if (!speaking && vowel !== 'a' && vowel !== 'o') continue;" in formant_method

    # The legacy branch this mirrors must keep its own gate.
    legacy = source.split("anim.getLipSyncValue()", 1)[1]
    assert "if (lipValue > 0.05) {" in legacy
    assert "for (const phoneme of ['i', 'u', 'e'])" in legacy


def test_mmd_formant_map_is_hoisted_not_rebuilt_per_frame():
    """The key map is a frozen module constant, not a literal built per frame.

    update() reads it on every rendered frame; returning a fresh object literal
    from the static getter allocated a new map plus a new Object.keys array
    ~60 times a second per model.
    """
    source = _read("static/mmd/mmd-expression.js")
    assert "const FORMANT_TO_MMD_VOWEL = Object.freeze(" in source
    assert "const FORMANT_KEYS = Object.freeze(Object.keys(FORMANT_TO_MMD_VOWEL));" in source
    getter = source.split("static get FORMANT_TO_MMD_VOWEL() {", 1)[1].split("}", 1)[0]
    assert "Object.freeze" not in getter, "getter must return the hoisted constant"


def test_mmd_expression_does_not_reclamp_delta():
    """delta hygiene lives in the analyzer, not duplicated at each call site."""
    source = _read("static/mmd/mmd-expression.js")
    formant_method = source.split("if (anim._formantAnalyzer) {", 1)[1].split(
        "anim.getLipSyncValue()", 1
    )[0]
    assert "anim._formantAnalyzer.update(delta)" in formant_method
    assert "Number.isFinite(delta)" not in formant_method


def test_analyzer_clamps_delta_internally():
    """The analyzer is the single place that sanitises delta."""
    source = _read("static/vrm/vrm-lipsync-formant.js")
    update = source.split("update(delta) {", 1)[1]
    assert "Number.isFinite(delta)" in update
    assert "Math.min(Math.max(delta, 0), MAX_DELTA)" in update
    # A non-finite smoothing result must never be written back into state.
    assert "Number.isFinite(next)" in update


def test_mmd_expression_has_reset_all_lip_morphs():
    """resetAllLipMorphs() exists and zeroes every vowel morph."""
    source = _read("static/mmd/mmd-expression.js")
    assert "resetAllLipMorphs()" in source
    method = source.split("resetAllLipMorphs()", 1)[1].split("\n    }", 1)[0]
    assert "Object.keys(this.lipMorphNames)" in method
    assert "this.setMorphWeight(name, 0)" in method


def test_mmd_stop_lip_sync_calls_reset_all():
    """stopLipSync clears all five vowel morphs, not just setMouth(0)."""
    source = _read("static/mmd/mmd-animation.js")
    stop_method = source.split("stopLipSync()", 1)[1].split("\n    }", 1)[0]
    assert "resetAllLipMorphs()" in stop_method
    assert "setMouth(0)" not in stop_method
