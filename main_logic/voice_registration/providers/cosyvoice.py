"""Alibaba CosyVoice clone and design registration adapter."""

import asyncio
import io
import logging
from urllib.parse import urlparse

import httpx

from utils.dashscope_region import DASHSCOPE_GLOBAL_LOCK, configure_dashscope_sdk_urls
from utils.http.external_client import get_external_http_client

from .base import VoiceCloneError, first_nested_value


logger = logging.getLogger(__name__)
VOICE_DESIGN_DEFAULT_MEDIA_TYPE = "audio/wav"


class QwenVoiceCloneError(VoiceCloneError):
    """Compatibility error name for CosyVoice registration failures."""


def qwen_language_hints(ref_language: str) -> list[str]:
    """Translate NEKO's clone language into DashScope clone hints."""
    return [] if ref_language == 'ch' else [ref_language]


class CosyVoiceRegistrationClient:
    """CosyVoice clone client based on DashScope VoiceEnrollmentService."""

    MAX_RETRIES = 3
    RETRY_DELAY = 3

    def __init__(self, api_key: str, tflink_upload_url: str, dashscope_base_url: str = ""):
        self.api_key = api_key
        self.tflink_upload_url = tflink_upload_url
        self.dashscope_base_url = dashscope_base_url

    async def upload_file(
        self,
        audio_buffer: io.BytesIO,
        filename: str,
        mime_type: str = 'audio/wav',
    ) -> str:
        file_size = len(audio_buffer.getvalue())
        if file_size > 100 * 1024 * 1024:
            raise QwenVoiceCloneError('文件大小超过100MB，超过tfLink的限制')

        audio_buffer.seek(0)
        files = {'file': (filename, audio_buffer, mime_type)}
        headers = {'Accept': 'application/json'}
        logger.info(
            "正在上传文件到tfLink，文件名: %s, 大小: %d bytes, MIME类型: %s",
            filename,
            file_size,
            mime_type,
        )
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(self.tflink_upload_url, files=files, headers=headers)
                if response.status_code != 200:
                    raise QwenVoiceCloneError(
                        f'上传到tfLink失败，状态码: {response.status_code}, 详情: {response.text[:200]}'
                    )
                try:
                    data = response.json()
                except ValueError as exc:
                    raise QwenVoiceCloneError(
                        f'上传成功但响应格式无法解析: {response.text[:200]}'
                    ) from exc

                logger.info("tfLink原始响应: %s", data)
                temporary_url = None
                for key in ('downloadLink', 'download_link', 'url', 'direct_link', 'link', 'download_url'):
                    if key in data:
                        temporary_url = data[key]
                        logger.info("找到下载链接键: %s", key)
                        break
                if not temporary_url:
                    raise QwenVoiceCloneError(f'上传成功但无法从响应中提取URL: {data}')
                if not temporary_url.startswith(('http://', 'https://')):
                    raise QwenVoiceCloneError(f'无效的URL格式: {temporary_url}')

                test_response = await client.head(temporary_url, timeout=10)
                if test_response.status_code >= 400:
                    raise QwenVoiceCloneError(
                        f'生成的临时URL无法访问: {temporary_url}, 状态码: {test_response.status_code}'
                    )
                logger.info("成功获取临时URL并验证可访问性: %s", temporary_url)
                return temporary_url
        except QwenVoiceCloneError:
            raise
        except httpx.TimeoutException as exc:
            raise QwenVoiceCloneError("上传音频到tfLink超时，请稍后重试") from exc
        except Exception as exc:
            raise QwenVoiceCloneError(f"上传音频到tfLink失败: {exc}") from exc

    def create_voice(
        self,
        prefix: str,
        url: str,
        language_hints: list[str],
        target_model: str | None = None,
    ) -> tuple[str, str | None]:
        import dashscope
        from dashscope.audio.tts_v2 import VoiceEnrollmentService
        from utils.api_config_loader import (
            cosyvoice_model_supports_language_hints,
            get_cosyvoice_clone_model,
        )

        if target_model is None:
            target_model = get_cosyvoice_clone_model(self.dashscope_base_url)
        kwargs: dict = {
            'target_model': target_model,
            'prefix': prefix,
            'url': url,
        }
        if language_hints and cosyvoice_model_supports_language_hints(target_model):
            kwargs['language_hints'] = language_hints

        try:
            with DASHSCOPE_GLOBAL_LOCK:
                dashscope.api_key = self.api_key
                configure_dashscope_sdk_urls(
                    dashscope,
                    self.dashscope_base_url,
                    websocket_path="inference",
                )
                service = VoiceEnrollmentService()
                voice_id = service.create_voice(**kwargs)
                request_id = service.get_last_request_id()
            logger.info("CosyVoice 音色注册成功: voice_id=%s", voice_id)
            return voice_id, request_id
        except Exception as exc:
            raise QwenVoiceCloneError(str(exc)) from exc

    async def clone_voice(
        self,
        audio_buffer: io.BytesIO,
        filename: str,
        prefix: str,
        language_hints: list[str],
        mime_type: str = 'audio/wav',
        target_model: str | None = None,
    ) -> tuple[str, str, str | None]:
        temporary_url = await self.upload_file(audio_buffer, filename, mime_type)
        last_error: Exception | None = None
        for attempt in range(self.MAX_RETRIES):
            try:
                logger.info(
                    "开始音色注册（尝试 %d/%d），使用URL: %s",
                    attempt + 1,
                    self.MAX_RETRIES,
                    temporary_url,
                )
                voice_id, request_id = await asyncio.to_thread(
                    self.create_voice,
                    prefix=prefix,
                    url=temporary_url,
                    language_hints=language_hints,
                    target_model=target_model,
                )
                return voice_id, temporary_url, request_id
            except QwenVoiceCloneError as exc:
                last_error = exc
                error_detail = str(exc)
                is_timeout = any(
                    keyword in error_detail.lower()
                    for keyword in ("responsetimeout", "response timeout", "timeout")
                )
                is_download_failed = "download audio failed" in error_detail or "415" in error_detail
                if (is_timeout or is_download_failed) and attempt < self.MAX_RETRIES - 1:
                    label = '超时' if is_timeout else '文件下载失败'
                    logger.warning("检测到%s错误，等待 %d 秒后重试...", label, self.RETRY_DELAY)
                    await asyncio.sleep(self.RETRY_DELAY)
                    continue
                if is_timeout:
                    raise QwenVoiceCloneError(
                        f'音色注册超时，已尝试{self.MAX_RETRIES}次'
                    ) from exc
                if is_download_failed:
                    raise QwenVoiceCloneError(
                        f'音色注册失败: 无法下载音频文件，已尝试{self.MAX_RETRIES}次'
                    ) from exc
                raise
        raise last_error or QwenVoiceCloneError("音色注册失败: 未知错误")


def customization_url(base_url: str) -> str:
    """Build the DashScope customization endpoint used by Voice Design."""
    fallback = "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/customization"
    raw = (base_url or "").strip()
    if not raw:
        return fallback
    try:
        parsed = urlparse(raw)
    except Exception:
        return fallback
    if not parsed.scheme or not parsed.netloc:
        return fallback
    if parsed.path.rstrip("/").endswith("/api/v1/services/audio/tts/customization"):
        return raw.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}/api/v1/services/audio/tts/customization"


def design_language_hints(ref_language: str) -> list[str]:
    normalized = str(ref_language or "ch").strip().lower()
    return ["en" if normalized == "en" else "zh"]


async def design_voice(
    *,
    api_key: str,
    base_url: str,
    prefix: str,
    voice_prompt: str,
    preview_text: str,
    ref_language: str,
    target_model: str,
    http_client: httpx.AsyncClient | None = None,
) -> tuple[str, str, str, str | None]:
    """Create a reusable CosyVoice voice from a text description."""
    from utils.api_config_loader import cosyvoice_model_supports_language_hints

    payload_input = {
        "action": "create_voice",
        "target_model": target_model,
        "voice_prompt": voice_prompt,
        "preview_text": preview_text,
        "prefix": prefix,
    }
    if cosyvoice_model_supports_language_hints(target_model):
        payload_input["language_hints"] = design_language_hints(ref_language)
    payload = {
        "model": "voice-enrollment",
        "input": payload_input,
        "parameters": {"sample_rate": 24000, "response_format": "wav"},
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    endpoint = customization_url(base_url)
    client = http_client or get_external_http_client()
    try:
        response = await client.post(endpoint, headers=headers, json=payload, timeout=90)
    except httpx.RequestError as exc:
        host = urlparse(endpoint).netloc or endpoint
        raise QwenVoiceCloneError(
            f"CosyVoice voice design network error while connecting to {host}: {exc}. "
            "Please check DashScope base URL, DNS, and proxy settings."
        ) from exc
    if response.status_code >= 400:
        raise QwenVoiceCloneError(
            f"CosyVoice voice design upstream error ({response.status_code}): {response.text[:300]}"
        )
    try:
        data = response.json()
    except Exception as exc:
        raise QwenVoiceCloneError("CosyVoice voice design returned invalid JSON") from exc

    voice_id = first_nested_value(data, {"voice_id", "voiceId"})
    if not isinstance(voice_id, str) or not voice_id.strip():
        raise QwenVoiceCloneError("CosyVoice voice design did not return voice_id")

    preview_audio_block = first_nested_value(data, {"preview_audio"})
    if isinstance(preview_audio_block, dict):
        preview_audio = first_nested_value(
            preview_audio_block,
            {"data", "audio", "audio_base64", "audio_base_64", "audio_url", "url"},
        )
        media_type = first_nested_value(
            preview_audio_block,
            {"media_type", "mime_type", "content_type", "response_format"},
        )
    else:
        preview_audio = preview_audio_block or first_nested_value(
            data,
            {"audio", "audio_base64", "audio_base_64", "audio_url", "preview_audio_url", "url"},
        )
        media_type = first_nested_value(
            data,
            {"media_type", "mime_type", "content_type", "response_format"},
        )
    request_id = first_nested_value(data, {"request_id", "requestId"})

    audio_text = str(preview_audio or "").strip()
    resolved_media_type = str(media_type or VOICE_DESIGN_DEFAULT_MEDIA_TYPE).strip()
    if audio_text.startswith("data:"):
        header, _, audio_text = audio_text.partition(",")
        if ";" in header:
            resolved_media_type = header[5:].split(";", 1)[0] or resolved_media_type
    if resolved_media_type in ("wav", "mp3", "mpeg"):
        resolved_media_type = "audio/mpeg" if resolved_media_type in ("mp3", "mpeg") else "audio/wav"
    return voice_id.strip(), audio_text, resolved_media_type, str(request_id or "").strip() or None


VoiceRegistrationClient = CosyVoiceRegistrationClient
QwenVoiceCloneClient = CosyVoiceRegistrationClient

# Compatibility names retained while callers migrate to the provider contract.
COSYVOICE_VOICE_DESIGN_DEFAULT_MEDIA_TYPE = VOICE_DESIGN_DEFAULT_MEDIA_TYPE
_cosyvoice_customization_url = customization_url
_cosyvoice_design_language_hints = design_language_hints
_cosyvoice_design_voice = design_voice
