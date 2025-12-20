import os
import sys
import asyncio
import json
import uuid
import logging
import uvicorn
import numpy as np
import config
import torch
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("CosyVoice-Server")

app = FastAPI()
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
COSYVOICE_PROJECT_ROOT = os.path.join(CURRENT_DIR, "CosyVoice")

# 2. 将该路径加入 Python 搜索路径
if COSYVOICE_PROJECT_ROOT not in sys.path:
    sys.path.insert(0, COSYVOICE_PROJECT_ROOT)

# 3. 【关键步骤】处理 third_party 依赖 (Matcha-TTS)
# CosyVoice 内部经常引用 third_party/Matcha-TTS，如果不加这个，可能会报 "No module named 'matcha'"
MATCHA_PATH = os.path.join(COSYVOICE_PROJECT_ROOT, "third_party", "Matcha-TTS")
if os.path.exists(MATCHA_PATH) and MATCHA_PATH not in sys.path:
    sys.path.insert(0, MATCHA_PATH)

print(f"已添加 CosyVoice 路径: {COSYVOICE_PROJECT_ROOT}")

# 4. 现在可以正常导入了
try:
    from cosyvoice.cli.cosyvoice import CosyVoice3
    from cosyvoice.utils.file_utils import load_wav
except ImportError as e:
    print("---------------------------------------------------------")
    print("❌ 导入失败！请检查路径是否正确。")
    print(f"当前尝试加载的路径: {COSYVOICE_PROJECT_ROOT}")
    print(f"错误信息: {e}")
    print("请确认该路径下是否有 'cosyvoice' 文件夹。")
    print("---------------------------------------------------------")
    sys.exit(1)


MODEL_DIR = os.path.join(COSYVOICE_PROJECT_ROOT, "pretrained_models/Fun-CosyVoice3-0.5B")
# 或者如果你把模型拷到了 Lanlan 下面：
# MODEL_DIR = "pretrained_models/Fun-CosyVoice3-0.5B"


logger.info("正在加载 CosyVoice3 模型，请稍候...")
# 【关键修改】开启 use_flow_cache
cosyvoice_model = CosyVoice3(MODEL_DIR, fp16=False)
logger.info("CosyVoice3 模型加载完成！")

PROMPT_WAV_PATH = os.path.join(COSYVOICE_PROJECT_ROOT, "asset/zero_shot_prompt.wav")
PROMPT_TEXT = "希望你以后能够做得比我还好呦。"

try:
    if os.path.exists(PROMPT_WAV_PATH):
        logger.info(f"正在加载参考音频: {PROMPT_WAV_PATH}")
        # 加载音频并重采样到 16000Hz
        default_prompt_speech_16k = load_wav(PROMPT_WAV_PATH, 16000)
    else:
        logger.critcial(f'找不到必须的参考音频: {PROMPT_WAV_PATH}')
        raise FileNotFoundError(f'参考音频文件不存在：{PROMPT_WAV_PATH}')
except Exception as e:
    logger.critical(f"加载参考音频失败: {e}")
    raise

# ==========================================
# 2. 辅助函数
# ==========================================
def create_response(action, task_id, payload=None):
    return {
        "header": {
            "action": action,
            "task_id": task_id,
            "event_id": str(uuid.uuid4())
        },
        "payload": payload or {}
    }


# ==========================================
# 3. WebSocket 接口 (适配阿里协议)
# ==========================================
@app.websocket("/api/v1/ws/cosyvoice")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    logger.info("New connection established")

    try:
        while True:
            data = await websocket.receive_text()
            request = json.loads(data)

            header = request.get("header", {})
            action = header.get("action")
            task_id = header.get("task_id", str(uuid.uuid4()))

            if action == "run-task":
                # 1. 解析参数
                payload = request.get("payload", {})
                text = payload.get("input", {}).get("text", "")

                # 2. 发送 task-started
                await websocket.send_text(json.dumps(create_response("task-started", task_id)))

                # 3. 执行推理 (使用 Zero-Shot 模式)
                try:
                    logger.info(f"开始生成: {text}")

                    # =================================================
                    # 🔴 核心修改：使用 inference_zero_shot
                    # =================================================
                    # 参数1: 目标文本
                    # 参数2: 参考音频的文本 (prompt_text)
                    # 参数3: 参考音频的数据 (prompt_speech_16k)
                    # 参数4: stream=True

                    model_output_gen = cosyvoice_model.inference_zero_shot(
                        tts_text=text,
                        prompt_text=PROMPT_TEXT,
                        prompt_wav=PROMPT_WAV_PATH,
                        stream=True
                    )

                    for i in model_output_gen:
                        tts_speech = i['tts_speech']
                        audio_data = (tts_speech.numpy() * 32768).astype(np.int16).tobytes()
                        await websocket.send_bytes(audio_data)
                        await asyncio.sleep(0)

                    # =================================================

                    # 4. 发送 task-finished
                    finish_msg = create_response("task-finished", task_id, {"usage": {"characters": len(text)}})
                    await websocket.send_text(json.dumps(finish_msg))
                    logger.info("生成完成")

                except Exception as e:
                    logger.error(f"Inference error: {e}")
                    import traceback
                    traceback.print_exc()
                    err_msg = create_response("task-failed", task_id, {"output": {"error": str(e)}})
                    await websocket.send_text(json.dumps(err_msg))

    except WebSocketDisconnect:
        logger.info("Client disconnected")
    except Exception as e:
        logger.error(f"Connection error: {e}")


if __name__ == "__main__":
    # 启动服务，端口 8000
    uvicorn.run(app, host=config.SERVER_HOST, port=config.SERVER_PORT)