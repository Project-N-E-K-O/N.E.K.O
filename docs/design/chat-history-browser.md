# Saved chat history browser

The shared React chat window exposes a read-only history dialog through the
existing tool wheel in both compact and full layouts. The modal registers with
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
returns at most 100 messages (the viewer requests batches of 30), and has a two-second SQLite execution budget.
An empty batch with a cursor continues scanning on demand rather than claiming
there is no match. Literal search runs on decoded visible text so JSON
metadata and image payloads cannot produce false hits. Oversized/malformed
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
