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
output; they are not copied into system messages. Evidence guidance is included
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
| Chinese serialized visual tools | 1348 | 367 | — |
| English serialized visual tools | 2214 | 471 | — |

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
cases. Native desktop capture and platform-specific behavior still require app
acceptance; these synthetic runs do not establish Windows/Linux/Tauri support.

The failed Qwen catgirl session was followed by three additional six-turn sessions;
all 18 follow-up turns passed. Across the compact visual runs this is 125/126
turns, with the original failure retained. Sanitized questions, answers, calls,
timings and manual adjudication are in
`tests/fixtures/screen_inspection/compact-evaluation.json`.
