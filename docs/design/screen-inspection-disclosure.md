# Compact screen inspection

PR #3657 feedback: keep the ordinary chat tool surface small, and test conversation,
memory recall and task dispatch with different models and personas.

## Model-facing contract

Only `read_current_screen` is registered. Its Chinese description is:

> 用户询问屏幕内容时读取共享画面。不传参数获取新截图，后续用法见结果。

An empty argument object captures the current shared source. The successful result
discloses how to continue with the same tool:

- `snapshot_id` and `window_id`: capture a matching allowed window. This hint is
  returned only when that image has an allowed window list.
- `snapshot_id` and `region`: crop original pixels. Coordinates are
  `[left, top, right, bottom]`, normalized to that snapshot; `avatar_region` can be
  copied when present.

Mixed, incomplete or unknown argument combinations fail without taking a screenshot.
Window allowlists, permission revalidation, ownership checks, expiry and image
budgets continue to be enforced by the existing inspection implementation.
The description and result hints have all eight supported locales.

There is no additional recurring screen system message after inspection. The
per-image result states that pixels expire after the turn; the released image has
a short restart receipt in its original history position. Titles stay in tool
output only during the owning turn; release removes the inventory and source
metadata, and titles are never copied into system messages. Evidence guidance is included
once in the result instead of being repeated in the image caption.

## Measured prompt size

Compared with the original three-tool schema. These are Unicode character counts,
not tokenizer estimates. Schema counts use the complete OpenAI tools array,
`ensure_ascii=False`, and compact JSON separators.

| Measure | Before | After | Official recall_memory |
| --- | ---: | ---: | ---: |
| Visual tool count | 3 | 1 | — |
| Chinese descriptions, total | 547 | 34 | 61 |
| English descriptions, total | 1413 | 138 | 197 |
| Chinese serialized visual tools | 1348 | 338 | — |
| English serialized visual tools | 2214 | 442 | — |

## Regression report

**Change and reason.** Consolidate three visual tools into one short entry so
ordinary turns carry less tool selection context. Defer continuation instructions
until an actual capture and remove the recurring follow-up system message.

**Before and after.** The overview/window/crop chain now uses one tool name with
three optional fields. The default call remains an empty argument object. Ordinary
provider message construction no longer appends visual instructions after a screen
turn. Other tools retain their original image count limit.

**Potential regressions.** A model may fail to follow continuation hints or may answer
a visual follow-up from old text after the recurring reminder is removed. Smaller
schemas alone do not establish improved conversation quality. Model testing must
cover these failure cases as well as false captures and interference with other tools.

**Automated validation, 2026-11-12.** 426 tests passed across screen inspection, disclosure, streaming, frontend bridge,
tool calling, turn cancellation, frame publication and screenshot route suites. Transport tests use doubles;
they do not measure live model judgment or persona adherence.

Ruff, `git diff --check`, prompt hygiene, async blocking, module layering, LLM
budget and documentation link checks passed on the PR worktree.

## Live model evaluation, 2026-11-12

Real provider requests ran through the production offline client and registry in
independent sessions. Models were deepseek-flash, qwen3.8-flash and gpt-6.1-sol;
personas were brief, talkative and catgirl. Capture and memory handlers were
synthetic: rendered 2560x1440 document images and a fixed memory fact. Production
image decode, original-pixel crop, snapshot validation, image delivery, release
and streaming paths were used. No test conversation was written to app memory.

The nonvisual comparison isolated tool exposure: memory-only baseline, the original
three-tool schema, and the compact schema. Six successive prompts per model/persona/
condition covered greeting, recall, quoted screen request, explicit no-capture,
emotional support and an unsupported weather request. All 162 turns answered;
there were zero unintended screen captures and all 27 recall prompts invoked the
memory tool and returned the supplied fact. This measures tool selection, not a
statistical claim that conversation quality is unchanged. The configured app has
no chat task-dispatch tool, so the weather prompt tests conversation only; actual
dispatch is not covered by this experiment.

Two visual sessions per model/persona tested window selection, exact code follow-up,
two-document comparison, an explicit crop, a missing window and return to chat:

| Model | Passed / tested |
| --- | ---: |
| deepseek-flash | 36 / 36 |
| qwen3.8-flash | 35 / 36 |
| gpt-6.1-sol | 36 / 36 |

One DeepSeek missing-window answer was manually adjudicated as passing: it said
“没看到” and declined to invent a total, while the initial keyword scorer only
recognized other negative phrases. Both verdicts remain in the raw record.

Qwen's one failure was an announced crop without a tool call. It did not fabricate
an observed value. A matching Qwen comparison using the original three tools,
legacy follow-up notice and four-image budget passed 35/36: one missing-window
case unnecessarily inspected the two unrelated windows. This comparison restores
the original tool behavior in the current client; it is not an untouched old app.
Neither variant is guaranteed to obey every instruction on every generation.

Initial compact trials exposed stale-snapshot recovery and mixed window/crop
arguments. Error results now supply the exact valid argument combinations. A
released screen image uses a short localized restart receipt in its original
history position, replacing the generic receipt; no recurring system instruction
or untrusted title is appended. Existing captions are not duplicated.

The current installed conversation model, gpt-6.1-sol, passed all controlled visual
cases. Native app acceptance is recorded below; the synthetic runs alone do not
establish platform-specific capture behavior.

## Native macOS acceptance, 2026-11-12

The installed Electron app was checked with its configured gpt-6.1-sol model
and a real TextEdit window containing only synthetic acceptance data. The running
release included the compact implementation and the latest local chat UI changes.

- A request for the unopened test window returned a missing-window answer.
- After opening Screen-Alpha.txt, the app selected that window and returned the
  exact values `TASK_ID = 731` and `STATUS = READY`.
- A follow-up explicitly requesting a fresh capture and a crop returned
  `const route = "capture | crop | verify";` exactly. Runtime events confirmed
  overview, window selection and original-pixel region capture, in that order.
  The crop was 1124x76 original pixels, derived from the selected window snapshot.
- A subsequent ordinary chat prompt received an ordinary response, with no new
  screen-inspection events.

This acceptance used the normal app conversation path. Unlike the independent
synthetic provider sessions above, these prompts entered the app's normal chat
history. No personal screen content or app conversation logs are published here.
This verifies the installed macOS path; it does not establish Windows/Linux/Tauri
support or eliminate the model-level failure observed in the Qwen experiment.


The failed Qwen catgirl session was followed by three additional six-turn sessions;
all 18 follow-up turns passed. Across the compact visual runs this is 125/126
turns, with the original failure retained. Sanitized questions, answers, calls,
timings and manual adjudication are in
`tests/fixtures/screen_inspection/compact-evaluation.json`.

## Review follow-up, 2026-11-13

This follow-up starts from compact-tool commit `0de8dc7a`. The maintainer's
November 12 comparison tested the earlier three-tool interface and specifically
reported retained window inventories affecting subsequent ordinary chat. Those
findings are checked against the current single-tool implementation here.

### Fixes and regression risks

- The Python frontend bridge test now imports its ESM suite from a CommonJS
  entry through `tests.node_harness.run_node_script`. This preserves the shared
  UTF-8 transport, startup-stall handling and watchdog, including Windows paths
  encoded as file URLs. The Windows failure was the launcher contract assertion,
  not a demonstrated native screenshot failure.
- Screen tool result text, including window inventories, source titles and
  snapshot metadata, is replaced by a localized release receipt at turn exit.
  Tool name and call ID remain paired; other tools' output is preserved. Cleanup
  covers normal completion, cancellation, provider failure and omitted images.
  The next request also hides previous-turn inventories before an older turn's
  cleanup completes. Same-turn selection still receives the inventory. User
  messages and the assistant's visible answers are retained, so this does not
  promise to erase screen facts already discussed in conversation.
- Only actual tool image slots relax the pipe guard. A failed screen call's text
  receipt does not. Ordinary attachments, proactive images and plugin attachments
  retain their existing guard. There is still no recurring visual system notice.
- Sending the capture request and receiving the response share one timeout.
  Cache ownership includes connection identity and the vision revocation epoch;
  consent is checked before capture and after preview encoding. Revoke/re-enable
  and connection replacement during decode or encoding discard the old result.
  Stale invoking sessions/reply owners are rejected before capture.
- Frontend enumeration uses the shared portal-risk helper, including old Linux
  bridges. Inspection checks remembered-window identity without remapping the
  selected source, writing preferences or stopping its stream. A restricted
  browser stream cannot be overridden by a stale monitor ID. Stream replacement
  is checked during capture, and unnamed streams get distinct scope identities.
  A known window stream can be reused only with its recorded source binding.
  Native calls and video playback have a seven-second total deadline; temporary
  video state is released on failure and late results are ignored. Inspection
  no longer invokes the generic proactive screenshot fallback, which could
  acquire a new stream or change the selected source.
- The built-in schema omits `additionalProperties`, which the Gemini SDK schema
  rejects; exact allowed argument combinations remain enforced by the handler.

The intentional native whole-screen permission boundary remains: all enumerated
windows are available, including windows on other monitors or obscured windows.
Single-window/browser sharing is restricted and never falls back to the desktop.
Native Windows/Linux/Tauri behavior is not established by browser doubles.

### Legacy dedup failure diagnosis

The failed Windows full-unit job ran merge commit
`a6be7eea16bf9caec231f42cf24c4ac5cddd9f4c` (PR head `0de8dc7a` merged with
upstream `d7fc3145`). Its second failure was
`test_an_everyday_dedup_archive_after_an_interrupted_plan_is_not_claimed`.
The legacy dedup module/test come from upstream and are absent from this PR head.

`memory.legacy_dedup.owned()` falls back to the planned fact ID plus an archive
timestamp in milliseconds when the archive reason differs. The test interrupts
the cleanup after writing its plan, then immediately runs everyday dedup without
ensuring a distinct timestamp. Two archives at `.123000` and `.123999` seconds
both normalize to the same millisecond and the everyday archive is incorrectly
claimed; `.124000` is correctly rejected. This was deterministically reproduced
using the exact `owned()` and `timestamp()` code from the failed CI merge. A
coarse clock or fast consecutive operations can expose the collision; this is
not evidence of a screen-inspection regression. No live memory data was used.

A deterministic test needs explicit different archive times for its distinct-time
case and a separate collision case. Production ownership needs provenance that
survives imports, rather than timestamp equality alone. This screen PR does not
change upstream memory maintenance or claim that failure is fixed.

### Validation scope of this follow-up

The automated regression command covers screen capture, single-tool disclosure,
OpenAI and Gemini tool loops, ordinary attachment/proactive/plugin pipe guards,
other-tool history, cancellation, provider frame delivery, screenshot routes,
shared Node launcher contracts, screen-history request projection and Chromium
screen-source workflows. Provider responses and native capture bridges are test
doubles; the screen-source suite runs in Chromium with synthetic DOM/provider
fixtures. Live providers, real task dispatch and native OS capture were not rerun.

The November 12 model/persona counts and installed macOS acceptance above remain
historical results for the previous compact version. They are not fresh results
for these privacy fixes. Windows CI results must be reported for the new pushed
head separately; a local macOS pass is not a Windows pass.

Final local result: **904 passed**, with four existing deprecation warnings, on
macOS. Ruff and the repository's prompt hygiene, async blocking, module layering,
LLM budget, core contract, i18n synchronization, Traditional Chinese coverage,
docstring and documentation-path checks passed, as did `git diff --check`.
