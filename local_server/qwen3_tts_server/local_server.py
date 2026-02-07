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
import numpy as np

# ========================================================
# 1. 初始化 Logging (解决 NameError)
# ========================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [TTS-Server] %(levelname)s - %(message)s'
)
logger = logging.getLogger("Qwen3-TTS-Server")

# ========================================================
# 2. 路径与环境配置
# ========================================================
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
MODEL_SOURCE_DIR = os.path.join(PROJECT_ROOT, "Qwen3-TTS")

if MODEL_SOURCE_DIR not in sys.path:
    sys.path.append(MODEL_SOURCE_DIR)

try:
    # 使用 demo 中验证成功的导入路径
    from qwen_tts.core.models.modeling_qwen3_tts import Qwen3TTSForConditionalGeneration
    from qwen_tts.core.models.processing_qwen3_tts import Qwen3TTSProcessor
    from qwen_tts.inference.qwen3_tts_model import Qwen3TTSModel
    # 先从模型库导入这个类
    from qwen_tts.inference.qwen3_tts_model import VoiceClonePromptItem
    torch.serialization.add_safe_globals([VoiceClonePromptItem])
    logger.info("✅ 成功导入 Qwen3-TTS 原生组件")
except ImportError as e:
    logger.error(f"❌ 组件导入失败: {e}")
    sys.exit(1)


# ========================================================
# 3. QwenLocalServer 类定义
# ========================================================
class QwenLocalServer:
    def __init__(self, model_path):
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.pt_path = os.path.join(PROJECT_ROOT, "nyaning_voice.pt")
        self.model = None
        self.cached_prompt = None

        # 对应你音频 uttid_f1.wav 的日语原文
        self.ref_text = "アラバマ シュー ノ サイダイ トシ ワ バーミングハム デ アル。"

        self._load_engine(model_path)

    def _load_engine(self, model_path):
        try:
            t0 = time.time()
            logger.info(f"正在启动 N.E.K.O 语音引擎 (Device: {self.device})...")

            # A. 加载处理器 (修复正则警告)
            processor = Qwen3TTSProcessor.from_pretrained(model_path, fix_mistral_regex=True)

            # B. 加载模型 (开启 FlashAttention 2.0)
            dtype = torch.bfloat16 if self.device == "cuda" else torch.float32
            raw_model = Qwen3TTSForConditionalGeneration.from_pretrained(
                model_path,
                torch_dtype=dtype,
                attn_implementation="flash_attention_2" if self.device == "cuda" else "eager",
                low_cpu_mem_usage=True
            ).to(self.device)

            # C. 封装推理包装器
            self.model = Qwen3TTSModel(model=raw_model, processor=processor)
            self.pad_token_id = raw_model.config.pad_token_id or raw_model.config.eos_token_id

            # D. 预加载/导出音色特征 (秒开优化)
            if os.path.exists(self.pt_path):
                logger.info(f"✨ 发现音色特征 {self.pt_path}，执行秒速加载")
                self.cached_prompt = torch.load(self.pt_path, map_location=self.device, weights_only=False)
            else:
                logger.warning("🎙️ 未发现导出的音色，将尝试从 uttid_f1.wav 提取...")
                ref_wav = os.path.join(PROJECT_ROOT, "uttid_f1.wav")
                if os.path.exists(ref_wav):
                    with torch.no_grad():
                        self.cached_prompt = self.model.create_voice_clone_prompt(
                            ref_audio=ref_wav,
                            ref_text=self.ref_text
                        )
                    torch.save(self.cached_prompt, self.pt_path)
                    logger.info("✅ 音色提取并保存完成")
                else:
                    logger.error(f"❌ 找不到参考音频: {ref_wav}")

            logger.info(f"🚀 语音引擎初始化完成，总耗时: {time.time() - t0:.2f}s")
        except Exception as e:
            logger.error(f"❌ 加载引擎异常: {e}")
            import traceback
            logger.error(traceback.format_exc())

    async def handle_tts(self, websocket):
        logger.info(f"客户端已连接: {websocket.remote_address}")
        loop = asyncio.get_running_loop()

        text_buffer = ""
        session_cfg = {"voice": None, "sample_rate": 24000}
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
                except Exception:
                    break

        def _producer_wrapper(full_text, job_id):
            try:
                if not self.model or self.cached_prompt is None:
                    logger.error("模型或音色未就绪，无法合成")
                    return

                start_time = time.time()
                logger.info(f"🎤 [{job_id}] 正在合成文本: {full_text[:30]}...")

                with torch.no_grad():
                    # 调用 generate_voice_clone
                    # 注意：如果开发者没提供 generate_stream，这里生成完整音频后再分块
                    wavs, sr = self.model.generate_voice_clone(
                        text=full_text,
                        voice_clone_prompt=self.cached_prompt,
                        language="Chinese"
                    )

                    # 强制 GPU 同步以获取精确的推理计时
                    if torch.cuda.is_available():
                        torch.cuda.synchronize()

                    # --- 2. 计算耗时与性能指标 ---
                    end_time = time.time()
                    inference_duration = end_time - start_time

                    # 【关键修正点】：wavs[0] 已经是 numpy 数组，直接使用
                    audio_data = wavs[0].flatten()

                    # 归一化并转为 16-bit PCM (Int16) 修复雪花音
                    audio_int16 = (audio_data * 32767).astype(np.int16)

                    # 计算音频实际时长（秒）
                    audio_real_duration = len(audio_int16) / sr
                    # 计算 RTF (实时率)，数值越小性能越强
                    rtf = inference_duration / audio_real_duration if audio_real_duration > 0 else 0

                    # --- 3. 报告合成结束 ---
                    logger.info(
                        f"✅ [{job_id}] 合成任务完成！\n"
                        f"   ----------------------------------------\n"
                        f"   ⏱️ 推理耗时: {inference_duration:.3f} 秒\n"
                        f"   🔊 音频长度: {audio_real_duration:.2f} 秒\n"
                        f"   🚀 实时率 (RTF): {rtf:.4f} {'(极速)' if rtf < 0.2 else ''}\n"
                        f"   ----------------------------------------"
                    )

                    # 4. 分块推送到 WebSocket 发送队列
                    chunk_size = 2048
                    for i in range(0, len(audio_int16), chunk_size):
                        if cancel_event.is_set():
                            logger.warning(f"⚠️ [{job_id}] 任务被取消")
                            break
                        chunk = audio_int16[i:i+chunk_size].tobytes()
                        # 将字节流放入异步队列
                        loop.call_soon_threadsafe(audio_queue.put_nowait, chunk)

            except Exception as e:
                logger.error(f"推理出错: {e}")
            finally:
                loop.call_soon_threadsafe(audio_queue.put_nowait, b"__END__")
                # 及时清理显存碎片
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()

        async def _sender_loop():
            while True:
                chunk = await audio_queue.get()
                if chunk == b"__END__":
                    await websocket.send(json.dumps({"type": "response.done", "job_id": current_job_id}))
                    continue
                try:
                    await websocket.send(chunk)
                except Exception:
                    break

        sender_task = asyncio.create_task(_sender_loop())

        try:
            await websocket.send(json.dumps({"type": "ready"}))
            async for message in websocket:
                if isinstance(message, bytes): continue
                try:
                    data = json.loads(message)
                except:
                    continue

                msg_type = data.get("type")

                # 兼容旧格式或 text 直接发送
                if "text" in data and not msg_type:
                    msg_type = "legacy.text"

                if msg_type == "input_text_buffer.append":
                    text_buffer += data.get("text", "")
                elif msg_type in ("input_text_buffer.commit", "legacy.text"):
                    if msg_type == "legacy.text":
                        text_buffer = data.get("text", "")

                    full_text = text_buffer.strip()
                    text_buffer = ""
                    if not full_text: continue

                    await _stop_current_job()
                    current_job_id = str(uuid.uuid4())
                    logger.info(f"收到请求 job_id={current_job_id}: {full_text[:30]}...")

                    await websocket.send(json.dumps({
                        "type": "response.start",
                        "job_id": current_job_id
                    }))

                    threading.Thread(target=_producer_wrapper, args=(full_text, current_job_id), daemon=True).start()

                elif msg_type == "cancel":
                    await _stop_current_job()

        finally:
            await _stop_current_job()
            sender_task.cancel()


async def main():
    MODEL_PATH = "/home/amadeus/models/qwen3_tts"
    # MODEL_PATH = "/mnt/h/pr/N.E.K.O/local_server/qwen3_tts_server/Qwen3-TTS/pretrained_model/Qwen3-TTS-12Hz-1.7B-Base"
    server_instance = QwenLocalServer(MODEL_PATH)
    async with websockets.serve(server_instance.handle_tts, "0.0.0.0", 8765):
        logger.info("🚀 本地 TTS 服务已启动: ws://localhost:8765")
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("服务器停止运行")