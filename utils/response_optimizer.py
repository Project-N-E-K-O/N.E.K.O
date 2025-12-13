
# -*- coding: utf-8 -*-
"""
Response optimizer for NEKO

功能：
- 对模型生成的回复应用词数/字符数限制
- 去重重复句子与连续重复段落（简单启发式）
- 在输出外层加上统一的格式包裹符（enclosure）以便前端/日志统一展示

配置优先级：环境变量 > 默认值
环境变量：
- `NEKO_RESPONSE_MAX_WORDS` （整数，若为0则不限制）
- `NEKO_RESPONSE_ENC_START` / `NEKO_RESPONSE_ENC_END`（包裹起/止符）

注意：中文文本通常没有空格分词，默认的“词数限制”在中文环境下会退化为字符数限制。
"""
from __future__ import annotations
import os
import re
from typing import Tuple

# 默认配置
DEFAULT_MAX_WORDS = int(os.getenv('NEKO_RESPONSE_MAX_WORDS', '200'))
DEFAULT_ENC_START = os.getenv('NEKO_RESPONSE_ENC_START', '【')
DEFAULT_ENC_END = os.getenv('NEKO_RESPONSE_ENC_END', '】')


def _split_sentences(text: str) -> list:
    # 使用简单的中英文标点分割句子，保留标点
    if not text:
        return []
    parts = re.split(r'(?<=[。！？!?。!？]|\.|\?|!)\s*', text)
    parts = [p.strip() for p in parts if p and p.strip()]
    return parts


def _count_words_or_chars(text: str) -> int:
    # 如果文本中包含空格，则以空格拆分词计数；否则退化为字符数（适用于中文）
    if not text:
        return 0
    if re.search(r'\s', text):
        return len([w for w in text.split() if w])
    return len(text)


def optimize_response(text: str,
                      max_words: int | None = None,
                      enclosure: Tuple[str, str] | None = None) -> str:
    """
    优化回复文本：去重、限制长度、应用包裹符并保证结构清晰。

    参数：
    - text: 原始回复文本
    - max_words: 最大“词”数（中文时为字符数）。为 None 时使用模块默认；为 0 或负数表示不限制。
    - enclosure: (start, end) 包裹符，为 None 时使用模块默认

    返回：处理后的字符串（已被包裹）。
    """
    if text is None:
        return ''
    text = str(text).strip()
    if enclosure is None:
        enclosure = (DEFAULT_ENC_START, DEFAULT_ENC_END)

    if max_words is None:
        max_words = DEFAULT_MAX_WORDS

    # 1) 规范空白
    text = re.sub(r'\s+', ' ', text)

    # 2) 切句并去除完全重复的句子
    sentences = _split_sentences(text)
    seen = set()
    deduped = []
    for s in sentences:
        key = s.strip()
        if not key:
            continue
        if key in seen:
            continue
        seen.add(key)
        deduped.append(key)

    # 3) 合并并按照 max_words 限制（中文退化为字符）
    if max_words and int(max_words) > 0:
        out_parts = []
        total = 0
        maxw = int(max_words)
        for s in deduped:
            s_len = _count_words_or_chars(s)
            if total + s_len <= maxw:
                out_parts.append(s)
                total += s_len
            else:
                # 还可以塞一部分
                remain = maxw - total
                if remain > 0:
                    truncated = s[:remain].rstrip()
                    if not truncated.endswith('…'):
                        truncated = truncated + '…'
                    out_parts.append(truncated)
                break
        final = ''.join(out_parts).strip()
    else:
        final = ''.join(deduped).strip()

    # 4) 小幅清理：去除重复的连续标点
    final = re.sub(r'[。]{2,}', '。', final)
    final = final.strip()

    # 5) 应用 enclosure
    start, end = enclosure
    # 如果已经被同样的 enclosure 包住则直接返回
    if final.startswith(start) and final.endswith(end):
        return final

    return f"{start}{final}{end}"


if __name__ == '__main__':
    # 简单示例
    demo = "你好！这是第一句。你好！这是第一句。这里有很多无关的重复。这里有很多无关的重复。最后一句。"
    print(optimize_response(demo, max_words=30))
