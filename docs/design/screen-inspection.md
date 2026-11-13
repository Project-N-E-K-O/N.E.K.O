# Question-guided screen inspection

The offline chat model can inspect the currently shared source, select an allowed window, and crop original pixels to read small details. This tool is read-only: they do not activate windows, change the shared source, click, type, or capture audio.

## Tool flow

1. `read_current_screen({})` obtains a fresh overview and a bounded window inventory. Its `snapshot_id` belongs to the current session and speech turn.
2. Call the same tool with `snapshot_id` and `window_id` to capture an allowed window. The frontend re-enumerates before capture; a missing window fails rather than substituting the desktop.
3. Call it with `snapshot_id` and `region` to crop retained original pixels. `region` is `[left, top, right, bottom]`, normalized to that image. The result discloses these continuation arguments only after capture; `avatar_region` can be copied when present.

The only resident description is “用户询问屏幕内容时读取共享画面。不传参数获取新截图，后续用法见结果。” It has 34 characters, compared with 61 for the Chinese memory-tool description. The complete Chinese OpenAI visual-tools JSON shrank from 1348 to 367 characters (compact separators); these are character counts, not tokens.

An overview is the currently shared source, which can itself be a restricted window. `kind`, `source_id`, `title`, `capture_scope`, `parent_snapshot_id`, and capture time accompany the image so the model can distinguish an overview, a separately captured window, and a crop. A crop retains its parent's capture time; enlarging it does not make it a new capture.

The model chooses the window and region from the question and image evidence. There are no application-name regexes, fixed desktop coordinates, or clothing answers. A desktop pet hidden behind other windows is correctly absent from a full-screen capture. This feature does not change window stacking or make the pet always on top.

## Follow-up evidence

Tool-image slots are released at the end of a turn. Their history position receives a short localized receipt asking for a fresh `read_current_screen({})` on visual follow-ups. There is no appended recurring system notice and no promotion of window titles into system messages. Stale or invalid calls receive localized recovery instructions only when they fail.

Evidence instructions are short, appear once per result and preserve literal code characters. The pipe guard is relaxed only for turns with tool images, not arbitrary user attachments or proactive screenshots.

## Lifetime and permission boundaries

- Responses must match both the requesting WebSocket and request ID. A replaced session, speech turn, or connection cannot receive the old result.
- Frontend capture checks the existing vision/tutorial and remembered-window gates before and after asynchronous native calls. Region reads revalidate the current shared scope before returning cached pixels.
- Native full-desktop sharing permits all enumerated native windows, including windows on other displays and possibly occluded/minimized windows when the platform exposes their pixels. It is not restricted to windows visible on the selected display. Selecting one shared window limits enumeration and capture to that window. A browser/window media stream cannot fall back to a desktop capture.
- Native enumeration that may open a Linux portal is skipped. An already shared media stream can still supply an overview; absent capabilities fail explicitly.
- Cache limits are four images, 40 million aggregate pixels, and a 120-second expiry. Each decoded image is limited to 25 million pixels. The data URL ceiling is 12 MiB, below the WebSocket transport ceiling.
- Decode, crop, and preview generation run in worker threads; ownership is checked after awaited image work before publication.
- Screen tools may deliver four tool images per turn for overview/window/detail/re-check. Other tools keep their existing count limit, and the existing aggregate byte ceiling is unchanged. Offline tool iteration capacity is six; turns with no tool calls still finish in one provider response.

## Capture and platform limits

The existing desktop capture provider supplies pixels. The live macOS/Electron screen test returned a 2560×1440 original and a 1280×720 overview. Native window thumbnails use the provider's existing resolution ceiling (1080p in that build). Crops use those retained pixels, not an already reduced overview. Transparent window pixels are composited on white before conversion to JPEG.

Live testing below covers macOS/Electron offline text chat. Web stream and capability failures are exercised with frontend doubles. It is not a claim of equivalent live testing on Windows, Linux, Tauri, or realtime audio-only sessions.

## Reproducible manual checks

Open the two text files from `tests/fixtures/screen_inspection` in separate native editor windows. Their different values detect accidental reuse of another window or an old answer. Ask without providing expected values or crop coordinates.

| Question variant | Expected evidence/result |
| --- | --- |
| “Read MAPLE's quantity and status in Readscreen-Checklist.” | Select the document; `42`, `HOLD`. |
| “What is retryDelayMs there? Transcribe the route line.” | Reacquire image evidence; `375`, `const route = "window \| region \| verify";`. |
| “Read Build, MAPLE and retryDelayMs in Second-Window.” | Select the second document; `cobalt-9136`, `6`, `820`. |
| “Compare ORCHID in both windows.” | First: `17 / READY`; second: `53 / WAIT`. |
| “Read the total in Budget-Audit-NotOpen.xlsx.” (not open) | Report the missing window; do not invent a total. |
| Close Second-Window, then ask for its current Build. | Report it unavailable; do not reuse `cobalt-9136` as current evidence. |
| “Do not capture; explain the difference between screen and window capture.” | No inspection tool call. |
| With the avatar covered by other windows, ask whether it appears in the full-screen image. | Report it absent; do not infer clothing or change window stacking. |
| Ask the file manager for the two fixture filenames and sizes. | Read the actual file-list window; compare with files on disk. |
| Ask for a browser video title/channel/code, including an English paraphrase. | Select the matching browser window and inspect details; literal `\|` characters must not truncate the reply. |

Historical three-tool revision (not the compact revision): fourteen live prompts were exercised across the two text windows, Finder, Chrome, and a full-screen avatar-absence check. The local live evaluation found and corrected a missing-image follow-up failure: the first response selected the correct document, but the next response invented code without recapturing. After the request-only continuity notice, the same follow-up reacquired the document. The final transcription check retained the exact ASCII quotes, pipes, and semicolons. Window selection, two-document comparison, missing/closed windows, and native file-list reading were also exercised. One browser trial still confused a playlist heading with the requested video title; on challenge it reacquired the window and reported the full video title unavailable. This is a remaining model interpretation limit, not a capture success claim. Evaluate source identity and actual delivered frames as well as answer text; a successful tool call alone does not prove its image reached the provider.

## Automated checks

```sh
python -m pytest -q tests/unit/test_screen_inspection.py tests/unit/test_screen_inspection_streaming.py tests/frontend/test_screen_inspection_bridge.py
node tests/frontend/screen_inspection.mjs
```

The backend tests cover original pixels and child coordinates, source allowlists, cross-turn/connection isolation, expiry, cancellation, byte/pixel budgets, transparent pixels, and localized failures. The streaming tests run the production tool loop through four image responses and verify all four reach the final provider request, including a title with multiple pipes. Frontend tests cover source restrictions, permission revocation, remembered windows, replacement sockets, portal avoidance, stream ownership, and transport limits.

## Compact revision evaluation

See `docs/design/screen-inspection-disclosure.md` for the 2026-11-12 controlled model/persona comparison, failures, recovery fixes and acceptance limits.
