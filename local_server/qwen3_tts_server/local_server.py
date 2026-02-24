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
from pathlib import Path

ENABLE_TRUE_STREAMING = True
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
MODEL_SOURCE_DIR = os.path.join(PROJECT_ROOT, "Qwen3-TTS-streaming")

if MODEL_SOURCE_DIR not in sys.path:
    sys.path.insert(0, MODEL_SOURCE_DIR)

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
    def __init__(
        self,
        model_path,
        voice_pt_path=None,
        ref_wav=None,
        ref_text=None,
        language=None,
        chunk_size=None,
        buffer_fallback_chars=None,
    ):
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.pt_path = voice_pt_path or os.path.join(PROJECT_ROOT, "nyaning_voice.pt") # 注意 这里的nyaning_voice 是测试用音声的音色 hidden2048 适配1.7B
        self.model = None
        self.model_hidden_size = None
        self.cached_prompt = None
        self.voice_lock = threading.Lock()
        self.prompt_cache = {}
        self.active_voice_file = str((Path(PROJECT_ROOT) / "active_voice.json").resolve())
        self.active_voice_path = None
        self.active_voice_mtime = None
        self.voice_version = 0
        self.pad_token_id = None
        self.ref_text = ref_text or "アラバマ シュー ノ サイダイ トシ ワ バーミングハム デ アル。" # 后面的是自己的sample样本 需要更改
        self.ref_wav = ref_wav or os.path.join(PROJECT_ROOT, "uttid_f1.wav") # 测试用sample 样本
        self.language = language or "Chinese"
        self.chunk_size = int(chunk_size) if chunk_size else 4096
        self.buffer_fallback_chars = int(buffer_fallback_chars) if buffer_fallback_chars else 30
        self.inference_lock =threading.Lock()

        # --- 任务队列 (解决并发卡顿) ---
        self.task_queue = queue.Queue()

        self._load_engine(model_path)
        # 启动后台工人
        threading.Thread(target=self._worker_loop, daemon=True).start()

    def _validate_prompt_dim(self, ref_spk, pt_path: str):
        """校验 ref_spk_embedding 维度是否匹配当前模型 hidden_size"""
        if self.model_hidden_size is None or ref_spk is None:
            return True
        spk_dim = ref_spk.shape[-1]
        if spk_dim != self.model_hidden_size:
            logger.error(
                f"❌ 音色文件 {pt_path} 的 ref_spk_embedding 维度 ({spk_dim}) "
                f"与当前模型 hidden_size ({self.model_hidden_size}) 不匹配！"
                f"请用当前模型重新生成 .pt 文件。"
            )
            return False
        return True

    def _load_prompt_from_pt(self, pt_path: str):
        payload = torch.load(pt_path, map_location=self.device, weights_only=False)

        if isinstance(payload, dict) and "items" in payload:
            items_raw = payload.get("items")
            if isinstance(items_raw, list):
                items = []
                for it in items_raw:
                    if isinstance(it, VoiceClonePromptItem):
                        items.append(it)
                        continue
                    if not isinstance(it, dict):
                        raise TypeError("Invalid voice prompt item")
                    ref_code = it.get("ref_code", None)
                    if ref_code is not None and not torch.is_tensor(ref_code):
                        ref_code = torch.tensor(ref_code, device=self.device)
                    if ref_code is not None:
                        ref_code = ref_code.to(device=self.device).long()
                    ref_spk = it.get("ref_spk_embedding", None)
                    if ref_spk is None:
                        raise ValueError("Missing ref_spk_embedding")
                    if not torch.is_tensor(ref_spk):
                        ref_spk = torch.tensor(ref_spk, device=self.device)
                    ref_spk = ref_spk.to(device=self.device, dtype=torch.bfloat16)
                    if not self._validate_prompt_dim(ref_spk, pt_path):
                        raise ValueError(
                            f"Prompt 维度不匹配: ref_spk={ref_spk.shape[-1]} "
                            f"vs model={self.model_hidden_size}。"
                            f"请用当前模型重新生成 .pt 文件。"
                        )
                    items.append(
                        VoiceClonePromptItem(
                            ref_code=ref_code,
                            ref_spk_embedding=ref_spk,
                            x_vector_only_mode=bool(it.get("x_vector_only_mode", False)),
                            icl_mode=bool(it.get("icl_mode", not bool(it.get("x_vector_only_mode", False)))),
                            ref_text=it.get("ref_text", None),
                        )
                    )
                return items

        if isinstance(payload, list):
            for it in payload:
                if isinstance(it, VoiceClonePromptItem):
                    if it.ref_code is not None:
                        it.ref_code = it.ref_code.to(device=self.device).long()
                    if it.ref_spk_embedding is not None:
                        it.ref_spk_embedding = it.ref_spk_embedding.to(device=self.device, dtype=torch.bfloat16)
                        if not self._validate_prompt_dim(it.ref_spk_embedding, pt_path):
                            raise ValueError(
                                f"Prompt 维度不匹配: ref_spk={it.ref_spk_embedding.shape[-1]} "
                                f"vs model={self.model_hidden_size}。"
                                f"请用当前模型重新生成 .pt 文件。"
                            )
            return payload

        return payload

    def _read_active_voice_path(self):
        try:
            if not os.path.exists(self.active_voice_file):
                return None, None
            mtime = os.path.getmtime(self.active_voice_file)
            with open(self.active_voice_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            p = None
            if isinstance(data, dict):
                p = data.get("voice_pt_path")
            if not p:
                return None, mtime
            p = str(p)
            return p, mtime
        except Exception as e:
            logger.error(f"读取 active_voice.json 失败: {e}")
            return None, None

    def _ensure_active_prompt_loaded(self):
        with self.voice_lock:
            desired_path, mtime = self._read_active_voice_path()
            if not desired_path:
                desired_path = self.pt_path
                try:
                    mtime = os.path.getmtime(desired_path) if os.path.exists(desired_path) else None
                except Exception:
                    mtime = None

            if (
                self.active_voice_path == desired_path
                and self.active_voice_mtime == mtime
                and self.cached_prompt is not None
            ):
                return self.cached_prompt, self.voice_version

            if desired_path in self.prompt_cache:
                self.cached_prompt = self.prompt_cache[desired_path]
            else:
                self.cached_prompt = self._load_prompt_from_pt(desired_path)
                self.prompt_cache[desired_path] = self.cached_prompt

            self.active_voice_path = desired_path
            self.active_voice_mtime = mtime
            self.pt_path = desired_path
            self.voice_version += 1
            logger.info(f"🔁 Active voice switched: {self.active_voice_path}")
            return self.cached_prompt, self.voice_version

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
                device_map=self.device,  # 旧的是cuda 硬编码
                low_cpu_mem_usage=True
            )
            raw_model.eval()

            # --- 兼容修复: 0.6B 模型的 code_predictor 可能被默认初始化为 float32 ---
            # device_map 模式下 .to(dtype) 会被 Accelerate 拦截，必须逐参数强制转换
            for param in raw_model.parameters():
                if param.data.dtype == torch.float32:
                    param.data = param.data.to(dtype=torch.bfloat16)
            for buf in raw_model.buffers():
                if buf.dtype == torch.float32:
                    buf.data = buf.data.to(dtype=torch.bfloat16)

            self.model = Qwen3TTSModel(model=raw_model, processor=processor)
            self.model_hidden_size = raw_model.config.talker_config.hidden_size
            logger.info(f"📐 模型 hidden_size = {self.model_hidden_size}")

            # --- 关键优化 2: 预设 Config 防止推理时反复初始化 ---
            self.pad_token_id = raw_model.config.pad_token_id or raw_model.config.eos_token_id
            if hasattr(self.model, 'generation_config'):
                self.model.generation_config.pad_token_id = self.pad_token_id

            # 加载/提取音色
            if os.path.exists(self.pt_path):
                logger.info(f"✨ 发现音色特征 {self.pt_path}，加载中...")
                self.cached_prompt = self._load_prompt_from_pt(self.pt_path)
            else:
                logger.warning("🎙️ 未发现音色特征，尝试提取...")
                ref_wav = self.ref_wav
                if os.path.exists(ref_wav):
                    with torch.no_grad():
                        with torch.amp.autocast(self.device, dtype=torch.bfloat16):
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
            sys.exit(1)

    def _worker_loop(self):
        logger.info("👷 智能拼句队列服务已启动")
        while True:
            task = self.task_queue.get()
            if task is None: break

            full_text, job_id, loop, audio_queue, cancel_event, prompt_snapshot, language = task

            if cancel_event.is_set():
                self.task_queue.task_done()
                continue

            self._do_inference(full_text, job_id, loop, audio_queue, cancel_event, prompt_snapshot, language)
            self.task_queue.task_done()

    def _do_inference(self, full_text, job_id, loop, audio_queue, cancel_event, prompt_snapshot, language):
        try:
            if not self.model or prompt_snapshot is None: return

            start_time = time.time()

            # 🟢 [核心开关]：在这里切换
            # True: 使用魔改版的流式 Generator (算出一点发一点)
            # False: 使用魔改版提供的普通接口 (算完一整句再发)
            USE_STREAMING_GENERATOR = True

            # 探测流式方法
            stream_func = None
            search_target = self.model
            depth = 0
            while search_target is not None and depth < 3:
                if hasattr(search_target, "stream_generate_voice_clone"):
                    stream_func = getattr(search_target, "stream_generate_voice_clone")
                    break
                search_target = getattr(search_target, "model", None)
                depth += 1

            # =======================================================
            # 🛠️ --- 精度与类型预处理 (彻底消灭 Casting fp32 警告) ---
            # =======================================================
            # 1. 如果传入的是路径字符串，手动加载成张量对象
            if isinstance(prompt_snapshot, str):
                try:
                    prompt_snapshot = torch.load(prompt_snapshot, map_location=self.device, weights_only=False)
                except Exception as e:
                    logger.error(f"❌ 加载音色文件失败: {e}")
                    return

            # 兼容最新版带 "items" 字典包装的 .pt 文件
            prompt_items = prompt_snapshot.get("items", []) if isinstance(prompt_snapshot, dict) else prompt_snapshot

            if isinstance(prompt_items, list):
                for item in prompt_items:
                    # 强转至设备与精度，匹配 Flash-Attention 2
                    if hasattr(item, 'ref_code') and item.ref_code is not None:
                        item.ref_code = item.ref_code.to(device=self.device, dtype=torch.long)
                    if hasattr(item, 'ref_spk_embedding') and item.ref_spk_embedding is not None:
                        item.ref_spk_embedding = item.ref_spk_embedding.to(device=self.device, dtype=torch.bfloat16)
            # =======================================================

            # =======================================================
            # 🚀 模式 A：真流式 (Generator 模式)
            # =======================================================
            if ENABLE_TRUE_STREAMING and USE_STREAMING_GENERATOR and stream_func:
                logger.info(f"🌊 [{job_id}] 模式：真流式 (Generator)")

                total_samples = 0  # 统计总采样点，用于计算 RTF

                with torch.inference_mode():
                    with self.inference_lock, torch.amp.autocast(self.device, dtype=torch.bfloat16):
                        for pcm_chunk in stream_func(
                            text=full_text,
                            voice_clone_prompt=prompt_snapshot,
                            language=language,
                            pad_token_id=self.pad_token_id,
                            emit_every_frames=16,        # 1.7B 推荐 16 帧
                            first_chunk_emit_every=4,
                            overlap_samples=512,
                        ):
                            if cancel_event.is_set(): break
                            if pcm_chunk is not None:
                                # 兼容魔改库返回元组的格式
                                audio_raw = pcm_chunk[0] if isinstance(pcm_chunk, (tuple, list)) else pcm_chunk
                                audio_data = np.asarray(audio_raw).flatten()
                                if len(audio_data) == 0: continue

                                # 累加采样的点数
                                total_samples += len(audio_data)

                                audio_int16 = (audio_data * 32767).clip(-32768, 32767).astype(np.int16)
                                loop.call_soon_threadsafe(audio_queue.put_nowait, audio_int16.tobytes())

                inference_duration = time.time() - start_time
                # Qwen3-TTS 默认采样率是 24000
                audio_real_duration = total_samples / 24000.0
                rtf = inference_duration / audio_real_duration if audio_real_duration > 0 else 0

                logger.info(f"✅ [{job_id}] 真流式完成 | 耗时:{inference_duration:.3f}s | 音频:{audio_real_duration:.2f}s | RTF:{rtf:.4f}")

            # =======================================================
            # 🐢 模式 B：块生成 (原本的整句逻辑)
            # =======================================================
            else:
                mode_str = "原版块生成" if not stream_func else "流式库-整句模式"
                logger.info(f"🐢 [{job_id}] 模式：{mode_str}")
                with torch.inference_mode():
                    with self.inference_lock, torch.amp.autocast(self.device, dtype=torch.bfloat16):
                        wavs, sr = self.model.generate_voice_clone(
                            text=full_text,
                            voice_clone_prompt=prompt_snapshot,
                            language=language,
                            pad_token_id=self.pad_token_id
                        )

                if torch.cuda.is_available():
                    torch.cuda.synchronize()

                inference_duration = time.time() - start_time
                audio_data = wavs[0].flatten()
                audio_int16 = (audio_data * 32767).clip(-32768, 32767).astype(np.int16)

                audio_real_duration = len(audio_int16) / sr
                rtf = inference_duration / audio_real_duration if audio_real_duration > 0 else 0

                logger.info(
                    f"✅ [{job_id}] 完成 | 耗时:{inference_duration:.3f}s | 音频:{audio_real_duration:.2f}s | RTF:{rtf:.4f}"
                )

                chunk_size = self.chunk_size
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
        sentence_buffer = ""

        current_job_id = None
        cancel_event = threading.Event()
        audio_queue = asyncio.Queue()
        last_voice_version = self.voice_version

        async def _stop_current_job(keep_buffer: bool = False):
            nonlocal current_job_id, cancel_event, sentence_buffer
            cancel_event.set()
            current_job_id = None
            cancel_event = threading.Event()
            while not audio_queue.empty():
                try:
                    audio_queue.get_nowait()
                except:
                    break
            if not keep_buffer:
                sentence_buffer = ""

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
                    sentence_buffer += text_fragment

                elif msg_type in ("input_text_buffer.commit", "legacy.text"):
                    if msg_type == "legacy.text":
                        sentence_buffer += data.get("text", "")

                    _, current_version = self._ensure_active_prompt_loaded()
                    if current_version != last_voice_version:
                        last_voice_version = current_version
                        # Hot voice switch: cancel current audio job but keep the buffered text.
                        # Otherwise the first committed sentence right after switching may be dropped.
                        await _stop_current_job(keep_buffer=True)

                    # 正则智能断句
                    parts = re.split(r'([。！？.!?\n]+)', sentence_buffer)

                    if len(parts) > 1:
                        for i in range(0, len(parts) - 1, 2):
                            sentence = parts[i] + parts[i + 1]
                            sentence = sentence.strip()
                            if not sentence: continue

                            if not current_job_id:
                                current_job_id = str(uuid.uuid4())
                                await websocket.send(json.dumps({"type": "response.start", "job_id": current_job_id}))

                            logger.info(f"📥 句子: {sentence[:20]}...")
                            prompt, _ = self._ensure_active_prompt_loaded()
                            self.task_queue.put((sentence, current_job_id, loop, audio_queue, cancel_event, prompt, self.language))

                        sentence_buffer = parts[-1]

                    # 缓冲区兜底 (防止一直不说话)
                    if len(sentence_buffer) > self.buffer_fallback_chars:
                        sentence = sentence_buffer
                        sentence_buffer = ""
                        if not current_job_id:
                            current_job_id = str(uuid.uuid4())
                            await websocket.send(json.dumps({"type": "response.start", "job_id": current_job_id}))
                        prompt, _ = self._ensure_active_prompt_loaded()
                        self.task_queue.put((sentence, current_job_id, loop, audio_queue, cancel_event, prompt, self.language))

                elif msg_type == "cancel":
                    await _stop_current_job()

        finally:
            await _stop_current_job()
            sender_task.cancel()


async def main():
    tts_custom = {}
    repo_root = None
    try:
        p = Path(__file__).resolve()
        config_path = None
        for parent in [p.parent] + list(p.parents):
            candidate = parent / "config" / "api_providers.json"
            if candidate.exists():
                config_path = candidate
                break
        if config_path is not None:
            with config_path.open("r", encoding="utf-8") as f:
                cfg = json.load(f)
            tts_custom = cfg.get("tts_custom", {}) or {}
            repo_root = config_path.parent.parent
    except Exception:
        tts_custom = {}
        repo_root = None


    model_path = tts_custom.get("model_path") or "/home/amadeus/models/qwen3_tts" # [旧的 1.7B 路径]
    # model_path = tts_custom.get("model_path") or "/home/amadeus/models/Qwen3-TTS-12Hz-0.6B-Base" # 0.6B 路径 按需更改 这里是硬编码
    host = tts_custom.get("host") or "127.0.0.1"
    port = int(tts_custom.get("port") or 8765)

    voice_pt_path = tts_custom.get("voice_pt_path")
    if voice_pt_path:
        p = Path(voice_pt_path)
        if not p.is_absolute() and repo_root is not None:
            p = (repo_root / p).resolve()
        voice_pt_path = str(p)

    ref_wav = tts_custom.get("ref_wav")
    if ref_wav:
        p = Path(ref_wav)
        if not p.is_absolute() and repo_root is not None:
            p = (repo_root / p).resolve()
        ref_wav = str(p)

    ref_text = tts_custom.get("ref_text")
    language = tts_custom.get("language")
    chunk_size = tts_custom.get("chunk_size")
    buffer_fallback_chars = tts_custom.get("buffer_fallback_chars")

    server = QwenLocalServer(
        model_path,
        voice_pt_path=voice_pt_path,
        ref_wav=ref_wav,
        ref_text=ref_text,
        language=language,
        chunk_size=chunk_size,
        buffer_fallback_chars=buffer_fallback_chars,
    )

    async with websockets.serve(server.handle_tts, host, port):
        logger.info(f"🚀 本地 TTS 服务已启动: ws://{host}:{port}")
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
