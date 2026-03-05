# -*- coding: utf-8 -*-
"""
CosyVoice 新手引导音频批量生成脚本
用法：
    python scripts/generate_tutorial_audio_cosyvoice.py --cosyvoice-dir /path/to/CosyVoice-main
    python scripts/generate_tutorial_audio_cosyvoice.py --cosyvoice-dir /path/to/CosyVoice-main --force
    环境变量 COSYVOICE_DIR 也可代替 --cosyvoice-dir
输出目录：static/tutorial_audio/<lang>/
"""

import os
import sys
import re
import argparse
import io

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ('utf-8', 'utf8'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# 路径由 CLI 参数或环境变量传入，不在此硬编码
COSYVOICE_MODEL = 'pretrained_models/iic/CosyVoice-300M-Instruct'
SPEAKER = '中文女'
SPEED   = 1.0
INSTRUCT = (
    "Very young girl, high clear voice like a child. "
    "No chest resonance, no nasal tone, completely pure and bright. "
    "Lively and energetic, bouncy speech pattern, genuinely childlike and natural."
    "<|endofprompt|>"
)

TUTORIAL_STEPS = {
    "zh-CN": [
        # ── 主页引导 ──────────────────────────────────────────
        ("home_step1",   "欢迎来到 neko ！这就是你的猫娘啦！接下来呢，我会带你熟悉各项功能哦~"),
        ("home_step1a",  "试试点击猫娘吧！每次点击都会触发不同的表情和动作变化哦。体验完后点击下一步继续~"),
        ("home_step1b",  "你可以拖拽猫娘移动位置，也可以用鼠标滚轮放大缩小，试试看吧~"),
        ("home_step1c",  "点击这个锁可以锁定猫娘位置，防止误触移动。再次点击就可以解锁啦~"),
        ("home_step2",   "在这里和猫娘进行文字对话，输入想法她会给你有趣的回应~"),
        ("home_step3",   "浮动工具栏包含多个实用功能按钮，让我来为你逐一介绍吧~"),
        ("home_step4",   "启用语音控制，猫娘通过语音识别理解你的话语~"),
        ("home_step5",   "分享屏幕、窗口或标签页，让猫娘看到你的画面~"),
        ("home_step6",   "打开 Agent 工具面板，使用各类辅助功能~"),
        ("home_step7",   "让猫娘暂时离开并隐藏界面，需要时再点击请她回来就好啦~"),
        ("home_step8",   "打开设置面板，接下来会依次介绍设置里的各个项目哦~"),
        ("home_step9",   "开启后猫娘会主动发起对话，频率可在此调整~"),
        ("home_step10",  "开启后猫娘会主动读取画面信息，间隔可在此调整~"),
        ("home_step11",  "在这里调整猫娘的性格、形象、声音等~"),
        ("home_step12",  "在这里配置 AI 服务的 API 密钥，这是和猫娘互动的必要配置~"),
        ("home_step13",  "在这里查看与管理猫娘的记忆内容~"),
        ("home_step14",  "在这里进入 Steam 创意工坊页面，管理订阅内容~"),
        # ── 系统托盘 ──────────────────────────────────────────
        ("systray_location", "neko 的图标会出现在屏幕右下角的系统托盘里，点击一下就能找到它哦。如果看不到，可以先展开托盘的小箭头，查看全部图标。"),
        ("systray_menu",     "右下角托盘里会有 neko 的图标，右键点击会出现很多选项。下面是两个常用功能："),
        ("systray_reset",    "如果猫娘被拖到屏幕外或位置不理想，点击这个选项可以让她回到默认位置~"),
        ("systray_hotkey",   "在这里可以设置全局快捷键，让你更高效地控制 neko~"),
        ("systray_feedback", "遇到问题或有好的建议？点这里上传日志提交反馈~"),
        ("systray_exit",     "想要关闭 neko 时，在这里点击退出即可。因为窗口是无边框的，所以托盘菜单是退出应用的主要方式~"),
        ("systray_complete", "引导完成啦！恭喜你完成了所有引导！现在你已经了解了 neko 的主要功能。开始享受和猫娘的互动吧！如需重新查看引导，可以在设置中点击重置引导~"),
        # ── 模型管理器 ────────────────────────────────────────
        ("model_manager_common_step1",  "首先选择您要使用的模型类型：Live2D，也就是二维动画；或 VRM，也就是三维模型。"),
        ("model_manager_common_step2",  "点击这里上传您的模型文件，支持 Live2D 和 VRM 格式。"),
        ("model_manager_live2d_step1",  "从已上传的 Live2D 模型中选择要使用的模型。"),
        ("model_manager_live2d_step2",  "为 Live2D 模型选择动作，点击播放动作按钮可以预览效果哦。"),
        ("model_manager_live2d_step3",  "为 Live2D 模型选择表情，可以设置常驻表情让模型保持该表情。"),
        ("model_manager_live2d_step4",  "选择一个常驻表情，让模型持续保持该表情，直到你再次更改。"),
        ("model_manager_live2d_step5",  "进入前请先选择一个模型。点击这里配置 Live2D 模型的情感表现，可为不同的情感设置对应的表情和动作组合。"),
        ("model_manager_live2d_step6",  "点击这里进入捏脸系统，可以精细调整 Live2D 模型的面部参数，打造独特的猫娘形象~"),
        ("model_manager_vrm_step1",     "从已上传的 VRM 模型中选择要使用的三维模型。"),
        ("model_manager_vrm_step2",     "为 VRM 模型选择动画，VRM 支持更丰富的三维动画效果哦。"),
        ("model_manager_vrm_step3",     "点击这个按钮可以预览选中的 VRM 动画效果。"),
        ("model_manager_vrm_step4",     "为 VRM 模型选择表情，VRM 模型支持多种面部表情。"),
        ("model_manager_vrm_step5",     "VRM 模型支持专业的三维光照系统，您可以调整环境光、主光源、补光和轮廓光，打造完美的视觉效果。"),
        ("model_manager_vrm_step6",     "调整环境光强度，环境光影响整体亮度，数值越高模型越亮。"),
        ("model_manager_vrm_step7",     "调整主光源强度，主光源是主要的照明来源，影响模型的明暗对比。"),
        ("model_manager_vrm_step8",     "调整整体曝光强度，数值越高整体越亮，越低则更暗更有对比。"),
        ("model_manager_vrm_step9",     "选择不同的色调映射算法，决定画面亮部和暗部的呈现风格。"),
        # ── 捏脸系统 ──────────────────────────────────────────
        ("parameter_editor_step1",  "首先选择要编辑的 Live2D 模型，只有选择了模型后，才能调整参数哦。"),
        ("parameter_editor_step2",  "这里显示了模型的所有可调参数，每个参数控制模型的不同部分，如眼睛大小、嘴巴形状、头部角度等。"),
        ("parameter_editor_step3",  "左侧是实时预览区域，调整参数时可以立即看到模型的变化效果。"),
        ("parameter_editor_step4",  "点击这个按钮可以将所有参数重置为默认值，如果调整效果不满意，可以用这个功能重新开始。"),
        # ── 情感管理器 ────────────────────────────────────────
        ("emotion_manager_step1",  "首先选择要配置情感的 Live2D 模型，每个模型可以有独立的情感配置，选好模型后才能进入下一步哦。"),
        ("emotion_manager_step2",  "这里可以为不同的情感，如开心、悲伤、生气等，配置对应的表情和动作组合。猫娘会根据对话内容自动切换情感表现。"),
        ("emotion_manager_step3",  "点击这个按钮可以将情感配置重置为默认值。"),
        # ── 角色管理器 ────────────────────────────────────────
        ("chara_manager_step1",   "这是您的主人档案，档案名是必填项，其他信息如性别、昵称等都是可选的。这些信息会影响猫娘对您的称呼和态度哦。"),
        ("chara_manager_step2",   "输入您的名字或昵称，猫娘会用这个名字来称呼您，最多二十个字符。"),
        ("chara_manager_step3",   "这是可选项，您可以输入您的性别或其他相关信息，这会影响猫娘对您的称呼方式。"),
        ("chara_manager_step4",   "这是可选项，您可以为自己设置一个昵称，猫娘可能会用这个昵称来称呼您。"),
        ("chara_manager_step5",   "点击这里配置 AI 服务的 API Key，这是猫娘能够进行对话的必要配置。"),
        ("chara_manager_step6",   "这里可以创建和管理多个猫娘角色，每个角色都有独特的性格、Live2D 形象和语音设定，您可以在不同的角色之间切换哦。"),
        ("chara_manager_step7",   "点击这个按钮创建一个新的猫娘角色，您可以为她设置名字、性格、形象和语音，每个角色都是独立的，有自己的记忆和性格。"),
        ("chara_manager_step8",   "点击猫娘名称可以展开或折叠详细信息，每个猫娘都有独立的设定，包括基础信息和进阶配置。"),
        ("chara_manager_step9",   "这是猫娘的名字，也是她的唯一标识，创建后可以通过修改名称按钮来更改。"),
        ("chara_manager_step10",  "这些是猫娘的性格设定字段，如性格、背景、爱好、口头禅等，您可以自由添加和编辑这些属性，让每个猫娘都有独特的个性哦。"),
        ("chara_manager_step11",  "点击此按钮可以将这个猫娘设为当前活跃角色，切换后主页和对话界面会使用该角色的形象和性格。"),
        ("chara_manager_step12",  "点击展开进阶设定，可以配置 Live2D 模型、语音 ID，以及添加自定义性格属性，如性格、爱好、口头禅等。"),
        ("chara_manager_step13",  "点击此链接可以选择或更换猫娘的 Live2D 形象或 VRM 模型，不同的模型会带来不同的视觉体验哦。"),
        ("chara_manager_step14",  "选择猫娘的语音角色，不同的语音 ID 对应不同的声音特征，让您的猫娘拥有独特的声音~"),
        # ── API 设置 ──────────────────────────────────────────
        ("settings_step1",  "如果您还没有 API Key，可以直接选择免费版开始使用，无需注册任何账号！"),
        ("settings_step2",  "这是最重要的设置，核心 API 服务商负责对话功能。"),
        ("settings_step3",  "将您选择的 API 服务商的 API Key 粘贴到这里，如果选择了免费版，这个字段可以留空。"),
        ("settings_step4",  "点击这里展开高级选项，包括辅助 API 配置和自定义 API 配置。"),
        ("settings_step5",  "辅助 API 负责记忆管理和自定义语音功能。免费版完全免费，但不支持自定义语音；阿里推荐选择，支持自定义语音；智谱支持 Agent 模式；OpenAI 记忆管理能力强。注意：只有阿里支持自定义语音功能哦。"),
        ("settings_step6",  "如果您选择了阿里作为辅助 API，需要在这里填写阿里的 API Key，如果不填写，系统会使用核心 API 的 Key。"),
        ("settings_step7",  "点击这里可以展开自定义 API 配置选项，如果您想使用自己的 API 服务器或其他兼容的 API 服务，可以在这里配置。"),
        ("settings_step8",  "勾选这个选项可以启用自定义 API 配置，启用后您可以为不同的功能模块配置独立的 API。"),
        ("settings_step9",  "摘要模型用于生成对话摘要和记忆管理，您可以配置独立的 API 服务来处理摘要生成任务。"),
        # ── 语音克隆 ──────────────────────────────────────────
        ("voice_clone_step1",  "语音克隆功能需要使用阿里云 API，请确保您已经在 API 设置中配置了阿里云的 API Key 哦。"),
        ("voice_clone_step2",  "选择您上传的音频文件的语言，这帮助系统更准确地识别和克隆声音特征。"),
        ("voice_clone_step3",  "输入一个十个字符以内的前缀，只能用数字和英文字母，这个前缀会作为克隆音色的标识。"),
        ("voice_clone_step4",  "点击这个按钮开始克隆您的音色，系统会处理音频并生成一个独特的音色 ID。"),
        ("voice_clone_step5",  "这里显示所有已成功克隆的音色，您可以在角色管理中选择这些音色来为猫娘配音哦。"),
        # ── Steam 创意工坊 ────────────────────────────────────
        ("steam_workshop_step1",  "这里显示所有您已订阅的 Steam 创意工坊内容，点击卡片可以查看详情或进行操作。"),
        ("steam_workshop_step2",  "如果您想使用 Steam 创意工坊中的语音音色，需要前往 Live2D 设置页面手动注册哦。"),
        # ── 记忆浏览 ──────────────────────────────────────────
        ("memory_browser_step1",  "刚刚结束的对话内容需要稍等片刻才会载入，如果没有看到最新的对话，可以点击猫娘名称来刷新。"),
        ("memory_browser_step2",  "这里列出了所有猫娘的记忆库，点击一个猫娘的名称可以查看和编辑她的对话历史。"),
        ("memory_browser_step3",  "开启这个功能后，系统会自动整理和优化记忆内容，提高对话质量，建议保持开启状态哦~"),
        ("memory_browser_step4",  "这里显示选中猫娘的所有对话记录，您可以在这里查看、编辑或删除特定的对话内容。"),
        # ── 结尾 ──────────────────────────────────────────────
        ("completed",        "引导完成啦！祝你使用愉快~"),
        ("reset_hint",       "如果想再次查看引导，可以前往记忆浏览页面，在新手引导区域重置哦。"),
        ("fullscreen_prompt","为了获得最佳的引导体验，建议进入全屏模式！全屏模式下引导内容会更清晰，不会被其他元素遮挡。"),
        ("drag_hint",        "按住拖动以移动提示框。"),
    ]
}


def preprocess_text(text):
    text = re.sub(r'N\.E\.K\.O', 'neko', text, flags=re.IGNORECASE)
    text = re.sub(r'\bNEKO\b', 'neko', text)
    return text


def generate(lang, out_dir, cosyvoice_dir, force=False):
    import warnings
    warnings.filterwarnings('ignore')

    steps = TUTORIAL_STEPS.get(lang)
    if not steps:
        print(f"[错误] 暂不支持语言: {lang}")
        sys.exit(1)

    # 在 os.chdir 之前将路径转为绝对路径，避免 chdir 后相对路径失效
    out_dir_abs = os.path.abspath(out_dir)
    os.makedirs(out_dir_abs, exist_ok=True)

    cosyvoice_abs = os.path.abspath(cosyvoice_dir)
    if not os.path.isdir(cosyvoice_abs):
        print(f"[错误] CosyVoice 目录不存在: {cosyvoice_abs}")
        sys.exit(1)
    os.chdir(cosyvoice_abs)
    if cosyvoice_abs not in sys.path:
        sys.path.insert(0, os.path.join(cosyvoice_abs, 'third_party', 'Matcha-TTS'))
        sys.path.insert(0, cosyvoice_abs)

    import soundfile as sf
    import torch
    from cosyvoice.cli.cosyvoice import CosyVoice

    model_path = os.path.join(cosyvoice_abs, COSYVOICE_MODEL)
    print(f"[CosyVoice] 加载模型: {model_path}")
    model = CosyVoice(model_path)
    print(f"[CosyVoice] 模型加载完成，声色: {SPEAKER}，语速: {SPEED}")

    ok = skip = fail = 0
    total = len(steps)

    for i, (name, text) in enumerate(steps, 1):
        out_path = os.path.join(out_dir_abs, f"{name}.wav")
        if os.path.exists(out_path) and not force:
            print(f"[{i:02d}/{total}] 跳过（已存在）: {name}.wav")
            skip += 1
            ok += 1
            continue

        text_processed = preprocess_text(text)
        try:
            chunks = []
            for j in model.inference_instruct(text_processed, SPEAKER, INSTRUCT, stream=False, speed=SPEED):
                chunks.append(j['tts_speech'])
            audio = torch.cat(chunks, dim=1).numpy().T
            tmp_path = out_path + '.tmp'
            sf.write(tmp_path, audio, model.sample_rate)
            os.replace(tmp_path, out_path)
            print(f"[{i:02d}/{total}] OK  {name}.wav")
            ok += 1
        except Exception as e:
            print(f"[{i:02d}/{total}] FAIL {name}  错误: {e}")
            tmp_path = out_path + '.tmp'
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            fail += 1

    print(f"\n完成：{ok} 成功（其中 {skip} 个跳过），{fail} 失败。输出目录：{out_dir}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cosyvoice-dir", default=os.environ.get("COSYVOICE_DIR"),
                        help="CosyVoice-main 根目录路径（也可通过环境变量 COSYVOICE_DIR 传入）")
    parser.add_argument("--lang",  default="zh-CN")
    parser.add_argument("--out",   default=None)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    if not args.cosyvoice_dir:
        parser.error("请通过 --cosyvoice-dir 或环境变量 COSYVOICE_DIR 指定 CosyVoice 目录")

    out_dir = args.out or os.path.join("static", "tutorial_audio", args.lang)
    generate(args.lang, out_dir, cosyvoice_dir=args.cosyvoice_dir, force=args.force)
