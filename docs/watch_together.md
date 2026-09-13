# Watch together

The built-in `/watch_together` scene uses the mini-game SDK exclusively. The
shared React chat surfaces expose a manual entry; the existing proactive
mini-game invitation policy also offers this scene. Accepting an invitation
opens the scene. In manual mode, only pressing Play starts audible playback.
Enabling Automatic watching may start audible playback once a selected or
discovered video is ready, subject to browser autoplay restrictions.
The manual button is in the expanded chat title bar. Compact mode retains its
existing seven-slot tool wheel and tutorial indices; use the direct scene URL
or an accepted invitation while remaining in that layout.

## Storage and migration

Data lives under `ConfigManager.app_docs_dir / watch_together`, outside the
source tree. There is no automatic eviction. Import with:

```powershell
uv run python scripts/migrate_watch_together.py ARCHIVE LIVE_CACHE --audio-assets AUDIO_DIRECTORY
```

Both sources are scanned. The archive's `backup-manifest.json` is checked when
present. Every file is copied to an immutable SHA256-addressed object through a
temporary file, verified, then atomically installed. Job manifests commit only
after the source file set and hashes are rechecked. Re-running safely reuses
verified objects and repairs corrupt copies. A failed/interrupted import can be
retried; source files are never changed. Import runs must use stable sources or
retry when an active preparation changes them.

The original job ID remains the primary identity. Different contents produce
different immutable versions, including multiple jobs for the same video.
The original timeline JSON and audio remain byte-for-byte unchanged. The host
maps legacy `/media/{job}/...` references only when serving a timeline. All
resource paths resolve through the manifest, and the media route supports HTTP
Range requests. Incomplete jobs stay visible but cannot be played. Imported
audio assets also retain their original names and hashes in a local manifest.

Analysis history and actual viewing history are separate tables. Legacy
watching timestamps, progress and completion remain unknown. New viewing
sessions record actual media events, progress and playback starts. The local
completion field means the player reached its end, not proof that every second
was watched (seeking remains possible). The local
history does not grant long-term character memory consent; this scene does not
request the memory capability.

## Playback and preparation

New video preparation requires FFmpeg and FFprobe. Install both executables
and add their directory to PATH, or configure `NEKO_FFMPEG_PATH` and
`NEKO_FFPROBE_PATH` with their absolute executable paths before starting N.E.K.O.
For Windows, an FFmpeg distribution must contain both `bin/ffmpeg.exe` and
`bin/ffprobe.exe`; point the two variables at those files. Packaged launchers
must supply these prerequisites or set the variables to their bundled tools.
This repository does not bundle media binaries. Preparation checks both tools
before downloading or making paid model calls; existing history can still play.
The downloaded duration is checked again before frame extraction and analysis.
If it no longer satisfies automatic selection, preparation stops safely. Manual
videos found to exceed five minutes after download pause before analysis and
ask for confirmation using the actual duration. Accepting resumes that same
job; cancelling stops it. Unanswered requests expire after five minutes.

The trusted media host preloads reaction files, owns the only reaction audio
output, and uses `video.currentTime` for scheduling. Pause/buffering stops audio,
resume uses the current offset, seek discards stale reactions and rearms future
reactions, and playback rate follows video. Generation changes invalidate
pending mounts. A Web Lock prevents concurrent timeline output from two scene
windows. The watch-together route suppresses ordinary host/plugin speech for the
whole viewing session. External text and active voice input do not interrupt the
reaction timeline. Exit releases media, renderers and the SDK route.

Live2D and VRM have symmetric trusted avatar providers mounted through
`game.avatar`. Mouth opening uses the actual reaction waveform. Available happy
expressions are best-effort; missing model expressions do not block playback.
The scene avatar is distinct from the desktop renderer. Historical audio keeps
its original voice and is labelled accordingly; it is never mirrored through
desktop speech a second time.

New preparations reuse the Bilibili parser, five-second base frames, one-second
samples from three seconds before through one second after danmaku hotspots,
and 30-second visual analysis windows. Full preparation and official character
TTS preloading finish before playback. Provider-reported usage includes invalid
format attempts. Cached/reasoning tokens are subsets, and TTS is separate.
Missing usage is displayed as unrecorded. New audio comes from the character's
provider-neutral official PCM cache, with voice-change detection.

New preparations snapshot the character session language, prefer matching
subtitles, and generate reactions and laughter in that language. Progress and
warning keys are localized across all eight locales; legacy text/audio remains
unchanged. Discovery has a 180-second backend deadline and a 190-second host
timeout; metadata preparation uses a 75-second host timeout. Each preparation
imports only its own job without writing a migration report. Staging artifacts
remain available for recovery. SQLite connections close after each transaction.

## Validation and remaining boundaries

Playback starts one SDK-only next-video preparation in the background by default.
The opt-out checkbox clears its queue; already running server work can finish and
remains in history. The current video/audio is never replaced by a preparation
completion. A separate status and Watch next button expose the prepared result;
in manual mode the user chooses when to switch. Discovery excludes videos already selected in
this page and enforces the same strict duration/danmaku policy. Only one next
preparation is allowed, and manual prepare/search is disabled while it runs.

Automatic discovery is exposed through SDK `media.request('discover', {topic})`.
The scene can choose a relevant popular video itself: a supplied topic takes
priority, otherwise it uses the selected video's title, or the popular feed.
Topic search sorts by views; the search is bounded to three pages and does not
relax its thresholds on empty results. Metadata is checked again before starting
preparation and inside the engine: duration must be strictly less than 180 seconds
and danmaku count times 60 divided by duration must be strictly greater than 100.
Automatic discovery excludes multipart videos to avoid dividing a whole video's
danmaku count by a single part's duration. Manual mode keeps playback under the
user's Play control; the opt-in automatic mode continues through the queue.

Manual URLs above 300 seconds return `confirmation_required` before any job,
download, model request or TTS starts. The scene displays the title, duration and
cost/time warning; cancelling starts nothing. Confirmation submits the exact
inspected duration, and changed durations require confirmation again. Exactly
five minutes does not trigger the warning. The existing 20-minute limit remains.

Run `tests/unit/test_watch_together_*.py`, the mini-game manifest tests, and
`node tests/frontend/test_watch_together_media_runtime.mjs`. Existing SDK,
same-origin host and lifecycle regression scripts cover shared behavior. Build
the React chat package to update both floating and full-window entries.

This version uses the internal player. It does not read a Bilibili website tab,
provide a browser extension, perform ASR, or incrementally analyze while playing.
Without subtitles, it explicitly falls back to images, metadata and danmaku.
The parser currently bounds videos to 20 minutes and downloads to 1 GiB.
The official TTS preload interface does not expose the old provider-specific
laughter instruction; newly synthesized laughter therefore uses normal current
character TTS. All previously auditioned laughter files remain intact. Arbitrary
motion selection and PNGTuber/MMD avatar mounting are not part of this SDK API.
Electron host-shell window registration must be checked in its separate source
repository; both `/` and `/chat` share the new React entry and use same-origin
absolute URLs.

## Automatic watching and speech ownership

The stage toolbar has an opt-in Automatic watching checkbox. Its first real user
interaction unlocks the same video/reaction elements reused across videos. It can
start from the selected video or discover the first one. End-of-video advances to
the single prepared next item; missing results and failures retry with exponential
backoff capped at 60 seconds. Failed candidates are excluded from subsequent
searches in this page. Browser autoplay denial stops automatic mode and requires
a new user gesture. Stop watching cancels automatic transitions, pauses media and
releases the game route, including when startup is still pending.

The same game route stays active during preparation and automatic transitions,
so ordinary proactive/plugin respond cues remain behind the SessionManager
takeover gate. The bounded/coalescing proactive queue retains ordering and expiry
until takeover ends. External text and STT do not invoke generic game speech or
interrupt the reaction timeline. This deliberately suppresses ordinary speech for
the entire viewing session; it does not add inter-video live chat replies.
NEKO Live uses the existing plugin push_message respond channel; no plugin code
change or automatic stream publishing is performed by this feature. Streaming
software must already capture the companion player and its audio.

Automatic mode allows background playback and uses timeupdate as a media-clock
fallback when animation frames stop. Browser throttling, device sleep and audio
autoplay restrictions still apply; live platform output needs an actual broadcast
acceptance test. Ordinary mode still pauses when its document becomes hidden.

Drag the character container to move either Live2D or VRM. Drag its bottom-right
handle to resize, or focus that handle and use arrow keys. Bounds are clamped on
window resize/fullscreen changes; the SDK ResizeObserver resizes either renderer.
