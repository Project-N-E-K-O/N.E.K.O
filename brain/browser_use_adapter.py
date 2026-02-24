import asyncio
import logging
import shutil
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils.config_manager import get_config_manager

logger = logging.getLogger(__name__)

_LLM_MODES: List[str] = ["schema", "text"]
_API_MODE_CACHE_LOCK = threading.Lock()
_API_MODE_CACHE: Dict[str, Dict[str, Any]] = {}


def _configure_browser_logging() -> None:
    """Reduce browser-use log noise.

    Disk logs still get WARNING+ entries; real-time progress uses print.
    """
    for name in ("service", "browser_use.browser", "browser_use.llm"):
        logging.getLogger(name).setLevel(logging.ERROR)
    for name in ("browser_use", "browser_use.agent"):
        logging.getLogger(name).setLevel(logging.WARNING)


def _resolve_fallback_dir() -> Optional[Path]:
    """Locate the bundled ``data/browser_use_prompts/`` directory."""
    if hasattr(sys, "_MEIPASS"):
        root = Path(sys._MEIPASS)
    elif getattr(sys, "frozen", False):
        root = Path(__file__).resolve().parent.parent
    else:
        root = Path(__file__).resolve().parent.parent
    d = root / "data" / "browser_use_prompts"
    return d if d.is_dir() else None


def _ensure_browser_use_prompts() -> None:
    """Ensure browser_use system prompt .md templates are loadable.

    ``browser_use`` reads its templates via ``importlib.resources.files()``.
    In Nuitka / PyInstaller builds the .md data files may be absent from the
    compiled package directory.

    Strategy (in order):
      1. If the sentinel file already exists in the package dir → done.
      2. Try to copy from our bundled fallback dir (works if dir is writable).
      3. If copy fails (e.g. read-only Program Files), monkey-patch
         ``SystemPrompt._load_prompt_template`` to read from the fallback dir
         directly — no filesystem write required.
    """
    try:
        import browser_use.agent.system_prompts as _sp_mod
    except ImportError:
        return

    prompts_dir = Path(_sp_mod.__file__).parent
    sentinel = prompts_dir / "system_prompt.md"
    if sentinel.exists():
        return

    fallback_dir = _resolve_fallback_dir()
    if fallback_dir is None:
        logger.warning("[BrowserUse] Prompt templates missing and no fallback found")
        return

    # --- Attempt 1: copy files into the package directory -------------------
    try:
        prompts_dir.mkdir(parents=True, exist_ok=True)
        copied = 0
        for md in fallback_dir.glob("*.md"):
            dest = prompts_dir / md.name
            if not dest.exists():
                shutil.copy2(md, dest)
                copied += 1
        if copied:
            logger.info(
                "[BrowserUse] Copied %d prompt template(s) to %s",
                copied, prompts_dir,
            )
        if sentinel.exists():
            return
    except OSError as exc:
        logger.info(
            "[BrowserUse] Cannot write to package dir (%s), will patch loader",
            exc,
        )

    # --- Attempt 2: monkey-patch _load_prompt_template ----------------------
    _patch_prompt_loader(fallback_dir)


def _patch_prompt_loader(fallback_dir: Path) -> None:
    """Replace ``SystemPrompt._load_prompt_template`` so it reads from
    *fallback_dir* instead of going through ``importlib.resources``."""
    try:
        from browser_use.agent.prompts import SystemPrompt
    except ImportError:
        return

    def _patched_load(self: Any) -> None:
        if self.is_browser_use_model:
            if self.flash_mode:
                fn = "system_prompt_browser_use_flash.md"
            elif self.use_thinking:
                fn = "system_prompt_browser_use.md"
            else:
                fn = "system_prompt_browser_use_no_thinking.md"
        elif getattr(self, "is_anthropic_4_5", False) and self.flash_mode:
            fn = "system_prompt_anthropic_flash.md"
        elif self.flash_mode and self.is_anthropic:
            fn = "system_prompt_flash_anthropic.md"
        elif self.flash_mode:
            fn = "system_prompt_flash.md"
        elif self.use_thinking:
            fn = "system_prompt.md"
        else:
            fn = "system_prompt_no_thinking.md"
        path = fallback_dir / fn
        if not path.exists():
            raise RuntimeError(
                f"Prompt template not found: {path}"
            )
        self.prompt_template = path.read_text(encoding="utf-8")

    SystemPrompt._load_prompt_template = _patched_load
    logger.info(
        "[BrowserUse] Patched SystemPrompt._load_prompt_template → %s",
        fallback_dir,
    )


_ensure_browser_use_prompts()
_configure_browser_logging()

_DEFAULT_TIMEOUT_S = 300
_DEFAULT_KEEP_ALIVE = True


def _dump_history(history, mode: str) -> None:
    """Print detailed diagnostics from a browser-use AgentHistory."""
    try:
        errors = history.errors() if hasattr(history, "errors") else []
        errs_str = [str(e)[:200] for e in errors if e] if errors else []
        print(f"[BrowserUse][{mode}] errors({len(errs_str)}): {errs_str}", flush=True)

        action_results = (
            history.action_results() if hasattr(history, "action_results") else []
        )
        for i, ar in enumerate(action_results or []):
            extracted = getattr(ar, "extracted_content", None)
            error = getattr(ar, "error", None)
            is_done = getattr(ar, "is_done", None)
            include_in_memory = getattr(ar, "include_in_memory", None)
            print(
                f"[BrowserUse][{mode}] step {i}: "
                f"done={is_done}, error={str(error)[:120] if error else None}, "
                f"extracted={str(extracted)[:120] if extracted else None}, "
                f"memory={include_in_memory}",
                flush=True,
            )

        final = None
        try:
            final = history.final_result()
        except Exception:
            pass
        print(
            f"[BrowserUse][{mode}] final_result={str(final)[:300] if final else None}",
            flush=True,
        )
    except Exception as exc:
        print(f"[BrowserUse][{mode}] _dump_history error: {exc}", flush=True)

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
        self._session_ever_started: bool = False
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
        """Lazy-create and cache a BrowserSession, with stale-session recovery."""
        if self._browser_session is not None and self._session_ever_started:
            cdp = getattr(self._browser_session, "_cdp_client_root", None)
            if cdp is None:
                print("[BrowserUse] cached session lost CDP connection, recreating", flush=True)
                await self._close_browser()
        if self._browser_session is None:
            from browser_use.browser.session import BrowserSession
            self._browser_session = BrowserSession(
                headless=self._headless,
                keep_alive=_DEFAULT_KEEP_ALIVE,
            )
            self._session_ever_started = False
        return self._browser_session

    def _current_api_signature(self) -> str:
        api_cfg = self._config_manager.get_model_api_config("agent")
        model = api_cfg.get("model", "") or ""
        base_url = api_cfg.get("base_url", "") or ""
        return f"{base_url}|{model}"

    def _get_mode_order(self, api_sig: str) -> List[str]:
        with _API_MODE_CACHE_LOCK:
            state = _API_MODE_CACHE.setdefault(
                api_sig,
                {"preferred_mode": "schema", "failed_modes": []},
            )
            preferred = state.get("preferred_mode", "schema")
            failed_modes = set(state.get("failed_modes", []))

        ordered = [preferred] + [m for m in _LLM_MODES if m != preferred]
        return [m for m in ordered if m not in failed_modes]

    def _mark_mode_result(self, api_sig: str, mode: str, ok: bool) -> None:
        with _API_MODE_CACHE_LOCK:
            state = _API_MODE_CACHE.setdefault(
                api_sig,
                {"preferred_mode": "schema", "failed_modes": []},
            )
            failed = set(state.get("failed_modes", []))
            if ok:
                state["preferred_mode"] = mode
                failed.discard(mode)
            else:
                failed.add(mode)
            state["failed_modes"] = [m for m in _LLM_MODES if m in failed]

    @staticmethod
    def _is_response_format_error(err) -> bool:
        msg = str(err).lower()
        return (
            "response_format" in msg
            and ("invalid" in msg or "must be text or json_object" in msg)
        )

    def _build_llm(self, mode: str = "schema") -> Any:
        """Build a browser-use compatible ChatOpenAI instance."""
        from browser_use.llm import ChatOpenAI as BUChatOpenAI
        api_cfg = self._config_manager.get_model_api_config("agent")
        base_url = api_cfg.get("base_url", "") or ""
        model = api_cfg.get("model", "") or ""
        kwargs: Dict[str, Any] = dict(
            model=model,
            api_key=api_cfg.get("api_key"),
            base_url=base_url,
            temperature=0.0,
            dont_force_structured_output=False,
            add_schema_to_system_prompt=False,
            remove_min_items_from_schema=False,
            remove_defaults_from_schema=False,
        )
        if mode == "text":
            kwargs["dont_force_structured_output"] = True
            kwargs["add_schema_to_system_prompt"] = True
            kwargs["remove_min_items_from_schema"] = True
            kwargs["remove_defaults_from_schema"] = True
        return BUChatOpenAI(**kwargs)

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

        from browser_use import Agent

        ok, info = self._config_manager.consume_agent_daily_quota(
            source="browser_use.run_instruction",
            units=1,
        )
        if not ok:
            return {
                "success": False,
                "error": (
                    "免费 Agent 模型今日试用次数已达上限 "
                    f"({info.get('used', 0)}/{info.get('limit', 300)})，请明日再试。"
                ),
            }

        for launch_attempt in range(2):
            browser_session = None
            try:
                browser_session = await self._get_browser_session()
                api_sig = self._current_api_signature()
                mode_order = self._get_mode_order(api_sig)
                print(f"[BrowserUse] mode_order={mode_order}", flush=True)
                last_err: Optional[Exception] = None
                history = None

                for mode in mode_order:
                    try:
                        print(f"[BrowserUse] trying mode={mode}", flush=True)
                        llm = self._build_llm(mode=mode)
                        if session_id and session_id in self._agents:
                            del self._agents[session_id]
                        agent = Agent(
                            task=instruction,
                            llm=llm,
                            browser_session=browser_session,
                            max_failures=1 if mode == "schema" else 3,
                            initial_actions=[
                                {"evaluate": {"code": _OVERLAY_JS}},
                            ],
                        )
                        if session_id:
                            self._agents[session_id] = agent

                        self._start_overlay(browser_session)

                        async def _on_step_end(a: Any) -> None:
                            s = getattr(a, "state", None)
                            if s is None:
                                return
                            step = getattr(s, "n_steps", "?")
                            out = getattr(s, "last_model_output", None)
                            goal = getattr(out, "next_goal", None) if out else None
                            acts = getattr(out, "action", []) if out else []
                            act_names = [type(a).__name__ for a in (acts or [])]
                            res = (getattr(s, "last_result", None) or [None])[-1]
                            err = getattr(res, "error", None) if res else None
                            done = getattr(res, "is_done", None) if res else None
                            print(
                                f"[BrowserUse][{mode}] step {step}: "
                                f"acts={act_names}, goal={str(goal)[:80] if goal else None}"
                                f"{f', err={str(err)[:80]}' if err else ''}"
                                f"{', DONE' if done else ''}",
                                flush=True,
                            )

                        history = await asyncio.wait_for(
                            agent.run(on_step_end=_on_step_end),
                            timeout=timeout_s,
                        )
                        self._session_ever_started = True

                        successful = (
                            history.is_successful()
                            if hasattr(history, "is_successful")
                            else True
                        )
                        n_steps = (
                            history.number_of_steps()
                            if hasattr(history, "number_of_steps")
                            else 999
                        )
                        _dump_history(history, mode)
                        print(
                            f"[BrowserUse] mode={mode} done: "
                            f"successful={successful}, steps={n_steps}",
                            flush=True,
                        )
                        if not successful and mode != _LLM_MODES[-1]:
                            self._mark_mode_result(api_sig, mode, ok=False)
                            print(
                                f"[BrowserUse] mode={mode} not successful "
                                f"(steps={n_steps}), falling back to next mode",
                                flush=True,
                            )
                            history = None
                            continue

                        if successful:
                            self._mark_mode_result(api_sig, mode, ok=True)
                        break
                    except Exception as e:
                        last_err = e
                        self._mark_mode_result(api_sig, mode, ok=False)
                        if self._is_response_format_error(e):
                            print(
                                f"[BrowserUse] exception in mode={mode}, "
                                f"falling back to next mode: {e}",
                                flush=True,
                            )
                            continue
                        raise

                if history is None:
                    raise last_err or RuntimeError("browser-use execution failed")

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

                print(
                    "[BrowserUse] result: "
                    f"done={bool(done)}, success={bool(successful)}, "
                    f"steps={getattr(history, 'number_of_steps', lambda: '?')()}",
                    flush=True,
                )
                return {
                    "success": bool(successful),
                    "result": str(final)[:1200],
                    "done": bool(done),
                    "steps": getattr(history, "number_of_steps", lambda: None)(),
                }
            except asyncio.TimeoutError:
                logger.warning("[BrowserUse] Task timed out after %ss", timeout_s)
                if browser_session:
                    await self._remove_overlay(browser_session)
                if session_id and session_id in self._agents:
                    del self._agents[session_id]
                return {"success": False, "error": f"Task timed out after {timeout_s}s"}
            except Exception as e:
                if browser_session:
                    await self._remove_overlay(browser_session)
                if session_id and session_id in self._agents:
                    del self._agents[session_id]
                await self._close_browser()
                if launch_attempt == 0 and not self._is_response_format_error(e):
                    logger.warning("[BrowserUse] Browser error (attempt 1), retrying: %s", e)
                    continue
                logger.error("[BrowserUse] Task failed: %s", e)
                return {"success": False, "error": str(e)}
        return {"success": False, "error": "browser-use execution failed"}

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
        self._session_ever_started = False
        self._agents.clear()

    async def close(self) -> None:
        """Graceful shutdown."""
        await self._close_browser()
