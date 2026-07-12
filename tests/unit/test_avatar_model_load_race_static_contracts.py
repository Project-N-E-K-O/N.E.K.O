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
   app-character.js / app-interpage.js kept calling it behind typeof guards,
   leaving _returnButtonDragHandlers document listeners uncleaned on teardown.
4. MMD load token provenance: token captured before the first await in
   mmd-core.loadModel and passed from the manager, otherwise a superseded call
   (e.g. the failure-fallback path) can pass the stale check and stack a ghost
   mesh, or _clearModel a newer call's freshly loaded model.
"""
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_vrm_load_model_uses_entry_token_without_blocking_queue():
    # 设计：token-only「后到者胜」，无串行队列（与 mmd-manager 对偶）。
    # 队列会让新加载 await 一个已被自己取代的旧加载 → 慢/挂死的 GLTF（无超时）
    # 无限期阻塞 VRM→VRM 切换（Codex P2）。并发正确性由 vrm-core 的 token 守卫兜底。
    source = (PROJECT_ROOT / "static/vrm/vrm-manager.js").read_text(encoding="utf-8")

    # token 在入口同步分配（后到者胜），直接调用加载体，不经 promise 队列
    assert "const loadToken = ++this._activeLoadToken;" in source
    assert "return this._loadModelInternal(modelUrl, options, loadToken);" in source
    assert "async _loadModelInternal(modelUrl, options, loadToken)" in source

    # 串行队列必须彻底移除：不得残留 _loadModelChain / previousLoad / 旧方法名
    assert "_loadModelChain" not in source, "串行队列指针应已移除（会阻塞 VRM→VRM 切换）"
    assert "previousLoad" not in source
    assert "_loadModelExclusive" not in source

    # loadModel 包装体必须极薄：bump token → 直接委派，中间不 await 任何前序加载
    wrapper = source.split("async loadModel(modelUrl, options = {})", 1)[1]
    wrapper = wrapper.split("async _loadModelInternal", 1)[0]
    assert "await this." not in wrapper, "loadModel 不得 await 前序加载（否则重现阻塞）"
    assert ".then(" not in wrapper, "loadModel 不得挂 promise 链（否则重现阻塞队列）"

    # 加载体内不得再自行分配 token（token 由入口分配后传入，用于守卫比对）
    body = source.split("async _loadModelInternal", 1)[1].split("async dispose()", 1)[0]
    assert "++this._activeLoadToken" not in body

    # 被取代的实例在 core 返回后直接早退，不写 _loadState（避免 clobber 并发胜出者）
    post_core = body.split(
        "const result = await this.core.loadModel(modelUrl, options, loadToken);", 1
    )[1]
    superseded_branch = post_core.split(
        "if (!this._isLoadTokenActive(loadToken)) {", 1
    )[1].split("}", 1)[0]
    assert "return result;" in superseded_branch
    # 真实写入才有 this. 前缀（注释里提到 _loadState 不算）
    assert "this._loadState" not in superseded_branch, "被取代分支不得写 _loadState（会 clobber 胜出者）"


def test_vrm_dispose_invalidates_without_blocking_queue():
    # 无队列后 dispose 只需 bump token 使在途加载失效（旧 load 在 core 守卫处 bail），
    # 不得引入任何会阻塞后续加载的队列指针。
    source = (PROJECT_ROOT / "static/vrm/vrm-manager.js").read_text(encoding="utf-8")

    dispose_body = source.split("async dispose()", 1)[1].split("\n    }\n", 1)[0]
    assert "++this._activeLoadToken;" in dispose_body
    assert "_loadModelChain" not in dispose_body


def test_vrm_core_load_is_token_guarded_across_dispose():
    # 清链修活性会重新引入并发损坏：慢的旧 load A 恢复后 core.loadModel 无 token 守卫
    # 会 remove/dispose/覆写新会话 B 的模型。core.loadModel 必须自带 token 守卫
    # （与 mmd-core 对偶），manager 必须把 loadToken 传进去。
    core_source = (PROJECT_ROOT / "static/vrm/vrm-core.js").read_text(encoding="utf-8")
    manager_source = (PROJECT_ROOT / "static/vrm/vrm-manager.js").read_text(encoding="utf-8")

    # core 接收 manager 分配的 token
    assert "async loadModel(modelUrl, options = {}, managerLoadToken = null)" in core_source
    assert "const loadToken = managerLoadToken !== null ? managerLoadToken : this.manager._activeLoadToken;" in core_source

    load_section = core_source.split("async loadModel(modelUrl, options = {}, managerLoadToken = null)", 1)[1]
    load_section = load_section.split("async disposeVRM(", 1)[0]

    # 两道守卫：GLTF 加载后（触碰共享 scene/currentModel 前）与 3 帧等待后（共享相机写入前）
    guard_count = load_section.count("this.manager._activeLoadToken !== loadToken")
    assert guard_count >= 2, f"expected >=2 token guards in vrm-core.loadModel, found {guard_count}"

    # 守卫必须在触碰共享状态之前：
    #   一道在 disposeVRM(old) 之前；
    #   二道在「最后一个 await（3 帧等待）之后、任何共享 manager 写入之前」——
    #   关键是必须先于共享相机/interaction 写入（Codex P2：否则被取代的旧加载会用自己的
    #   存档相机覆写共享相机把可见的新模型顶到屏外），scene.add 随后无 await 一并覆盖。
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

    # 二道守卫之后到 scene.add 之间不得再插入 await（否则又暴露一个未守卫窗口）
    between = load_section[second_guard:scene_add]
    assert "await " not in between.replace("await this._disposeAbandonedVRM(vrm);", ""), (
        "二道守卫与 scene.add 之间不应有额外 await（会重现未守卫的共享写入窗口）"
    )

    # 早退释放被取代的 VRM 资源（避免显存泄漏）
    assert "async _disposeAbandonedVRM(vrm)" in core_source
    assert load_section.count("this._disposeAbandonedVRM(") >= 2

    # manager 把 loadToken 传进 core.loadModel
    assert "const result = await this.core.loadModel(modelUrl, options, loadToken);" in manager_source


def test_vrm_cleanup_ui_is_restored_and_delegates_to_mixin():
    ui_source = (PROJECT_ROOT / "static/vrm/vrm-ui-buttons.js").read_text(encoding="utf-8")
    manager_source = (PROJECT_ROOT / "static/vrm/vrm-manager.js").read_text(encoding="utf-8")

    # cleanupUI 必须存在并委托 mixin 的 cleanupFloatingButtons
    # （后者负责 _returnButtonDragHandlers / _uiWindowHandlers / RAF / DOM 的完整清理）
    assert "VRMManager.prototype.cleanupUI = function () {" in ui_source
    cleanup_body = ui_source.split("VRMManager.prototype.cleanupUI = function () {", 1)[1]
    cleanup_body = cleanup_body.split("};", 1)[0]
    assert "this.cleanupFloatingButtons();" in cleanup_body

    # dispose() 侧的调用点保持存在（cleanupUI 恢复后不再是 dead call）
    assert "this.cleanupUI();" in manager_source

    # mixin 的 cleanupFloatingButtons 必须清理 return 按钮的 document 级拖拽监听
    mixin_source = (PROJECT_ROOT / "static/avatar/avatar-ui-buttons.js").read_text(encoding="utf-8")
    cleanup_section = mixin_source.split("ManagerPrototype.cleanupFloatingButtons = function() {", 1)[1]
    assert "this._returnButtonDragHandlers = null;" in cleanup_section


def test_mmd_load_token_captured_before_first_await_and_passed_from_manager():
    core_source = (PROJECT_ROOT / "static/mmd/mmd-core.js").read_text(encoding="utf-8")
    manager_source = (PROJECT_ROOT / "static/mmd/mmd-manager.js").read_text(encoding="utf-8")

    # core 接收 manager 分配的 token，且在首个 await（模块导入）之前捕获
    assert "async loadModel(modelUrl, options = {}, managerLoadToken = null)" in core_source
    load_section = core_source.split("async loadModel(modelUrl, options = {}, managerLoadToken = null)", 1)[1]
    token_capture = load_section.index("const loadToken = managerLoadToken !== null")
    module_await = load_section.index("await this._getMMDModule()")
    assert token_capture < module_await

    # 取代检查必须先于 _clearModel，避免迟到的回退加载清掉新模型
    clear_model = load_section.index("this._clearModel();")
    assert load_section.index("this.manager._activeLoadToken !== loadToken") < clear_model

    # manager 两条加载路径都要把 loadToken 传给 core，且失败回退前先检查是否已被取代
    assert "await this.core.loadModel(modelPath, options, loadToken);" in manager_source
    assert "await this.core.loadModel(defaultModelPath, options, loadToken);" in manager_source
    fallback_guard = manager_source.index("跳过回退")
    fallback_load = manager_source.index("await this.core.loadModel(defaultModelPath, options, loadToken);")
    assert fallback_guard < fallback_load
