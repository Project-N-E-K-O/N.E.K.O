import asyncio
import logging
from typing import Any, Dict, Optional

from utils.config_manager import get_config_manager

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_S = 300

# Blue breathing glow overlay.  Blocks user mouse; CDP automation bypasses it.
_OVERLAY_JS = r"""
(function(){
  if(document.getElementById('__bu_ov')) return;
  var s=document.createElement('style');
  s.id='__bu_ov_style';
  s.textContent=`
    @keyframes __bu_breathe{0%,100%{box-shadow:inset 0 0 30px 6px rgba(60,140,255,.45)}50%{box-shadow:inset 0 0 60px 14px rgba(60,140,255,.8)}}
    #__bu_ov{position:fixed;top:0;left:0;width:100vw;height:100vh;z-index:2147483647;
      pointer-events:all;cursor:not-allowed;
      animation:__bu_breathe 2.5s ease-in-out infinite;
      border:3px solid rgba(60,140,255,.6);box-sizing:border-box}
    #__bu_ov_bar{position:fixed;top:0;left:0;width:100%;
      color:rgba(60,140,255,.85);font:bold 16px/36px sans-serif;letter-spacing:2px;
      text-align:center;z-index:2147483647;pointer-events:none}
  `;
  document.head.appendChild(s);
  var ov=document.createElement('div');ov.id='__bu_ov';
  var bar=document.createElement('div');bar.id='__bu_ov_bar';
  bar.textContent='\u26a0 N.E.K.O. WORKING IN PROGRESS \u26a0';
  ov.appendChild(bar);
  document.documentElement.appendChild(ov);
})();
"""

_REMOVE_OVERLAY_JS = r"""
(function(){
  var ov=document.getElementById('__bu_ov');if(ov)ov.remove();
  var st=document.getElementById('__bu_ov_style');if(st)st.remove();
})();
"""


class BrowserUseAdapter:
    """Adapter for browser-use execution channel.

    Features:
      - Visible browser window with blue breathing-glow overlay during tasks.
      - Overlay is maintained by a parallel asyncio task that injects it
        every 2 seconds via CDP Runtime.evaluate, so it persists across
        all page navigations.
      - Session-aware Agent reuse for multi-turn task execution.
      - Automatic session cleanup on error or explicit close.
    """

    def __init__(self, headless: bool = False) -> None:
        self._config_manager = get_config_manager()
        self.last_error: Optional[str] = None
        self._headless = headless
        self._browser_session: Any = None
        # session_id -> Agent instance (preserves memory/plan/history)
        self._agents: Dict[str, Any] = {}
        self._overlay_task: Optional[asyncio.Task] = None
        try:
            from browser_use import Agent  # noqa: F401
            from browser_use.browser.session import BrowserSession  # noqa: F401
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

    async def _get_browser_session(self) -> Any:
        """Lazy-create and cache a BrowserSession."""
        if self._browser_session is None:
            from browser_use.browser.session import BrowserSession
            self._browser_session = BrowserSession(headless=self._headless)
        return self._browser_session

    def _build_llm(self) -> Any:
        """Build a browser-use compatible ChatOpenAI instance."""
        from browser_use.llm import ChatOpenAI as BUChatOpenAI
        api_cfg = self._config_manager.get_model_api_config("agent")
        return BUChatOpenAI(
            model=api_cfg.get("model"),
            api_key=api_cfg.get("api_key"),
            base_url=api_cfg.get("base_url"),
        )

    async def _cdp_eval_on_page(self, session: Any, js: str) -> None:
        """Evaluate JS on the currently focused page via CDP Runtime.evaluate.

        Uses the page-targeted CDPSession (with session_id) so the command
        reaches the actual page context, not the browser root.
        """
        try:
            cdp_session = await session.get_or_create_cdp_session(focus=False)
            await cdp_session.cdp_client.send.Runtime.evaluate(
                params={"expression": js},
                session_id=cdp_session.session_id,
            )
        except Exception as e:
            logger.debug("[BrowserUse] _cdp_eval_on_page failed: %s", e)

    async def _overlay_loop(self, session: Any) -> None:
        """Continuously re-inject overlay every 1.5 seconds until cancelled.

        Also registers a Page.addScriptToEvaluateOnNewDocument for each new
        target encountered, so navigations within the same tab auto-inject.
        """
        registered_targets: set = set()
        while True:
            try:
                cdp_session = await session.get_or_create_cdp_session(focus=False)
                sid = cdp_session.session_id
                # Register init script for this target if not done yet
                if sid and sid not in registered_targets:
                    try:
                        await cdp_session.cdp_client.send.Page.addScriptToEvaluateOnNewDocument(
                            params={"source": _OVERLAY_JS, "runImmediately": True},
                            session_id=sid,
                        )
                        registered_targets.add(sid)
                        logger.debug("[BrowserUse] Overlay init script registered for target session %s", sid[:12])
                    except Exception:
                        pass
                # Evaluate on current page immediately
                await cdp_session.cdp_client.send.Runtime.evaluate(
                    params={"expression": _OVERLAY_JS},
                    session_id=sid,
                )
            except Exception as e:
                logger.debug("[BrowserUse] Overlay loop tick failed: %s", e)
            await asyncio.sleep(1.5)

    def _start_overlay(self, session: Any) -> None:
        """Start the overlay injection loop as a background task."""
        self._stop_overlay()
        self._overlay_task = asyncio.create_task(self._overlay_loop(session))

    def _stop_overlay(self) -> None:
        """Cancel the overlay injection loop."""
        if self._overlay_task is not None:
            self._overlay_task.cancel()
            self._overlay_task = None

    async def _remove_overlay(self, session: Any) -> None:
        """Stop the overlay loop and clear overlay from the current page."""
        self._stop_overlay()
        await self._cdp_eval_on_page(session, _REMOVE_OVERLAY_JS)

    async def run_instruction(
        self,
        instruction: str,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Execute a browser task.

        Args:
            instruction: What to do.
            timeout_s: Max seconds before timeout (default 300s).
            session_id: Reuse Agent if same session_id (multi-turn).
        """
        status = self.is_available()
        if not status.get("ready"):
            return {"success": False, "error": "; ".join(status.get("reasons", []))}

        browser_session = None
        try:
            from browser_use import Agent

            browser_session = await self._get_browser_session()
            llm = self._build_llm()

            # Reuse or create Agent based on session_id
            agent: Any = None
            if session_id and session_id in self._agents:
                agent = self._agents[session_id]
                agent.task = instruction
            else:
                agent = Agent(
                    task=instruction,
                    llm=llm,
                    browser_session=browser_session,
                    # Overlay on first page before LLM takes over
                    initial_actions=[
                        {"evaluate": {"code": _OVERLAY_JS}},
                    ],
                )
                if session_id:
                    self._agents[session_id] = agent

            # Start parallel overlay loop (kicks in once browser is running)
            self._start_overlay(browser_session)

            logger.info("[BrowserUse] Starting task: %s", instruction[:80])
            history = await asyncio.wait_for(agent.run(), timeout=timeout_s)
            logger.info("[BrowserUse] agent.run() returned")

            # Remove overlay after task completes
            await self._remove_overlay(browser_session)

            # Use browser-use's own success detection
            done = history.is_done() if hasattr(history, "is_done") else True
            successful = history.is_successful() if hasattr(history, "is_successful") else done
            final = ""
            try:
                final = history.final_result() or ""
            except Exception:
                pass
            if not final:
                try:
                    final = str(history.extracted_content()) or ""
                except Exception:
                    final = str(history)

            logger.info("[BrowserUse] Done=%s, success=%s, steps=%s",
                        done, successful,
                        getattr(history, "number_of_steps", lambda: "?")())
            return {
                "success": bool(successful),
                "result": str(final)[:1200],
                "done": bool(done),
                "steps": getattr(history, "number_of_steps", lambda: None)(),
            }
        except asyncio.TimeoutError:
            logger.warning("[BrowserUse] Task timed out after %ss: %s", timeout_s, instruction[:80])
            if browser_session:
                await self._remove_overlay(browser_session)
            if session_id and session_id in self._agents:
                del self._agents[session_id]
            return {"success": False, "error": f"Task timed out after {timeout_s}s"}
        except Exception as e:
            logger.warning("[BrowserUse] Task failed: %s", e)
            if browser_session:
                await self._remove_overlay(browser_session)
            if session_id and session_id in self._agents:
                del self._agents[session_id]
            await self._close_browser()
            return {"success": False, "error": str(e)}

    async def close_session(self, session_id: str) -> None:
        """Close and discard a specific session's Agent."""
        self._agents.pop(session_id, None)

    async def _close_browser(self) -> None:
        self._stop_overlay()
        if self._browser_session is not None:
            try:
                await self._browser_session.stop()
            except Exception:
                pass
            self._browser_session = None
        self._agents.clear()

    async def close(self) -> None:
        """Graceful shutdown."""
        await self._close_browser()
