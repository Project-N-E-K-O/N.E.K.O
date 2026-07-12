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
"""Focus-mode cognition for ``LLMSessionManager``: inline focus decisions,
idle thinking, focus transitions, and indicator/charge/thinking pushes
to the frontend.

Method-only mixin: every instance attribute is assigned in
``LLMSessionManager.__init__`` (``main_logic.core.manager``).
"""

import time
from typing import Optional
from main_logic.omni_offline_client import OmniOfflineClient
from main_logic.session_state import SessionEvent, CognitionMode, TurnOwner
from ._shared import logger, _purge_closed_tool_calls

# Late-binding read point for symbols that tests rebind on the facade via
# ``monkeypatch.setattr("main_logic.core.<attr>", ...)``. Do NOT from-import
# those names here: a from-import snapshots the value at import time and the
# facade patch would no longer reach this module's methods.
from main_logic import core as _core_facade


class FocusMixin:
    """Focus-mode cognition methods (see module docstring)."""

    async def _focus_inline_decision(self, user_text: str) -> bool:
        """Path A (inline) Focus gate: score the just-arrived user message and
        return whether THIS reply should run thinking-on.

        Scores via the shared ``FocusScorer`` (keyword + cadence + open-thread
        signals), advances ``self.state``'s hysteresis (``update_focus``), and
        returns ``mode is FOCUS``. An explicit topic switch forces an immediate
        exit. Best-effort: any failure degrades to regular (thinking-off) and
        never blocks the user reply. Returns False fast when the master switch
        is off (skips the snapshot cost).
        """
        # Reconcile the badge first — catches a Focus state cleared since the
        # last turn without a FOCUS_EXIT event (clear_focus / master-switch
        # self-clear), even on the early-return path when the switch is now off.
        await self._reconcile_focus_indicator()
        from config import FOCUS_MODE_ENABLED  # live read (re-imported per call)
        if not FOCUS_MODE_ENABLED:
            # Flag flipped off → clear ALL focus residue unconditionally, not
            # just when mode==FOCUS. The leaky accumulator can sit in REGULAR
            # with charge just under the enter bar; if we only cleared on FOCUS,
            # that frozen charge would survive the disabled window and let an
            # unrelated mild cue enter on stale evidence once re-enabled.
            # update_focus self-clears when the (config) switch is off (idempotent).
            await self.state.update_focus(0.0)
            # Reconcile AGAIN after the self-clear: if the switch was flipped off
            # mid-episode, the clear above is silent (no FOCUS_EXIT), so clear the
            # badge this turn rather than waiting for the next one.
            await self._reconcile_focus_indicator()
            return False
        # Per-user master switch (对话设置 → focusCognitionEnabled): the user can
        # turn 凝神 off entirely while the global config flag stays on. Defaults on
        # when unset. The master emotion read is independent (its own pipeline) and
        # is NOT affected by this gate.
        try:
            focus_user_enabled = bool(
                (await _core_facade.aload_global_conversation_settings()).get('focusCognitionEnabled', True)
            )
        except Exception:
            focus_user_enabled = True
        if not focus_user_enabled:
            # Hard-clear (not update_focus(0.0)): with the config flag still on,
            # update_focus only DECAYS by retention, leaving residual charge and a
            # FOCUS mode lingering for a turn or two — so the badge/glow wouldn't
            # drop until the charge bled below exit. clear_focus zeroes everything
            # silently (no FOCUS_EXIT) so 凝神 turns off at once when the user asks.
            await self.state.clear_focus()
            await self._reconcile_focus_indicator()
            return False
        if not (user_text and user_text.strip()):
            return False
        try:
            from config.prompts.prompts_focus import detect_topic_switch
            # Focus scores the user's MESSAGE, not the screen: the inline
            # signals (vulnerability keywords + reply cadence) read user_text
            # and the scorer's own cadence buffer — never the activity snapshot
            # (silence / open_thread are idle-only). So Focus is
            # privacy-independent BY CONSTRUCTION and must NOT be gated on
            # privacy mode: understanding the user's emotional state from what
            # they typed is core to an AI companion. Privacy mode governs only
            # SCREEN / app-state visibility (see docs/contributing/
            # developer-notes.md rule 6). Hence no snapshot fetch here.
            # emotion 信号读 master 情绪画像的最近读数（异步算、滞后一拍，与
            # cadence 用历史一脉相承）。画像关 / 还没算过时 latest 为 None，
            # FocusScorer 让 emotion 信号自动退出加权、退回 keyword+cadence。
            # 读 _note_user_turn 在「本轮 analyze 启动前」存的快照，保证 emotion
            # 信号确定性地滞后一拍——当前 turn 不消费它自己即将算出的读数（否则快
            # tier / 中途 await 让 fire-and-forget analyze 抢先更新 latest，同一条
            # 消息会时而读旧读数时而读自己的，违反 scorer 的 lag-one-turn 契约）。
            emotion_reading = getattr(self, '_focus_emotion_reading', None)
            scored = self._focus_scorer.score(
                user_text=user_text, emotion_reading=emotion_reading,
            )
            topic_changed = detect_topic_switch(user_text)
            mode = await self.state.update_focus(
                scored.score, topic_changed=topic_changed,
            )
            if mode is CognitionMode.FOCUS:
                if not self._focus_artifacts_pending:
                    # 首次进入本 episode：记下历史长度，退出时只清这之后（episode
                    # 期间）产生的闭合 tool call，不动 Focus 之前的普通工具调用。
                    hist = getattr(
                        getattr(self, "session", None), "_conversation_history", None
                    )
                    self._focus_artifacts_history_start = (
                        len(hist) if isinstance(hist, list) else None
                    )
                # arm：本 episode 的 thinking/tool 残留，退出时要清（见下）。
                self._focus_artifacts_pending = True
            # Log every turn (incl. REGULAR) so tuning can watch the charge
            # accumulate toward FOCUS_CHARGE_ENTER, not just the entry moment.
            logger.info(
                "[%s] 凝神 inline: score=%.2f charge=%s mode=%s signals=%s",
                self.lanlan_name, scored.score,
                self.state.snapshot().get("focus_charge"), mode.value, scored.signals,
            )
            # Stream the post-turn charge for the frontend edge glow.
            await self._push_focus_charge(self.state.snapshot().get("focus_charge"))
            # 退出边沿同步清理（此刻在 stream_text 之前、无并发）：inline 自然退出
            # （decayed / hard_cap / topic_switch）都在此命中并清；FOCUS 维持时是
            # no-op（仍在 episode 内）。silent early-return 路径由 reconcile 兜底。
            await self._maybe_purge_focus_artifacts()
            return mode is CognitionMode.FOCUS
        except Exception as e:
            logger.warning("[%s] focus inline decision failed (degrading to regular): %s",
                           self.lanlan_name, e)
            # Don't leave a stale FOCUS episode if score / update_focus raised
            # mid-episode — degrade cleanly to regular.
            try:
                if self.state.mode is CognitionMode.FOCUS:
                    await self.state.update_focus(0.0, topic_changed=True)
            except Exception as _exit_err:
                logger.debug("[%s] focus inline fail-exit also failed: %s",
                             self.lanlan_name, _exit_err)
            return False

    def _focus_idle_thinking(self) -> bool:
        """Path B (idle) — does THIS proactive reply run thinking-on?

        Read-only: returns whether the session is currently in Focus. A
        proactive turn never raises the charge, so there is nothing to score
        here. The charge decay happens AFTER the turn, in
        ``_focus_idle_cooldown`` (it needs to know whether the turn actually
        spoke). Returns False when the master switch is off. Privacy-independent
        (no snapshot, no screen signals).
        """
        from config import FOCUS_MODE_ENABLED  # live read
        if not FOCUS_MODE_ENABLED:
            return False
        # Honor the per-user master switch here too (defense in depth): if the
        # user turned 凝神 off mid-episode, a proactive turn must not keep running
        # thinking-on before the next inline turn clears the episode.
        try:
            if not bool(_core_facade.load_global_conversation_settings().get('focusCognitionEnabled', True)):
                return False
        except Exception:
            # Fail-open: a settings-read hiccup must not silently disable an
            # active Focus episode — fall through to the live state.mode below.
            pass
        return self.state.mode is CognitionMode.FOCUS

    async def _on_focus_transition(self, event: SessionEvent, payload: dict) -> None:
        """SM subscriber for FOCUS_ENTER / FOCUS_EXIT — immediate badge update on
        the normal hysteresis path. Delegates to the idempotent push.

        NB: history artifact arm/purge is deliberately NOT done here.
        ``_dispatch_subscribers`` fires async callbacks fire-and-forget
        (``ensure_future``), so a FOCUS_EXIT-driven purge could race the reply
        stream. Arming + purging happen SYNCHRONOUSLY on the inline decision path
        and the per-turn reconcile instead (see ``_maybe_purge_focus_artifacts``)."""
        await self._push_focus_indicator(event is SessionEvent.FOCUS_ENTER)

    async def _maybe_purge_focus_artifacts(self) -> None:
        """On the edge where Focus mode turns OFF, wipe the thinking + closed
        tool-call traces the just-ended episode left in history, so they can't
        bias the REGULAR reply that follows (or a fresh session).

        Called SYNCHRONOUSLY from two places (NOT the async FOCUS_EXIT event,
        which fires fire-and-forget and could race the stream):
          - the inline decision, right after update_focus and BEFORE stream_text
            — catches inline exits (decayed / hard_cap / topic_switch);
          - the per-turn _reconcile_focus_indicator — catches the silent exits
            (master switch / per-user setting / privacy self-clear / clear_focus)
            and a proactive-cooldown exit on the next inline turn.

        Idempotent: runs once per episode, only when Focus was actually entered
        (``_focus_artifacts_pending``) AND the mode has dropped back to REGULAR.
        All call sites sit OUTSIDE the stream boundary, so there is no concurrent
        history mutation. Only text sessions (OmniOfflineClient) keep history.
        """
        if not self._focus_artifacts_pending:
            return
        if self.state.mode is CognitionMode.FOCUS:
            return  # 仍在 focus，残留还在用，不能清
        self._focus_artifacts_pending = False
        start = self._focus_artifacts_history_start or 0
        self._focus_artifacts_history_start = None
        sess = getattr(self, "session", None)
        if not isinstance(sess, OmniOfflineClient):
            return
        history = getattr(sess, "_conversation_history", None)
        if not history:
            return
        try:
            removed = _purge_closed_tool_calls(history, start=start)
            if removed:
                logger.info(
                    "[%s] 凝神退出：从历史清除 thinking/已闭合 tool call 残留 %d 条",
                    self.lanlan_name, removed,
                )
        except Exception as e:
            logger.warning("[%s] 凝神退出历史清理失败(忽略): %s", self.lanlan_name, e)

    async def _reconcile_focus_indicator(self) -> None:
        """Catch Focus states dropped WITHOUT a FOCUS_EXIT event (clear_focus
        history-wipe, master-switch / privacy self-clear in update_focus) so the
        badge AND the charge glow can't get stuck on. Called once per turn. The
        charge push reads the (now-cleared → 0) snapshot, so a silent exit that
        only reconciles the binary state still tells the glow to fade out. Also
        the catch-all purge point for silent Focus exits (no FOCUS_EXIT event)."""
        await self._push_focus_indicator(self.state.mode is CognitionMode.FOCUS)
        await self._push_focus_charge()
        await self._maybe_purge_focus_artifacts()

    async def resync_focus_for_new_window(self) -> None:
        """Re-emit ALL focus signals to a freshly-connected window (greeting_check):
        the charge glow, the binary focus_state, AND the transient thinking pulse.
        ``force=True`` bypasses the idempotent cache so a window opened mid-FOCUS
        (or mid-thinking) gets the current indicator even though no enter/exit /
        thinking transition fires for it."""
        await self._push_focus_charge()
        await self._push_focus_indicator(self.state.mode is CognitionMode.FOCUS, force=True)
        await self._push_focus_thinking(getattr(self, "_focus_thinking_active", False), force=True)

    async def _push_focus_indicator(self, active: bool, *, force: bool = False) -> None:
        """Mirror the cognition indicator (focus_state) to the frontend (drives
        the screen-reader status node; the visible glow is charge-driven). Idempotent
        on the cached state so the event path and the per-turn reconcile never
        double-fire — except ``force=True`` (a new window re-sync) re-pushes even
        when unchanged. Ephemeral UI state: pushed live over the websocket and
        mirrored to the sync queue for cross-server, but never persisted to history.
        Best-effort: a ws failure must never disturb the caller."""
        # getattr default guards bypass-__init__ constructions (bare test mgrs,
        # cross-server / unpickled managers) — they simply have no badge to sync.
        if not force and active == getattr(self, "_focus_indicator_active", False):
            return
        self._focus_indicator_active = active
        msg = {"type": "focus_state", "active": active}
        try:
            self.sync_message_queue.put({"type": "json", "data": msg})
        except Exception as e:
            logger.debug("[%s] focus_state sync-queue push failed: %s", self.lanlan_name, e)
        try:
            ws = self.websocket
            if ws and hasattr(ws, 'client_state') and ws.client_state == ws.client_state.CONNECTED:
                if self.websocket_lock:
                    async with self.websocket_lock:
                        await ws.send_json(msg)
                else:
                    await ws.send_json(msg)
        except Exception as e:
            logger.debug("[%s] focus_state ws push failed: %s", self.lanlan_name, e)

    async def _push_focus_charge(self, charge: Optional[float] = None) -> None:
        """Stream the live Focus charge (0..1) to the frontend so the edge glow
        can scale continuously: onset at FOCUS_CHARGE_EXIT, the non-linear jump +
        breathing at FOCUS_CHARGE_ENTER, peak toward FOCUS_CHARGE_CAP. Carries the
        wall-clock stamp so the frontend extrapolates the same time decay between
        pushes for a smooth fade (no per-second server spam). Pushed on every
        charge change AND on (re)connect so a freshly-opened window (e.g. the
        separate /chat_full window) lands on the correct brightness immediately.
        Ephemeral, ws + sync-queue only, never persisted. Best-effort.

        ``at_ms`` is the charge's LAST-CHANGE wall-clock (not "now"), so the
        frontend extrapolates the time decay from the right moment — a reconnect
        after a long gap must not replay a stale un-decayed charge as if current.
        When Focus is disabled we push 0 (not skip) so a lit glow can't linger."""
        from config import FOCUS_MODE_ENABLED  # live read
        try:
            snap = self.state.snapshot()
        except Exception:
            snap = {}
        if not FOCUS_MODE_ENABLED:
            charge, at = 0.0, 0.0
        else:
            if charge is None:
                try:
                    charge = float(snap.get("focus_charge") or 0.0)
                except Exception:
                    charge = 0.0
            at = snap.get("focus_charge_at") or 0.0
        at_ms = int(at * 1000) if at and at > 0 else int(time.time() * 1000)
        msg = {"type": "focus_charge", "charge": round(max(0.0, float(charge)), 4),
               "at_ms": at_ms}
        try:
            self.sync_message_queue.put({"type": "json", "data": msg})
        except Exception as e:
            logger.debug("[%s] focus_charge sync-queue push failed: %s", self.lanlan_name, e)
        try:
            ws = self.websocket
            if ws and hasattr(ws, 'client_state') and ws.client_state == ws.client_state.CONNECTED:
                if self.websocket_lock:
                    async with self.websocket_lock:
                        await ws.send_json(msg)
                else:
                    await ws.send_json(msg)
        except Exception as e:
            logger.debug("[%s] focus_charge ws push failed: %s", self.lanlan_name, e)

    async def _push_focus_thinking(self, active: bool, *, force: bool = False) -> None:
        """Pulse a transient "model is thinking" signal to the frontend so the
        chat history can show a thinking-dots bubble while a Focus turn runs
        thinking-on but hasn't emitted any visible content yet. Pushed True right
        before such a turn streams, cleared (False) the moment the first visible
        chunk lands (send_lanlan_response) or the turn ends. Idempotent on the
        cached state so per-chunk callers can clear blindly without spamming —
        except ``force=True`` (a new-window re-sync) re-pushes even when
        unchanged, mirroring _push_focus_indicator so a window opened mid-thinking
        lands on the current bubble. Ephemeral: ws + sync-queue only, never
        persisted. Best-effort — a ws failure must never disturb the caller.

        getattr default guards bypass-__init__ constructions (bare test mgrs,
        cross-server / unpickled managers) — they simply have no bubble to sync."""
        if not force and active == getattr(self, "_focus_thinking_active", False):
            return
        self._focus_thinking_active = active
        msg = {"type": "focus_thinking", "active": active}
        try:
            self.sync_message_queue.put({"type": "json", "data": msg})
        except Exception as e:
            logger.debug("[%s] focus_thinking sync-queue push failed: %s", self.lanlan_name, e)
        try:
            ws = self.websocket
            if ws and hasattr(ws, 'client_state') and ws.client_state == ws.client_state.CONNECTED:
                if self.websocket_lock:
                    async with self.websocket_lock:
                        await ws.send_json(msg)
                else:
                    await ws.send_json(msg)
        except Exception as e:
            logger.debug("[%s] focus_thinking ws push failed: %s", self.lanlan_name, e)

    async def handle_thinking_active(self, active: bool = True) -> None:
        """Session callback: the model started (active=True) or finished
        (active=False) emitting reasoning/thinking chunks for the current stream
        (the text is filtered out upstream; only this boolean pulse reaches us).
        Drives the chat thinking-dots bubble for ANY reasoning turn — decoupled
        from the Focus inline decision. A Focus turn pre-pulses the bubble before
        streaming (still works, idempotent); a non-Focus turn whose provider
        reasons internally pulses here on its first reasoning chunk. The bubble
        is cleared on the first visible token (send_lanlan_response), when the
        text turn ends (the unconditional finally in the text path), or — for a
        proactive/greeting/avatar turn that reasons but commits no visible text —
        by the active=False clear from prompt_ephemeral's finally. Best-effort —
        idempotent via ``_push_focus_thinking``'s cached state."""
        await self._push_focus_thinking(active)

    def _make_thinking_active_callback(self, session_ref):
        """Bind ``handle_thinking_active`` to ONE specific OmniOfflineClient so a
        reasoning pulse only drives the bubble while that client is the live
        session. The thinking bubble is a single per-window surface; a pulse from
        a NON-current client — a pending hot-swap session, or a just-demoted old
        session still draining a stream after the swap — must not light or clear
        the current window (CodeRabbit). The live session always matches, so its
        pulses/clears pass through unchanged; everything else is a silent no-op.
        getattr default tolerates call-time teardown where self.session is None."""
        async def _on_thinking_active(active: bool) -> None:
            if session_ref is getattr(self, "session", None):
                await self.handle_thinking_active(active)
        return _on_thinking_active

    async def _focus_idle_cooldown(
        self, *, replied: bool, episode_token, turn_token=None,
    ) -> None:
        """Path B (idle) Focus COOLDOWN: decay the charge once, after a Phase-2
        proactive turn finishes. A proactive turn NEVER raises the charge —
        entering and sustaining Focus is driven solely by the inline path (the
        user's own messages). This only lets an active episode cool down.

        Decay rate by whether the turn actually spoke, via two config knobs:
          * ``replied=True`` (a Phase-2 proactive reply was delivered) →
            ``FOCUS_IDLE_REPLIED_RETENTION``.
          * ``replied=False`` (Phase-2 reached but produced no reply — empty /
            aborted) → ``FOCUS_IDLE_SILENT_RETENTION``.
        Currently both are tuned to the same value (0.8), so speaking and silence
        cool the episode at one gentle rate; the split is kept for future
        re-tuning (invariant: replied <= silent). Focus persistence is driven by
        how often a proactive turn fires, not raw time.

        ``episode_token`` / ``turn_token`` pin the decay to the exact focus state
        this proactive turn observed when it made its thinking decision — the
        episode id and the turn count at Phase 2. The decay is SKIPPED unless the
        SM is STILL in that same episode AND no inline turn has landed since:
          * ``not replied`` AND the user already took over (``owner is USER``) →
            the user spoke during an UNDELIVERED proactive turn and aborted it
            before it said anything. The inline path marks USER_INPUT
            (owner→USER) the moment they speak, but its focus update lands LATER
            (after mini-game / agent-callback handling), so the episode + turn
            token still match here. This aborted proactive tick must not decay
            the charge before the user's own message is scored — that
            (user-driven) episode is the inline path's to update. owner stays
            USER through PROACTIVE_DONE (which only clears a PROACTIVE owner), so
            it is still observable at this point. A turn that DID reply
            (``replied=True``) genuinely spent the episode and still takes the
            replied retention even if the user fired back fast enough to flip the
            owner first; once the inline update actually lands the turn-token
            guard below takes over.
          * ``episode_token is None`` → the turn observed REGULAR (no active
            episode). There is nothing to cool, and a proactive tick must not
            erode the pre-entry accumulator the inline path is building toward
            ENTER — entering Focus is the inline path's job alone.
          * episode id changed → the inline path exited and/or entered a new
            episode while this proactive request was finishing.
          * turn count changed → the inline path recharged THIS same episode (a
            user message landed mid-flight). A stale proactive tick must not
            decay that fresh, user-driven charge.

        Decays with ``count_turn=False`` so a proactive tick never consumes a
        hard-cap turn slot (that bounds inline turns). Pure charge cooldown:
        privacy-independent, no snapshot. Best-effort; never blocks the exit.
        """
        # Idle-path counterpart to the inline reconcile — keeps the badge honest
        # on proactive-only stretches (idempotent).
        await self._reconcile_focus_indicator()
        from config import (  # live read
            FOCUS_MODE_ENABLED,
            FOCUS_IDLE_REPLIED_RETENTION,
            FOCUS_IDLE_SILENT_RETENTION,
        )
        try:
            if not FOCUS_MODE_ENABLED:
                # Master switch off → update_focus self-clears any residue.
                await self.state.update_focus(0.0)
                # Same-turn badge clear on a mid-episode switch-off (symmetric
                # with the inline path); the self-clear emits no FOCUS_EXIT.
                await self._reconcile_focus_indicator()
                return
            # User took over an UNDELIVERED turn: the user spoke during the
            # proactive request (USER_INPUT flipped owner→USER) and aborted it
            # before it said anything, but their inline focus update has not
            # landed yet, so the episode/turn token below would still match.
            # Hand the charge to the imminent inline turn instead of decaying it
            # with this aborted proactive tick.
            #   Gated on ``not replied``: a turn that DID commit a reply
            # (``replied=True``) genuinely spent the episode and must still take
            # the replied retention even if the user fired back fast enough to
            # flip the owner before this cooldown ran — owner==USER alone would
            # wrongly let quick replies after a successful proactive chat skip
            # their decay. (Once the inline focus update actually lands, the
            # episode/turn-token guard below takes over.)
            if not replied and self.state.owner is TurnOwner.USER:
                logger.debug(
                    "[%s] focus idle cooldown skipped: user took over an undelivered turn",
                    self.lanlan_name,
                )
                return
            # Only cool an episode this turn actually observed — never the
            # REGULAR pre-entry accumulator (entering Focus is inline-only).
            if episode_token is None:
                logger.debug(
                    "[%s] focus idle cooldown skipped: no active episode observed",
                    self.lanlan_name,
                )
                return
            # Race guard: skip if the focus state moved since this turn observed
            # it — a different episode (inline exited / re-entered) or a fresh
            # inline turn that recharged this same episode (turn count bumped).
            snap = self.state.snapshot()
            current_episode = snap.get("focus_episode_id")
            current_turn = snap.get("focus_turn_count")
            if current_episode != episode_token or (
                turn_token is not None and current_turn != turn_token
            ):
                logger.debug(
                    "[%s] focus idle cooldown skipped: focus state changed "
                    "(episode %s→%s, turn %s→%s)",
                    self.lanlan_name, episode_token, current_episode,
                    turn_token, current_turn,
                )
                return
            retention = (
                FOCUS_IDLE_REPLIED_RETENTION if replied
                else FOCUS_IDLE_SILENT_RETENTION
            )
            # score=0 + retention<1 ⇒ charge can only decay (never cross the
            # enter bar from REGULAR), so this can't ENTER Focus — only cool an
            # inline-driven episode toward the exit bar. count_turn=False keeps
            # it off the hard-cap turn budget.
            mode = await self.state.update_focus(
                0.0, retention_override=retention, count_turn=False,
            )
            logger.info(
                "[%s] 凝神 idle(cooldown replied=%s): charge=%s mode=%s",
                self.lanlan_name, replied,
                self.state.snapshot().get("focus_charge"), mode.value,
            )
            await self._push_focus_charge(self.state.snapshot().get("focus_charge"))
            # 若这次 cooldown 把 Focus 衰减出去(→REGULAR)，立即清 episode 残留：
            # proactive/greeting 的 prompt_ephemeral 会在下个 inline turn 之前就从
            # _conversation_history 构建、且不走 reconcile，必须赶在它前面清掉，否则
            # 会把刚结束的 Focus tool-call/reasoning 残留带进随后的 REGULAR 轮。
            await self._maybe_purge_focus_artifacts()
        except Exception as e:
            logger.warning("[%s] focus idle cooldown failed (degrading to regular): %s",
                           self.lanlan_name, e)
            try:
                if self.state.mode is CognitionMode.FOCUS:
                    await self.state.update_focus(0.0, topic_changed=True)
            except Exception as _exit_err:
                logger.debug("[%s] focus idle fail-exit also failed: %s",
                             self.lanlan_name, _exit_err)
