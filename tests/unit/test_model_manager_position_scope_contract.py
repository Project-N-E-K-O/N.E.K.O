"""模型管理页「位置隔离」契约测试。

管理页会自动把模型摆到安全区中心（临时位移），因此本页任何位置持久化都必须被改写成
「后端已经存着的那个位置」，否则下一次自动保存 / 点保存设置就会把居中位置写回全局
偏好，主页面也跟着变。

改写的实现方式是：运行时（Live2D / VRM / MMD）的 ``saveUserPreferences`` 开头判断
``window.ModelManagerSafetyZone`` 在不在，在就调用 ``rewritePositionWrite()`` 拿改写
后的 position / display / viewport；PNGTuber 则在 page-controller 里直接摘掉位置字段。

这组测试把「两边必须对齐」的约定钉住：参数顺序、改写调用、PNGTuber 的位置字段名。
上游哪天改了签名或字段名，这里会直接报红，而不是让保护悄悄失效。
"""

import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
GUARD_JS = PROJECT_ROOT / "static" / "js" / "model_manager" / "safety-zone-guard.js"
PAGE_CONTROLLER_JS = PROJECT_ROOT / "static" / "js" / "model_manager" / "page-controller.js"
PNGTUBER_CORE_JS = PROJECT_ROOT / "static" / "pngtuber-core.js"
MODEL_MANAGER_TEMPLATE = PROJECT_ROOT / "templates" / "model_manager.html"

# 三种运行时的 saveUserPreferences 形参表：下标 1=position、4=display、5=viewport
RUNTIME_PREFERENCE_SAVERS = {
    "static/live2d/live2d-core.js": "modelPath, position, scale, parameters, display, viewport",
    "static/vrm/vrm-core.js": "modelPath, position, scale, rotation, display, viewport, cameraPosition",
    "static/mmd/mmd-core.js": "modelPath, position, scale, rotation, display, viewport, cameraPosition",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _function_body(source: str, header: str) -> str:
    """截取从 header 起到下一个同级 ``}`` 之间的大致区间，够用于字符串断言。"""
    start = source.index(header)
    return source[start:start + 1200]


def _parse_signature_params(source: str, expected_params: str):
    # 只认定义（async ... ) {），不要误匹配到调用点
    match = re.search(r"async saveUserPreferences\(([^)]*)\)\s*\{", source)
    assert match, "找不到 saveUserPreferences 的定义"
    actual = re.sub(r"\s+", "", match.group(1))
    assert actual == re.sub(r"\s+", "", expected_params), (
        f"saveUserPreferences 形参变了: {actual}"
    )
    return [part.strip() for part in match.group(1).split(",")]


def test_runtime_save_signatures_keep_position_display_viewport_slots():
    """参数位置是改写逻辑的隐式约定，必须钉死。"""
    for relative_path, expected_params in RUNTIME_PREFERENCE_SAVERS.items():
        params = _parse_signature_params(_read(PROJECT_ROOT / relative_path), expected_params)
        assert params[0] == "modelPath"
        assert params[1] == "position", f"{relative_path} 第 2 个参数不再是 position"
        assert params[4] == "display", f"{relative_path} 第 5 个参数不再是 display"
        assert params[5] == "viewport", f"{relative_path} 第 6 个参数不再是 viewport"


def test_runtimes_ask_the_safety_zone_module_before_saving_position():
    """运行时必须真的去问管理页模块要改写值，改写结果必须回写到 position/display/viewport。"""
    for relative_path in RUNTIME_PREFERENCE_SAVERS:
        source = _read(PROJECT_ROOT / relative_path)
        body = _function_body(source, "async saveUserPreferences(")
        assert "window.ModelManagerSafetyZone" in body, f"{relative_path} 没有接入位置改写"
        assert "rewritePositionWrite(modelPath, position, display, viewport)" in body, (
            f"{relative_path} 调用改写函数的参数不对"
        )
        assert "position = scoped.position;" in body
        assert "display = scoped.display;" in body
        assert "viewport = scoped.viewport;" in body


def test_runtime_rewrite_happens_before_position_is_consumed():
    """VRM 会在函数开头就把 display 快照成 displaySnapshot，改写必须排在它前面。"""
    source = _read(PROJECT_ROOT / "static" / "vrm" / "vrm-core.js")
    body = _function_body(source, "async saveUserPreferences(")
    assert body.index("rewritePositionWrite(") < body.index("const displaySnapshot = display")


def test_safety_zone_module_exposes_rewrite_position_write():
    source = _read(GUARD_JS)
    assert "async function rewritePositionWrite(modelPath, position, display, viewport) {" in source
    assert "rewritePositionWrite" in _function_body(source, "window.ModelManagerSafetyZone = {")
    # 管理页才加载这个模块：非管理页不装载，运行时里的判断自然不成立。
    assert "model-manager-page" in source


def test_pngtuber_position_fields_are_stripped_before_staging():
    """PNGTuber 不写位置：暂存前要把两套布局的 offset 字段都摘掉。"""
    pngtuber_source = _read(PNGTUBER_CORE_JS)
    layout_fields = set(re.findall(r"offset[XY]:\s*'([^']+)'", pngtuber_source))
    assert layout_fields == {
        "offset_x",
        "offset_y",
        "mobile_offset_x",
        "mobile_offset_y",
    }, f"PNGTuber 的位置字段名变了: {sorted(layout_fields)}"

    staging = _function_body(_read(PAGE_CONTROLLER_JS), "function stageModelManagerPNGTuberPlacement(")
    for field in sorted(layout_fields):
        assert f"'{field}'" in staging, f"暂存 PNGTuber 配置时没有摘掉位置字段 {field}"
    # 摘掉的只是位置：缩放等其它字段仍然照常合并。
    assert "mergePNGTuberConfigForSave(" in staging
    assert "delete placementForSave[key]" in staging


def test_pngtuber_staging_still_marks_unsaved_changes():
    """位置不落库，但暂存动作本身不能失效（否则保存按钮不会解锁）。"""
    staging = _function_body(_read(PAGE_CONTROLLER_JS), "function stageModelManagerPNGTuberPlacement(")
    assert "window.hasUnsavedChanges = true;" in staging
    assert "savePositionBtn.disabled = false;" in staging


def test_safety_zone_script_is_loaded_on_the_manager_page():
    template = _read(MODEL_MANAGER_TEMPLATE)
    script = "/static/js/model_manager/safety-zone-guard.js"
    assert script in template, "模型管理页没有加载安全区模块"
    assert template.index(script) > template.index("/static/js/model_manager/background-model-drag.js")
