# -*- coding: utf-8 -*-
"""
Tutorial TTS Router - Edge TTS 新手引导语音合成接口

使用免费的 Edge TTS（Microsoft Neural Voices）为新手引导提供高质量语音。
支持中文、英文、日文等多语言，带磁盘缓存避免重复合成。
"""

import os
import hashlib
import time

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

from utils.logger_config import get_module_logger

router = APIRouter(prefix="/api/tutorial-tts", tags=["tutorial-tts"])
logger = get_module_logger(__name__, "Main")

# Edge TTS 语音映射（全部使用女性 Neural 语音，与项目风格一致）
EDGE_TTS_VOICE_MAP = {
    'zh-CN': 'zh-CN-XiaoyiNeural',         # 中文女声，温柔亲切
    'zh-TW': 'zh-TW-HsiaoChenNeural',      # 台湾中文女声
    'en':    'en-US-JennyNeural',           # 英文女声，自然清晰
    'ja':    'ja-JP-NanamiNeural',          # 日文女声，自然柔和
    'ko':    'ko-KR-SunHiNeural',           # 韩文女声
    'ru':    'ru-RU-SvetlanaNeural',        # 俄文女声
}
DEFAULT_VOICE = 'en-US-JennyNeural'

# 缓存配置
CACHE_MAX_AGE_DAYS = 7
MAX_TEXT_LENGTH = 500


def _get_cache_dir() -> str:
    """获取或创建 Tutorial TTS 缓存目录"""
    try:
        from .shared_state import get_config_manager
        cm = get_config_manager()
        cache_dir = os.path.join(str(cm.config_dir), 'cache', 'tutorial_tts')
    except Exception:
        # 回退到当前工作目录下的缓存
        cache_dir = os.path.join(os.getcwd(), 'cache', 'tutorial_tts')
    os.makedirs(cache_dir, exist_ok=True)
    return cache_dir


def _normalize_lang(lang: str) -> str:
    """将 i18n 语言代码规范化为语音映射键"""
    if not lang:
        return 'zh-CN'
    lang = lang.strip().lower()
    if lang in ('zh', 'zh-cn', 'cmn', 'zh-hans'):
        return 'zh-CN'
    if lang in ('zh-tw', 'zh-hant'):
        return 'zh-TW'
    if lang.startswith('en'):
        return 'en'
    if lang.startswith('ja'):
        return 'ja'
    if lang.startswith('ko'):
        return 'ko'
    if lang.startswith('ru'):
        return 'ru'
    return 'en'


def _cleanup_old_cache(cache_dir: str):
    """清理超过 CACHE_MAX_AGE_DAYS 天的缓存文件"""
    try:
        cutoff = time.time() - (CACHE_MAX_AGE_DAYS * 86400)
        for filename in os.listdir(cache_dir):
            if not filename.endswith('.mp3'):
                continue
            filepath = os.path.join(cache_dir, filename)
            try:
                if os.path.getmtime(filepath) < cutoff:
                    os.remove(filepath)
                    logger.debug(f"清理过期缓存: {filename}")
            except OSError:
                pass
    except Exception as e:
        logger.warning(f"缓存清理出错: {e}")


@router.post("/synthesize")
async def synthesize_tutorial_tts(request: Request):
    """
    合成新手引导语音

    接收 JSON: {"text": "要朗读的文本", "lang": "zh-CN"}
    返回 MP3 音频文件
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid request body"}, status_code=400)

    text = (body.get("text") or "").strip()
    lang = body.get("lang", "zh-CN")

    if not text:
        return JSONResponse({"error": "text is empty"}, status_code=400)
    if len(text) > MAX_TEXT_LENGTH:
        return JSONResponse(
            {"error": f"text too long (max {MAX_TEXT_LENGTH} chars)"},
            status_code=400
        )

    # 确定语音
    voice_key = _normalize_lang(lang)
    voice = EDGE_TTS_VOICE_MAP.get(voice_key, DEFAULT_VOICE)

    # 计算缓存键
    cache_key = hashlib.sha256(f"{voice}:{text}".encode('utf-8')).hexdigest()
    cache_dir = _get_cache_dir()
    cache_path = os.path.join(cache_dir, f"{cache_key}.mp3")

    # 命中缓存直接返回
    if os.path.exists(cache_path) and os.path.getsize(cache_path) > 0:
        logger.debug(f"Tutorial TTS 缓存命中: {text[:30]}...")
        return FileResponse(cache_path, media_type="audio/mpeg")

    # 使用 edge-tts 合成
    try:
        import edge_tts
    except ImportError:
        logger.error("edge-tts 未安装，无法合成语音")
        return JSONResponse(
            {"error": "edge-tts not available"},
            status_code=503
        )

    try:
        logger.info(f"Tutorial TTS 合成: voice={voice}, text={text[:50]}...")
        communicate = edge_tts.Communicate(text, voice)

        # 原子写入：先写临时文件再重命名，避免并发读取到不完整文件
        tmp_path = cache_path + ".tmp"
        await communicate.save(tmp_path)
        os.replace(tmp_path, cache_path)

        return FileResponse(cache_path, media_type="audio/mpeg")

    except Exception as e:
        logger.error(f"Tutorial TTS 合成失败: {e}")
        # 清理临时文件
        tmp_path = cache_path + ".tmp"
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        return JSONResponse({"error": "synthesis failed"}, status_code=502)


@router.get("/voices")
async def list_tutorial_voices():
    """列出所有可用的教程语音及其对应语言"""
    return JSONResponse({
        "voices": {k: v for k, v in EDGE_TTS_VOICE_MAP.items()},
        "default": DEFAULT_VOICE
    })


@router.post("/cleanup-cache")
async def cleanup_cache():
    """手动触发缓存清理"""
    cache_dir = _get_cache_dir()
    _cleanup_old_cache(cache_dir)
    return JSONResponse({"status": "ok"})
