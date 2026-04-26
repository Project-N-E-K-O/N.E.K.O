"""
LLM 调用客户端

走 utils.llm_client.create_chat_llm（基于 OpenAI SDK），自动按 provider
（OpenAI / Anthropic / Qwen / DeepSeek 等）选用正确的 max_tokens vs
max_completion_tokens 字段名 + cache headers + extra_body。

功能：
- 真实 API 调用（OpenAI 兼容 + Anthropic via base_url 探测）
- 超时 / 重试
- 构建 Prompt：弹幕总结 + 专属知识库参考
- 失败返回 None（上游编排器处理降级）
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是一个虚拟主播直播间弹幕分析助手。
你需要根据观众发送的弹幕，生成一条引导 AI 发言的引导词。

要求：
1. 总结弹幕讨论的核心主题和观众情绪，不要逐条复述弹幕原文
2. 结合已知的角色设定、世界观和专属知识库生成引导方向
3. 引导词应能启发 AI 做出有内容的回应，而非简单复读弹幕
4. 如果弹幕包含问题，引导 AI 先回答问题再延展话题
5. 保持引导词简洁、有信息量

知识库参考信息：
{knowledge_context}

请为以下弹幕列表生成引导词：
"""


def _normalize_base_url(raw: str) -> str:
    """Strip OpenAI-compat path suffix and ensure ``/v1`` is present so the
    OpenAI SDK appends ``/chat/completions`` correctly against typical
    OpenAI-compatible providers.

    Examples::

        https://api.deepseek.com                         → https://api.deepseek.com/v1
        https://api.deepseek.com/v1                      → https://api.deepseek.com/v1
        https://api.deepseek.com/v1/chat/completions     → https://api.deepseek.com/v1
        https://api.deepseek.com/chat/completions        → https://api.deepseek.com/v1
    """
    from urllib.parse import urlparse

    url = (raw or "").rstrip("/")
    if url.endswith("/chat/completions"):
        url = url[: -len("/chat/completions")].rstrip("/")
    # 用户给的 base_url 可能只是 host（如 "https://api.deepseek.com"，配置文档
    # 里就是这种）—— 补 /v1，否则 OpenAI SDK 会去打 /chat/completions（少了
    # /v1）导致 404。已经带 path 段（/v1 / /v2 / 自定义 prefix）就别动。
    parsed = urlparse(url)
    if parsed.scheme and parsed.netloc and (not parsed.path or parsed.path == "/"):
        url = url.rstrip("/") + "/v1"
    return url


class LLMClient:
    """LLM API 调用客户端（thin wrapper over create_chat_llm)."""

    def __init__(
        self,
        api_url: str = "https://api.deepseek.com/v1",
        api_key: str = "",
        model: str = "deepseek-chat",
        timeout_sec: float = 10.0,
        retry_times: int = 2,
        max_completion_tokens: int = 512,
        temperature: float = 0.7,
    ):
        self.api_url = _normalize_base_url(api_url)
        self.api_key = api_key
        self.model = model
        self.timeout_sec = timeout_sec
        self.retry_times = retry_times
        self.max_completion_tokens = max_completion_tokens
        self.temperature = temperature

        # 统计
        self.total_calls = 0
        self.success_calls = 0
        self.failed_calls = 0

    @classmethod
    def from_config(cls, config: dict) -> "LLMClient":
        """从配置字典创建客户端

        config 格式（兼容两种来源）:
        1. 直接从 config_enhanced.json 的 cloud 字段传入:
           {"url": "https://api.deepseek.com", "api_key": "sk-xxx", ...}
        2. 从 _init_background_llm 传入 background_llm 全量:
           {"cloud": {"url": "...", "api_key": "..."}, ...}
        """
        if not config:
            cloud = {}
        elif "cloud" in config:
            cloud = config["cloud"]
        else:
            cloud = config
        api_url = _normalize_base_url(cloud.get("url", "https://api.deepseek.com/v1"))
        api_key = cloud.get("api_key", "")
        model = cloud.get("model", "deepseek-chat")
        timeout_sec = float(cloud.get("timeout_sec", 10))
        retry_times = int(cloud.get("retry_times", 2))
        return cls(
            api_url=api_url,
            api_key=api_key,
            model=model,
            timeout_sec=timeout_sec,
            retry_times=retry_times,
        )

    async def generate_guidance(
        self,
        danmaku_texts: list[str],
        knowledge_context: str = "",
        system_prompt_override: Optional[str] = None,
    ) -> Optional[str]:
        """根据弹幕列表生成引导词。

        Args:
            danmaku_texts: 弹幕文本列表
            knowledge_context: 专属知识库上下文（已完成占位符替换）
            system_prompt_override: 自定义 System Prompt（含 {knowledge_context}
                占位符则自动填充）；None 时使用默认 SYSTEM_PROMPT

        Returns:
            引导词字符串，失败返回 None
        """
        danmaku_block = "\n".join(f"- {t}" for t in danmaku_texts)
        user_prompt = (
            f"以下是在直播间中观众发送的弹幕：\n\n{danmaku_block}\n\n"
            f"请根据以上弹幕生成 AI 发言引导词。"
        )

        ctx_str = knowledge_context or "(暂无知识库信息)"
        if system_prompt_override:
            sys_content = system_prompt_override.replace("{knowledge_context}", ctx_str)
        else:
            sys_content = SYSTEM_PROMPT.format(knowledge_context=ctx_str)

        messages = [
            {"role": "system", "content": sys_content},
            {"role": "user", "content": user_prompt},
        ]
        return await self._call_llm(messages)

    async def _call_llm(self, messages: list[dict]) -> Optional[str]:
        """通过 create_chat_llm 调用，含重试与超时。"""
        from utils.llm_client import create_chat_llm

        self.total_calls += 1
        last_error: Optional[str] = None

        for attempt in range(self.retry_times + 1):
            try:
                llm = create_chat_llm(
                    model=self.model,
                    base_url=self.api_url,
                    api_key=self.api_key,
                    temperature=self.temperature,
                    max_completion_tokens=self.max_completion_tokens,
                    timeout=self.timeout_sec,
                    max_retries=0,
                )
                try:
                    response = await asyncio.wait_for(
                        llm.ainvoke(messages),
                        timeout=self.timeout_sec,
                    )
                finally:
                    await llm.aclose()

                text = (response.content or "").strip()
                if not text:
                    last_error = "API 返回空内容"
                    await asyncio.sleep(min(0.5 * (2 ** attempt), 5.0))
                    continue

                self.success_calls += 1
                return text

            except asyncio.TimeoutError:
                last_error = f"超时 (>{self.timeout_sec}s)"
                logger.warning(
                    "[LLMClient] 超时 (attempt %d/%d)",
                    attempt + 1, self.retry_times + 1,
                )
                await asyncio.sleep(min(0.5 * (2 ** attempt), 5.0))
                continue

            except Exception as e:
                last_error = str(e)[:200]
                logger.warning(
                    "[LLMClient] 调用失败 (attempt %d/%d): %s",
                    attempt + 1, self.retry_times + 1, last_error,
                )
                await asyncio.sleep(min(0.5 * (2 ** attempt), 5.0))
                continue

        self.failed_calls += 1
        logger.error("[LLMClient] 所有重试都失败，最后错误: %s", last_error)
        return None

    def get_stats(self) -> dict:
        """获取调用统计 — 供 plugin 主体的 status / config 接口使用
        （bilibili_danmaku/__init__.py 多处调用）。"""
        return {
            "total_calls": self.total_calls,
            "success_calls": self.success_calls,
            "failed_calls": self.failed_calls,
            "api_url": self.api_url,
            "model": self.model,
            "timeout_sec": self.timeout_sec,
            "retry_times": self.retry_times,
        }
