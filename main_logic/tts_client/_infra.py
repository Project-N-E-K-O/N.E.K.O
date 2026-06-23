# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Shared TTS infrastructure: audio resampling, sentence pipeline, queue proxy."""

import numpy as np
import soxr
import time
import json
import re
import os
import math
import asyncio
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Main")

# 关闭哨兵：core.py 通过 request_queue.put((TTS_SHUTDOWN_SENTINEL, None))
# 通知 worker 退出主循环。不能复用 (None, None)，因为它已被用作"本轮 utterance
# 结束、flush/commit 缓冲区"的信号（见 _non_bistream_tts_main_loop、step/qwen
# worker 的 sid is None 分支）。两种语义必须分开。
TTS_SHUTDOWN_SENTINEL = "__shutdown__"

def _parse_env_float(env_name: str, default: float, min_value: float) -> float:
    raw = os.getenv(env_name)
    if raw is None or raw == "":
        value = default
    else:
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = default
    if not math.isfinite(value):
        value = default
    return max(value, min_value)

def _resample_audio(audio_int16: np.ndarray, src_rate: int, dst_rate: int,
                    resampler: 'soxr.ResampleStream | None' = None) -> bytes:
    """High-quality audio resampling using soxr
    
    Args:
        audio_int16: audio numpy array in int16 format
        src_rate: source sample rate
        dst_rate: target sample rate
        resampler: optional streaming resampler, maintains state across chunks
        
    Returns:
        resampled bytes
    """
    if src_rate == dst_rate:
        return audio_int16.tobytes()
    
    # 转换为 float32 进行高质量重采样
    audio_float = audio_int16.astype(np.float32) / 32768.0
    
    if resampler is not None:
        # 使用流式重采样器（维护 chunk 边界状态）
        resampled_float = resampler.resample_chunk(audio_float)
    else:
        # 无状态重采样（不推荐用于流式音频）
        resampled_float = soxr.resample(audio_float, src_rate, dst_rate, quality='HQ')
    
    # 转回 int16
    resampled_int16 = (resampled_float * 32768.0).clip(-32768, 32767).astype(np.int16)
    return resampled_int16.tobytes()

# 48kHz / 16-bit / mono PCM（所有 worker 重采样后的统一输出格式）每秒字节数。
_TTS_OUTPUT_BYTES_PER_SECOND = 48000 * 2

class AudioJitterBuffer:
    """Coalesce upstream PCM chunks to ride over inter-chunk gaps.

    Streaming TTS upstreams emit fragmented chunks, and the largest gap is right
    after the first chunk (model first-frame latency). Holding back an initial
    head-start of ``initial_buffer_bytes`` before releasing the first batch gives
    the player a lead it can ride over that opening gap; steady state then flushes
    whenever ``steady_buffer_bytes`` has accumulated. The two knobs are independent:
    ``initial_buffer_bytes=0`` releases the first chunk immediately (no head-start)
    yet still coalesces later chunks by ``steady_buffer_bytes``; ``steady_buffer_bytes=0``
    keeps the head-start but then passes each chunk straight through; both 0 is full
    low-latency pass-through.

    Provider-agnostic: a worker appends resampled PCM bytes and drives reset() on a
    new utterance / interrupt and flush() on response.done. The buffer never owns
    those boundaries because only the worker sees them.
    """
    def __init__(self, response_queue, initial_buffer_bytes: int, steady_buffer_bytes: int):
        self._response_queue = response_queue
        self._initial_buffer_bytes = initial_buffer_bytes
        self._steady_buffer_bytes = steady_buffer_bytes
        self.buffer = bytearray()
        self.started = False

    def reset(self):
        self.buffer.clear()
        self.started = False

    def append(self, audio_bytes):
        if not audio_bytes:
            return
        self.buffer.extend(audio_bytes)
        if not self.started:
            if len(self.buffer) < self._initial_buffer_bytes:
                return
            self._flush()  # 越过 head-start，放行并标记 started
            return
        if len(self.buffer) >= self._steady_buffer_bytes:
            self._flush()

    def flush(self):
        self._flush()

    def _flush(self):
        if not self.buffer:
            return
        self._response_queue.put(bytes(self.buffer))
        self.buffer.clear()
        # 放行即视为本轮已开播：短句不足 initial 阈值靠终结 flush 首次放行时，
        # 后续若有上游晚到的 stray delta 走 steady 而非重新 head-start 再被 reset 丢弃。
        self.started = True

def make_audio_jitter_buffer(response_queue, initial_ms_default: float = 400,
                             steady_ms_default: float = 200,
                             legacy_env_prefix: str | None = None) -> AudioJitterBuffer:
    """Build an :class:`AudioJitterBuffer` from the generic NEKO_TTS_JITTER_* env knobs.

    ``legacy_env_prefix`` (e.g. "NEKO_QWEN_TTS") keeps honoring a provider's
    pre-unification env names (``<prefix>_INITIAL_BUFFER_MS`` / ``_STEADY_BUFFER_MS``)
    as a fallback when the generic knobs are unset, so existing overrides keep working.
    """
    def _resolve_ms(generic_env: str, legacy_suffix: str, default: float) -> float:
        if os.getenv(generic_env) not in (None, ""):
            return _parse_env_float(generic_env, default, 0)
        if legacy_env_prefix:
            legacy_env = f"{legacy_env_prefix}_{legacy_suffix}"
            if os.getenv(legacy_env) not in (None, ""):
                return _parse_env_float(legacy_env, default, 0)
        return default

    initial_ms = _resolve_ms("NEKO_TTS_JITTER_INITIAL_MS", "INITIAL_BUFFER_MS", initial_ms_default)
    steady_ms = _resolve_ms("NEKO_TTS_JITTER_STEADY_MS", "STEADY_BUFFER_MS", steady_ms_default)
    initial_bytes = int(initial_ms / 1000 * _TTS_OUTPUT_BYTES_PER_SECOND)
    steady_bytes = int(steady_ms / 1000 * _TTS_OUTPUT_BYTES_PER_SECOND)
    return AudioJitterBuffer(response_queue, initial_bytes, steady_bytes)

def _enqueue_error(response_queue, error_value):
    """Unified error logging and error-message enqueueing."""
    if isinstance(error_value, str):
        formatted_msg = error_value
    else:
        try:
            formatted_msg = json.dumps(error_value, ensure_ascii=False, default=str)
        except Exception:
            formatted_msg = str(error_value)
    logger.error(f"TTS错误: {formatted_msg}")
    response_queue.put(("__error__", formatted_msg))

try:
    from websockets.connection import State as _WsState
except (ImportError, AttributeError):
    _WsState = None

def _ws_is_open(ws_conn) -> bool:
    """Connection-state check compatible with different websockets versions."""
    if ws_conn is None:
        return False
    if _WsState is not None:
        return getattr(ws_conn, "state", None) is _WsState.OPEN
    return not getattr(ws_conn, "closed", True)

# ─── 非流式输入 TTS 公共基础设施 ───────────────────────────────────────────

class SentenceBuffer:
    """Text sentence buffer — mimics GPT-SoVITS v3 TextBuffer's punctuation-based sentence splitting.

    Accumulates text fragments and automatically splits out complete sentences at
    end-of-sentence punctuation, so TTS can "synthesize while receiving text"
    instead of waiting for the LLM's full reply.
    """

    _SENTENCE_END_RE = re.compile(r'[。！？；…\.\!\?\;]+')
    _MIN_CHARS = 2  # 避免过短片段（如孤立标点）单独合成

    def __init__(self):
        self._buf = ""

    def append(self, text: str) -> list[str]:
        """Append text; returns the list of completed sentences (possibly empty)."""
        self._buf += text
        sentences: list[str] = []
        last = 0
        for m in self._SENTENCE_END_RE.finditer(self._buf):
            seg = self._buf[last:m.end()]
            if len(seg.strip()) >= self._MIN_CHARS:
                sentences.append(seg)
                last = m.end()
        if last:
            self._buf = self._buf[last:]
        return sentences

    def flush(self) -> str | None:
        """Return the remaining text and clear the buffer. Returns None when there is no valid text."""
        text = self._buf
        self._buf = ""
        return text if text.strip() else None

    def clear(self):
        """Discard all buffered text."""
        self._buf = ""

class _AudioQueueProxy:
    """Proxy for response_queue, routing synthesize_fn's put calls to the correct slot buffer.

    synthesize_fn's closure captured the response_queue reference at setup().
    By having setup() capture this proxy instead of the real queue, we can route
    audio chunks to the buffer of the corresponding sentence based on the current
    asyncio Task, without changing synthesize_fn's signature.

    When there is no active task mapping (e.g. sending the __ready__ signal during
    setup), put calls are forwarded directly to the real queue.
    """

    __slots__ = ('_real_queue', '_task_map')

    def __init__(self, real_queue):
        self._real_queue = real_queue
        # task → (seq, gen_id, slot_put_fn)
        self._task_map: dict = {}

    def put(self, item):
        task = None
        try:
            task = asyncio.current_task()
        except RuntimeError:
            pass
        if task is not None and task in self._task_map:
            seq, gen_id, slot_put_fn = self._task_map[task]
            slot_put_fn(seq, gen_id, item)
        else:
            # 非 synth 上下文（setup / 错误处理），直接转发
            self._real_queue.put(item)

    def _register(self, task, seq: int, gen_id: int, slot_put_fn) -> None:
        self._task_map[task] = (seq, gen_id, slot_put_fn)

    def _unregister(self, task) -> None:
        self._task_map.pop(task, None)

    def _clear(self) -> None:
        self._task_map.clear()

async def _non_bistream_tts_main_loop(
    request_queue,
    response_queue,
    synthesize_fn,
    *,
    label: str = "TTS",
    max_concurrent: int = 3,
    sentence_trace_fn=None,
):
    """Generic main loop for non-bistream-input TTS (sentence splitting + parallel synthesis + in-order delivery).

    Text is split into sentences as soon as it arrives; TTS requests for multiple
    sentences are issued in parallel (bounded by ``max_concurrent``), but audio is
    delivered to ``response_queue`` strictly in sentence order, keeping frontend
    playback order correct.

    Design points
    --------
    - **Parallel requests**: synthesis of sentence N can start without waiting for
      sentence N-1 to finish.
    - **In-order delivery**: the drain coroutine forwards audio chunks in
      increasing seq_id order.
    - **Interrupt safety**: on ``__interrupt__`` / speech_id switch,
      ``_generation_id`` is incremented immediately; every in-flight task detects
      the stale generation, drops its data and exits, so no leftover audio leaks
      into response_queue.
    - **No GIL blocking**: request_queue.get runs via ``run_in_executor``;
      internal synchronization uses asyncio primitives only (Event / Semaphore),
      never threading.Lock or time.sleep.

    response_queue proxy mechanism
    -----------------------
    ``synthesize_fn``'s closure has already captured the ``response_queue``
    reference. To redirect audio into per-sentence buffers without changing
    synthesize_fn's signature, the caller (``_run_sentence_tts_worker``) should
    pass an ``_AudioQueueProxy`` instance as ``response_queue``. The proxy's
    ``put`` looks up the slot buffer for the current asyncio Task and writes
    there. If the caller passes the real queue (backward compatibility), it
    degrades to serial mode (max_concurrent=1).

    Args:
        request_queue: multiprocess request queue receiving (speech_id, text) tuples
        response_queue: response queue or an ``_AudioQueueProxy`` instance
        synthesize_fn: async def(text: str, speech_id: str) -> None
        label: log prefix
        max_concurrent: maximum parallel syntheses
    """
    sentence_buf = SentenceBuffer()
    current_speech_id = None

    # ── 代理检测 ──
    is_proxy = isinstance(response_queue, _AudioQueueProxy)
    real_queue = response_queue._real_queue if is_proxy else response_queue
    proxy: _AudioQueueProxy | None = response_queue if is_proxy else None

    # 非代理模式退化为串行（向后兼容）
    if not is_proxy:
        max_concurrent = 1

    # ── 并行合成 + 顺序投递基础设施 ──

    _next_seq: int = 0                                  # 下一个分配的序号
    _slot_buffers: dict[int, list] = {}                 # seq_id → [chunk, ...]
    _slot_done: dict[int, asyncio.Event] = {}           # seq_id → 合成完成事件
    _slot_new_data: dict[int, asyncio.Event] = {}       # seq_id → 有新数据通知
    _tasks: dict[int, asyncio.Task] = {}                # seq_id → synth task
    _sentence_enqueued_at: dict[int, float] = {}        # seq_id → enqueue monotonic time
    _sem = asyncio.Semaphore(max_concurrent)
    _drain_seq: int = 0                                 # drain 当前正在投递的序号
    _drain_task: asyncio.Task | None = None
    _generation_id: int = 0                             # 每次 cancel 递增

    def _trace_sentence(event: str, seq: int, sid: str, text: str, **extra) -> None:
        if sentence_trace_fn is None:
            return
        try:
            sentence_trace_fn(event, seq, sid, text, **extra)
        except Exception:
            pass

    def _alloc_slot() -> int:
        nonlocal _next_seq
        seq = _next_seq
        _next_seq += 1
        _slot_buffers[seq] = []
        _slot_done[seq] = asyncio.Event()
        _slot_new_data[seq] = asyncio.Event()
        return seq

    def _free_slot(seq: int) -> None:
        _slot_buffers.pop(seq, None)
        _slot_done.pop(seq, None)
        _slot_new_data.pop(seq, None)
        _tasks.pop(seq, None)
        _sentence_enqueued_at.pop(seq, None)

    def _slot_put(seq: int, gen_id: int, item) -> None:
        """Write one chunk into the given slot's buffer (called back by the proxy)."""
        if gen_id != _generation_id:
            return
        buf = _slot_buffers.get(seq)
        evt = _slot_new_data.get(seq)
        if buf is None or evt is None:
            return
        buf.append(item)
        evt.set()

    async def _synth_one(seq: int, text: str, sid: str, gen_id: int) -> None:
        """Run synthesize_fn under semaphore protection."""
        done_evt = _slot_done.get(seq)
        if done_evt is None:
            return

        async with _sem:
            if gen_id != _generation_id:
                return
            task = asyncio.current_task()
            started_at = time.perf_counter()
            enqueued_at = _sentence_enqueued_at.get(seq, started_at)
            queue_wait_ms = int((started_at - enqueued_at) * 1000)
            _trace_sentence("start", seq, sid, text, queue_wait_ms=queue_wait_ms)
            if proxy is not None:
                proxy._register(task, seq, gen_id, _slot_put)
            try:
                await synthesize_fn(text, sid)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if gen_id == _generation_id:
                    _trace_sentence("error", seq, sid, text, error=str(exc))
                    _slot_put(seq, gen_id,
                              ("__synth_error__", f"{label} 合成失败: {exc}"))
            finally:
                total_ms = int((time.perf_counter() - started_at) * 1000)
                _trace_sentence("done", seq, sid, text, total_ms=total_ms)
                if proxy is not None:
                    proxy._unregister(task)
                if done_evt is _slot_done.get(seq):
                    done_evt.set()
                    nd = _slot_new_data.get(seq)
                    if nd:
                        nd.set()

    async def _drain_loop(gen_id: int) -> None:
        """Forward audio from slot buffers to the real response_queue in seq_id order."""
        nonlocal _drain_seq
        while gen_id == _generation_id:
            seq = _drain_seq
            buf = _slot_buffers.get(seq)
            done_evt = _slot_done.get(seq)
            new_data_evt = _slot_new_data.get(seq)

            if buf is None or done_evt is None or new_data_evt is None:
                # 当前序号的 slot 还没分配，让出控制权
                await asyncio.sleep(0.01)
                continue

            cursor = 0
            while gen_id == _generation_id:
                # 转发已有的 chunk
                while cursor < len(buf):
                    item = buf[cursor]
                    cursor += 1
                    if (isinstance(item, tuple) and len(item) >= 2
                            and item[0] == "__synth_error__"):
                        _enqueue_error(real_queue, item[1])
                    else:
                        real_queue.put(item)

                if done_evt.is_set():
                    # 该句子合成完毕，转发剩余 chunk 后推进到下一句
                    while cursor < len(buf):
                        item = buf[cursor]
                        cursor += 1
                        if (isinstance(item, tuple) and len(item) >= 2
                                and item[0] == "__synth_error__"):
                            _enqueue_error(real_queue, item[1])
                        else:
                            real_queue.put(item)
                    _free_slot(seq)
                    _drain_seq = seq + 1
                    break

                # 等待新数据或完成信号
                new_data_evt.clear()
                if cursor < len(buf) or done_evt.is_set():
                    continue
                try:
                    await asyncio.wait_for(new_data_evt.wait(), timeout=0.1)
                except asyncio.TimeoutError:
                    pass

    def _ensure_drain() -> None:
        nonlocal _drain_task
        if _drain_task is None or _drain_task.done():
            _drain_task = asyncio.create_task(_drain_loop(_generation_id))

    def _enqueue_sentence(text: str, sid: str) -> None:
        seq = _alloc_slot()
        _sentence_enqueued_at[seq] = time.perf_counter()
        _trace_sentence("enqueue", seq, sid, text)
        task = asyncio.create_task(_synth_one(seq, text, sid, _generation_id))
        _tasks[seq] = task
        _ensure_drain()

    async def _drain_remaining() -> None:
        """Wait until all submitted sentences are synthesized and delivered."""
        tasks = list(_tasks.values())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for _ in range(200):  # 最多等 2 秒
            if not _slot_buffers:
                break
            await asyncio.sleep(0.01)

    async def _cancel_all() -> None:
        nonlocal _drain_task, _next_seq, _drain_seq, _generation_id
        _generation_id += 1  # 使所有 in-flight 的 synth 和 drain 立即失效

        for task in list(_tasks.values()):
            if not task.done():
                task.cancel()
        for task in list(_tasks.values()):
            if not task.done():
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

        if _drain_task and not _drain_task.done():
            _drain_task.cancel()
            try:
                await _drain_task
            except (asyncio.CancelledError, Exception):
                pass
        _drain_task = None

        _slot_buffers.clear()
        _slot_done.clear()
        _slot_new_data.clear()
        _tasks.clear()
        _sentence_enqueued_at.clear()
        _next_seq = 0
        _drain_seq = 0
        if proxy is not None:
            proxy._clear()

    # ── 主循环 ──
    loop = asyncio.get_running_loop()

    while True:
        try:
            sid, tts_text = await loop.run_in_executor(None, request_queue.get)
        except Exception:
            break

        if sid == TTS_SHUTDOWN_SENTINEL:
            break

        if sid == "__interrupt__":
            await _cancel_all()
            sentence_buf.clear()
            current_speech_id = None
            continue

        if current_speech_id != sid and sid is not None:
            await _cancel_all()
            current_speech_id = sid
            sentence_buf.clear()

        if sid is None:
            remaining = sentence_buf.flush()
            if remaining and current_speech_id is not None:
                _enqueue_sentence(remaining, current_speech_id)
            await _drain_remaining()
            current_speech_id = None
            continue

        if tts_text and tts_text.strip():
            for sent in sentence_buf.append(tts_text):
                _enqueue_sentence(sent, current_speech_id)

    await _cancel_all()

def _run_sentence_tts_worker(
    request_queue,
    response_queue,
    async_setup_fn,
    *,
    label: str,
    sentence_trace_fn=None,
):
    """Generic skeleton for HTTP per-sentence synthesis TTS workers.

    Wraps the boilerplate shared by all ``_non_bistream_tts_main_loop``-family
    workers: asyncio event loop startup, ready-signal sending, main-loop exception
    handling, resource cleanup.

    Internally creates an ``_AudioQueueProxy`` and passes it to
    ``async_setup_fn``, so the ``synthesize_fn`` closure captures the proxy
    instead of the real queue, enabling per-task routing of audio to the correct
    slot buffer during parallel synthesis.

    Args:
        request_queue / response_queue: multiprocess queues.
        async_setup_fn: an **async** factory function with the signature::

            async def setup(queue_proxy) -> tuple[synthesize_fn, cleanup_fn | None]

            - queue_proxy: an ``_AudioQueueProxy`` instance; synthesize_fn should
              put audio data through it (rather than referencing response_queue
              directly).
            - synthesize_fn: ``async def(text: str, speech_id: str) -> None``
            - cleanup_fn: optional ``async def() -> None``

            If setup hits an unrecoverable error, it should itself
            ``queue_proxy.put(("__ready__", False))`` and raise.
        label: log / error message prefix.
    """
    proxy = _AudioQueueProxy(response_queue)

    async def _worker():
        cleanup_fn = None
        try:
            synthesize_fn, cleanup_fn = await async_setup_fn(proxy)
        except Exception as exc:
            logger.error(f"{label} 初始化失败: {exc}")
            try:
                response_queue.put(("__ready__", False))
            except Exception:
                pass
            return

        logger.info(f"{label} 已就绪，发送就绪信号")
        response_queue.put(("__ready__", True))

        try:
            await _non_bistream_tts_main_loop(
                request_queue, proxy, synthesize_fn,
                label=label,
                sentence_trace_fn=sentence_trace_fn,
            )
        except Exception as exc:
            _enqueue_error(response_queue, f"{label} Worker 错误: {exc}")
            response_queue.put(("__ready__", False))
        finally:
            if cleanup_fn:
                try:
                    await cleanup_fn()
                except Exception:
                    pass

    try:
        asyncio.run(_worker())
    except Exception as e:
        logger.error(f"{label} Worker 启动失败: {e}")
        response_queue.put(("__ready__", False))
