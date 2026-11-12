# Saved chat history browser

The shared React chat window exposes a read-only history dialog in desktop and
web layouts. It selects a configured character, browses an archive date, searches
literal text (case insensitive), filters by speaker, and opens a message's nearby
context. Closing the context returns to the same search page. Escape closes the
native modal and restores focus. Plain text rendering prevents archived markup
from executing. Preferences store only order, page size and timestamp visibility
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
returns at most 100 messages, and has a two-second SQLite execution budget.
An empty page with a cursor explicitly offers continued search rather than
claiming there is no match. Literal search runs on decoded visible text so JSON
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
