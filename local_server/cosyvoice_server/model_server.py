import os
import sys
import asyncio
import json
import uuid
import logging
import uvicorn
import numpy as np
import config
import queue
from concurrent.futures import ThreadPoolExecutor
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
    logger.error(f"导入失败: {e}")
    sys.exit(1)

MODEL_DIR = os.path.join(COSYVOICE_PROJECT_ROOT, "pretrained_models/Fun-CosyVoice3-0.5B")
# 或者如果你把模型拷到了 Lanlan 下面：
# MODEL_DIR = "pretrained_models/Fun-CosyVoice3-0.5B"


logger.info("正在加载 CosyVoice3 模型，请稍候...")
cosyvoice_model = CosyVoice3(MODEL_DIR, fp16=False)
logger.info("CosyVoice3 模型加载完成！")

# 默认参考音频配置
PROMPT_WAV_PATH = os.path.join(COSYVOICE_PROJECT_ROOT, "asset/sft_longwan_zh.wav")
PROMPT_TEXT = "希望你以后能够做得比我还好呦。"

# PROMPT_WAV_PATH = os.path.join(COSYVOICE_PROJECT_ROOT, "asset/Angry_ZH_prompt.wav")
# PROMPT_TEXT = "刚才还好好的，一眨眼又消失了，真的是要气死我了。。"

try:
    if os.path.exists(PROMPT_WAV_PATH):
        logger.info(f"正在加载参考音频: {PROMPT_WAV_PATH}")
        # 这里只加载一次作为全局默认，实际推理中可能会被锁住，但在单线程模型下没问题
        # 注意：CosyVoice 内部推理是无状态的，但显存是共享的
    else:
        logger.critical(f'找不到必须的参考音频: {PROMPT_WAV_PATH}')
except Exception as e:
    logger.critical(f"加载参考音频失败: {e}")

# 创建全局线程池，用于运行阻塞的模型推理
executor = ThreadPoolExecutor(max_workers=2)


def create_response(action, task_id, payload=None):
    return {
        "header": {
            "action": action,
            "task_id": task_id,
            "event_id": str(uuid.uuid4())
        },
        "payload": payload or {}
    }

def generator(input_queue: queue.Queue):
    """
    【核心组件】
    这是一个运行在推理线程中的同步生成器。
    它不断从 input_queue 获取文本，并 yield 给 CosyVoice。
    """
    while True:
        # 阻塞等待新文本
        text = input_queue.get()
        if text is None:  # 结束信号
            break

        # 只有非空文本才 yield，避免空转
        if text.strip():
            logger.debug(f"Bridge yielding text: {text}")
            yield text


def inference_loop(input_queue: queue.Queue, output_queue: asyncio.Queue, loop):
    """
    运行在 ThreadPoolExecutor 中的阻塞函数
    """
    try:
        # 调用 inference_zero_shot，传入 generator
        logger.info("后台推理线程启动，等待输入流...")

        # 注意：这里 prompt_speech_16k 需要实时加载或者传入，这里为了简化使用全局加载
        # 实际生产中建议每次从文件读取或传入 buffer
        prompt_speech_16k = load_wav(PROMPT_WAV_PATH, 16000)

        model_output_gen = cosyvoice_model.inference_zero_shot(
            tts_text=generator(input_queue),  # <--- 关键：传入生成器
            prompt_text=PROMPT_TEXT,
            prompt_wav=prompt_speech_16k,
            stream=True
        )

        for i in model_output_gen:
            tts_speech = i['tts_speech']
            audio_data = (tts_speech.numpy() * 32768).astype(np.int16).tobytes()

            # 将音频数据放入 asyncio 队列，发送给主线程
            # run_coroutine_threadsafe 是必须的，因为我们在普通线程里操作 async 队列
            asyncio.run_coroutine_threadsafe(output_queue.put(audio_data), loop)

    except Exception as e:
        logger.error(f"推理线程异常: {e}")
    finally:
        # 发送结束信号给输出队列
        asyncio.run_coroutine_threadsafe(output_queue.put(None), loop)
        logger.info("后台推理线程结束")


@app.websocket("/api/v1/ws/cosyvoice")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    logger.info("🔗 客户端已连接 (Bistream Mode)")

    # 每个连接独立的队列
    input_queue = queue.Queue()  # 主线程 -> 推理线程 (传文本)
    output_queue = asyncio.Queue()  # 推理线程 -> 主线程 (传音频)

    loop = asyncio.get_running_loop()
    task_id = str(uuid.uuid4())

    # 1. 启动后台推理线程
    # 这是一个长期运行的任务，直到连接断开或收到结束信号
    inference_future = loop.run_in_executor(
        executor,
        inference_loop,
        input_queue,
        output_queue,
        loop
    )

    # 2. 定义接收循环 (从 WS 收文本)
    async def receive_task():
        try:
            while True:
                data = await websocket.receive_text()
                request = json.loads(data)
                action = request.get("header", {}).get("action")

                if action == "run-task":
                    # 获取增量文本
                    payload = request.get("payload", {})
                    text = payload.get("input", {}).get("text", "")
                    if text:
                        # 放入同步队列，供后台线程消费
                        input_queue.put(text)

                elif action == "finish-task":
                    # 客户端通知说话结束
                    input_queue.put(None)
                    break
        except WebSocketDisconnect:
            logger.warning("接收循环检测到断开")
            input_queue.put(None)  # 确保推理线程退出
        except Exception as e:
            logger.error(f"接收循环错误: {e}")
            input_queue.put(None)

    # 3. 定义发送循环 (往 WS 发音频)
    async def send_task():
        try:
            # 先发一个 task-started
            await websocket.send_text(json.dumps(create_response("task-started", task_id)))

            while True:
                # 等待推理线程产生的音频
                audio_data = await output_queue.get()

                if audio_data is None:  # 推理结束信号
                    break

                await websocket.send_bytes(audio_data)

            # 发送 task-finished
            await websocket.send_text(json.dumps(create_response("task-finished", task_id)))

        except Exception as e:
            logger.error(f"发送循环错误: {e}")

    # 4. 并发运行接收和发送
    # gather 会等待两个任务都结束
    # 注意：通常 send_task 会在 output_queue 收到 None 时结束
    # 而 receive_task 会在 WebSocket 断开时结束
    try:
        await asyncio.gather(receive_task(), send_task())
    except Exception as e:
        logger.error(f"主处理逻辑异常: {e}")
    finally:
        logger.info("连接关闭，清理资源")
        # 确保队列里有 None 以防线程卡住
        input_queue.put(None)


if __name__ == "__main__":
    # 启动服务，端口 8000
    uvicorn.run(app, host=config.SERVER_HOST, port=config.SERVER_PORT)