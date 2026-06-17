"""
Live2D 自动化拆图工具 — Gradio Web UI v3.0
=============================================

v3.0: 用户自选部位 + 面部放大精拆五官
    - 复选框选部位，不是选"拆几层"
    - 面部区域裁剪放大后 SAM 精拆眼/嘴/眉
    - 头发/身体分离
"""

import time
from pathlib import Path
from typing import Optional

import gradio as gr
import numpy as np
from PIL import Image

from config import N_COLOR_CLUSTERS, OUTPUT_DIR, ensure_dirs
from src.preprocess import Preprocessor
from src.matting import BackgroundRemover, AlphaRefiner
from src.segment import segment_image
from src.export import LayerExporter, export_preview_image

# ---- 初始化 ----
ensure_dirs()
preprocessor = Preprocessor()
bg_remover = BackgroundRemover()
alpha_refiner = AlphaRefiner()

# GroundedSAM 实例 (懒加载)
_gsam = None


def get_gsam():
    global _gsam
    if _gsam is None:
        from src.grounded_sam import get_gsam as _get
        _gsam = _get()
    return _gsam


# ---- 可选部位 ----
AVAILABLE_PARTS = [
    "Face_Skin",
    "Eye_L",
    "Eye_R",
    "Mouth",
    "Eyebrow_L",
    "Eyebrow_R",
    "Nose",
    "Hair",
    "Body",
]

DEFAULT_PARTS = [
    "Face_Skin", "Eye_L", "Eye_R", "Mouth",
    "Eyebrow_L", "Eyebrow_R", "Hair", "Body",
]


# ---- 核心流水线 ----

def process_pipeline(
    image: Optional[Image.Image],
    face_skin: bool = True,
    eye_l: bool = True, eye_r: bool = True,
    mouth: bool = True,
    eyebrow_l: bool = True, eyebrow_r: bool = True,
    nose: bool = False,
    hair: bool = True, body: bool = True,
    method: str = "anime_face",
    n_clusters: int = N_COLOR_CLUSTERS,
    feather_radius: int = 2,
    gpt_api_key: str = "",
    progress: gr.Progress = gr.Progress(),
) -> tuple:
    """
    完整处理流水线:
    Input → Preprocess → Remove BG → Semantic Segment → Preview + Layers
    """
    if image is None:
        return None, [], {}, "⏳ 请上传一张角色立绘"

    try:
        # 收集选中的部位
        parts = []
        if face_skin: parts.append("Face_Skin")
        if eye_l: parts.append("Eye_L")
        if eye_r: parts.append("Eye_R")
        if mouth: parts.append("Mouth")
        if eyebrow_l: parts.append("Eyebrow_L")
        if eyebrow_r: parts.append("Eyebrow_R")
        if nose: parts.append("Nose")
        if hair: parts.append("Hair")
        if body: parts.append("Body")

        if not parts:
            return None, [], {}, "⚠️ 请至少选择一个部位"

        # Step 1: 预处理
        progress(0.05, desc="预处理中...")
        processed = preprocessor.preprocess(image)

        # Step 2: 背景移除
        progress(0.15, desc="移除背景中...")
        foreground = bg_remover.remove(processed)

        # Step 3: Alpha 精细化
        progress(0.25, desc="边缘优化中...")
        alpha_refiner.feather_radius = feather_radius
        foreground = alpha_refiner.refine(foreground)

        # Step 4: 语义分割
        if method == "anime_face":
            progress(0.35, desc="🔍 检测面部 → 放大 → SAM 精拆五宫中...")
            layers = segment_image(foreground, method="anime_face", parts=parts, gpt_api_key=gpt_api_key)
        elif method == "grounded_sam":
            progress(0.35, desc="加载 AI 模型 (首次较慢)...")
            gsam = get_gsam()
            progress(0.45, desc="语义检测中 (GroundingDINO)...")
            layers = segment_image(foreground, method="grounded_sam", gsam_instance=gsam)
        else:
            progress(0.45, desc="颜色分割中 (K-Means)...")
            layers = segment_image(foreground, method="color")

        if not layers:
            layers = {"Foreground": foreground}
            status_msg = "⚠️ 未检测到可分割的部位，请尝试上传更清晰的立绘"
        else:
            found = [n for n in parts if n in layers]
            missed = [n for n in parts if n not in layers]
            status_msg = f"✅ 已提取: {', '.join(found)}"
            if missed:
                status_msg += f" | ⚠️ 未找到: {', '.join(missed)}"
            status_msg += f" | 共 {len(layers)} 层"

        # Step 5: 生成预览
        progress(0.85, desc="生成预览...")
        preview = export_preview_image(layers, max_dim=1024)

        gallery_images = []
        for name, img in layers.items():
            thumb = make_thumbnail(img, size=200)
            gallery_images.append((thumb, name))

        progress(1.0, desc="完成!")

        print(f"\n{'='*50}")
        print(status_msg)
        for name in layers.keys():
            print(f"  • {name}")
        print(f"{'='*50}\n")

        return preview, gallery_images, layers, status_msg

    except Exception as e:
        import traceback
        traceback.print_exc()
        return None, [], {}, f"❌ 处理失败: {str(e)}"


def make_thumbnail(image: Image.Image, size: int = 200) -> Image.Image:
    from utils.image_utils import create_checkerboard_bg
    w, h = image.size
    ratio = min(size / w, size / h)
    new_size = (int(w * ratio), int(h * ratio))
    thumb = image.resize(new_size, Image.LANCZOS)
    bg = create_checkerboard_bg(new_size)
    bg_rgba = bg.convert("RGBA")
    composite = Image.alpha_composite(bg_rgba, thumb)
    return composite.convert("RGB")


# ---- 导出 ----

def export_layers_callback(layers: dict) -> Optional[str]:
    if not layers:
        return None
    exporter = LayerExporter()
    zip_path = exporter.export_zip(layers)
    return str(zip_path)


# ---- 重新分割 ----

def resegment_only(
    layers: dict,
    face_skin: bool, eye_l: bool, eye_r: bool,
    mouth: bool, eyebrow_l: bool, eyebrow_r: bool,
    nose: bool, hair: bool, body: bool,
    method: str, n_clusters: int, feather_radius: int,
    gpt_api_key: str = "",
) -> tuple:
    if not layers:
        return None, [], {}, "⚠️ 请先上传图片处理"

    parts = []
    if face_skin: parts.append("Face_Skin")
    if eye_l: parts.append("Eye_L")
    if eye_r: parts.append("Eye_R")
    if mouth: parts.append("Mouth")
    if eyebrow_l: parts.append("Eyebrow_L")
    if eyebrow_r: parts.append("Eyebrow_R")
    if nose: parts.append("Nose")
    if hair: parts.append("Hair")
    if body: parts.append("Body")

    # 重建前景
    first_layer = next(iter(layers.values()))
    foreground = Image.new("RGBA", first_layer.size, (0, 0, 0, 0))
    for img in layers.values():
        foreground = Image.alpha_composite(foreground, img.convert("RGBA"))

    alpha_refiner.feather_radius = feather_radius
    foreground = alpha_refiner.refine(foreground)

    if method == "anime_face":
        new_layers = segment_image(foreground, method="anime_face", parts=parts, gpt_api_key=gpt_api_key)
    elif method == "grounded_sam":
        gsam = get_gsam()
        new_layers = segment_image(foreground, method="grounded_sam", gsam_instance=gsam)
    else:
        new_layers = segment_image(foreground, method="color")

    if not new_layers:
        new_layers = {"Foreground": foreground}

    preview = export_preview_image(new_layers, max_dim=1024)
    gallery_images = [
        (make_thumbnail(img, 200), name)
        for name, img in new_layers.items()
    ]

    return preview, gallery_images, new_layers, f"✅ 重新分割完成 | {len(new_layers)} 层 | {method}"


# ---- UI ----

CUSTOM_CSS = """
.main-title { text-align: center; margin-bottom: 0.5em; }
.subtitle { text-align: center; color: #888; margin-bottom: 2em; }
.status-box { padding: 12px; border-radius: 8px; font-weight: bold; }
.checklist-row label { min-width: 100px; }
footer { display: none !important; }
"""


def create_ui():
    with gr.Blocks(title="Live2D 自动化拆图工具 v3.0") as demo:

        gr.Markdown(
            """
            # 🎨 Live2D 自动化拆图工具 v3.0
            <p class="subtitle">上传立绘 → 选部位 → AI 精拆 → 导出透明 PNG</p>
            """,
            elem_classes=["main-title"],
        )

        layers_state = gr.State({})

        with gr.Row(equal_height=True):
            # ===== 左栏 =====
            with gr.Column(scale=2):
                gr.Markdown("### 📤 1. 上传图像")
                input_image = gr.Image(
                    type="pil", label="角色立绘 (正面)",
                    sources=["upload", "clipboard"],
                )

                with gr.Accordion("✂️ 要提取的部位", open=True):
                    gr.Markdown("*勾选你要拆的部位，没勾的就不拆*")

                    with gr.Row():
                        face_skin_cb = gr.Checkbox(label="🟤 Face_Skin", value=True)
                        hair_cb = gr.Checkbox(label="💇 Hair", value=True)
                        body_cb = gr.Checkbox(label="👤 Body", value=True)

                    with gr.Row():
                        eye_l_cb = gr.Checkbox(label="👁️ Eye_L", value=True)
                        eye_r_cb = gr.Checkbox(label="👁️ Eye_R", value=True)
                        mouth_cb = gr.Checkbox(label="👄 Mouth", value=True)

                    with gr.Row():
                        eyebrow_l_cb = gr.Checkbox(label="✏️ Eyebrow_L", value=True)
                        eyebrow_r_cb = gr.Checkbox(label="✏️ Eyebrow_R", value=True)
                        nose_cb = gr.Checkbox(label="👃 Nose", value=False)

                with gr.Accordion("⚙️ 高级设置", open=False):
                    gpt_api_key_input = gr.Textbox(
                        label="🤖 GPT Vision API Key",
                        placeholder="输入 Rightcode API Key 以启用 GPT 精确五官定位 (留空则用估算)",
                        type="password",
                        value="",
                        info="可选。GPT-5.5 看图定位五官，比比例估算更准",
                    )
                    method_radio = gr.Radio(
                        choices=[
                            ("🎯 动漫面部+SAM (推荐)", "anime_face"),
                            ("🧠 GroundedSAM 语义分割", "grounded_sam"),
                            ("🎨 K-Means 颜色聚类 (兜底)", "color"),
                        ],
                        value="anime_face",
                        label="分割引擎",
                    )
                    cluster_slider = gr.Slider(
                        minimum=4, maximum=24, value=N_COLOR_CLUSTERS, step=1,
                        label="K-Means 聚类数 (仅颜色模式)",
                    )
                    feather_slider = gr.Slider(
                        minimum=0, maximum=8, value=2, step=1,
                        label="边缘羽化",
                    )

                with gr.Row():
                    process_btn = gr.Button("🚀 开始拆图", variant="primary", size="lg")
                    resegment_btn = gr.Button("🔄 重新分割", variant="secondary", size="lg")

            # ===== 右栏 =====
            with gr.Column(scale=3):
                gr.Markdown("### 📊 2. 拆分结果")

                status_text = gr.Markdown(
                    "⏳ 上传正面立绘，勾选要拆的部位，点击「开始拆图」\n\n"
                    "> 🎯 **推荐**: 动漫面部+SAM 引擎，自动检测面部并放大精拆五官",
                    elem_classes=["status-box"],
                )

                preview_image = gr.Image(
                    type="pil", label="合成预览", interactive=False,
                )

                with gr.Accordion("📑 3. 各图层预览", open=True):
                    layer_gallery = gr.Gallery(
                        label="", columns=5, height=300,
                        object_fit="contain", show_label=False,
                    )

                with gr.Row():
                    export_btn = gr.Button("📦 导出全部图层 (ZIP)", variant="primary", size="lg")
                    export_file = gr.File(label="下载", file_types=[".zip"])

        # ---- 底部 ----
        gr.Markdown(
            """
            ---
            ### 💡 使用指南
            1. **上传**正面角色立绘 (PNG/JPG)
            2. **勾选**要拆分的部位 — 不想要的去掉勾就行
            3. 点击「**开始拆图**」— 面部区域会自动放大处理，五官精度更高
            4. 不满意可以修改勾选后「**重新分割**」
            5. 「**导出全部图层**」下载 ZIP，解压即用

            ### 🔧 分割引擎说明
            | 引擎 | 原理 | 适用场景 |
            |------|------|----------|
            | **🎯 动漫面部+SAM** | 面部检测 → 裁剪放大 → SAM 精拆五官 | 正面/半侧面立绘 |
            | **🧠 GroundedSAM** | 文字提示 AI 检测 + SAM 分割 | 复杂角度 |
            | **🎨 K-Means** | 颜色聚类 | 兜底方案 |
            """
        )

        # ---- 事件绑定 ----
        cb_inputs = [
            face_skin_cb, eye_l_cb, eye_r_cb,
            mouth_cb, eyebrow_l_cb, eyebrow_r_cb,
            nose_cb, hair_cb, body_cb,
        ]

        process_btn.click(
            fn=process_pipeline,
            inputs=[input_image] + cb_inputs + [method_radio, cluster_slider, feather_slider, gpt_api_key_input],
            outputs=[preview_image, layer_gallery, layers_state, status_text],
        )

        resegment_btn.click(
            fn=resegment_only,
            inputs=[layers_state] + cb_inputs + [method_radio, cluster_slider, feather_slider, gpt_api_key_input],
            outputs=[preview_image, layer_gallery, layers_state, status_text],
        )

        export_btn.click(
            fn=export_layers_callback,
            inputs=[layers_state],
            outputs=[export_file],
        )

    return demo


if __name__ == "__main__":
    demo = create_ui()
    demo.launch(
        server_name="0.0.0.0",
        server_port=7860,
        share=False,
        show_error=True,
        css=CUSTOM_CSS,
        theme=gr.themes.Soft(),
    )
