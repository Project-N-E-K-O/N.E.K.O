"""
LLM 调用客户端

功能：
- 使用 OpenAI SDK（ChatOpenAI，来自主项目 utils/llm_client）调用 LLM API
- 超时控制、重试机制（SDK 内置）
- 构建 Prompt：弹幕总结 + 专属知识库参考
- 失败返回 None（上游编排器处理降级）
"""

from __future__ import annotations

import logging
from typing import Optional

from openai import AuthenticationError, RateLimitError, APITimeoutError, APIConnectionError
from utils.llm_client import ChatOpenAI

import time as _time

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
        api_url: str = "",
        api_key: str = "",
        model: str = "deepseek-chat",
        timeout_sec: float = 10.0,
        retry_times: int = 2,
        max_tokens: int = 512,
        temperature: float | None = None,
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

    async def test_connection(self) -> dict:
        """测试 API 连通性。

        发送最小 chat completion 请求，不重试，5 秒超时。
        不更新 total_calls / success_calls / failed_calls 统计。

        Returns:
            {"success": True, "elapsed": float} 或
            {"success": False, "error": str, "error_code": str}
        """
        try:
            start = _time.monotonic()
            client = ChatOpenAI(
                model=self.model,
                base_url=self.api_url,
                api_key=self.api_key,
                max_completion_tokens=5,
                max_retries=0,
                timeout=5.0,
            )
            await client.ainvoke([{"role": "user", "content": "hi"}])
            elapsed = _time.monotonic() - start
            return {"success": True, "elapsed": round(elapsed, 2)}
        except AuthenticationError:
            return {"success": False, "error": "API Key 无效或已过期", "error_code": "auth_failed"}
        except RateLimitError:
            # Rate limit: key 有效但限流，当作成功
            return {"success": True, "rate_limited": True, "elapsed": 0}
        except APITimeoutError:
            return {"success": False, "error": "连接超时（5秒）", "error_code": "timeout"}
        except APIConnectionError:
            return {"success": False, "error": "无法连接到目标服务器", "error_code": "connection_refused"}
        except Exception as e:
            err_str = str(e).lower()
            if "getaddrinfo" in err_str or "name or service not known" in err_str:
                return {"success": False, "error": "域名解析失败", "error_code": "dns_error"}
            return {"success": False, "error": str(e)[:200], "error_code": "unknown"}

    @classmethod
    def from_config(cls, config: dict) -> "LLMClient":
        """从配置字典创建客户端

        config 格式（兼容两种来源）:
        1. 直接从 config.json 的 cloud 字段传入:
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
        api_url = cloud.get("url", "").rstrip("/")
        # SDK 自动处理 /chat/completions 路径，不需要手动拼接
        if api_url.endswith("/chat/completions"):
            api_url = api_url[: -len("/chat/completions")]
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

    async def call(self, messages: list[dict]) -> Optional[str]:
        """执行通用 LLM API 调用（公开接口）"""
        return await self._call_llm(messages)

    async def _call_llm(self, messages: list[dict]) -> Optional[str]:
        """执行 LLM API 调用，使用 OpenAI SDK（重试由 SDK 内置处理）"""
        self.total_calls += 1

        client = ChatOpenAI(
            model=self.model,
            base_url=self.api_url,
            api_key=self.api_key,
            max_completion_tokens=self.max_tokens,
            temperature=self.temperature,
            max_retries=self.retry_times,
            timeout=self.timeout_sec,
        )
        try:
            resp = await client.ainvoke(messages)
            text = resp.content
            if not text:
                self.failed_calls += 1
                logger.warning("[LLMClient] API 返回空内容")
                return None

            self.success_calls += 1
            return text.strip()

        except Exception as e:
            self.failed_calls += 1
            logger.error("[LLMClient] 调用失败: %s", e)
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
