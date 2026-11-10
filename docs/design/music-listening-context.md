# Song mood and current-turn listening context

The player publishes a playback identity and actual audible state in both the
browser page and Electron's chat/model windows. Occupied, loading, buffering,
paused, ended and audible are distinct. A model-window controller uses the
audible state for an optional `listening` expression; chat, speech and direct
interaction take priority. An operation generation cancels stale fades and SDK
loads. Missing expression mappings leave the avatar's existing behavior intact.

## Whole-track text classification

`utils/music_mood.py` uses available song tags or publicly accessible NetEase
lyrics, then calls the configured emotion text model. It does not download or
analyze audio, infer from private chat, use an audio capture device, or hardcode
particular songs. A title without adequate lyrics/tags returns `unknown` without
calling a model. Textual arousal is tentative: lyrics can disagree with the
arrangement. Source and classifier version accompany confidence and the
joyful/tender/calm/sad/intense/unknown label, valence and arousal.

This optional text inference uses the existing provider's quota. Set
`NEKO_MUSIC_MOOD_ENABLED=0` before launching to disable lyrics and model calls;
playback and the mild default listening state remain available. Requests contain
only bounded title, artist, tags and lyric text, never signed media URLs, covers,
account cookies, conversations or model settings. Lyrics use a fixed public
origin without account credentials; provider errors or unavailable lyrics do
not interrupt playback or chat.

Limits: 1,800 input tokens, 256 output tokens, 12-second model timeout within a
19-second classification budget; no more than two pending classifications and
six starts per minute. The bounded in-memory cache keys on stable track identity,
evidence and classifier version, retaining known results for one day and unknown
results for five minutes. It does not persist lyrics. Confidence is capped at
0.78 for lyrics and 0.68 for tags; below 0.6 uses the default listening pose.
Pause, track change and model change invalidate pending frontend ownership;
late results cannot reactivate a stopped or replaced expression.

## One-shot observation

Immediately before an ordinary text message starts a reply, the chat window
requests one observation from the model window through the existing partition
bridge. It waits at most 300 ms. No poll or proactive reply is created. The
snapshot binds request ID, playback ID and timestamp to the current track.

The optional Live2D expression profile extension supplies a recent final render
sample and the visible accent state. Without that extension, the snapshot can
still carry the confirmed track and classification, but cannot claim observed
gestures. Tears are included only when the expression's tear paths are actually
visible; a sad label alone does not establish tears. Native YUI gestures are
recognized only for the three audited rigs. Other models retain generic face
observations when available. Profile format and installation belong to the
separate Live2D expression PR; this music feature degrades safely without it.

The backend revalidates against its owning player's current state immediately
before dispatch, including pause/resume and connection loss. A validated short
natural-language block uses the actual character name and the turn's language
(Simplified/Traditional Chinese, English or Japanese; English fallback). Persona
and voice stay with the active character configuration. The block is merged into
`TurnContext.ephemeral`, excluded from stored history and memory inputs. Ordinary
user/assistant messages keep their normal history behavior. Voice-only turns and
synthetic/proactive inputs do not attach this snapshot.

## Validation

Frontend contracts cover both surfaces, replacement tracks, pause/resume,
buffering, generation cancellation, missing model windows and real bridge state.
Backend tests exercise the actual offline request view and saved history, plus
owner connection loss, TTL, request mismatch, low confidence and failure/cache
boundaries. These tests do not measure visual expression quality. Desktop-size
appearance requires separate comparison with the optional profile extension.
