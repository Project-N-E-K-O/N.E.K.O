# Live2D Auto-Layer — AI 自动拆图工具

一键上传角色立绘 → 自动识别并分割面部、五官、头发、身体 → 导出透明 PNG 图层包。

专为 Live2D Cubism 绑定流程设计，把最耗时的"拆图"从数小时压缩到数秒。

## 核心能力

| 阶段 | 功能 | 状态 |
|------|------|------|
| 背景移除 | rembg (isnet-anime) 一键去背 | ✅ |
| 面部检测 | lbpcascade_animeface 精确定位面部 | ✅ |
| 五官精拆 | 面部放大 → SAM 逐器官分割 (眼/嘴/眉/鼻) | ✅ |
| 头发分离 | 颜色 + 位置启发式分离头发与身体 | ✅ |
| GPT 增强 | GPT-5.5 Vision 看图定位，替代比例估算 | ✅ |
| 备用引擎 | GroundingDINO + K-Means 兜底 | ✅ |
| 图层导出 | 透明 PNG → ZIP 一键下载 | ✅ |
| 智能补图 | Stable Diffusion Inpainting 修复遮挡 | 🚧 V1 |
| PSD 导出 | Cubism 规范 100+ 层 PSD | 🚧 V2 |

## 架构

```
用户上传立绘
  → Preprocess (分辨率标准化)
  → Matting (rembg 去背景 + Alpha 精细化)
  → Segment (面部检测 → 放大 → SAM 分割五官 → 分离头发/身体)
  → Export (PNG + ZIP)
```

### 分割引擎三选一

| 引擎 | 原理 | 优点 | 缺点 |
|------|------|------|------|
| 🎯 **AnimeFace+SAM** | 面部检测 → SAM 放大精拆 | 语义理解，五官独立不粘连 | 需要 SAM 模型 |
| 🧠 **GroundedSAM** | 文字提示 → 检测 → 分割 | 通用性强 | 二次元检测精度有限 |
| 🎨 **K-Means** | 颜色聚类 | 无需模型，即时可用 | 不看语义，效果差 |

## 快速开始

### 环境要求

- Python ≥ 3.10
- 建议 16GB+ RAM（SAM 模型约 2.5GB 内存占用）

### 安装

```bash
# 克隆仓库
git clone https://github.com/qiguang113/live2d-auto-layer.git
cd live2d-auto-layer

# 安装依赖
pip install -r requirements.txt

# 安装 SAM + GroundingDINO (可选，但推荐)
pip install segment-anything groundingdino
```

### 启动

```bash
python app.py
```

浏览器打开 **http://localhost:7860**

### 首次使用

首次运行会自动下载以下模型到 `models/` 目录：

| 模型 | 大小 | 用途 |
|------|------|------|
| `sam_vit_b_01ec64.pth` | 358MB | SAM 分割 |
| `groundingdino_swint_ogc.pth` | 662MB | GroundingDINO 检测 (可选引擎) |
| `lbpcascade_animeface.xml` | 241KB | 动漫面部检测 |

### 可选：GPT-5.5 Vision 增强

在 UI 的「高级设置」中填入 API Key，GPT-5.5 会直接"看"面部图片输出精确的五官坐标，比比例估算准得多。

默认使用 Rightcode ChatGPT API (`https://right.codes/codex/v1`)，可修改 `vision_landmarks.py` 中的 `base_url` 适配任意 OpenAI 兼容接口。

## 使用步骤

1. **上传**正面角色立绘 (PNG/JPG)
2. **勾选**要提取的部位（面部/左眼/右眼/嘴/头发/身体…）
3. 可选：展开「高级设置」填入 GPT API Key 提升精度
4. 点击「🚀 开始处理」
5. 查看各图层预览
6. 点击「📦 导出全部图层」下载 ZIP

## 项目结构

```
Live2D/
├── app.py                     # Gradio Web UI 主入口
├── config.py                  # 全局配置、图层命名规范
├── requirements.txt           # Python 依赖
├── src/
│   ├── segment.py             # 分割入口 (分发到各引擎)
│   ├── anime_face.py          # 动漫面部检测 + SAM 混合分割 (主力)
│   ├── vision_landmarks.py    # GPT-5.5 Vision 地标检测
│   ├── grounded_sam.py        # GroundingDINO + SAM 联合分割
│   ├── preprocess.py          # 图像预处理
│   ├── matting.py             # rembg 背景移除 + Alpha 精细化
│   └── export.py              # PNG + ZIP 导出
├── utils/
│   └── image_utils.py         # 图像工具函数
├── models/                    # 模型权重 (自动下载)
└── outputs/                   # 导出文件
```

## License

MIT
