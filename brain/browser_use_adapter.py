from typing import Any, Dict, Optional

from utils.config_manager import get_config_manager


class BrowserUseAdapter:
    """Optional adapter for browser-use execution channel."""

    def __init__(self) -> None:
        self._config_manager = get_config_manager()
        self.last_error: Optional[str] = None
        try:
            from browser_use import Agent, Browser  # noqa: F401
            self._ready_import = True
        except Exception as e:
            self._ready_import = False
            self.last_error = str(e)

    def is_available(self) -> Dict[str, Any]:
        ready = self._ready_import
        reasons = []
        ok, gate_reasons = self._config_manager.is_agent_api_ready()
        if not ok:
            reasons.extend(gate_reasons)
            ready = False
        if not self._ready_import:
            reasons.append(f"browser-use not installed: {self.last_error}")
        return {"enabled": True, "ready": ready, "reasons": reasons, "provider": "browser-use"}

    async def run_instruction(self, instruction: str) -> Dict[str, Any]:
        status = self.is_available()
        if not status.get("ready"):
            return {"success": False, "error": "; ".join(status.get("reasons", []))}
        try:
            from browser_use import Agent, Browser
            from langchain_openai import ChatOpenAI

            api_cfg = self._config_manager.get_model_api_config("agent")
            browser = Browser()
            llm = ChatOpenAI(
                model=api_cfg.get("model"),
                api_key=api_cfg.get("api_key"),
                base_url=api_cfg.get("base_url"),
            )
            agent = Agent(task=instruction, llm=llm, browser=browser)
            history = await agent.run()
            return {"success": True, "result": str(history)[:1200]}
        except Exception as e:
            return {"success": False, "error": str(e)}
