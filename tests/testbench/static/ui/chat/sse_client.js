/**
 * chat/sse_client.js — 用 fetch + ReadableStream 消费 POST SSE 流 (P09).
 *
 * 为什么不用 EventSource (`core/api.js::openSse`):
 *   - EventSource 规范只支持 GET, 不能带 body.
 *   - `/api/chat/send` 把 `content / role / time_advance` 放 JSON body, 所以必须自己
 *     走 fetch + Response.body.getReader() + TextDecoder.
 *
 * 输出格式: 后端每一帧是 `data: <json>\n\n`. 流式过程中可能一次 read 拿到半帧
 * 或多帧, 因此用 buffer 边累积边按 `\n\n` 切分.
 *
 * 回调:
 *   - onEvent(eventObj)    后端 payload 已 JSON.parse 后的对象, 含 `event` 字段.
 *   - onError(errInfo)     传输层错误或一次"顶级 try/catch 兜底"异常, 不是
 *                          后端主动 emit 的 {event:'error', ...} — 后者经
 *                          `onEvent` 照常分发.
 *   - onDone()             流干净结束 (reader 读到 done).
 *
 * 返回值: `{ abort }` — 调用 abort() 立即中断底层 fetch (AbortController).
 * 未来 Auto-Dialog / 用户点 [Stop] 时会用到.
 */

import { emit } from '../../core/state.js';

const FRAME_SEP = '\n\n';

/**
 * @param {string} url
 * @param {object} body         JSON body 整体 payload (不需要预 stringify)
 * @param {object} cbs          { onEvent, onError, onDone }
 * @returns {{ abort: () => void }}
 */
export function streamPostSse(url, body, cbs = {}) {
  const controller = new AbortController();
  const { onEvent, onError, onDone } = cbs;

  (async () => {
    let resp;
    try {
      resp = await fetch(url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Accept': 'text/event-stream',
        },
        body: JSON.stringify(body || {}),
        signal: controller.signal,
      });
    } catch (err) {
      if (controller.signal.aborted) {
        onDone?.();
        return;
      }
      const info = { type: 'network', message: String(err) };
      onError?.(info);
      emit('sse:error', { url, ...info });
      return;
    }

    if (!resp.ok) {
      // P24 Day 7 (§12.3.F): 尽可能把 body 解析成 JSON 让 onError 拿到
      // 结构化 detail (例如 `{error_type, message, errors:[...]}`).
      // 失败时 fall back 到 text — 保留原行为, 不破坏老调用方.
      let rawText = null;
      let detailObj = null;
      try {
        rawText = await resp.text();
        if (rawText) {
          try {
            detailObj = JSON.parse(rawText);
          } catch { /* not JSON, keep rawText as-is */ }
        }
      } catch (_) { /* ignore */ }

      // `detailObj.detail` 是 FastAPI HTTPException 标准包装; 有则拆出.
      const wrapped = (detailObj && typeof detailObj === 'object'
        && detailObj.detail !== undefined)
        ? detailObj.detail
        : detailObj;

      let message;
      if (wrapped && typeof wrapped === 'object' && wrapped.message) {
        message = String(wrapped.message);
      } else if (typeof wrapped === 'string') {
        message = wrapped;
      } else {
        message = rawText || `HTTP ${resp.status}`;
      }

      const info = {
        type: 'http_error',
        status: resp.status,
        message,
        detail: wrapped || null,
      };
      onError?.(info);
      emit('sse:error', { url, ...info });
      return;
    }

    if (!resp.body) {
      // 极端浏览器 / polyfill 没有 stream 支持时, 一次性读整个 body.
      try {
        const text = await resp.text();
        _dispatchBuffer(text, onEvent);
        onDone?.();
      } catch (err) {
        onError?.({ type: 'read_error', message: String(err) });
      }
      return;
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buffer = '';
    try {
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let sepIdx;
        while ((sepIdx = buffer.indexOf(FRAME_SEP)) !== -1) {
          const frame = buffer.slice(0, sepIdx);
          buffer = buffer.slice(sepIdx + FRAME_SEP.length);
          _dispatchFrame(frame, onEvent);
        }
      }
      // Drain any trailing partial frame (tolerant to missing final CRLF CRLF).
      if (buffer.trim()) _dispatchFrame(buffer, onEvent);
      onDone?.();
    } catch (err) {
      if (controller.signal.aborted) {
        onDone?.();
        return;
      }
      onError?.({ type: 'read_error', message: String(err) });
    }
  })();

  return {
    abort() { controller.abort(); },
  };
}

function _dispatchFrame(frame, onEvent) {
  // 跳过注释 / keepalive (``: ping``).
  const lines = frame.split('\n');
  const payloads = [];
  for (const line of lines) {
    if (!line || line.startsWith(':')) continue;
    if (line.startsWith('data:')) {
      payloads.push(line.slice(5).trimStart());
    }
    // event: 和 id: 字段本期不用; 简单忽略.
  }
  if (!payloads.length) return;
  const dataStr = payloads.join('\n');
  if (dataStr === '[DONE]') return;
  let obj;
  try { obj = JSON.parse(dataStr); }
  catch (_) { obj = { event: 'raw', raw: dataStr }; }
  onEvent?.(obj);
}

function _dispatchBuffer(text, onEvent) {
  for (const raw of text.split(FRAME_SEP)) {
    if (raw) _dispatchFrame(raw, onEvent);
  }
}
