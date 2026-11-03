"""Static contracts for avatar model-load race fixes (PR #2273/#2276 review).

Covers:
1. VRM loadModel concurrency = token-only "last writer wins" (NO serial queue).
   Entry-allocated token supersedes prior loads; vrm-core.loadModel bails at its
   token guards before touching shared scene/currentModel, so overlapping loads
   cannot interleave remove/dispose/add. A queue was tried first but removed: it
   made a replacement load await an already-superseded predecessor, so a slow/hung
   GLTF (no timeout) blocked ordinary VRM->VRM switches (Codex P2). Mirrors MMD.
2. vrm-core.loadModel token guards: managerLoadToken passed in + guarded before
   old-model removal and before scene.add, with _disposeAbandonedVRM on bail.
3. VRMManager.cleanupUI restored: dropped in #510 while dispose() /
   app-character.js / app-interpage kept calling it behind typeof guards,
   leaving _returnButtonDragHandlers document listeners uncleaned on teardown.
4. MMD load token provenance: token captured before the first await in
   mmd-core.loadModel and passed from the manager, otherwise a superseded call
   (e.g. the failure-fallback path) can pass the stale check and stack a ghost
   mesh, or _clearModel a newer call's freshly loaded model.
"""
from pathlib import Path

from tests.unit.avatar_ui_buttons_source import read_avatar_ui_buttons_source


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_vrm_load_normalizes_legacy_bundled_idle_animation_path():
    source = (PROJECT_ROOT / "static/vrm/vrm-manager.js").read_text(
        encoding="utf-8"
    )
    helper = source.split("function normalizeBundledVrmAnimationPath", 1)[1].split(
        "class VRMManager",
        1,
    )[0]
    load_body = source.split("async _loadModelInternal", 1)[1].split(
        "async dispose()",
        1,
    )[0]

    assert "COMPRESSED_BUNDLED_VRMA_NAMES.has(assetName)" in helper
    assert "decodeURIComponent(assetName)" in helper
    assert ".replace(/\\.vrma(?=[?#]|$)/i, '.vrma.gz')" in helper
    assert "normalizeBundledVrmAnimationPath(" in load_body
    assert "window.lanlan_config?.vrmIdleAnimation" in load_body
    public_playback = source.split("async playVRMAAnimation", 1)[1].split(
        "seekVRMAAnimation",
        1,
    )[0]
    assert "normalizeBundledVrmAnimationPath(url)" in public_playback


def test_model_manager_starts_saved_idle_rotation_with_normalized_urls():
    source = (PROJECT_ROOT / "static/js/model_manager/page-controller.js").read_text(
        encoding="utf-8"
    )
    restore_body = source.split("let vrmIdleAnims =", 1)[1].split(
        "// 加载MMD待机动作选项",
        1,
    )[0]

    normalization = "vrmIdleAnims = vrmIdleAnims.map(url =>"
    start = "startIdleRotation('vrm', vrmIdleAnims);"
    assert normalization in restore_body
    assert "normalizeBundledVrmAnimationUrl(url, availableIdleAnimationValues)" in restore_body
    assert restore_body.index(normalization) < restore_body.index(start)


def test_vrm_reset_uses_the_same_desktop_framing_ratio_as_initial_load():
    manager_source = (PROJECT_ROOT / "static/vrm/vrm-manager.js").read_text(
        encoding="utf-8"
    )
    reset_body = manager_source.split("resetModelPosition()", 1)[1].split(
        "resetModelScale()",
        1,
    )[0]

    assert reset_body.count("screenHeight * 0.40") == 2
    assert "screenHeight * 0.45" not in reset_body


def test_vrm_load_model_uses_entry_token_without_blocking_queue():
    # Design: token-only "latest request wins," with no serialized queue (symmetric with mmd-manager).
    # A queue would make a new load await an old load that it has already replaced → a slow/hung GLTF (no timeout)
    # could block VRM→VRM switching indefinitely (Codex P2). Concurrency correctness is covered by the token guard in vrm-core.
    source = (PROJECT_ROOT / "static/vrm/vrm-manager.js").read_text(encoding="utf-8")

    # Allocate the token synchronously at the entry point (latest request wins) and call the loader directly, without a promise queue.
    # Starting in round 2, attach .catch cleanup (not await, per this contract): on failure, reset _loadState
    # so an idle governor does not treat a stuck 'preparing' state as permanently active (breaking throttling).
    assert "const loadToken = ++this._activeLoadToken;" in source
    assert "return this._loadModelInternal(modelUrl, options, loadToken).catch((e) => {" in source
    assert "if (this._isLoadTokenActive(loadToken)) this._loadState = 'idle';" in source
    assert "async _loadModelInternal(modelUrl, options, loadToken)" in source

    # Remove serialized queues completely: no _loadModelChain / previousLoad / legacy method names may remain
    assert "_loadModelChain" not in source, "串行队列指针应已移除（会阻塞 VRM→VRM 切换）"
    assert "previousLoad" not in source
    assert "_loadModelExclusive" not in source

    # Keep the loadModel wrapper extremely thin: bump the token → delegate directly, without awaiting any earlier load.
    # Since #2253, a thin wrapper that only tracks in-flight counts (_loadModelImplementation) sits between loadModel and token allocation,
    # so assert the two layers separately: the wrapper's only permitted await is that one delegation,
    # while the token-allocation layer must not contain any await.
    outer = source.split("async loadModel(modelUrl, options = {})", 1)[1]
    outer = outer.split("async _loadModelImplementation", 1)[0]
    assert "return await this._loadModelImplementation(modelUrl, options);" in outer, (
        "loadModel 包装层必须直接委派给 _loadModelImplementation"
    )
    outer_rest = outer.replace(
        "return await this._loadModelImplementation(modelUrl, options);", ""
    )
    assert "await this." not in outer_rest, "loadModel 包装层除委派外不得 await 任何东西"
    assert ".then(" not in outer, "loadModel 不得挂 promise 链（否则重现阻塞队列）"

    wrapper = source.split("async _loadModelImplementation", 1)[1]
    wrapper = wrapper.split("async _loadModelInternal", 1)[0]
    assert "await this." not in wrapper, "loadModel 不得 await 前序加载（否则重现阻塞）"
    assert ".then(" not in wrapper, "loadModel 不得挂 promise 链（否则重现阻塞队列）"

    # The loader must not allocate its own token (the entry point allocates and passes it in for guard checks)
    body = source.split("async _loadModelInternal", 1)[1].split("async dispose()", 1)[0]
    assert "++this._activeLoadToken" not in body

    # A superseded instance returns immediately after core returns, without writing _loadState (avoids clobbering the concurrent winner)
    post_core = body.split(
        "const result = await this.core.loadModel(modelUrl, options, loadToken);", 1
    )[1]
    superseded_branch = post_core.split(
        "if (!this._isLoadTokenActive(loadToken)) {", 1
    )[1].split("}", 1)[0]
    assert "return result;" in superseded_branch
    # Real writes have the this. prefix (_loadState mentioned in a comment does not count)
    assert "this._loadState" not in superseded_branch, "被取代分支不得写 _loadState（会 clobber 胜出者）"


def test_vrm_dispose_invalidates_without_blocking_queue():
    # Without a queue, dispose only needs to bump the token to invalidate in-flight loads (the old load bails at the core guard);
    # do not introduce a queue pointer that could block subsequent loads.
    source = (PROJECT_ROOT / "static/vrm/vrm-manager.js").read_text(encoding="utf-8")

    dispose_body = source.split("async dispose()", 1)[1].split("\n    }\n", 1)[0]
    assert "++this._activeLoadToken;" in dispose_body
    assert "_loadModelChain" not in dispose_body


def test_vrm_core_load_is_token_guarded_across_dispose():
    # Clearing the chain for liveness would reintroduce concurrency corruption: when slow old load A resumes, core.loadModel without a token guard
    # could remove/dispose/overwrite the new session B model. core.loadModel must have its own token guard
    # (matching mmd-core), and the manager must pass loadToken in.
    core_source = (PROJECT_ROOT / "static/vrm/vrm-core.js").read_text(encoding="utf-8")
    manager_source = (PROJECT_ROOT / "static/vrm/vrm-manager.js").read_text(encoding="utf-8")

    # Core accepts a token allocated by the manager
    assert "async loadModel(modelUrl, options = {}, managerLoadToken = null)" in core_source
    assert "const loadToken = managerLoadToken !== null ? managerLoadToken : this.manager._activeLoadToken;" in core_source

    load_section = core_source.split("async loadModel(modelUrl, options = {}, managerLoadToken = null)", 1)[1]
    load_section = load_section.split("async disposeVRM(", 1)[0]

    # Two guards: after GLTF loading (before touching shared scene/currentModel) and after the 3-frame wait (before writing shared camera state)
    guard_count = load_section.count("this.manager._activeLoadToken !== loadToken")
    assert guard_count >= 2, f"expected >=2 token guards in vrm-core.loadModel, found {guard_count}"

    # Guards must run before touching shared state:
    #   first before disposeVRM(old);
    #   second after the "last await (3-frame wait) and before any shared manager write" —
    #   crucially, before writing shared camera/interaction state (Codex P2: otherwise a superseded old load could use its own
    #   saved camera to overwrite the shared camera and push the visible new model off-screen); scene.add follows without an await.
    first_guard = load_section.index("this.manager._activeLoadToken !== loadToken")
    old_remove = load_section.index("await this.disposeVRM();")
    frame_wait = load_section.index("await new Promise(resolve => {")
    face_camera = load_section.index("this.manager.interaction.enableFaceCamera = false;")
    camera_write = load_section.index("this.manager.camera.position.set(")
    scene_add = load_section.index("this.manager.scene.add(vrm.scene);")
    second_guard = load_section.index("this.manager._activeLoadToken !== loadToken", first_guard + 1)
    assert first_guard < old_remove, "first guard must precede old-model disposeVRM"
    assert frame_wait < second_guard, "second guard must sit AFTER the 3-frame-wait await"
    assert second_guard < face_camera, "second guard must precede shared enableFaceCamera write"
    assert second_guard < camera_write, "second guard must precede shared camera.position write (Codex P2)"
    assert second_guard < scene_add, "second guard must precede scene.add of new model"

    # Do not add an await between the second guard and scene.add (that would expose another unguarded window)
    between = load_section[second_guard:scene_add]
    assert "await " not in between.replace("await this._disposeAbandonedVRM(vrm);", ""), (
        "二道守卫与 scene.add 之间不应有额外 await（会重现未守卫的共享写入窗口）"
    )

    # Early return must release superseded VRM resources (avoid a GPU memory leak)
    assert "async _disposeAbandonedVRM(vrm)" in core_source
    assert load_section.count("this._disposeAbandonedVRM(") >= 2

    # Manager passes loadToken to core.loadModel
    assert "const result = await this.core.loadModel(modelUrl, options, loadToken);" in manager_source


def test_vrm_cleanup_ui_is_restored_and_delegates_to_mixin():
    ui_source = (PROJECT_ROOT / "static/vrm/vrm-ui-buttons.js").read_text(encoding="utf-8")
    manager_source = (PROJECT_ROOT / "static/vrm/vrm-manager.js").read_text(encoding="utf-8")

    # cleanupUI must exist and delegate to the mixin's cleanupFloatingButtons
    # (which performs complete cleanup of _returnButtonDragHandlers / _uiWindowHandlers / RAF / DOM)
    assert "VRMManager.prototype.cleanupUI = function () {" in ui_source
    cleanup_body = ui_source.split("VRMManager.prototype.cleanupUI = function () {", 1)[1]
    cleanup_body = cleanup_body.split("};", 1)[0]
    assert "this.cleanupFloatingButtons();" in cleanup_body

    # Keep the call site in dispose() (after cleanupUI is restored, it must not remain dead code)
    assert "this.cleanupUI();" in manager_source

    # The mixin's cleanupFloatingButtons must remove the return button's document-level drag listeners
    mixin_source = read_avatar_ui_buttons_source()
    cleanup_section = mixin_source.split("ManagerPrototype.cleanupFloatingButtons = function() {", 1)[1]
    assert "this._returnButtonDragHandlers = null;" in cleanup_section


def test_mmd_load_token_captured_before_first_await_and_passed_from_manager():
    core_source = (PROJECT_ROOT / "static/mmd/mmd-core.js").read_text(encoding="utf-8")
    manager_source = (PROJECT_ROOT / "static/mmd/mmd-manager.js").read_text(encoding="utf-8")

    # Core accepts the manager-assigned token and captures it before the first await (module import)
    assert "async loadModel(modelUrl, options = {}, managerLoadToken = null)" in core_source
    load_section = core_source.split("async loadModel(modelUrl, options = {}, managerLoadToken = null)", 1)[1]
    token_capture = load_section.index("const loadToken = managerLoadToken !== null")
    module_await = load_section.index("await this._getMMDModule()")
    assert token_capture < module_await

    # Check for supersession before _clearModel so a late fallback load cannot clear the new model
    clear_model = load_section.index("this._clearModel();")
    assert load_section.index("this.manager._activeLoadToken !== loadToken") < clear_model

    # Both manager load paths must pass loadToken to core and check supersession before falling back after failure
    assert "await this.core.loadModel(modelPath, options, loadToken);" in manager_source
    assert "await this.core.loadModel(defaultModelPath, options, loadToken);" in manager_source
    fallback_guard = manager_source.index("跳过回退")
    fallback_load = manager_source.index("await this.core.loadModel(defaultModelPath, options, loadToken);")
    assert fallback_guard < fallback_load
