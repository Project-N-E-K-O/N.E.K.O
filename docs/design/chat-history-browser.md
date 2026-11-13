# Saved chat history browser

The shared React chat window exposes a read-only history dialog through the
compact tool wheel and the full-layout toolbar (or its narrow overflow menu). The modal registers with
the desktop full-window overlay and geometry protocols so native hit testing
captures controls, scrolling and the backdrop; closing restores normal hit regions. It selects a configured character, browses an archive date, searches
literal text (case insensitive), filters by speaker, and opens a message's nearby
context. Closing the context returns to the same search page. Escape closes the
native modal and restores focus. Plain text rendering prevents archived markup
from executing. Preferences store only order and timestamp visibility
in this browser; searches and transcripts are not persisted in browser storage.

## Data and API

`GET /api/chat-history/characters` lists configured characters.
`GET /api/chat-history/messages` accepts `character`, `day` (YYYY-MM-DD), `q`
(maximum 200 characters), `role` (all/user/assistant), `order` (asc/desc),
`cursor` (last scanned row id), `limit` (1–100), or `anchor` (message id).
Results include `messages`, `nextCursor`, `scanned` and `available`.

Data comes from the existing per-character `time_indexed_original` table, opened
read-only. Dates are the archive's stored local calendar dates; timestamps are
archive times, not reconstructed per-message send times. The newest unsaved
conversation may not yet be present. This viewer neither changes retention nor
starts cloud sync. It does not replay messages into a live conversation.

Rows are ordered by persistent archive ID. Each request scans at most 5,000 rows,
returns at most 100 messages (the viewer requests batches of 30), and has a two-second wall-clock budget. SQLite deadline interrupts after progress return a partial page with the last scanned cursor; both normal and chronological pagination resume without dropping rows. A deadline before any progress still reports unavailable.
An empty batch with a cursor continues scanning on demand rather than claiming
there is no match. Literal search runs on decoded visible text so JSON
metadata and image payloads cannot produce false hits. Assistant text uses the same visible-text parser as memory reads: recorded visible lengths and legacy action-note rules apply before search and context projection. Oversized/malformed
serialized messages are skipped; the read cap is 262,144 characters per row.
Anchor context is restricted to the same session, with up to ten rows on either
side; non-private, system and scoped records are excluded throughout.

Database work runs in a worker thread. Local access checks precede configuration
and storage reads; missing characters and invalid input fail explicitly. Symlink
character/database aliases and path traversal are rejected. Responses disable
caching. A missing archive is distinct from a corrupt, locked or timed-out one;
errors offer retry rather than presenting an empty successful result.

## Validation

Backend tests exercise cursor continuity, Unicode and literal search, date and
speaker filters, context/session isolation, private scope exclusions, scan-budget
continuation, path rejection, unavailable databases and HTTP validation/access.
React tests cover search highlighting, inert markup, context return, character
changes and preferences. Run the existing locale cache contract alongside these.

## Continuous scrolling

The viewer requests 30 rows initially and appends the next cursor batch within
240 pixels of the bottom. It fills short viewports and continues empty scan
batches, with a 180ms scheduling interval and one request in flight. It never
fetches the whole archive up front. All requests retain the selected character,
date (or no date for all history), speaker, keyword and order. Filter changes
abort prior requests and start a new list; stale responses cannot append rows.
An error keeps loaded rows and the cursor, stops automatic loading, and offers
retry. Exhaustion displays an explicit end state. No next-page or first-page
buttons and no user-facing page-size setting remain.

A measured variable-height window renders the visible messages with four rows
of overscan on each side. Binary search locates visible offsets; spacers retain
scroll range and measurements are corrected when rows render or width changes.
Loaded message data stays in this dialog's memory for reverse scrolling; DOM
size stays tied to viewport height. Closing the dialog releases the feed.
Width changes retain offscreen measurements as estimates until those rows are
visible again, avoiding a wholesale reset of the reading position.
Opening context hides and preserves the list, then restores its scroll position
without re-fetching. The backend retains its existing read-only bounded cursors.
Date filters use an indexed timestamp range, and every request explicitly closes
its SQLite connection on success and failure. Deeply nested invalid JSON rows
are skipped without blocking subsequent messages.

Regression tests cover append/deduplication, in-flight exclusion, retries, stale
response cancellation, date/all-date scopes, empty batches, end-of-data, and
bounded rendering with 10,000 messages.

## Saved voice and chronological review

Rows join the optional local `voice_archive/archive.sqlite3` using the immutable
`metadata.chat_original_source.recordId` and character. Only rows with stored
chunks expose a voice control; no text/time similarity matching or new TTS calls
are used. The voice archive is optional: text browsing works without it. This
PR includes the recorder in `main_logic/voice_archive.py` and its integration in
`core/turn.py` and `core/tts_runtime.py`; no separate producer is required. It uses
the existing server-created chat identity and records original outgoing audio.
No retrospective synthesis or external recording service is used. The archive's read endpoint returns the
original PCM/WAV or Ogg segments only when requested.

`GET /api/chat-history/playback` keeps the selected character/date/keyword scope
and returns at most 30 candidates per scan, with an exclusive `(timestamp,id)`
cursor (`after_time`, `cursor`). Ordering is chronological even if import IDs are
out of time order. Empty batches retain continuation; missing archives finish
immediately and unavailable archives report an error. The client retains only
the current queue batch and loads audio for the current message. It skips entries
whose recordings were removed, shows partial-recording status, and stops on
network/decode failures without skipping an unknown part of the conversation.

The dialog owns playback independently of virtual rows. Its review strip shows
the current timestamp/text, pause/resume, stop and a context link. Per-row controls
use the same speaker/stop icon as live chat. Closing the dialog, changing filters,
starting another replay or receiving live-speech playback cancels the request and
releases the media element. Search-result display order does not change playback
order. Settings contain a persisted, default-on automatic continuation switch.
Clicking a row's voice control starts there and continues strictly after its
(timestamp, id) across dates, retaining the character and keyword filters. The
date selector scopes browsing only: a voice selected at 23:55 can continue to
00:05 the next day, even while browsing a single date. Changing the browsing
date still cancels playback without sending that date to the playback endpoint. Opening the dialog alone
does not start audio. Turning continuation off lets all segments of the current
message finish, then stops; turning it on during playback enables continuation.
There is no separate start-all button. User-only results have no TTS controls.


## Recording opt-in and storage

History settings expose **Save voice recordings**, off by default. The local-only
`GET/POST /api/voice-archive/settings` endpoint reads/writes
`config/voice_archive.json` (`{"enabled": true}`) using atomic file replacement.
The setting applies to every character and is checked on subsequent events.
Disabling keeps existing recordings; past unrecorded speech cannot be recovered.
The separate automatic continuation preference only controls playback.

The writer stores SHA-256 clip IDs derived from character and speech ID, exact
record IDs, checksummed PCM16/48 kHz or Ogg chunks, and completion/error markers.
Audio arriving before text is joined when the text callback provides identity.
Durable writes run outside the event loop, serialize per session, and complete
before releasing their lock even after cancellation. Disk errors do not prevent
live speech; interrupted or partly disabled recordings remain marked incomplete.
Voice data and SQLite sidecars belong to `MIGRATED_RUNTIME_ENTRY_NAMES` and move
with the stopped application's runtime data. Credentials and private transcripts
are absent from PR fixtures and screenshots.

Playback rejects overlap with already-active live speech. A user-only speaker
filter permits a selected context recording but does not start continuation.
Mixed-format clips are bounded and loaded one format run at a time. Missing or
corrupt voice databases do not hide text history; replay reports unavailable.

The recorder, exact-identity join, history controls and replay API stay in this
PR so a normal installation can produce the data that the new controls consume.
The feature remains isolated in its own modules with small turn/TTS hooks.
