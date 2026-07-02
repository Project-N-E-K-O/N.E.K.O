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

"""Text translation endpoint (/translate).

Split out of the former monolithic ``main_routers/system_router.py``.
"""

from ._shared import _validate_local_mutation_request, logger, router
from fastapi import Request
from utils.language_utils import detect_language, translate_text, normalize_language_code


@router.post('/translate')
async def translate_text_api(request: Request):
    """
    Text translation API (used by the frontend subtitle module).

    Request format:
    {
        "text": "text to translate",
        "target_lang": "target language code ('zh', 'en', 'ja', 'ko')",
        "source_lang": "source language code (optional; auto-detected when null)"
    }

    Response format:
    {
        "success": true/false,
        "translated_text": "translated text",
        "source_lang": "detected source language code",
        "target_lang": "target language code"
    }
    """
    validation_error = _validate_local_mutation_request(request)
    if validation_error is not None:
        return validation_error

    try:
        data = await request.json()
        text = data.get('text', '').strip()
        target_lang = data.get('target_lang', 'zh')
        source_lang = data.get('source_lang')
        
        if not text:
            return {
                "success": False,
                "error": "文本不能为空",
                "translated_text": "",
                "source_lang": "unknown",
                "target_lang": target_lang
            }
        
        # 归一化目标语言代码（复用公共函数）
        target_lang_normalized = normalize_language_code(target_lang, format='short')
        
        # 检测源语言（如果未提供）
        if source_lang is None:
            detected_source_lang = detect_language(text)
        else:
            # 归一化源语言代码（复用公共函数）
            detected_source_lang = normalize_language_code(source_lang, format='short')
        
        # 如果源语言和目标语言相同，不需要翻译
        if detected_source_lang == target_lang_normalized or detected_source_lang == 'unknown':
            return {
                "success": True,
                "translated_text": text,
                "source_lang": detected_source_lang,
                "target_lang": target_lang_normalized
            }
        
        # 检查是否跳过 Google 翻译（前端传递的会话级失败标记）
        skip_google = data.get('skip_google', False)
        
        # 调用翻译服务
        try:
            translated, google_failed = await translate_text(
                text, 
                target_lang_normalized, 
                detected_source_lang,
                skip_google=skip_google
            )
            return {
                "success": True,
                "translated_text": translated,
                "source_lang": detected_source_lang,
                "target_lang": target_lang_normalized,
                "google_failed": google_failed  # 告诉前端 Google 翻译是否失败
            }
        except Exception as e:
            logger.error(f"翻译失败: {e}")
            # 翻译失败时返回原文
            return {
                "success": False,
                "error": str(e),
                "translated_text": text,
                "source_lang": detected_source_lang,
                "target_lang": target_lang_normalized
            }
            
    except Exception as e:
        logger.error(f"翻译API处理失败: {e}")
        return {
            "success": False,
            "error": str(e),
            "translated_text": "",
            "source_lang": "unknown",
            "target_lang": "zh"
        }
