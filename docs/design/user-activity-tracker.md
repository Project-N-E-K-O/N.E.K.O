# User Activity Tracker

Status: **v1 (rules-primary, LLM advisory)** — implemented in
`main_logic/activity/`. Authored during the proactive-chat overhaul.
The rule-based state machine is the authority for all gating
decisions (propensity / source filtering); an emotion-tier LLM is
called on a 20s cadence + on-demand for the
``activity_scores / activity_guess / open_threads`` enrichment fields,
which are advisory context only. Failures of the LLM degrade silently
to rule-only behaviour. Subsequent contributors may freely extend
keyword tables, add signal sources, or tune the LLM enrichment; the
public surface (`UserActivityTracker.get_snapshot`) is the contract
that should not change without a follow-up doc.

## Why this exists

The proactive-chat backend used to decide whether to speak based on a
binary `last_user_activity_time < 10s` check and the LLM's own judgment.
Two failure modes resulted:

1. **Pending reflections almost never surfaced** — the LLM had no
   contextual reason to call them up, and every prompt rule pushed it
   toward `[PASS]` or topical novelty.
2. **Either constant chatter or total silence** — the model couldn't
   distinguish "user is in deep focus" from "user is bored on Bilibili",
   so it either interrupted important work or stayed quiet during obvious
   chat windows.

The tracker injects a structured snapshot of *what the user is doing
right now* into Phase 2 of proactive chat, so the prompt can shape
behaviour by user state rather than blanket rules. The snapshot
combines:

* **Rule-derived signals** (state, propensity, reasons, dwell, idle,
  unfinished_thread, etc.) — pure heuristic, no LLM. Window titles,
  foreground process, CPU, voice RMS, conversation timestamps go in,
  one of nine states comes out.
* **Emotion-tier LLM enrichment** (activity_scores, activity_guess,
  open_threads) — advisory only, cached, fail-silent. Lets the
  proactive AI see soft cross-state scores and a one-sentence
  narrative when the cheap rules can't capture nuance.

The rules path is authoritative: propensity and source filtering are
always rule-derived. LLM enrichment never gates anything; it just adds
context the proactive prompt can choose to weigh.

## Public surface

```python
from main_logic.activity import UserActivityTracker

tracker = UserActivityTracker(lanlan_name='xiao8')

# Hooks called when signals occur (synchronous, never block)
tracker.on_user_message()
tracker.on_ai_message()
tracker.on_voice_mode(active=True)
tracker.on_voice_rms()

# Read by proactive-chat code paths
snapshot = await tracker.get_snapshot()
print(snapshot.state, snapshot.propensity, snapshot.propensity_reasons)
```

The snapshot is a frozen dataclass — see `main_logic/activity/snapshot.py`
for all fields.

## State taxonomy

| State | Trigger | Propensity | Behaviour notes |
|---|---|---|---|
| `away` | System idle ≥ 15 min | `open` | Normal proactive — frontend backoff handles frequency |
| `stale_returning` | Just back from `away` (≤ 60s window) | `greeting_window` | Encourage greeting, allow 1d+ reminiscence |
| `gaming` | Game window in foreground (subcategory='game') | `restricted_screen_only` | Only screen-derived chatter; no externals, no reminisce |
| `focused_work` | Work window + ≥ 90s dwell + recent input | `restricted_screen_only` | Same as gaming |
| `casual_browsing` | Entertainment window + ≥ 30s dwell | `open` | Encourage external material |
| `chatting` | Communication app in foreground | `open` | Allow externals, careful with screen comments |
| `voice_engaged` | Voice mode + RMS active in last 8s | `open` | Match voice flow; short replies; careful introducing externals |
| `idle` | At computer but no clear category | `open` | Continuation > reminisce > externals |
| `transitioning` | ≥ 5 distinct window observations in last 5 min | `open` | Screen still allowed; source-weight layer suppresses externals |

`away` deliberately keeps `open` propensity — the user explicitly
clarified that long absences just mean "speak less often" (handled by
the existing frontend backoff curve in `static/app-proactive.js`),
not "don't speak". The greeting machinery in `core.py:trigger_greeting`
uses a separate path on first reconnect.

## Propensity directives (what Phase 2 sees)

| Propensity | Allowed channels | Recommended emphasis |
|---|---|---|
| `closed` | (reserved; no longer emitted) | — |
| `restricted_screen_only` | Screen only | Avoid duplication with last 1h; no externals; no reminiscence |
| `open` | All channels | Reminiscence and externals both available |
| `greeting_window` | All channels | Encourage gentle greeting + 1d+ reminiscence |

Phase 2 prompt rewrites map these directives into language directives
(see `config/prompts_proactive.py` for the post-revision prompt).

## Architecture

```text
main_logic/activity/
├── __init__.py            Public exports (UserActivityTracker, snapshot types)
├── snapshot.py            ActivitySnapshot / WindowObservation / Propensity types,
                           state-to-propensity mapping
├── system_signals.py      SystemSignalCollector singleton — Win GetLastInputInfo,
                           psutil CPU rolling avg, active window/process polling,
                           nvidia-smi GPU utilisation
├── state_machine.py       ActivityStateMachine — pure-rules classifier with dwell
                           tracking, stale-recovery sticky window, transitioning
                           detection, gaming-by-GPU fallback
├── llm_enrichment.py      Emotion-tier LLM calls (activity scores + guess +
                           open_threads detection) with i18n prompt templates
                           and JSON parsing
└── tracker.py             UserActivityTracker — per-character orchestrator,
                           hooks, conversation buffer, enrichment caches,
                           20s activity_guess background loop

config/
└── activity_keywords.py   Keyword library (~2700 lines): 943 title rows,
                           692 process names, 64 launchers, 518 browser domains,
                           plus classifier helpers
```

Dataflow:

```text
                ┌────────────────────────────┐
                │ SystemSignalCollector      │
                │  (process singleton)       │
                │  - GetLastInputInfo        │
                │  - psutil.cpu_percent      │
                │  - GetForegroundWindow     │ poll every 5s
                │  - psutil.Process(pid)     │
                └─────────────┬──────────────┘
                              │ SystemSnapshot
                              ▼
   user_msg / ai_msg / ┌──────────────────────────────┐
   voice_mode / rms  ──▶│ UserActivityTracker          │ (per character)
                       │  └─ ActivityStateMachine     │
                       │     - dwell tracking          │
                       │     - state classifier        │
                       │     - stale-recovery sticky   │
                       └─────────────┬─────────────────┘
                                     │ ActivitySnapshot
                                     ▼
                       ┌────────────────────────────────────────┐
                       │ proactive_chat (Phase 1+2)             │
                       │  - bypass unified LLM if propensity ==  │
                       │    restricted_screen_only               │
                       │  - inject state section into Phase 2    │
                       │    system prompt                        │
                       └────────────────────────────────────────┘
```

## Signal sources (all heuristic, all rules)

The tracker pulls from many channels — each is best-effort, and any
single one being unavailable degrades gracefully without breaking the
others.

**System level (process singleton, poll every 5s)**

* `GetLastInputInfo` — Windows API for keyboard/mouse system-wide idle.
  The only reliable way to detect "user has stepped away" without input
  hooks. Survives the user being on another app the tracker doesn't
  recognise.
* `psutil.cpu_percent()` — 30s rolling average + latest instant. Used
  only as a confirmation signal; we deliberately don't gate on
  "low CPU" (too unreliable) and only mention high CPU when relevant.
* `GetForegroundWindow` + `GetWindowThreadProcessId` + `psutil.Process` —
  active window title + owning process. The bulk of categorisation runs
  on this pair.
* `nvidia-smi` (subprocess, every other tick) — first-GPU utilisation
  percentage. Powers the gaming-by-GPU fallback (small/indie/new game
  whose title isn't in the keyword DB). Probe runs once at startup; if
  it fails we mark GPU signal unavailable and stop polling. Non-NVIDIA
  hosts (AMD, Intel iGPU) get `gpu_utilization=None` — gaming detection
  falls back to keyword matching only there.

**Per-character (event-driven, zero cost)**

* `on_user_message(text=...)` — driven from two sites in `main_logic/core.py`:
  the voice-mode `handle_input_transcript` path and the text-mode
  WebSocket entry inside `_process_stream_data_internal`. Both pass the
  user's input text. Feeds `seconds_since_user_msg`, the focused-work
  "recent input" check, and the conversation buffer that emotion-tier
  LLM enrichment reads. Bumps `_conv_seq` so `open_threads` cache
  invalidates.
* `on_ai_message(text=...)` — driven at AI turn end from `_emit_turn_end`
  (regular replies), `handle_proactive_complete` (agent direct-reply path),
  and `finish_proactive_delivery` (`/api/proactive_chat` success path).
  Surfaces as `seconds_since_ai_msg`, runs the question heuristic for
  `unfinished_thread`, also bumps `_conv_seq`.
* `on_voice_mode(active)` — driven at voice session start/stop.
* `on_voice_rms()` — driven from VAD / RMS-threshold detection. The
  state machine treats voice as engaged only with a recent RMS within
  `VOICE_ACTIVE_WINDOW_SECONDS` (8s).

**Derived signals**

* Window dwell time — implicit from state machine internals.
* Window switch rate over last 5 min — used for `transitioning` detection.
* State age — exposed as `state_age_seconds` for the prompt.
* Time-of-day context — `hour`, `weekday`, `period` (`morning` /
  `afternoon` / `evening` / `night`).

## Classification rules

`config/activity_keywords.py` ships four data tables. Match priority is
**gaming > work > communication > entertainment**. Within a single
category, first hit wins so put more-specific entries before
more-generic ones.

```python
# Title classification (apps in non-browser windows)
GAME_TITLE_KEYWORDS         # [(canonical, [aliases])]
GAME_LAUNCHER_TITLE_KEYWORDS # game-launcher windows (weaker signal)
WORK_TITLE_KEYWORDS          # [(canonical, [aliases], subcategory)]
COMMUNICATION_TITLE_KEYWORDS
ENTERTAINMENT_TITLE_KEYWORDS

# Process classification (psutil Process.name())
GAME_PROCESS_NAMES           # [exe_name]
GAME_LAUNCHER_PROCESS_NAMES
WORK_PROCESS_NAMES           # [(exe_name, subcategory)]
COMMUNICATION_PROCESS_NAMES
ENTERTAINMENT_PROCESS_NAMES

# Browser-domain classification (substring inside browser title)
WORK_BROWSER_DOMAIN_KEYWORDS    # [(domain, subcategory)]
COMMUNICATION_DOMAIN_KEYWORDS
ENTERTAINMENT_DOMAIN_KEYWORDS
```

Match semantics:

* All matching is case-insensitive.
* Aliases containing ASCII letter/digit are wrapped with regex `\b`
  word boundaries. `COD` matches `Call of Duty Modern Warfare` but
  doesn't match `Code.exe` or `cod.txt`.
* Pure-CJK aliases (e.g. `原神`) skip word boundaries and use plain
  substring — Unicode boundary semantics don't apply naturally here.
* `is_browser_process` uses **exact basename** match (case-insensitive)
  to avoid `tor.exe` substring-matching `Calculator.exe`.

For browser windows, the tracker first runs `classify_browser_title`
against the domain tables (URL/page title is more telling than the bare
browser name), then falls back to `classify_window_title` to catch
branded SaaS apps where the title shows the app name (`Notion`, `Figma`).

## State machine details

Tunables live at the top of `main_logic/activity/state_machine.py`:

| Constant | Default | Meaning |
|---|---|---|
| `AWAY_IDLE_SECONDS` | 900 (15 min) | System input idle → `away` |
| `STALE_RECOVERY_SECONDS` | 60 | Window after `away→active` flagged as `stale_returning` |
| `VOICE_ACTIVE_WINDOW_SECONDS` | 8 | Voice RMS recency for `voice_engaged` |
| `FOCUSED_WORK_MIN_DWELL_SECONDS` | 90 | Dwell on work window before `focused_work` fires |
| `FOCUSED_WORK_RECENT_INPUT_SECONDS` | 300 | "Recent input" window for focused-work |
| `CASUAL_BROWSING_MIN_DWELL_SECONDS` | 30 | Dwell on entertainment before `casual_browsing` fires |
| `WINDOW_SWITCH_TRANSITION_THRESHOLD` | 5 | Window switches in lookback for `transitioning` |
| `WINDOW_HISTORY_LOOKBACK_SECONDS` | 300 | Switch-rate window |
| `TRANSITION_RECENT_WINDOW_SECONDS` | 30 | `transitioned_recently` flag duration |
| `UNFINISHED_THREAD_WINDOW_SECONDS` | 300 (5 min) | How long an open AI question stays followable |
| `UNFINISHED_THREAD_MAX_FOLLOWUPS` | 2 | Hard cap on follow-ups per thread |
| `GAMING_GPU_THRESHOLD_PERCENT` | 60 | GPU % required for gaming-by-GPU fallback |
| `GAMING_GPU_MAX_IDLE_SECONDS` | 60 | Max input idle for gaming-by-GPU fallback to fire |

Stale recovery: when state goes `away → anything-else`, the machine
sets `_stale_returning_until = now + STALE_RECOVERY_SECONDS`. Any
snapshot read inside that window emits `stale_returning` instead of
the underlying state, so the greeting opportunity gets a chance even
if the user's first action was opening their IDE.

Transitioning is intentionally low-priority: the user explicitly
clarified that screen-based chat is allowed in basically any state,
including transitioning. Only the source-weight layer (`reminiscence`
channel decay) should suppress external sources during transitions.

## Examples

Snapshot during a coding session:

```text
state: focused_work | propensity: restricted_screen_only
state_age_seconds: 0.0 (just classified)
previous_state: idle
transitioned_recently: True
stale_returning: False
propensity_reasons: ['专注 VS Code 已 200s']
system_idle_seconds: 2.0
cpu_avg_30s: 42% | cpu_instant: 58%
seconds_since_user_msg: 30s
seconds_since_ai_msg: 90s
voice_recent_rms_active: False
voice_mode_active: False
hour: 16 | weekday: 1 | period: afternoon
active_window: VS Code (work/ide), title='proactive_chat.py - lanlan - Visual Studio Code'
```

Phase 2 prompt would receive `propensity=restricted_screen_only` and
emit only screen-derived chatter, skipping external news / music / meme
material entirely.

Snapshot during casual browsing on Bilibili:

```text
state: casual_browsing | propensity: open
propensity_reasons: ['浏览娱乐：bilibili.com']
active_window: bilibili.com (entertainment/video), is_browser=True
system_idle_seconds: 3.0
seconds_since_user_msg: 1200 (haven't chatted in 20 min)
```

Phase 2 prompt would receive `propensity=open` and feel free to
introduce a fresh topic — news, music recommendations, memes, or a
gentle reminisce about something from a few days ago.

Snapshot 30 seconds after returning from being away 20 min:

```text
state: stale_returning | propensity: greeting_window
propensity_reasons: ['用户刚从离开状态回来']
state_age_seconds: 28
previous_state: away
stale_returning: True
active_window: Slack (communication/work_im)
```

Phase 2 receives `greeting_window` — encouraged to start with a warm
"hey, you're back" rather than diving into a topic, and may naturally
mix in an older reminiscence (1d+ ago) since the conversation has had
a clean break.

## Extending the keyword library

To add a new game:

```python
# config/activity_keywords.py — within GAME_TITLE_KEYWORDS list
('Some New Game', ['Some New Game', 'SNG', '某游戏', '某遊戲', 'ある新作', '어떤 새 게임']),

# Within GAME_PROCESS_NAMES list (only if verified from Steam DB / official)
'SomeNewGame.exe',
```

Always supply localised aliases for the title (EN / 简 / 繁 / JP / KR
where applicable). Process names should only be added when verified —
fabricated executables degrade matching quality. When unsure, keep
just the title row.

To add a new work app subcategory: pick from the existing set
(`ide`, `note`, `office`, `pdf`, `design`, `3d_cad`, `gamedev`,
`science`, `latex`, `terminal`, `db`, `devops`, `vcs`, `*_web`) — the
state machine doesn't care about the specific subcategory beyond
logging, but consistency helps future grouping.

To add a new entire category (e.g. `creative` for artists/musicians):
update `ActivityCategory` in `config/activity_keywords.py`, add a new
data table, register it in `_build_title_table` / `_build_process_table`,
and decide its priority slot. Then add a new state in
`main_logic/activity/snapshot.py` (`ActivityState`) and the matching
classifier branch in `main_logic/activity/state_machine.py`. Lastly,
choose a propensity for the new state in `_STATE_TO_PROPENSITY`.

## Unfinished thread mechanism

When the AI's last reply ends with a question (heuristic: `?` / `？` in
the last 60 chars, or sentence-final CN particle `吗` / `呢` / `么` /
`吧`), the tracker opens a 5-minute follow-up window. The snapshot
exposes this as `ActivitySnapshot.unfinished_thread`:

```python
@dataclass(frozen=True, slots=True)
class UnfinishedThread:
    text: str                # tail of the AI message that opened the thread
    age_seconds: float       # how long ago
    follow_up_count: int     # times we've already followed up
    max_follow_ups: int      # hard cap (UNFINISHED_THREAD_MAX_FOLLOWUPS)
```

The Phase 2 prompt has an explicit override: when `unfinished_thread`
is present in the state section, the AI may continue that thread
([CHAT]) regardless of the propensity — even in `gaming` /
`focused_work` where external sources are otherwise filtered out.

Lifecycle:

* AI message tripped the question heuristic → record opens with
  `follow_up_count=0`.
* Each successful proactive emission while the record is active calls
  `tracker.mark_unfinished_thread_used()` → counter increments.
* Counter reaches `UNFINISHED_THREAD_MAX_FOLLOWUPS` → record auto-clears.
* User sends a message → record clears (implicit acknowledgement).
* 5 minutes elapse → record auto-expires.

Because the override is gated by *snapshot inclusion*, exhaustion is
silent: once the cap or window kicks in, the prompt simply doesn't
mention the thread anymore. No need for "you may not follow up" rules
in the prompt — what the model can't see, it can't violate.

## GPU-fallback gaming detection

When the active window's category is `unknown` (e.g. a small / indie /
new title not in our keyword DB) and GPU utilization is sustained
above 60% with the user still interacting (idle ≤ 60s), the state
machine flags the state as `gaming` anyway. Reasons in the snapshot
include both `state_gaming` and `gaming_by_gpu` so the source is
visible in the prompt.

Guards prevent obvious false positives:

* Work-classified windows (IDE / video editing / ML notebooks) are
  exempted — GPU load there is expected, not a gaming signal.
* Long idle (background rendering, AFK farming) is exempted — we want
  to detect *active engagement*, not background load.
* Multi-GPU systems use only the first GPU's utilisation; secondary
  GPUs frequently run unrelated decoders and would false-positive.

GPU signal degrades gracefully on non-NVIDIA hosts: the first probe to
`nvidia-smi` fails, the collector flips `_gpu_available` off for the
process lifetime, and gaming detection runs purely on keywords.

## Reason localisation

`propensity_reasons` are stored as `(code, params)` tuples — language
agnostic. Rendering happens at `format_activity_state_section` time
via the `ACTIVITY_REASON_TEMPLATES` dict (zh / en / ja / ko / ru) in
`config/prompts_activity.py`. This keeps state-machine code free of
i18n concerns and avoids re-emitting the snapshot when the user's
prompt language changes. The other three nested-dict tables for the
activity tracker (`ACTIVITY_STATE_LABELS`,
`ACTIVITY_PROPENSITY_DIRECTIVES`, `ACTIVITY_STATE_SECTION_LABELS`)
live alongside it for the same reason — the project i18n convention
puts every translatable string under `config/prompts_*` so adding a
new language is a single-directory pass.

## Emotion-tier LLM enrichment

Three advisory fields on `ActivitySnapshot` are populated by emotion-tier
LLM calls — small, cheap model invocations that add semantic context
the rule layer can't produce:

* `activity_scores: dict[str, float]` — soft scores (0-1, independent
  per state) across `gaming` / `focused_work` / `casual_browsing` /
  `chatting` / `voice_engaged` / `idle`. Lets the prompt see "user is
  mostly focused-work but with some chat happening" instead of a single
  hard label.
* `activity_guess: str` — a one-sentence narrative description.
  Reads like "主人在 VS Code 里调试 proactive_chat，刚才发了一条求助"
  — gives the proactive AI character a story-shaped picture of what
  the user is up to, vs. a table of structured fields.
* `open_threads: list[str]` — short phrases describing topics that
  were raised but not closed. Catches cases the rule-based question
  heuristic misses (AI promises, abandoned user threads, agreed plans
  without follow-through).

The hard rule-based `state` and `propensity` fields remain authoritative
for source filtering and propensity decisions. Enrichment is purely
additive — even when the LLM disagrees with the rules, the proactive
prompt sees both and reconciles. If the LLM is unavailable / down /
times out, the cache stays on its previous value and the prompt simply
omits unrendered fields. No load-bearing path depends on enrichment.

### Lifecycle

* `activity_guess` + `activity_scores` (paired, single LLM call):
  driven by a 20s background loop on the tracker. The loop short-circuits
  when:
  - state signature unchanged AND no new user message since last compute, OR
  - `state == 'away'` (no point describing absence), OR
  - last successful call < 30s ago (anti-thrash).

  The state signature combines `state`, active-window canonical name,
  active-window subcategory, and a coarse idle bucket. A typical
  steady-state session burns the LLM ~once per behavioural shift,
  not once per tick.

* `open_threads`: lazy / on-demand. The proactive_chat code path calls
  `tracker.kickoff_open_threads_compute(lang=...)` near the top of
  Phase 1, in parallel with source-fetch tasks. Freshness is keyed by
  a unified conversation revision (`_conv_seq`, bumped by both
  `on_user_message` and `on_ai_message` — AI-side promises and abandoned
  mid-sentences open new threads too). Within the same revision,
  repeated kickoffs are no-ops. The compute task captures `_conv_seq`
  before the LLM call and discards its result if the seq advanced
  during the await — preventing stale completions from overwriting
  caches built on a newer buffer.

  By the time Phase 2 reads `get_snapshot`, the cache is either fresh
  (LLM came back fast enough) or still on the previous value (LLM
  slow → fall back to last-known). Either way the prompt has something
  to render, and the next proactive tick will pick up the latest result.

### Cost model

The `emotion` model tier is the cheapest model in the codebase's
provider config — designed for small / fast / structured tasks. Two
calls per behavioural shift (one for guess+scores, one for open_threads
when user spoke), each ~300-500 input tokens + ~100-150 output tokens.
For a typical session that's a few cents of model spend per hour, much
less than the proactive Phase 2 itself.

If `emotion` config is missing / API down / timeout: enrichment fields
stay empty / cached, the formatter omits them, the prompt still works.
Failure modes are silent and non-load-bearing.

## Token budget

The state-section + Phase 2 prompt structure is deliberately compact
because Phase 2 already carries character_prompt / memory_context /
screenshot, all of which dwarf any structural overhead. Empirical
counts (with placeholders only, character/memory excluded):

| Component | tokens (zh) | notes |
|---|---|---|
| state_section, minimal (idle, no enrichment) | 34 | 3 lines after header |
| state_section, mid (rule-classified + thread) | 93 | + reasons + recency + thread |
| state_section, full (with LLM enrichment) | 173 | + scores + narrative + open_threads |
| Phase 2 main prompt static (excluding state_section) | 194 | decision frame + headers |

A typical full-enriched Phase 2 invocation adds ~370 tokens of
structural overhead on top of the dynamic content (character / memory /
screen / external sources). For comparison, a screenshot occupies
~5-15K image tokens, and character_prompt averages 1-2K — so the
tracker enrichment is well under 5% of the total request.

To slim further if needed:
- Drop `activity_guess` (saves ~30-60 tokens, loses LLM narrative)
- Drop `open_threads` (saves ~40-100 tokens, loses semantic threads)
- Hide the rule-reason line when state is idle (saves ~10-20 tokens)
- Switch to `state` only without the localized label (saves ~5 tokens)

None of these are urgent. The decision-frame in Phase 2 was already
compressed from a 9-rule list (~462 zh tokens) to a 5-step priority
list (~194 zh tokens).

## Remote deployment

The whole tracker is built around the assumption that the Python
backend has access to the *user's* machine — `GetForegroundWindow`,
`GetLastInputInfo`, `psutil` and `nvidia-smi` are all OS-local APIs.
When the backend runs on a remote server (cloud VM) and the user
accesses via a different machine (different PC, mobile shell), those
APIs report the *server's* state, which is useless or actively
misleading.

Two layers of accommodation:

### 1. Degraded mode (automatic + env override)

`SystemSignalCollector` flags itself `os_signals_available=False`
in any of these cases:

- Backend platform is non-Windows (Linux/macOS server) — `pygetwindow`
  doesn't ship; idle/window APIs unavailable.
- Backend is Windows but `pygetwindow` isn't installed.
- Env var `NEKO_ACTIVITY_TRACKER_REMOTE=1` (or `ACTIVITY_TRACKER_REMOTE=1`)
  is set — covers the Windows-remote edge case where the local OS APIs
  *would* technically work but report data about the server rather
  than the user. Set this on any deployment where the backend isn't
  on the user's actual machine.

In degraded mode the collector skips the OS syscalls entirely (no
`GetForegroundWindow`, no `nvidia-smi`) and emits a minimal snapshot
with `os_signals_available=False`. The state machine treats this as
"no window data" — window-based classification falls through to `idle`,
gaming/focused_work/casual_browsing/chatting never fire. Conversation
and voice signals (msg timestamps, voice mode, voice RMS, unfinished
threads, LLM enrichment) all keep working because they don't depend
on OS APIs.

The state-section formatter prepends `（远程模式·无屏幕信号）` /
`(remote / no screen signal)` / etc. to the header, telling the
proactive AI explicitly that the OS-derived state isn't trustworthy
and to weigh conversation signals more heavily.

### 2. Frontend-pushed signals (extension point)

`UserActivityTracker.push_external_system_signal(...)` accepts OS
signals from outside the backend — designed for a frontend (Electron
app, browser via WebSocket, mobile shell) to read its local OS state
and POST it on a heartbeat. When fresh (≤ 30s), pushed signals
override the local collector entirely. When stale (heartbeat stops),
the tracker falls back to the local collector and the degraded marker
re-appears.

Field signature:

```python
tracker.push_external_system_signal(
    window_title='原神 - 4.5',
    process_name='GenshinImpact.exe',
    idle_seconds=2.0,
    cpu_avg_30s=42.0,
    gpu_utilization=78.0,
)
```

All fields optional — pass whatever the frontend can read on each
platform. The push primes `os_signals_available=True` so the AI sees
non-degraded state.

The HTTP endpoint to receive these pushes hasn't been added yet — when
the frontend implementation lands, wire it via something like
`POST /api/activity_signal/{lanlan_name}` in `system_router.py`.
Until then, the API surface exists for whoever builds it.

### What works in fully-degraded remote mode

Even with no OS signals at all:

- `voice_engaged` state (frontend-driven via voice mode + RMS hooks)
- `seconds_since_user_msg` / `seconds_since_ai_msg`
- `unfinished_thread` mechanism (text-based, no OS)
- LLM enrichment — `activity_guess` / `activity_scores` / `open_threads`
  all run on conversation alone, just with less context

### What's lost

- `gaming` / `focused_work` / `casual_browsing` / `chatting` rule
  classifications collapse to `idle` (state machine has no window data)
- `away` never fires (no idle signal)
- `stale_returning` never fires (depends on `away→active` transition)
- GPU-fallback gaming never fires (no GPU signal)
- `transitioned_recently` / `window_switch_rate_5min` are stuck at zero

The proactive prompt in degraded mode therefore relies almost entirely
on the conversation-derived signals + the LLM enrichment narrative.
This is a usable baseline — the AI can still detect open threads,
follow up on AI questions, and time its proactive cadence by message
recency — just without the rich window-aware state classification.

## Future work

* **Open-thread quality upgrades** — `open_threads` is already live via
  the emotion-tier LLM (see `llm_enrichment.call_open_threads`). v2 can
  raise recall on implicit promises ("I'll get back to that later"),
  improve cross-turn merging when the same thread is referenced under
  different wording, and tune dedup against the rule-based
  `unfinished_thread` to avoid surfacing the same hanging item twice.
* **Activity-guess quality upgrades** — `activity_guess` and
  `activity_scores` are already live via the 20s background loop. v2
  can stabilise scores under window flicker, add cost-aware refresh
  pacing (currently fixed 20s tick + 30s anti-thrash + state-signature
  dedup), and ground the narrative against persona memory for richer
  one-liners.
* **Fullscreen detection** — many games run windowless or use generic
  process names; comparing window rect to monitor rect is a strong
  fallback signal complementary to the GPU one.
* **AMD / Intel GPU support** — `nvidia-smi` only covers NVIDIA. Adding
  Windows Performance Counters (`pdh.dll` via ctypes) would catch
  `\GPU Engine(*engtype_3D)\Utilization Percentage` for any vendor.
* **Mouse / keyboard event histogram** — beyond the binary "is the
  user idle", a 1-min histogram would tell `casual_browsing` from
  `idle` more reliably.
* **Cross-monitor awareness** — currently we only see the foreground
  window. A multi-monitor user might have IDE on one screen and Slack
  on another; without enumeration we'll just go by what's
  foreground-active. Low priority since the dominant signal is which
  window has *focus*.

The emotion-tier LLM is already integrated; the layering rule for
future work stays the same: keep the rule path as a hard-floor
classifier, let the LLM only enrich `open_threads` /
`activity_scores` / `activity_guess`. The propensity directive must
remain rule-derivable so prompt costs don't tail-spin.

## Wiring (for integrators)

The tracker is owned by `LLMSessionManager` (`main_logic/core.py`) per
character. The integration touch-points are:

* Constructor: `self._activity_tracker = UserActivityTracker(self.lanlan_name)`.
* User-message hooks (text passed in):
  * `handle_input_transcript` (voice mode, with `is_voice_source=True`) →
    `on_voice_rms()` + `on_user_message(text=transcript)` when transcript non-empty.
  * Text-mode WebSocket entry inside `_process_stream_data_internal` →
    `on_user_message(text=data)` directly.
  * `_dispatch_openclaw_handoff` calls `handle_input_transcript(...,
    is_voice_source=False)` to reuse the queue/cache plumbing without
    re-firing **either** tracker hook. Both must be skipped here:
    `on_voice_rms` is voice-only and would falsely flag `voice_engaged`
    in text mode; `on_user_message` is also skipped because the
    text-mode entry at `_process_stream_data_internal` already called
    `on_user_message(text=data)` directly with the same payload one
    step earlier — calling it again here would double-bump
    `_conv_seq` and append the same text twice into the conversation
    buffer.
* AI-turn-end hooks (text accumulated via `_current_ai_turn_text` buffer):
  * `_emit_turn_end` → `on_ai_message(text=...)` for regular replies.
  * `handle_proactive_complete` → same (agent direct-reply path).
  * `finish_proactive_delivery` → same (`/api/proactive_chat` success path).
* Voice session start/stop → `on_voice_mode(True/False)`.
* RMS / VAD threshold breach (currently driven from
  `handle_input_transcript`'s voice path; future: real RMS callback) →
  `on_voice_rms()`.

Phase 1 of `proactive_chat` takes an early snapshot via
`await mgr._activity_tracker.get_snapshot()` for gating decisions
(state, propensity, propensity_reasons, unfinished_thread). When
`propensity == 'restricted_screen_only'`, Phase 1 may short-circuit
the unified-LLM call entirely (saving a model invocation) because no
external sources will be admitted.

Just before Phase 2 prompt rendering, the route fetches a fresh snapshot
again and uses `dataclasses.replace()` to splice the latest enrichment
fields (`activity_scores`, `activity_guess`, `open_threads`) onto the
early snapshot. This dual-snapshot pattern keeps gating decisions
consistent (no mid-Phase-1 state drift invalidating
restricted_screen_only filtering) while letting `kickoff_open_threads_compute`
results computed during Phase 1 actually reach the same round's prompt.

App shutdown should call `await get_system_signal_collector().stop()`
to cleanly cancel the polling task. Without it the asyncio task
will be cancelled at process exit anyway, but explicit shutdown gets
the final logger line cleanly.
