"""Static contracts for avatar model-load race fixes (PR #2273 review follow-up).

Covers three fixes:
1. VRM loadModel serialization: entry-allocated token + promise queue, so two
   overlapping core.loadModel runs can no longer interleave remove/dispose/add
   on the shared scene (ghost orphan model / newer model disposed by stale call).
2. VRMManager.cleanupUI restored: it was dropped in #510 while dispose() /
   app-character.js / app-interpage.js kept calling it behind typeof guards,
   leaving _returnButtonDragHandlers document listeners uncleaned on teardown.
3. MMD load token provenance: token must be captured before the first await in
   mmd-core.loadModel and passed from the manager, otherwise a superseded call
   (e.g. the failure-fallback path) can pass the stale check and stack a ghost
   mesh, or _clearModel a newer call's freshly loaded model.
"""
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_vrm_load_model_is_serialized_with_entry_token():
    source = (PROJECT_ROOT / "static/vrm/vrm-manager.js").read_text(encoding="utf-8")

    # token 在入口同步分配（后到者胜），加载体经串行队列独占执行
    assert "const loadToken = ++this._activeLoadToken;" in source
    assert "const previousLoad = this._loadModelChain || Promise.resolve();" in source
    assert "return this._loadModelExclusive(modelUrl, options, loadToken);" in source
    assert "async _loadModelExclusive(modelUrl, options, loadToken)" in source

    # 排队期间被更新调用取代的加载体必须整体跳过（连网络请求都不发起）
    wrapper = source.split("async loadModel(modelUrl, options = {})", 1)[1]
    wrapper = wrapper.split("async _loadModelExclusive", 1)[0]
    assert "if (!this._isLoadTokenActive(loadToken)) return null;" in wrapper

    # 队列指针必须吞掉异常，避免下一次加载被上一次的 rejection 卡住
    assert "this._loadModelChain = currentLoad.then(() => undefined, () => undefined);" in source

    # 加载体内不得再自行分配 token（否则队列的取代检查失效）
    exclusive_body = source.split("async _loadModelExclusive", 1)[1]
    assert "++this._activeLoadToken" not in exclusive_body.split("async dispose()", 1)[0]


def test_vrm_dispose_clears_load_queue_pointer():
    # Codex P2：dispose 只 bump token 不清 _loadModelChain 时，被取代/挂死的旧 load
    # 会作为 previousLoad 把 dispose 后的新 load 堵在队列后（活性 bug）。dispose 必须
    # 在 bump token 后清空队列指针，新 load 的 previousLoad 才退回 Promise.resolve()。
    source = (PROJECT_ROOT / "static/vrm/vrm-manager.js").read_text(encoding="utf-8")

    dispose_body = source.split("async dispose()", 1)[1].split("\n    }\n", 1)[0]
    assert "++this._activeLoadToken;" in dispose_body
    assert "this._loadModelChain = null;" in dispose_body
    # 清链必须紧跟在 token bump 之后（同一失效动作），不能漏在别处
    assert dispose_body.index("++this._activeLoadToken;") < dispose_body.index("this._loadModelChain = null;")


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

    # 两道守卫：GLTF 加载后（触碰共享 scene/currentModel 前）与 scene.add 前
    guard_count = load_section.count("this.manager._activeLoadToken !== loadToken")
    assert guard_count >= 2, f"expected >=2 token guards in vrm-core.loadModel, found {guard_count}"

    # 守卫必须在触碰共享状态之前：一道在 disposeVRM(old) 之前，二道在 scene.add 之前
    first_guard = load_section.index("this.manager._activeLoadToken !== loadToken")
    old_remove = load_section.index("await this.disposeVRM();")
    scene_add = load_section.index("this.manager.scene.add(vrm.scene);")
    second_guard = load_section.index("this.manager._activeLoadToken !== loadToken", first_guard + 1)
    assert first_guard < old_remove, "first guard must precede old-model disposeVRM"
    assert second_guard < scene_add, "second guard must precede scene.add of new model"

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
    mixin_source = (PROJECT_ROOT / "static/avatar-ui-buttons.js").read_text(encoding="utf-8")
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
