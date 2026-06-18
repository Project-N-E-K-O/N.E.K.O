# Focus / True-Name Modes ("凝神 / 真名")

Status: **v1 Focus — both trigger paths wired, shipped behind
`FOCUS_MODE_ENABLED` (default OFF).** This document specifies a two-tier
"elevated cognition" mechanism layered on top of the existing
proactive-chat / activity-tracker / memory pipelines. **v1 ships Focus
only**; True-Name is specified here for boundary clarity but is deferred
to v2. The switch defaults off because thresholds are not yet tuned
against real signal distributions and the thinking-on behaviour (inline
reasoning leaking into the streamed `content`, per-provider thinking cost)
is not yet end-to-end validated — enable per-provider after validation.

Implemented + wired (the thinking-on path is complete on BOTH triggers but
inert until `FOCUS_MODE_ENABLED` is turned on):
* `config/__init__.py` — all `FOCUS_*` tuning knobs (centralised).
* `config/prompts/prompts_focus.py` — vulnerability lexicon (7 locales) +
  topic-switch markers + `scan_vulnerability_keywords` / `detect_topic_switch`.
* `main_logic/session_state.py` — `CognitionMode`, `FocusThresholds`, the
  pure `_focus_decide` hysteresis, `SessionStateMachine.update_focus`,
  `FOCUS_ENTER` / `FOCUS_EXIT` events, reset/snapshot integration.
* `main_logic/activity/focus_scorer.py` — `FocusScorer` (**inline-only**:
  keyword + cadence; idle silence/open-thread signals were removed).
* `main_logic/core.py` — `FocusScorer` instance + `_focus_inline_decision`
  (Path A), and the split idle helpers `_focus_idle_thinking` (read-only:
  is the session in Focus → thinking_on) + `_focus_idle_cooldown(replied)`
  (post-turn charge decay). The inline path scores each user message and
  passes `thinking_on` into `stream_text`. The idle path does **not** score —
  a proactive turn never raises the charge; it only cools an active episode
  down, faster when it spoke (`FOCUS_IDLE_REPLIED_RETENTION`) than when it
  stayed silent (`FOCUS_IDLE_SILENT_RETENTION`). Entry/sustain is inline-only.
* `main_logic/omni_offline_client.py` — `stream_text(..., thinking_on=...)`
  threads `extra_body=None` (per-call thinking-on override) into `astream`;
  no LLM rebuild.
* `main_routers/system_router.py` — proactive Phase-2 generate sites (main
  stream / format-fix / BM25 regen) take `disable_thinking=not
  _focus_idle_thinking()`. The cooldown decay runs at the unified exit
  `_end_proactive`, but ONLY for turns that reached the Phase-2 idle decision
  (short-circuit replies — mini-game invite / break-reminder — never spend
  Focus), keyed on whether the turn delivered (`action=="chat"`). It decays
  with `count_turn=False` (a cooldown tick never spends a hard-cap turn slot)
  and is pinned to the episode the thinking decision observed (a stale tick
  won't decay a freshly inline-entered episode). Voice nudges never run a
  Focus reply, so they don't cool down either.
* `tests/unit/test_focus_mode.py` — focus unit tests (hysteresis / scorer /
  SM / lexicon / `thinking_on` threading / idle cooldown) + SM regression.

Pending: the `FOCUS_EXIT` memory subscriber (cross-process: a new
`memory_server` endpoint POSTed from a main-process `FOCUS_EXIT`
subscriber) and the frontend focus indicator.

Known tuning interaction: both paths tick the SAME leaky accumulator, but
only the inline path adds charge. Idle (proactive) ticks only decay it —
faster when the turn spoke (`FOCUS_IDLE_REPLIED_RETENTION`) than when it
stayed silent (`FOCUS_IDLE_SILENT_RETENTION`), so persistence is driven by
how often she speaks, not raw time. Because proactive fires on a schedule
and a turn that *speaks* also consumes one `FOCUS_HARD_CAP_TURNS` slot,
watch the cadence × retention interaction when tuning.

Naming: the user-facing fantasy terms are **凝神 (Focus)** and **真名
(True-Name)**. Internal identifiers use the English `focus` / `true_name`
to keep code ASCII and avoid CJK in identifiers. The everyday baseline is
**regular** mode.

## Why this exists

Project N.E.K.O.'s product thesis is **"90% 没心没肺 + 10% 神明降临"**
(90% light-hearted, 10% a moment where she *arrives*). Retention data:
D1≈30%, D7≈2.5%, D30≈1%. The bottleneck is not feature count or raw model
strength — it is that running heavyweight cognition *all the time*
dilutes the magic into noise. Today thinking is globally disabled to save
token/latency (see `config/providers.py::MODELS_EXTRA_BODY_MAP`, every
proactive call passes `disable_thinking=True`).

Focus is the mechanism that buys back the "10%": **signal-triggered,
user-invisible** entry into a thinking-on / stronger-model turn, so the
companion occasionally answers with a depth that lands as *"she suddenly
gets me"* — without the user ever pressing a button. True-Name is the
second-order moat layered on top once the user already loves her: it lets
her propose **destructive** rewrites of her own persona / memory and
dispatch sub-model tasks, gated behind explicit emotional consent.

### Design invariants (these are product law, not engineering taste)

1. **Focus is never user-toggled.** If the user can perceive "I enabled
   focus, therefore she got smarter," the magic dies. Entry is driven by
   signals, silently.
2. **Focus is never sticky across episodes.** Entering focus and staying
   there forever collapses the scarcity that makes "arrival" feel rare.
   It persists *within* an emotional episode via hysteresis, then exits.
3. **True-Name is consent, not auth.** It is *not* an admin backdoor /
   password. She self-detects a tension between her stored model of the
   user and the user's real state, then *proposes* a rewrite; the user
   confirms with a single word ("嗯" / "好"). No LLM-output channel can
   self-trigger it (the AI restating the consent phrase, proactive text
   containing it, or a tool return value containing it are all inert).
4. **Cultural invariants are NOT bypassable, even by True-Name.** The
   dehumanizing-term ban (`feedback_no_dehumanizing_terms`), the prompt
   watermark/delimiter rules, and i18n lockstep stay enforced by the
   linters. True-Name bypasses *safety/guardrail* surfaces, not the
   project's cultural rules.
5. **Focus is privacy-independent — it scores the user's MESSAGE, not the
   screen.** The inline trigger reads only `user_text` (vulnerability
   keywords) and the scorer's own reply-cadence buffer; it fetches no
   activity snapshot. Privacy mode governs SCREEN / app-state visibility
   only and must never gate Focus (or any other understanding-the-user
   feature) — reading the user's emotional state from what they typed is
   core to a companion. The idle path does not score and reads no snapshot
   at all — it is a pure charge cooldown, so it is privacy-independent too.
   See `docs/contributing/developer-notes.md` rule 6.

## Mode model

A new per-session field on the state machine
(`main_logic/session_state.py::SessionStateMachine`), orthogonal to the
existing `TurnOwner` / `ProactivePhase` axes (which track *who owns the
turn*; mode tracks *how hard she is thinking*):

```python
class CognitionMode(Enum):
    REGULAR   = "regular"
    FOCUS     = "focus"
    TRUE_NAME = "true_name"   # v2
```

```python
@dataclass
class ModeToken:
    mode: CognitionMode
    granted_at: float
    granted_by: str            # "signal" | "user_consent" | "system"
    # Focus: hysteresis state (see below). True-Name: hard 1-episode TTL.
    episode_id: str | None     # ties a focus run to one emotional episode
    # True-Name only:
    task_id: str | None        # no aimless True-Name — always task-bound
    snapshot_id: str | None    # persona+memory frozen before destructive writes
```

The SM gains `mode: CognitionMode = REGULAR` and a small hysteresis
scorer state (`signal_score: float`, `low_streak: int`,
`focus_turn_count: int`). Read path stays O(1)/lock-free, consistent with
the existing `is_proactive_preempted` contract. Mode transitions fire new
`SessionEvent`s (`FOCUS_ENTER` / `FOCUS_EXIT` / `TRUE_NAME_PROPOSE` /
`TRUE_NAME_GRANT` / `TRUE_NAME_DONE`) so memory + frontend can subscribe
without polling.

**Persistence:** Focus token lives only in `SessionStateMachine`
(in-memory; a fresh session starts REGULAR). True-Name token additionally
writes an audit record to disk, but a process restart **never**
auto-restores True-Name — restart ⇒ REGULAR, always.

## Signal scoring (when does Focus trigger)

Three layers, cheapest first. Layers 1–2 already have data sources in
`ActivitySnapshot` (`main_logic/activity/snapshot.py`) — minimal new
collection needed.

### Layer 1 — near-zero, always on (rule-derived, no LLM)
* **Silence-vs-baseline:** `seconds_since_user_msg` weighted against the
  user's typical activity for this hour/weekday (already have `hour` /
  `weekday` / `period`).
* **Cadence collapse:** recent K user-message lengths drop sharply vs
  baseline ("嗯。" "哦。" "知道了。").
* **Keyword/negation hits:** 累 / 难受 / 一个人 / 没意思 / 不想 + negation.
* **Time-anchored follow-ups:** an event the user previously mentioned is
  now due — reuse `UnfinishedThread` / `open_threads` and the
  tracker's follow-up queue (`main_logic/activity/tracker.py`).

### Layer 2 — mid-cost, rides the existing Phase-1 LLM pass
* **Emotion trajectory:** is `activity_scores` trending monotonically
  down across the last N turns (a *trend*, not a single-turn threshold).
* **Passive multimodal:** shared-image luminance/composition drops; a
  desktop screenshot shows résumé / leave-request / medical app.
* **Topic re-approach:** a topic grazed repeatedly but never opened
  (three deflected "换工作" mentions) — extends `open_threads`.

### Layer 3 — expensive, only paid once Layers 1+2 cross the bar
* **Focus decision:** combined L1+L2 score > `T_in` ⇒ **enter focus
  directly, this reply goes thinking-on, no second confirmation.** Magic
  requires the user not perceiving the gear-shift.
* **True-Name self-check (v2):** while already in focus + thinking, the
  model self-evaluates "is there significant tension between my stored
  persona/memory and what I'm now reading about the user?" If yes, it
  does *not* rewrite — it emits a proposal and waits for one-word consent.

The `FocusScorer` produces the score for the **inline** path only (keyword
+ cadence over the user's message). The **idle** path does not score: a
proactive turn is a pure cooldown that decays the charge (see below). So
the silence / open-thread / time-anchored-follow-up signals in Layers 1–2
above are **not** wired into the trigger — entry and sustain are driven
solely by what the user actually says.

## Two trigger paths

The current proactive pipeline only covers the **idle** window:
`SessionStateMachine.can_start_proactive` returns False while
`stream_text` is in flight and during cooldown. But in the 90/10 thesis,
the high-magic "she gets me" moment lands mostly on **the very message
where the user opens up** — which flows through `stream_text`, *not*
`proactive_chat`. So Focus needs two entry paths sharing one scorer:

| Path | Scenario | Entry point | Mounting site |
|---|---|---|---|
| **A: Inline focus** | user sends a message → score it → if over the bar, upgrade *this* reply | lightweight scorer before `stream_text` generation | `main_logic/core.py` `stream_text` entry (near the `last_user_activity_time` / USER_INPUT fire) |
| **B: Idle cooldown** | a Phase-2 proactive turn fires while in focus → keep it thinking-on, but decay the charge afterwards (never raise it) | thinking read before Phase-2 generate; decay at the proactive unified exit, gated to Phase-2 turns | `main_routers/system_router.py` — three Phase-2 sites take `disable_thinking = not _focus_idle_thinking()` (suppressed under vision); `_end_proactive` calls `_focus_idle_cooldown(replied=action=="chat", episode_token=...)` only when the Phase-2 idle decision was reached (count_turn=False, episode-pinned) |

### Path coupling — this is the key to "she lingers"

A focus episode is entered and re-charged ONLY by the inline path (the
user's own messages). A proactive turn that fires mid-episode does not
re-score or prop the charge up — it runs thinking-on (she's still in it)
and, once the turn finishes, decays the charge: faster if she actually
spoke (`FOCUS_IDLE_REPLIED_RETENTION`), slower if she stayed silent
(`FOCUS_IDLE_SILENT_RETENTION`). So how long she lingers is driven by how
often she *speaks*, not raw elapsed time.

This produces the loop *"after she arrives once, she follows up a turn or
two before letting go"* via the **decay rate**, not a more-sensitive idle
trigger: while the user keeps opening up, inline ticks keep the charge
above the exit bar; once they go quiet, successive proactive turns cool it
down until the session-level hysteresis exits. Persistence is the slow
idle retention, not sticky-by-flag.

(An earlier draft proposed instead *dropping the idle trigger threshold*
while in focus — `T_idle = in_focus ? T_base * 0.4 : T_base` — so she'd
re-approach after a shorter silence. That was rejected in favour of the
cooldown model: the idle path must never make Focus *more* likely, only
let it fade. The idle-threshold-drop sub-feature is not implemented.)

These two mechanisms are orthogonal: hysteresis decides *when the
session-level focus mode exits*; path coupling decides *how sensitive the
idle trigger is while focus is active*.

## Exit (hysteresis + hard cap)

Focus exit uses a Schmitt-trigger pattern, mirroring entry but inverted —
and like entry, **invisible to the user** (she eases from a heavy
"今天太累了…" answer down to a light "那要不要点个外卖？", which reads as
*"she stayed with me through the mood,"* not *"she abruptly went dumb
again"*).

* **Asymmetric thresholds:** enter at `T_in` (high, ~1–2× / week water
  line); exit only when score < `T_out` (low, ≈ `0.3 × T_in`) for `K`
  consecutive turns.
* **Hard cap:** at most `M` turns in focus before a forced exit (default
  `M=8`), so a stuck-on focus can't drag the magic into the everyday.
* **Explicit topic-switch exit:** a clear topic change ("对了，今天天气…")
  exits immediately.

Starting knobs: `K=3`, `M=8`, `T_out=0.3·T_in`. All A/B-tunable.

## Memory piggyback (the "tidy up while she's down here" idea)

Focus already pays for thinking-on + the stronger model. Several memory
stations (`reflection.synthesize`, `persona.resolve_corrections`,
`facts` Stage-2, `recent.review_history`) are *also* thinking-on LLM
calls already running on idle cadences in `app/memory_server.py`. An
**emotional episode is a natural reflection boundary** — far better input
for `synthesize_reflections` than an arbitrary idle-timer slice, because
it's a complete, vulnerable conversational arc. So Focus-exit becomes the
trigger that batches the relevant memory maintenance.

### This redraws the Focus/True-Name boundary

| | Focus (v1) | True-Name (v2) |
|---|---|---|
| **Memory class** | **Additive / refinement** | **Destructive / retraction** |
| Operations | append (ban-list add, facts add, recent priority-mark), synthesize (reflection, persona promote_merge), small persona `resolve_corrections` | retract stale facts, override fundamental persona, force-forget, sub-agent dispatch |
| One-liner | only makes memory *finer / more accurate*; never overturns an existing conclusion | makes the changes the existing memory system can't sustain on its own |

This makes True-Name's scarcity self-justifying: you only need it when
the *additive* path can no longer cope (accumulating contradictions /
user explicitly says "忘了之前那个版本的我").

### Timing — inline vs deferred

Not everything can wait for episode-end; split by "can it tolerate a
deferred commit?":

| Operation | Timing | Why |
|---|---|---|
| Ban-list add (`user_directives.record` / `record_from_text`) | **Inline** (within episode) | "别再提 XX" — if the next AI line still mentions it, ruined. Must take effect this turn. |
| Persona emergency override (explicit "我不喜欢你这样") | **Inline** | same — next line must already avoid it |
| Recent priority-mark | **Inline** (flag only, not body) | marks this slice as an emotional episode for the episode-end input |
| Reflection synthesize (`synthesize_reflections`) | **Deferred** (episode-exit batch) | a single turn doesn't show the full arc |
| Persona refine / `promote_merge` | **Deferred** | avoid premature commit mid-arc |
| Facts update (`aextract_facts_and_detect_signals`) | **Deferred** | same |
| Ban-list re-audit (`purge_expired`) | **Deferred** | deletions shouldn't happen while the user is vulnerable |

Mounting: the `FOCUS_EXIT` transition emits an
`EmotionalEpisodeFinished` event carrying the episode's message slice +
`episode_id`. A memory-side subscriber feeds that slice into the existing
station functions (all of which are externally kickable — confirmed) by
**reusing the existing scheduler**, just sourced from the episode
boundary instead of the idle timer.

### Known race (flagged, resolved at implementation time)

After `FOCUS_EXIT` kicks deferred maintenance, the pipeline may still be
mid-`synthesize` (thinking, several seconds) when the user immediately
sends another message. Resolution direction: **don't block the reply;
inject the in-flight pipeline's partial result as ephemeral context into
that turn's system prompt** (via the existing `prompt_ephemeral` channel),
and let the pipeline reconcile on commit using the memory module's
existing lock / conflict-resolution. Detail deferred — not a blocker for
sign-off.

## Mechanism summary: request / escalation / regression

| Stage | Focus | True-Name (v2) |
|---|---|---|
| **Request** | L1+L2 signals auto-trigger, user-side silent | during focus, model self-detects persona/memory tension → user one-word emotional consent |
| **Escalation** | **drop the thinking-off `extra_body`** for this turn (let the provider default run free); switch to the stronger conversation model | + unlock `memory.force_write` / `persona.override` / `subagent.dispatch`; force a persona+memory snapshot; tag all writes `[TRUE_NAME task_id]` to a separate audit log |
| **Regression** | hysteresis/hard-cap/topic-switch → auto exit; fire `EmotionalEpisodeFinished` | one `task_id` done ⇒ exit; **24h emotional-undo window** for the user to revert this arrival's rewrites |

Two regression rules unique to each tier:
* **Focus never sticky** — every user message is an independent scoring
  decision; the next arrival is a separate event.
* **True-Name keeps a 24h emotional-undo window** — this is a deliberate
  *exception* to `feedback_decided_path_no_undo`, because persona/memory
  rewrites are deep enough to warrant a grace window before becoming
  irreversible.

### Thinking-on = drop the override (escalation detail)

No reverse map. Today `config/providers.py` maps each model to a
*thinking-off* `extra_body` (`thinking_budget: 0`, `thinking:
{type: disabled}`, `enable_thinking: false`, `reasoning.effort: "none"`,
…). Focus simply **does not inject that override** — passing
`extra_body=None` skips the auto-resolved thinking-off body and lets the
provider run on its own default (which, for thinking-capable models,
means thinking comes back on). This path already exists:
`main_routers/system_router.py::_make_llm` does
`if not disable_thinking: kw["extra_body"] = None`. Focus just sets
`disable_thinking=False` on the existing knob — nothing new to build in
`config/providers.py`.

The deliberate trade-off: we don't pin a thinking *budget/level* in focus
("放飞自我" — let it run free). If a provider's default thinking is too
expensive/slow in practice, that's a per-provider tuning concern handled
later, not a reason to build an inverse table up front.

## Implementation map (v1 = Focus only)

1. `config/providers.py` — **no change.** Focus reuses the existing
   `extra_body=None` skip path (`_make_llm(disable_thinking=False)`); the
   inline `stream_text` path needs the same "skip the thinking-off body"
   wiring exposed where it builds its LLM.
2. `main_logic/session_state.py` — add `CognitionMode`, `ModeToken`,
   `mode` field, hysteresis scorer state, the new `SessionEvent`s, and
   `enter_focus` / `exit_focus` transitions (O(1) read path preserved).
3. `main_logic/activity/` — `FocusScorer` (shared L1+L2 scoring over the
   existing `ActivitySnapshot`); no public-surface change to
   `get_snapshot`.
4. `main_logic/core.py` — Path A: score before `stream_text` generation;
   on over-bar, build the LLM with thinking-on + stronger model.
5. `main_routers/system_router.py` — Path B: replace the hard-coded
   `disable_thinking=True` at the proactive **Phase-2** generate sites
   (main stream / format-fix regen / BM25 regen) with `not
   _focus_idle_thinking()` (read-only, suppressed under a vision model), and
   decay the charge once at `_end_proactive` via `_focus_idle_cooldown`. The
   idle path never raises the charge.
6. `app/memory_server.py` (+ memory stations) — subscribe to
   `EmotionalEpisodeFinished`; route the episode slice into
   `synthesize_reflections` / `resolve_corrections` / facts / ban-list
   re-audit, reusing the existing scheduler plumbing.
7. Frontend — a subtle focus indicator (thinking glow). **No toggle.**
   (True-Name's countdown badge is v2.)
8. i18n — any new user-visible strings updated across all 8 locales in
   lockstep (`feedback_i18n_lockstep_admin_approve`); new prompt strings
   go in `config/prompts/prompts_*`.

## Open knobs (A/B-tunable, not hard-coded decisions)

* `T_in` — focus entry water line. **Start tight** ("1–2× / week"),
  collect data, loosen later for under-triggered users. Magic-dilution vs
  magic-burial is a tuning curve, not a one-shot choice.
* `T_out`, `M` — exit hysteresis (`FOCUS_CHARGE_EXIT` `0.3`, hard cap `8`).
* `FOCUS_CHARGE_RETENTION` (inline `0.5`) + idle cooldown retentions
  `FOCUS_IDLE_SILENT_RETENTION` (`0.95`, she just waited) and
  `FOCUS_IDLE_REPLIED_RETENTION` (`0.6`, she spoke) — these set how many
  proactive turns Focus lingers, weighted by how often she actually speaks.
* **Topic-boundary detection** (gates hard-exit + inline ban-list): cheap
  per-turn classifier model vs keyword/syntax heuristic. *Recommendation:
  heuristic for v1 (zero-cost), upgrade to classifier if false exits hurt.*
* **Path-A scorer placement**: before generation (+~200ms, distilled
  small model) vs parallel/mid-stream switch. *Recommendation: before
  generation — the ~200ms is acceptable precisely because a focus turn is
  meant to "think before answering."*

## Non-goals / explicitly deferred to v2

* True-Name in any form (destructive memory/persona rewrite, force-forget,
  sub-agent dispatch, consent ritual, 24h undo, audit log, countdown
  badge). Rationale: today's D7=2.5% bottleneck is "not magical enough" —
  Focus alone addresses it; True-Name is the second-order moat that only
  *works* once the user already loves her (nobody consents to a rewrite of
  themselves at D7=2.5%). Ship Focus, pull D7 to 15–20%, then build
  True-Name as the D30 weapon.
* Sub-model task dispatch and its non-inheritance-of-`ModeToken` security
  model (sub-agents see only the task prompt; output is reviewed by the
  main model before landing) — specified at v2.
