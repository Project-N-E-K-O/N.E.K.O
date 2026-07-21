"""Fix _summarize_buffered method in reply_buffer_service.py"""
with open('reply_buffer_service.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find _summarize_buffered method
start = None
end = None
for i, line in enumerate(lines):
    if 'async def _summarize_buffered(self, texts: list[str], is_group: bool) -> str:' in line:
        start = i
    if start is not None and i > start and line.strip() == '    def get_state(self) -> dict:':
        end = i
        break

if start and end:
    new_method = [
        '    async def _summarize_buffered(self, texts: list[str], is_group: bool) -> str:\n',
        '        """缓冲结束后，让 LLM 看所有缓冲消息生成一条总结回复。"""\n',
        '        try:\n',
        '            combined = "\\n".join(f"[{i+1}] {t[:200]}" for i, t in enumerate(texts))\n',
        '            prompt = (\n',
        '                f"对方连续发了 {len(texts)} 条消息，内容如下：\\n\\n"\n',
        '                f"{combined}\\n\\n"\n',
        '                "请用一两句话自然回复，总结或回应对方的要点。不要逐条回复，像真人在听对方讲完一堆话之后的自然反应。"\n',
        '            )\n',
        '\n',
        '            # 优先用已有会话（兼容 Lanlan API）\n',
        '            sessions = getattr(self.plugin, "_user_sessions", {}) or {}\n',
        '            for s in (sessions or {}).values():\n',
        '                if isinstance(s, dict) and s.get("session") and hasattr(s["session"], "stream_text"):\n',
        '                    resp_text = ""\n',
        '                    async for chunk in s["session"].stream_text(prompt):\n',
        '                        if hasattr(chunk, "text"): resp_text += chunk.text\n',
        '                        elif isinstance(chunk, str): resp_text += chunk\n',
        '                    result = resp_text.strip()\n',
        '                    if result:\n',
        '                        return result\n',
        '                    break\n',
        '\n',
        '            # 回退：直接创建 LLM\n',
        '            from utils.config_manager import get_config_manager\n',
        '            from utils.llm_client import create_chat_llm_async\n',
        '            model_config = get_config_manager().get_model_api_config("conversation")\n',
        '            if not model_config.get("base_url") or not model_config.get("model"):\n',
        '                return ""\n',
        '            llm = await create_chat_llm_async(\n',
        '                model=str(model_config["model"]), base_url=str(model_config["base_url"]),\n',
        '                api_key=str(model_config.get("api_key", "")),\n',
        '                max_completion_tokens=300, timeout=10.0,\n',
        '                provider_type=model_config.get("provider_type"),\n',
        '            )\n',
        '            resp = await asyncio.wait_for(llm.ainvoke([{"role": "user", "content": prompt}]), timeout=10.0)\n',
        '            result = str(getattr(resp, "content", "") or "").strip()\n',
        '            return result if result else ""\n',
        '        except Exception as e:\n',
        '            self.plugin._emit_log("WARN", f"[Buffer] 总结LLM调用失败: {e}")\n',
        '            return ""\n',
        '\n',
    ]
    lines[start:end] = new_method

    with open('reply_buffer_service.py', 'w', encoding='utf-8') as f:
        f.writelines(lines)
    print(f'Replaced lines {start+1}-{end}')
else:
    print(f'NOT FOUND: start={start}, end={end}')
