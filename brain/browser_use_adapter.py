import asyncio
import logging
import os
import shutil
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils.config_manager import get_config_manager
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Agent")

_LLM_MODES: List[str] = ["schema", "text"]
_API_MODE_CACHE_LOCK = threading.Lock()
_API_MODE_CACHE: Dict[str, Dict[str, Any]] = {}


def _configure_browser_logging() -> None:
    """Reduce browser-use log noise.

    Disk logs still get WARNING+ entries; real-time progress uses print.
    """
    for name in ("browser_use.llm"):
        logging.getLogger(name).setLevel(logging.ERROR)
    for name in ("browser_use", "service", "browser_use.browser", "browser_use.agent"):
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


def _seed_extension_cache() -> None:
    """Copy bundled browser-use extensions into the runtime cache so that
    ``BrowserProfile._ensure_default_extensions_downloaded()`` finds them
    already extracted and skips the network download entirely.

    The bundled extensions live in ``data/browser_use_extensions/`` next to
    the executable (populated at build time by ``build_nuitka.bat``).
    """
    try:
        from browser_use.config import CONFIG
    except Exception:
        return

    target_dir = CONFIG.BROWSER_USE_EXTENSIONS_DIR
    if not target_dir:
        return

    for root_candidate in (
        os.path.dirname(os.path.abspath(sys.argv[0])),
        os.path.dirname(os.path.abspath(__file__)),
        os.getcwd(),
    ):
        src_dir = os.path.join(root_candidate, "data", "browser_use_extensions")
        if os.path.isdir(src_dir):
            break
    else:
        return

    target = Path(target_dir)
    target.mkdir(parents=True, exist_ok=True)
    copied = 0
    for entry in os.listdir(src_dir):
        src_ext = os.path.join(src_dir, entry)
        if not os.path.isdir(src_ext):
            continue
        dest_ext = target / entry
        manifest = dest_ext / "manifest.json"
        if manifest.exists():
            continue
        try:
            shutil.copytree(src_ext, str(dest_ext), dirs_exist_ok=True)
            copied += 1
        except Exception as exc:
            logger.debug("[BrowserUse] Failed to seed extension %s: %s", entry, exc)
    if copied:
        print(
            f"[BrowserUse] Seeded {copied} bundled extension(s) into cache",
            flush=True,
        )


_seed_extension_cache()


def _find_bundled_chromium() -> Optional[str]:
    """Find the Chromium executable bundled inside ``playwright_browsers/``.

    Handles both ``chrome-win`` and ``chrome-win64`` directory names that
    different Playwright versions may produce.
    """
    import glob as _glob

    browsers_dir = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
    if not browsers_dir:
        for root in (
            os.path.dirname(os.path.abspath(sys.argv[0])),
            os.path.dirname(os.path.abspath(__file__)),
            os.getcwd(),
        ):
            candidate = os.path.join(root, "playwright_browsers")
            if os.path.isdir(candidate):
                browsers_dir = candidate
                break
    if not browsers_dir or not os.path.isdir(browsers_dir):
        return None

    for pattern in (
        os.path.join(browsers_dir, "chromium-*", "chrome-win64", "chrome.exe"),
        os.path.join(browsers_dir, "chromium-*", "chrome-win", "chrome.exe"),
        os.path.join(browsers_dir, "chromium-*", "chrome-linux*", "chrome"),
        os.path.join(browsers_dir, "chromium-*", "chrome-mac", "Chromium.app",
                     "Contents", "MacOS", "Chromium"),
    ):
        matches = _glob.glob(pattern)
        if matches:
            matches.sort()
            exe = matches[-1]
            if os.path.isfile(exe):
                return exe
    return None


def _find_system_chrome_path() -> Optional[str]:
    """Find an installed system Chrome / Chromium executable."""
    try:
        from browser_use.browser.watchdogs.local_browser_watchdog import (
            LocalBrowserWatchdog,
        )
        return LocalBrowserWatchdog._find_installed_browser_path()
    except Exception:
        return None


def _find_chrome_path() -> Optional[str]:
    """Pre-flight: locate a usable Chrome / Chromium executable.

    Checks the bundled Playwright Chromium first, then falls back to
    browser_use's system-wide search.
    """
    bundled = _find_bundled_chromium()
    if bundled:
        return bundled
    return _find_system_chrome_path()

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

    _ip_country_cache: Optional[str] = None

    def __init__(self, headless: bool = False) -> None:
        self._config_manager = get_config_manager()
        self.last_error: Optional[str] = None
        self._headless = headless
        self._chrome_path: Optional[str] = None
        self._browser_session: Any = None
        self._session_ever_started: bool = False
        self._agents: Dict[str, Any] = {}
        self._overlay_task: Optional[asyncio.Task] = None
        self._cancelled: bool = False
        try:
            from browser_use import Agent  # noqa: F401
            from browser_use.browser.session import BrowserSession  # noqa: F401
            self._ready_import = True
        except Exception as e:
            self._ready_import = False
            self.last_error = str(e)

    @staticmethod
    def _get_ip_country() -> Optional[str]:
        """Return the user's IP country code (e.g. 'US', 'JP', 'CN').

        Priority: Steam GeoIP -> ipinfo.io fallback.
        Cached after first successful lookup.
        """
        if BrowserUseAdapter._ip_country_cache is not None:
            return BrowserUseAdapter._ip_country_cache

        # Try Steam GeoIP first
        try:
            from main_routers.shared_state import get_steamworks
            sw = get_steamworks()
            if sw is not None:
                raw = sw.Utils.GetIPCountry()
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                if raw:
                    code = raw.upper()
                    BrowserUseAdapter._ip_country_cache = code
                    return code
        except Exception as e:
            logger.debug("[BrowserUse] Steam GeoIP failed: %s", e)

        # Fallback: public GeoIP API
        try:
            import urllib.request
            import json as _json
            with urllib.request.urlopen("https://ipinfo.io/json", timeout=3) as resp:
                data = _json.loads(resp.read())
            code = (data.get("country") or "").upper()
            if code:
                BrowserUseAdapter._ip_country_cache = code
                return code
        except Exception as e:
            logger.debug("[BrowserUse] ipinfo.io fallback failed: %s", e)

        return None

    def cancel_running(self) -> None:
        """Signal the currently running task to stop at the next step boundary."""
        self._cancelled = True
        logger.info("[BrowserUse] cancel_running called, task will abort at next step")

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
            stale = False
            cdp = getattr(self._browser_session, "_cdp_client_root", None)
            if cdp is None:
                print("[BrowserUse] cached session lost CDP connection, recreating", flush=True)
                stale = True

            if not stale:
                watchdog = getattr(self._browser_session, "_local_browser_watchdog", None)
                proc = getattr(watchdog, "_subprocess", None) if watchdog else None
                if proc is not None:
                    try:
                        if not proc.is_running():
                            print("[BrowserUse] browser process dead, recreating session", flush=True)
                            stale = True
                    except Exception:
                        stale = True

            if stale:
                await self._close_browser()

        if self._browser_session is None:
            from browser_use.browser.session import BrowserSession
            kwargs: Dict[str, Any] = dict(
                headless=self._headless,
                keep_alive=_DEFAULT_KEEP_ALIVE,
            )
            if self._chrome_path:
                kwargs["executable_path"] = self._chrome_path
            self._browser_session = BrowserSession(**kwargs)
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
    def _is_browser_disconnected_error(err) -> bool:
        """Detect errors indicating the browser window was closed by user."""
        msg = str(err).lower()
        return any(s in msg for s in (
            "websocket connection closed",
            "browser not connected",
            "target may have detached",
            "browser is in an unstable state",
            "no valid agent focus",
        ))

    @staticmethod
    def _is_response_format_error(err) -> bool:
        msg = str(err).lower()
        return (
            "response_format" in msg
            and ("invalid" in msg or "must be text or json_object" in msg)
        )

    @staticmethod
    def _is_content_filter_error(err) -> bool:
        """Detect LLM content inspection / safety filter rejections."""
        msg = str(err).lower()
        return any(s in msg for s in (
            "data_inspection_failed",
            "datainspectionfailed",
            "inappropriate content",
            "content_filter",
            "content filter",
            "responsible ai policy",
            "content management policy",
        ))

    @staticmethod
    def _is_unsupported_param_error(err) -> bool:
        """Detect Gemini-style 'Unknown name' payload errors."""
        msg = str(err).lower()
        return "unknown name" in msg and "invalid json payload" in msg

    @staticmethod
    def _is_schema_error(err) -> bool:
        """Detect Gemini schema nesting depth / structure errors."""
        msg = str(err).lower()
        return "nesting depth" in msg or (
            "generationconfig" in msg and "schema" in msg
        )

    @staticmethod
    def _is_gemini_compatible_endpoint(base_url: str) -> bool:
        return any(s in base_url for s in (
            "googleapis.com", "generativelanguage", "lanlan.app",
        ))

    def _build_llm(self, mode: str = "schema") -> Any:
        """Build a browser-use compatible ChatOpenAI instance.

        For Gemini-compatible endpoints, strips parameters that the API
        rejects (``frequency_penalty``, ``seed``, ``service_tier``).
        A thin wrapper class is used so the constraint survives any
        internal copy / re-init performed by browser-use.
        """
        from browser_use.llm import ChatOpenAI as BUChatOpenAI
        api_cfg = self._config_manager.get_model_api_config("agent")
        base_url = api_cfg.get("base_url", "") or ""
        model = api_cfg.get("model", "") or ""
        is_gemini = self._is_gemini_compatible_endpoint(base_url)
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
        if is_gemini:
            kwargs["frequency_penalty"] = None
            kwargs["seed"] = None
            kwargs["service_tier"] = None
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
        self._cancelled = False

        country = self._get_ip_country()
        if country:
            instruction = (
                f"[User IP country: {country}] "
                f"Keep this in mind when choosing search engine or regional settings.\n\n"
                f"{instruction}"
            )

        status = self.is_available()
        if not status.get("ready"):
            return {"success": False, "error": "; ".join(status.get("reasons", []))}

        bundled_chrome = _find_bundled_chromium()
        system_chrome = _find_system_chrome_path()
        chrome = bundled_chrome or system_chrome
        fallback_chrome: Optional[str] = None
        if bundled_chrome and system_chrome and bundled_chrome != system_chrome:
            fallback_chrome = system_chrome

        if not chrome:
            msg = (
                "未找到 Chrome / Chromium 浏览器，请安装 Google Chrome 后重试。"
                "  (checked standard paths on this system)"
            )
            print(f"[BrowserUse] PREFLIGHT FAIL: {msg}", flush=True)
            return {"success": False, "error": msg}
        self._chrome_path = chrome
        source = "bundled" if chrome == bundled_chrome else "system"
        print(
            f"[BrowserUse] preflight OK, chrome={chrome}, source={source}",
            flush=True,
        )

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
                        _disconnect_errors = 0
                        _content_filter_errors = 0

                        async def _on_step_end(a: Any) -> None:
                            nonlocal _disconnect_errors, _content_filter_errors
                            if self._cancelled:
                                print("[BrowserUse] Task cancelled by user", flush=True)
                                raise asyncio.CancelledError("Task cancelled by user")
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
                            if err and BrowserUseAdapter._is_content_filter_error(err):
                                _content_filter_errors += 1
                                if _content_filter_errors >= 2:
                                    print(
                                        f"[BrowserUse] Content filter triggered ({_content_filter_errors} consecutive errors), aborting task",
                                        flush=True,
                                    )
                                    raise RuntimeError(
                                        "CONTENT_FILTER: The page content was rejected by the AI model's safety filter. "
                                        "This usually happens when the page contains sensitive topics."
                                    )
                            elif err and BrowserUseAdapter._is_browser_disconnected_error(err):
                                _disconnect_errors += 1
                                if _disconnect_errors >= 2:
                                    print(
                                        f"[BrowserUse] Browser disconnected ({_disconnect_errors} consecutive errors), aborting task",
                                        flush=True,
                                    )
                                    raise ConnectionError("Browser disconnected - user closed the browser")
                            else:
                                _disconnect_errors = 0
                                _content_filter_errors = 0

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
                    except ConnectionError as e:
                        if self._is_browser_disconnected_error(e):
                            print(
                                f"[BrowserUse] Browser disconnected during mode={mode}, stopping all retries",
                                flush=True,
                            )
                            last_err = e
                            await self._close_browser()
                            break
                        raise
                    except Exception as e:
                        last_err = e
                        self._mark_mode_result(api_sig, mode, ok=False)
                        if (self._is_response_format_error(e)
                                or self._is_unsupported_param_error(e)
                                or self._is_schema_error(e)):
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
            except asyncio.CancelledError:
                logger.info("[BrowserUse] Task cancelled by user")
                if browser_session:
                    await self._remove_overlay(browser_session)
                if session_id and session_id in self._agents:
                    del self._agents[session_id]
                return {"success": False, "error": "Task cancelled by user"}
            except asyncio.TimeoutError:
                logger.warning("[BrowserUse] Task timed out after %ss", timeout_s)
                if browser_session:
                    await self._remove_overlay(browser_session)
                if session_id and session_id in self._agents:
                    del self._agents[session_id]
                return {"success": False, "error": f"timed out after {timeout_s}s"}
            except Exception as e:
                if (
                    launch_attempt == 0
                    and browser_session is None
                    and fallback_chrome
                    and self._chrome_path != fallback_chrome
                ):
                    logger.warning(
                        "[BrowserUse] Bundled browser launch failed, "
                        "falling back to system browser: %s",
                        e,
                    )
                    self._chrome_path = fallback_chrome
                    await self._close_browser()
                    continue
                if browser_session:
                    await self._remove_overlay(browser_session)
                if session_id and session_id in self._agents:
                    del self._agents[session_id]
                if self._is_browser_disconnected_error(e):
                    logger.warning("[BrowserUse] Browser disconnected, task aborted: %s", e)
                    return {"success": False, "error": "Browser disconnected - browser window was closed"}
                if self._is_content_filter_error(e):
                    logger.warning("[BrowserUse] Content filter triggered: %s", e)
                    return {
                        "success": False,
                        "error": "CONTENT_FILTER: The page content was rejected by the AI model's safety filter. "
                                 "This usually happens when the page contains sensitive topics.",
                    }
                if launch_attempt == 0 and not self._is_response_format_error(e):
                    await self._close_browser()
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
            # Grab browser PID before stopping so we can force-kill if .stop() hangs
            watchdog = getattr(self._browser_session, "_local_browser_watchdog", None)
            proc = getattr(watchdog, "_subprocess", None) if watchdog else None
            browser_pid = None
            if proc is not None:
                try:
                    browser_pid = proc.pid
                except Exception:
                    pass

            try:
                await asyncio.wait_for(self._browser_session.stop(), timeout=10)
            except asyncio.TimeoutError:
                logger.warning("[BrowserUse] _browser_session.stop() timed out after 10s")
                self._force_kill_browser(browser_pid)
            except Exception as exc:
                logger.warning("[BrowserUse] _browser_session.stop() raised: %s", exc)
                self._force_kill_browser(browser_pid)

            self._browser_session = None
        self._session_ever_started = False
        self._agents.clear()

    @staticmethod
    def _force_kill_browser(browser_pid: Optional[int]) -> None:
        """Force-kill a browser process tree by PID when graceful .stop() fails."""
        if browser_pid is None:
            return
        import signal
        import subprocess
        try:
            import psutil
            parent = psutil.Process(browser_pid)
            for child in parent.children(recursive=True):
                try:
                    child.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            parent.kill()
            logger.info("[BrowserUse] force-killed browser tree (pid=%d) via psutil", browser_pid)
            return
        except ImportError:
            logger.warning("[BrowserUse] psutil not available, falling back to platform kill for pid=%d", browser_pid)
        except Exception as exc:
            logger.warning("[BrowserUse] psutil kill failed for pid=%d: %s, falling back", browser_pid, exc)

        try:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/PID", str(browser_pid), "/T", "/F"],
                    capture_output=True, timeout=10,
                )
                logger.info("[BrowserUse] force-killed browser tree (pid=%d) via taskkill", browser_pid)
            else:
                subprocess.run(
                    ["pkill", "-TERM", "-P", str(browser_pid)],
                    capture_output=True, timeout=10,
                )
                subprocess.run(
                    ["pkill", "-KILL", "-P", str(browser_pid)],
                    capture_output=True, timeout=10,
                )
                logger.info("[BrowserUse] force-killed child processes of pid=%d via pkill", browser_pid)
                os.kill(browser_pid, signal.SIGKILL if hasattr(signal, "SIGKILL") else signal.SIGTERM)
                logger.info("[BrowserUse] force-killed browser pid=%d via os.kill", browser_pid)
        except (subprocess.SubprocessError, OSError, PermissionError) as e:
            logger.warning("[BrowserUse] platform kill failed for pid=%d: %s", browser_pid, e)
            try:
                os.kill(browser_pid, signal.SIGKILL if hasattr(signal, "SIGKILL") else signal.SIGTERM)
                logger.info("[BrowserUse] force-killed browser pid=%d via os.kill fallback", browser_pid)
            except (OSError, PermissionError) as e2:
                logger.warning("[BrowserUse] failed to kill browser pid=%d: %s", browser_pid, e2)

    async def close(self) -> None:
        """Graceful shutdown."""
        await self._close_browser()
