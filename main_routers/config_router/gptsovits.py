# -*- coding: utf-8 -*-
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

"""GPT-SoVITS voice listing and connectivity test endpoints.

Split out of the former monolithic ``main_routers/config_router.py``.
"""

from ._shared import logger, router

import asyncio
from fastapi import Request
from fastapi.responses import JSONResponse


@router.post("/gptsovits/list_voices")
async def list_gptsovits_voices(request: Request):
    """Proxy a request to the GPT-SoVITS v3 API to fetch the available voice config list."""
    import aiohttp
    from utils.gptsovits_config import is_valid_http_url
    try:
        data = await request.json()
        # 边界规范化：转字符串(兼容 None)+trim+去尾斜杠，校验与下游请求复用同一个值，
        # 避免 {"api_url": null} 崩 / 带空白的 URL 绕过校验后拼出畸形端点。
        api_url = str(data.get("api_url") or "").strip().rstrip("/")

        if not api_url:
            return JSONResponse({"success": False, "error": "TTS_GPT_SOVITS_URL_REQUIRED", "code": "TTS_GPT_SOVITS_URL_REQUIRED"}, status_code=400)

        # URL 校验：本地或远程均可（按维护者 SSRF posture 决定，对齐 vLLM-Omni）；
        # 仅挡非 http(s) / 空 host，不再限制 loopback。
        if not is_valid_http_url(api_url):
            return JSONResponse({"success": False, "error": "TTS_GPT_SOVITS_URL_INVALID", "code": "TTS_GPT_SOVITS_URL_INVALID"}, status_code=400)

        endpoint = f"{api_url}/api/v3/voices"
        async with aiohttp.ClientSession() as session:
            async with session.get(endpoint, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                try:
                    result = await resp.json(content_type=None)
                except Exception:
                    text = await resp.text()
                    # 上游响应可能含 TTS 原文 echo，不写 logger
                    logger.error(f"GPT-SoVITS v3 API 返回非 JSON 响应 (HTTP {resp.status}, body_len={len(text)})")
                    print(f"[GSV] API 非 JSON 响应 raw: {text[:200]}")
                    return {"success": False, "error": "Upstream TTS service error", "code": "TTS_CONNECTION_FAILED"}
                if resp.status == 200:
                    return {"success": True, "voices": result}
                logger.error(f"GPT-SoVITS v3 API 返回错误状态 HTTP {resp.status}")
                print(f"[GSV] API 错误状态 raw: {str(result)[:200]}")
                return {"success": False, "error": "Upstream TTS service error", "code": "TTS_CONNECTION_FAILED"}
    except aiohttp.ClientError as e:
        logger.error(f"GPT-SoVITS v3 API 请求失败: {e}")
        return {"success": False, "error": "Internal TTS connection error", "code": "TTS_CONNECTION_FAILED"}
    except Exception as e:
        logger.error(f"获取 GPT-SoVITS 语音列表失败: {e}")
        return {"success": False, "error": "Internal TTS connection error", "code": "TTS_CONNECTION_FAILED"}


@router.post("/gptsovits/test_connectivity")
async def test_gptsovits_connectivity(request: Request):
    """Test the full GPT-SoVITS pipeline: WebSocket connect → init → ready → send short text → receive response.

    Does not play audio; only verifies the service is reachable and the speech synthesis engine works.
    """
    import websockets as _ws
    import json as _json
    from utils.gptsovits_config import is_valid_http_url

    try:
        data = await request.json()
        # 边界规范化（同 list_voices）：转字符串(兼容 None)+trim+去尾斜杠后再校验与拼接。
        api_url = str(data.get("api_url") or "http://127.0.0.1:9881").strip().rstrip("/")
        voice_id = (data.get("voice_id", "") or "init").strip()
        # i18n test text
        test_text = data.get("test_text", "") or "连通性测试"

        # URL 校验：本地或远程均可（SSRF posture 同 vLLM-Omni）；仅挡非 http(s) / 空 host。
        if not is_valid_http_url(api_url):
            return {"success": False, "error": "URL 格式无效", "error_code": "missing_params"}

        # Convert HTTP URL to WebSocket URL
        if api_url.startswith("http://"):
            ws_base = "ws://" + api_url[7:]
        elif api_url.startswith("https://"):
            ws_base = "wss://" + api_url[8:]
        else:
            ws_base = "ws://" + api_url
        ws_url = f"{ws_base}/api/v3/tts/stream-input"

        # Strip gsv: prefix and parse advanced params (same as gptsovits_tts_worker)
        if voice_id.startswith("gsv:"):
            voice_id = voice_id[4:].strip() or "init"
        extra_params = {}
        if '|' in voice_id:
            parts = voice_id.split('|', 1)
            voice_id = parts[0].strip() or "init"
            try:
                extra_params = _json.loads(parts[1])
                if not isinstance(extra_params, dict):
                    extra_params = {}
            except (_json.JSONDecodeError, IndexError, TypeError, ValueError):
                extra_params = {}

        async with asyncio.timeout(10):
            async with _ws.connect(ws_url, ping_interval=None, max_size=10 * 1024 * 1024) as ws:
                # Step 1: Send init (merge advanced params, filter reserved fields)
                safe_params = {k: v for k, v in extra_params.items() if k not in ("cmd", "voice_id")}
                init_msg = {"cmd": "init", "voice_id": voice_id, **safe_params}
                await ws.send(_json.dumps(init_msg))

                # Step 2: Wait for ready
                ready_msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
                ready_data = _json.loads(ready_msg)
                if ready_data.get("type") != "ready":
                    error_detail = str(ready_data.get("message", ready_data))[:200]
                    return {"success": False, "error": f"init 失败: {error_detail}", "error_code": "unknown"}

                # Step 3: Send test text (use "append" command, same as gptsovits_tts_worker)
                await ws.send(_json.dumps({"cmd": "append", "data": test_text}))
                # Small delay to let GSV process the text before sending end
                await asyncio.sleep(0.1)
                await ws.send(_json.dumps({"cmd": "end"}))

                # Step 4: Wait for first response
                first_response = await asyncio.wait_for(ws.recv(), timeout=10.0)

                # Collect responses for verification
                audio_chunks = []
                got_sentence = False
                gsv_error = ""

                if isinstance(first_response, bytes):
                    audio_chunks.append(first_response)
                    logger.info(f"[GSV Test] First response: binary {len(first_response)} bytes")
                else:
                    logger.info(f"[GSV Test] First response (text, len={len(first_response)})")
                    print(f"[GSV Test] First response: {first_response[:200]}")
                    try:
                        first_data = _json.loads(first_response)
                        if first_data.get("type") == "sentence":
                            got_sentence = True
                    except _json.JSONDecodeError:
                        pass

                # Continue receiving until done or timeout
                try:
                    while True:
                        msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
                        if isinstance(msg, bytes):
                            audio_chunks.append(msg)
                            logger.debug(f"[GSV Test] Audio chunk: {len(msg)} bytes")
                        else:
                            logger.info(f"[GSV Test] JSON msg (len={len(msg)})")
                            print(f"[GSV Test] JSON msg: {msg[:200]}")
                            msg_data = _json.loads(msg)
                            if msg_data.get("type") == "sentence":
                                got_sentence = True
                            if msg_data.get("type") == "error":
                                gsv_error = str(msg_data.get("message", ""))[:200]
                                logger.warning(f"[GSV Test] GSV error: {gsv_error}")
                                break
                            if msg_data.get("type") == "done":
                                break
                except asyncio.TimeoutError:
                    logger.info(f"[GSV Test] Receive timeout, collected {len(audio_chunks)} audio chunks")
                except Exception as e:
                    logger.info(f"[GSV Test] Receive ended: {e}")

                # Success if we got a "sentence" event (text was accepted) or audio data
                result = {"success": got_sentence or len(audio_chunks) > 0}
                if not result["success"]:
                    result["error"] = gsv_error if gsv_error else "GSV 服务未返回有效响应"
                    result["error_code"] = "unknown"

                if result["success"]:
                    logger.info(f"[ConnectivityTest] GPT-SoVITS → ✅ 收到 {len(audio_chunks)} 个音频块")
                else:
                    logger.info("[ConnectivityTest] GPT-SoVITS → ❌ 未收到有效响应")

                # --- 以下为编写连通测试时使用的音频播放验证代码，已确认可行（2026-04-22） ---
                # --- 保留供后续调试使用，正常运行时不启用 ---
                # import base64
                # raw_pcm = b""
                # sample_rate = 0
                # for chunk in audio_chunks:
                #     if len(chunk) >= 44:
                #         sr = int.from_bytes(chunk[24:28], 'little')
                #         if sample_rate == 0:
                #             sample_rate = sr
                #         pcm = chunk[44:]
                #         if len(pcm) >= 2:
                #             if len(pcm) % 2 != 0:
                #                 pcm = pcm[:-1]
                #             raw_pcm += pcm
                # if raw_pcm:
                #     result["audio_data"] = base64.b64encode(raw_pcm).decode('ascii')
                #     result["sample_rate"] = sample_rate
                #     result["audio_length_ms"] = int(len(raw_pcm) / 2 / sample_rate * 1000) if sample_rate else 0

                return result

    except (TimeoutError, asyncio.TimeoutError):
        return {"success": False, "error": "请求超时（10秒）", "error_code": "timeout"}
    except OSError as e:
        err_str = str(e).lower()
        if "connection refused" in err_str or "connect call failed" in err_str:
            return {"success": False, "error": "无法连接到 GPT-SoVITS 服务", "error_code": "connection_refused"}
        return {"success": False, "error": f"连接失败: {e}", "error_code": "connection_refused"}
    except Exception as e:
        logger.error(f"GPT-SoVITS 连通测试失败: {e}")
        return {"success": False, "error": str(e)[:200], "error_code": "unknown"}
