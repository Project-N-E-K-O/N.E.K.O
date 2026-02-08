import asyncio
import websockets
import json
import logging
import torch
import sys
import os
import time
import threading
import uuid
import queue
import numpy as np
import re

# ========================================================
# 1. 初始化 Logging
# ========================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [TTS-Server] %(levelname)s - %(message)s'
)
logger = logging.getLogger("Qwen3-TTS-Server")

# ========================================================
# 2. 路径配置 (适配 Linux 原生路径)
# ========================================================
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
# 假设你还在 WSL 的这个位置
MODEL_SOURCE_DIR = os.path.join(PROJECT_ROOT, "Qwen3-TTS")

if MODEL_SOURCE_DIR not in sys.path:
    sys.path.append(MODEL_SOURCE_DIR)

try:
    from qwen_tts.core.models.modeling_qwen3_tts import Qwen3TTSForConditionalGeneration
    from qwen_tts.core.models.processing_qwen3_tts import Qwen3TTSProcessor
    from qwen_tts.inference.qwen3_tts_model import Qwen3TTSModel, VoiceClonePromptItem

    torch.serialization.add_safe_globals([VoiceClonePromptItem])
    logger.info("✅ 成功导入 Qwen3-TTS 原生组件")
except ImportError as e:
    logger.error(f"❌ 组件导入失败: {e}")
    sys.exit(1)


# ========================================================
# 3. Server 类定义 (融合版)
# ========================================================
class QwenLocalServer:
    def __init__(self, model_path):
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.pt_path = os.path.join(PROJECT_ROOT, "nyaning_voice.pt")
        self.model = None
        self.cached_prompt = None
        self.pad_token_id = None
        self.ref_text = "アラバマ シュー ノ サイダイ トシ ワ バーミングハム デ アル。"

        # --- 任务队列 (解决并发卡顿) ---
        self.task_queue = queue.Queue()
        # 启动后台工人
        threading.Thread(target=self._worker_loop, daemon=True).start()

        self._load_engine(model_path)

    def _load_engine(self, model_path):
        try:
            t0 = time.time()
            logger.info(f"正在启动 N.E.K.O 语音引擎 (Device: {self.device})...")

            processor = Qwen3TTSProcessor.from_pretrained(model_path, fix_mistral_regex=True)

            # --- 关键优化 1: 显式指定 Bfloat16 和 Flash Attention 2 ---
            raw_model = Qwen3TTSForConditionalGeneration.from_pretrained(
                model_path,
                torch_dtype=torch.bfloat16,  # 给 transformers 看
                dtype=torch.bfloat16,  # 给 qwen 看
                attn_implementation="flash_attention_2",
                device_map="cuda",
                low_cpu_mem_usage=True
            )
            raw_model.eval()

            self.model = Qwen3TTSModel(model=raw_model, processor=processor)

            # --- 关键优化 2: 预设 Config 防止推理时反复初始化 ---
            self.pad_token_id = raw_model.config.pad_token_id or raw_model.config.eos_token_id
            if hasattr(self.model, 'generation_config'):
                self.model.generation_config.pad_token_id = self.pad_token_id

            # 加载/提取音色
            if os.path.exists(self.pt_path):
                logger.info(f"✨ 发现音色特征 {self.pt_path}，加载中...")
                self.cached_prompt = torch.load(self.pt_path, map_location=self.device, weights_only=False)
            else:
                logger.warning("🎙️ 未发现音色特征，尝试提取...")
                ref_wav = os.path.join(PROJECT_ROOT, "uttid_f1.wav")
                if os.path.exists(ref_wav):
                    with torch.no_grad():
                        with torch.amp.autocast('cuda', dtype=torch.bfloat16):
                            self.cached_prompt = self.model.create_voice_clone_prompt(
                                ref_audio=ref_wav, ref_text=self.ref_text
                            )
                    torch.save(self.cached_prompt, self.pt_path)
                    logger.info("✅ 音色提取完成")
                else:
                    logger.error(f"❌ 找不到参考音频: {ref_wav}")

            logger.info(f"🚀 引擎就绪 | 精度: {raw_model.dtype} | 耗时: {time.time() - t0:.2f}s")
        except Exception as e:
            logger.error(f"❌ 加载引擎异常: {e}")

    def _worker_loop(self):
        logger.info("👷 智能拼句队列服务已启动")
        while True:
            task = self.task_queue.get()
            if task is None: break

            full_text, job_id, loop, audio_queue, cancel_event = task

            if cancel_event.is_set():
                self.task_queue.task_done()
                continue

            self._do_inference(full_text, job_id, loop, audio_queue, cancel_event)
            self.task_queue.task_done()

    def _do_inference(self, full_text, job_id, loop, audio_queue, cancel_event):
        try:
            if not self.model or self.cached_prompt is None: return

            start_time = time.time()

            # --- 关键优化 3: 使用 inference_mode 和 autocast ---
            with torch.inference_mode():
                with torch.amp.autocast('cuda', dtype=torch.bfloat16):
                    wavs, sr = self.model.generate_voice_clone(
                        text=full_text,
                        voice_clone_prompt=self.cached_prompt,
                        language="Chinese",
                        pad_token_id=self.pad_token_id
                    )

            if torch.cuda.is_available():
                torch.cuda.synchronize()

            inference_duration = time.time() - start_time

            # --- 🚨 核心修复：找回丢失的 Int16 转换 (这就是没声音的原因！) ---
            # 兔老师的代码可能直接发了 float，我们需要转回 int16
            audio_data = wavs[0].flatten()
            audio_int16 = (audio_data * 32767).astype(np.int16)

            audio_real_duration = len(audio_int16) / sr
            rtf = inference_duration / audio_real_duration if audio_real_duration > 0 else 0

            logger.info(
                f"✅ [{job_id}] 完成 | 耗时:{inference_duration:.3f}s | 音频:{audio_real_duration:.2f}s | RTF:{rtf:.4f}"
            )

            # 发送数据
            chunk_size = 4096
            for i in range(0, len(audio_int16), chunk_size):
                if cancel_event.is_set(): break
                chunk = audio_int16[i:i + chunk_size].tobytes()
                loop.call_soon_threadsafe(audio_queue.put_nowait, chunk)

        except Exception as e:
            logger.error(f"❌ 推理错误: {e}")
            import traceback
            logger.error(traceback.format_exc())
        finally:
            loop.call_soon_threadsafe(audio_queue.put_nowait, b"__END__")

    async def handle_tts(self, websocket):
        logger.info(f"客户端连接: {websocket.remote_address}")
        loop = asyncio.get_running_loop()

        # 智能拼句缓冲区
        self.sentence_buffer = ""

        current_job_id = None
        cancel_event = threading.Event()
        audio_queue = asyncio.Queue()

        async def _stop_current_job():
            nonlocal current_job_id, cancel_event
            cancel_event.set()
            current_job_id = None
            cancel_event = threading.Event()
            while not audio_queue.empty():
                try:
                    audio_queue.get_nowait()
                except:
                    break
            self.sentence_buffer = ""

        # 发送循环
        async def _sender_loop():
            while True:
                try:
                    chunk = await audio_queue.get()
                    if chunk == b"__END__":
                        if current_job_id:
                            # 适配 N.E.K.O 客户端协议
                            response_done = {"type": "response.done", "job_id": current_job_id}
                            await websocket.send(json.dumps(response_done))
                        continue
                    await websocket.send(chunk)
                except Exception:
                    break

        sender_task = asyncio.create_task(_sender_loop())

        try:
            # 发送 ready 信号
            await websocket.send(json.dumps({"type": "ready"}))

            async for message in websocket:
                if isinstance(message, bytes): continue
                try:
                    data = json.loads(message)
                except:
                    continue

                msg_type = data.get("type")
                if "text" in data and not msg_type: msg_type = "legacy.text"

                if msg_type == "input_text_buffer.append":
                    text_fragment = data.get("text", "")
                    self.sentence_buffer += text_fragment

                elif msg_type in ("input_text_buffer.commit", "legacy.text"):
                    if msg_type == "legacy.text":
                        self.sentence_buffer += data.get("text", "")

                    # 正则智能断句
                    parts = re.split(r'([。！？.!?\n]+)', self.sentence_buffer)

                    if len(parts) > 1:
                        for i in range(0, len(parts) - 1, 2):
                            sentence = parts[i] + parts[i + 1]
                            sentence = sentence.strip()
                            if not sentence: continue

                            if not current_job_id:
                                current_job_id = str(uuid.uuid4())
                                await websocket.send(json.dumps({"type": "response.start", "job_id": current_job_id}))

                            logger.info(f"📥 句子: {sentence[:20]}...")
                            self.task_queue.put((sentence, current_job_id, loop, audio_queue, cancel_event))

                        self.sentence_buffer = parts[-1]

                    # 缓冲区兜底 (防止一直不说话)
                    if len(self.sentence_buffer) > 30:
                        sentence = self.sentence_buffer
                        self.sentence_buffer = ""
                        if not current_job_id:
                            current_job_id = str(uuid.uuid4())
                            await websocket.send(json.dumps({"type": "response.start", "job_id": current_job_id}))
                        self.task_queue.put((sentence, current_job_id, loop, audio_queue, cancel_event))

                elif msg_type == "cancel":
                    await _stop_current_job()

        finally:
            await _stop_current_job()
            sender_task.cancel()


async def main():
    # 请确保路径正确
    MODEL_PATH = "/home/amadeus/models/qwen3_tts"
    server = QwenLocalServer(MODEL_PATH)
    async with websockets.serve(server.handle_tts, "0.0.0.0", 8765):
        logger.info("🚀 本地 TTS 服务已启动: ws://localhost:8765")
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
