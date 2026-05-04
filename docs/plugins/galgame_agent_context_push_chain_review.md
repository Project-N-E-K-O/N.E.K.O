# Galgame Agent Context Push Chain Review

Date: 2026-04-30

Status: implemented.

Scope: OCR / bridge event ingestion, Game LLM Agent scene summary scheduling, and proactive context delivery to the cat agent.

## 1. Conclusion

The reported case, "stable_line already has 10 lines but no context was sent", was not caused by OCR failing to recognize text. The inspected OCR session had already produced enough stable dialogue lines.

The root cause was in the Game LLM Agent summary scheduling path:

- `line_count` scene summaries were previously tied too strongly to the current `latest_snapshot.scene_id`.
- When one bridge poll batch contained old-scene stable lines followed by a `scene_changed` event, the snapshot advanced to the new scene before the next agent tick counted those old-scene lines.
- The old-scene lines remained in `history_lines`, but the agent no longer counted them toward the line-count summary threshold.
- A summary that had already been scheduled by line count could also be dropped after the current scene changed.

The implemented fix makes line-count summary scheduling history-based and per-scene. Stable dialogue lines are now counted by their own `scene_id`, not only by the current snapshot scene.

## 2. Evidence From The Failing Session

Inspected OCR session:

- Runtime path: `C:\Users\ALEXGREENO\AppData\Local\N.E.K.O\galgame-bridge\ocr-b26cd1879f02`
- `game_id`: `ocr-b26cd1879f02`
- `source`: `ocr_reader`
- `session_id`: `ocr-74785be6-cb09-442e-bff3-09741ed77df9`
- `scene-0001`: 10 stable `line_changed` events, event seq `4` through `31`
- `scene_changed` to `scene-0002`: event seq `35`

Conclusion: OCR and bridge event writing had already produced enough stable lines. The failure happened after bridge state merge, inside agent-side summary scheduling or delivery.

## 3. Selected Design

Selected solution: keep the existing bridge poll order, and fix the agent to process stable lines by scene from event/history data.

Reasoning:

- `history_lines` already preserves stable old-scene lines, so no bridge data model change is required.
- The agent is the layer that owns proactive context push policy and threshold counting.
- Per-scene counters handle both current-scene and old-scene line-count summaries.
- This keeps ordinary one-tick bridge latency from becoming a correctness issue.

Rejected alternative: changing `bridge_tick()` ordering alone. That could reduce latency, but it would not fully solve batched-poll cases where lines and `scene_changed` arrive together.

## 4. Implemented Data Structures

Implemented in `plugin/plugins/galgame_plugin/game_llm_agent.py`.

`AgentSceneTracker` now keeps summary state per scene:

- `summary_scene_states`: scene-id keyed state for line-count summary progress.
- `summary_last_processed_event_seq`: last processed bridge event seq for summary counting.
- per-scene `seen_line_keys`: deduplicates stable lines.
- per-scene `lines_since_push`: counts stable lines since the last scheduled line-count summary.
- per-scene last-line and scheduled-seq metadata for diagnostics and delivery metadata.

Important code points:

- `game_llm_agent.py:266` `AgentSceneTracker`
- `game_llm_agent.py:317` `remember_scene_line()`
- `game_llm_agent.py:344` `mark_scene_summary_scheduled()`
- `game_llm_agent.py:368` `summary_scene_statuses()`
- `game_llm_agent.py:4034` `_run_scene_summary_task()`
- `game_llm_agent.py:4139` `_maybe_push_periodic_scene_summary()`
- `game_llm_agent.py:4522` proactive push through `message_type="proactive_notification"`
- `game_llm_agent.py:4913` `query_status.debug.summary`

## 5. Implemented Behavior

The Game LLM Agent now behaves as follows:

1. `_maybe_push_periodic_scene_summary()` first checks push policy through `_should_push_scene(shared)`.
2. It builds sequence metadata from `history_events`.
3. It scans stable lines from `history_lines`.
4. It counts each line against the line's own `scene_id`.
5. It deduplicates lines with `_line_summary_key()`.
6. Each scene advances its own `lines_since_push`.
7. Any scene that reaches `_SCENE_SUMMARY_PUSH_LINE_INTERVAL` can schedule a `line_count` summary.
8. A scheduled `line_count` summary is allowed to deliver after the current snapshot scene changes, as long as session and generation still match.
9. Non-`line_count` stale scene summaries still drop on scene mismatch.
10. `query_status.debug.summary` exposes per-scene summary status and diagnostics.

The current-scene legacy status fields are still mirrored from the per-scene state for compatibility.

## 6. Core Pseudocode

```python
if not should_push_scene(shared):
    return

event_seq_by_line = build_seq_index(history_events)

for line in stable_lines(history_lines):
    scene_id = line.scene_id
    line_key = line_summary_key(line)
    event_seq = event_seq_by_line.get(line_key)

    if event_seq <= tracker.summary_last_processed_event_seq:
        continue

    accepted = tracker.remember_scene_line(scene_id, line_key, event_seq)
    if not accepted:
        continue

    if tracker.scene_lines_since_push(scene_id) >= LINE_INTERVAL:
        schedule_scene_summary(trigger="line_count", scene_id=scene_id)
        tracker.mark_scene_summary_scheduled(scene_id, seq=event_seq)

tracker.summary_last_processed_event_seq = newest_processed_seq
```

Delivery guard:

```python
if generation_mismatch:
    drop()
elif session_mismatch:
    drop()
elif trigger != "line_count" and scene_mismatch:
    drop()
else:
    push_agent_message(message_type="proactive_notification")
```

## 7. Chain Review

### OCR Event Source

OCR bridge writes `events.jsonl` and `session.json`. The inspected session had enough stable `line_changed` events before scene transition.

Result: source layer OK.

### Candidate Selection

Candidate selection is handled by `choose_candidate()`.

Relevant code:

- `plugin/plugins/galgame_plugin/service.py:863` `choose_candidate()`

Auto mode preference:

1. bridge SDK candidates with text
2. memory-reader candidates with text
3. OCR candidates
4. bridge SDK fallback candidates

Result: an empty memory-reader session should not block OCR in this case.

### Bridge Poll And State Merge

Bridge poll applies events to both history and latest snapshot.

Relevant code:

- `plugin/plugins/galgame_plugin/__init__.py:3608` applies `apply_event_to_histories()`
- `plugin/plugins/galgame_plugin/__init__.py:3618` updates `latest_snapshot` with `apply_event_to_snapshot()`
- `plugin/plugins/galgame_plugin/service.py:1681` `apply_event_to_histories()`
- `plugin/plugins/galgame_plugin/service.py:1710` handles `line_changed`

Observed behavior:

- `line_changed` enters `history_lines`.
- `scene_changed` updates `latest_snapshot.scene_id`.
- Old-scene lines are not deleted when the snapshot scene advances.

Result: bridge merge layer preserved the necessary data.

### Timer Ordering

`bridge_tick()` still ticks the agent before starting bridge polling.

Relevant code:

- `plugin/plugins/galgame_plugin/__init__.py:2960` `bridge_tick()`
- `plugin/plugins/galgame_plugin/__init__.py:2969` `self._game_agent.tick(self._snapshot_state())`
- `plugin/plugins/galgame_plugin/__init__.py:2978` `self._start_background_bridge_poll()`

Result: this can still cause normal one-tick latency. It is no longer a correctness dependency for the fixed line-count summary case.

### Agent Summary Scheduling

The summary scheduler now processes stable lines from history by scene. This is the primary fixed layer.

Result: old-scene stable lines can trigger a `line_count` summary even when `latest_snapshot.scene_id` already points to the next scene.

### Async Summary Delivery

`_run_scene_summary_task()` preserves stale guards:

- generation mismatch drops;
- session mismatch drops;
- non-`line_count` scene mismatch drops;
- `line_count` scene mismatch can deliver.

When delivered after scene change, metadata includes:

- `delivered_after_scene_change`
- `current_scene_id`
- `current_scene_id_at_schedule`
- `scheduled_from_event_seq`

Result: scheduled line-count summaries are no longer lost only because the observed scene has advanced.

### Push Outlet

Delivered summaries go through `_push_agent_message()` and plugin `push_message()`.

Relevant code:

- `plugin/plugins/galgame_plugin/game_llm_agent.py:4522` `message_type="proactive_notification"`

Expected observable result:

- `query_status.recent_pushes` contains the push.
- `list_messages(direction="outbound")` contains the outbound proactive notification.

## 8. Test Coverage

Targeted tests in `plugin/tests/unit/plugins/test_galgame_bridge.py`:

- `test_game_llm_agent_pushes_scene_summary_after_eight_lines` at line `11481`
- `test_game_llm_agent_delivers_line_count_summary_after_scene_change` at line `11530`
- `test_game_llm_agent_counts_batched_old_scene_lines_after_snapshot_advances` at line `11640`
- `test_game_llm_agent_does_not_duplicate_batched_old_scene_summary` at line `11695`
- `test_game_llm_agent_counts_scene_summary_lines_independently` at line `11745`
- `test_game_llm_agent_scene_summary_push_policy_blocks_event_history_count` at line `11796`
- `test_game_llm_agent_scene_summary_counters_reset_on_session_change` at line `11830`
- `test_game_llm_agent_discards_stale_background_scene_summary` at line `12336`
- `test_game_llm_agent_query_status_returns_structured_fields` at line `9421`

Coverage confirmed:

- current-scene 8-line threshold still pushes;
- old-scene 8 stable lines still push when current snapshot is already next scene;
- repeated ticks do not duplicate old-scene summaries;
- scenes count independently;
- `push_notifications=false` blocks scheduling;
- `mode="silent"` blocks scheduling;
- session changes reset summary counters;
- stale non-`line_count` summaries still drop;
- `query_status` remains structured after adding summary diagnostics.

## 9. Verification

Syntax check:

```powershell
$env:PYTHONDONTWRITEBYTECODE=1
N.E.K.O\.venv\Scripts\python.exe -m py_compile `
  N.E.K.O\plugin\plugins\galgame_plugin\game_llm_agent.py `
  N.E.K.O\plugin\tests\unit\plugins\test_galgame_bridge.py
```

Result: passed.

Targeted summary-chain tests:

```powershell
$tmp = Join-Path $env:TEMP ('codex-pytest-agent-line-' + [guid]::NewGuid().ToString('N'))
N.E.K.O\.venv\Scripts\python.exe -m pytest `
  N.E.K.O/plugin/tests/unit/plugins/test_galgame_bridge.py::test_game_llm_agent_pushes_scene_summary_after_eight_lines `
  N.E.K.O/plugin/tests/unit/plugins/test_galgame_bridge.py::test_game_llm_agent_delivers_line_count_summary_after_scene_change `
  N.E.K.O/plugin/tests/unit/plugins/test_galgame_bridge.py::test_game_llm_agent_counts_batched_old_scene_lines_after_snapshot_advances `
  N.E.K.O/plugin/tests/unit/plugins/test_galgame_bridge.py::test_game_llm_agent_does_not_duplicate_batched_old_scene_summary `
  N.E.K.O/plugin/tests/unit/plugins/test_galgame_bridge.py::test_game_llm_agent_counts_scene_summary_lines_independently `
  N.E.K.O/plugin/tests/unit/plugins/test_galgame_bridge.py::test_game_llm_agent_scene_summary_push_policy_blocks_event_history_count `
  N.E.K.O/plugin/tests/unit/plugins/test_galgame_bridge.py::test_game_llm_agent_scene_summary_counters_reset_on_session_change `
  N.E.K.O/plugin/tests/unit/plugins/test_galgame_bridge.py::test_game_llm_agent_discards_stale_background_scene_summary `
  --basetemp=$tmp -q
```

Result:

- `8 passed`
- `1 warning`

Status API regression:

```powershell
$tmp = Join-Path $env:TEMP ('codex-pytest-agent-status-' + [guid]::NewGuid().ToString('N'))
N.E.K.O\.venv\Scripts\python.exe -m pytest `
  N.E.K.O/plugin/tests/unit/plugins/test_galgame_bridge.py::test_game_llm_agent_query_status_returns_structured_fields `
  --basetemp=$tmp -q
```

Result:

- `1 passed`
- `1 warning`

Note: sandboxed pytest temp directories can hit Windows `WinError 5`; the successful pytest runs used elevated or normal-permission execution.

## 10. Runtime Acceptance Checklist

For the original failure class, acceptance requires:

- OCR creates stable `line_changed` events in `events.jsonl`.
- `history_lines` contains those lines with their original `scene_id`.
- `query_status.debug.summary.scene_states` shows per-scene counters advancing.
- After the configured line interval, `query_status.recent_pushes` contains a proactive notification.
- `list_messages(direction="outbound")` contains the delivered outbound context.
- If the snapshot has already advanced to the next scene, the push metadata marks `delivered_after_scene_change`.

## 11. Remaining Notes

The line-count context push issue described here is implemented and covered by targeted tests.

Remaining non-blocking considerations:

- `bridge_tick()` still starts bridge polling after the agent tick, so one-tick latency can still happen.
- `scene_changed` summaries still represent the new scene rather than a formal "previous scene completed" event.
- Full test suite was not run; only the targeted summary-chain tests and one status API regression were run.
