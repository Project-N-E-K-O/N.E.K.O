"""
Live2D 自动化拆图工具 — 全局配置
"""

from pathlib import Path

# ---- 路径配置 ----
PROJECT_ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = PROJECT_ROOT / "outputs"
MODEL_DIR = PROJECT_ROOT / "models"

# ---- 图像预处理 ----
MAX_IMAGE_SIZE = 2048          # 超大图缩放到此尺寸以内
ENHANCE_CONTRAST = True        # 是否增强对比度
CONTRAST_ALPHA = 1.15          # 对比度增强系数
SHARPEN_RADIUS = 0.5           # 锐化半径 (0 = 不锐化)

# ---- Alpha 抠图 (matting) ----
ALPHA_FEATHER_RADIUS = 2       # Alpha 边缘羽化像素
ALPHA_ERODE_ITERATIONS = 1     # 边缘腐蚀迭代 (去除黑边)

# ---- 语义分割 ----
N_COLOR_CLUSTERS = 10          # K-Means 聚类数
MIN_CLUSTER_AREA_RATIO = 0.005 # 最小区域占比 (过滤噪声)
SPATIAL_WEIGHT = 0.3           # 空间坐标在聚类中的权重

# ---- 图层命名规范 (Live2D Cubism 风格) ----
CUBISM_LAYER_NAMES = [
    "Face_Skin",
    "Hair_Front",
    "Hair_Side_L",
    "Hair_Side_R",
    "Hair_Back",
    "Eye_L_White",
    "Eye_R_White",
    "Eye_L_Pupil",
    "Eye_R_Pupil",
    "Eyebrow_L",
    "Eyebrow_R",
    "Nose",
    "Mouth",
    "Body_Upper",
    "Body_Lower",
    "Arm_L",
    "Arm_R",
    "Hand_L",
    "Hand_R",
]

# ---- 导出 ----
EXPORT_FORMAT = "PNG"          # 图层导出格式
EXPORT_ZIP_NAME = "live2d_layers.zip"


def ensure_dirs():
    """确保输出和模型目录存在"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
