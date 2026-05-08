"""
LLM 调用客户端

功能：
- 真实 HTTP 调用公司自建 LLM API
- 超时控制（asyncio.wait_for）
- 重试机制
- 构建 Prompt：弹幕总结 + 专属知识库参考
- 失败返回 None（上游编排器处理降级）
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

import aiohttp

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


class LLMClient:
    """LLM API 调用客户端"""

    def __init__(
        self,
        api_url: str = "https://api.deepseek.com/v1/chat/completions",
        api_key: str = "",
        model: str = "deepseek-chat",
        timeout_sec: float = 10.0,
        retry_times: int = 2,
        max_tokens: int = 512,
        temperature: float = 0.7,
    ):
        self.api_url = api_url
        self.api_key = api_key
        self.model = model
        self.timeout_sec = timeout_sec
        self.retry_times = retry_times
        self.max_tokens = max_tokens
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
            cloud = config["cloud"]  # 全量 background_llm dict
        else:
            cloud = config            # 已经是 cloud 子对象
        api_url = cloud.get("url", "https://api.deepseek.com").rstrip("/")
        # 构造 OpenAI 兼容的 chat/completions 路径
        if not api_url.endswith("/chat/completions"):
            if api_url.endswith("/v1"):
                api_url = api_url + "/chat/completions"
            else:
                api_url = api_url + "/v1/chat/completions"
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
        """
        根据弹幕列表生成引导词

        Args:
            danmaku_texts: 弹幕文本列表（普通字符串列表，已提取完成）
            knowledge_context: 专属知识库上下文（已完成占位符替换）
            system_prompt_override: 自定义 System Prompt（含 {knowledge_context} 占位符则自动填充）；
                                    为 None 时使用默认 SYSTEM_PROMPT

        Returns:
            引导词字符串，失败返回 None
        """
        # 构建弹幕部分
        danmaku_block = "\n".join(
            f"- {t}" for t in danmaku_texts
        )

        user_prompt = f"以下是在直播间中观众发送的弹幕：\n\n{danmaku_block}\n\n请根据以上弹幕生成 AI 发言引导词。"

        ctx_str = knowledge_context or "(暂无知识库信息)"
        if system_prompt_override:
            # 自定义模板：支持 {knowledge_context} 占位符
            sys_content = system_prompt_override.replace("{knowledge_context}", ctx_str)
        else:
            sys_content = SYSTEM_PROMPT.format(knowledge_context=ctx_str)

        messages = [
            {"role": "system", "content": sys_content},
            {"role": "user", "content": user_prompt},
        ]

        return await self._call_llm(messages)

    async def _call_llm(self, messages: list[dict]) -> Optional[str]:
        """执行 LLM API 调用，含重试和超时"""
        self.total_calls += 1

        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "stream": False,
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        last_error: Optional[str] = None
        async with aiohttp.ClientSession() as session:
            for attempt in range(self.retry_times + 1):
                try:
                    timeout = aiohttp.ClientTimeout(total=self.timeout_sec)
                    async with session.post(
                        self.api_url,
                        json=payload,
                        headers=headers,
                        timeout=timeout,
                    ) as resp:
                        if resp.status != 200:
                            body = await resp.text()
                            last_error = f"HTTP {resp.status}: {body[:200]}"
                            logger.warning(
                                "[LLMClient] 请求失败 (attempt %d/%d): %s",
                                attempt + 1, self.retry_times + 1, last_error,
                            )
                            await asyncio.sleep(min(0.5 * (2 ** attempt), 5.0))
                            continue

                        data = await resp.json()
                        choices = data.get("choices") or []
                        text = (choices[0] if choices else {}).get("message", {}).get("content", "")
                        if not text:
                            last_error = "API 返回空内容"
                            await asyncio.sleep(min(0.5 * (2 ** attempt), 5.0))
                            continue

                        self.success_calls += 1
                        return text.strip()

                except asyncio.TimeoutError:
                    last_error = f"超时 (>{self.timeout_sec}s)"
                    logger.warning(
                        "[LLMClient] 超时 (attempt %d/%d)",
                        attempt + 1, self.retry_times + 1,
                    )
                    await asyncio.sleep(min(0.5 * (2 ** attempt), 5.0))
                    continue

                except aiohttp.ClientError as e:
                    last_error = str(e)[:200]
                    logger.warning(
                        "[LLMClient] 网络错误 (attempt %d/%d): %s",
                        attempt + 1, self.retry_times + 1, last_error,
                    )
                    await asyncio.sleep(min(0.5 * (2 ** attempt), 5.0))
                    continue

                except Exception as e:
                    last_error = str(e)[:200]
                    logger.error(
                        "[LLMClient] 未知错误 (attempt %d/%d): %s",
                        attempt + 1, self.retry_times + 1, last_error,
                    )
                    await asyncio.sleep(min(0.5 * (2 ** attempt), 5.0))
                    continue

        # 所有重试都失败
        self.failed_calls += 1
        logger.error("[LLMClient] 所有重试都失败，最后错误: %s", last_error)
        return None

    def get_stats(self) -> dict:
        """获取调用统计"""
        return {
            "total_calls": self.total_calls,
            "success_calls": self.success_calls,
            "failed_calls": self.failed_calls,
            "api_url": self.api_url,
            "model": self.model,
            "timeout_sec": self.timeout_sec,
            "retry_times": self.retry_times,
        }
