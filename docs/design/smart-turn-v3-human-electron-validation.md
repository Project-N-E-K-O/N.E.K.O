# SmartTurn v3 human-voice and Electron validation

## Status and evidence boundary

SmartTurn v3 is not yet approved as product-quality endpointing for Chinese,
English, or Japanese. Validation deliberately produces two different evidence
layers:

1. **Model checkpoint replay** feeds authorized, labelled 16 kHz mono PCM16 WAV
   tails directly to the pinned SmartTurn ONNX model.
2. **Live Electron route acceptance** exercises the real Electron microphone,
   AudioWorklet, WebSocket, VAD, coordinator, SmartTurn, turn seal, and ASR
   route.

A checkpoint pass says only that the pinned model classified the registered
tails within the pre-registered bounds. It does not exercise Electron capture,
Silero candidate timing, coordinator retries, provider commit, or ASR/network
behavior. Consequently, `product_quality_approval.status` remains `blocked`
even when `gate.outcome` is `pass`; the registered live Electron matrix is
separate, required evidence.

## Privacy and local storage

Human recordings, manifests, criteria drafts, authorization records, run
sheets, reports, and diagnostic traces belong under
`data/smart_turn_v3_human/`. The repository ignores `/data/`, so raw voice and
its local metadata are not committed by the workflow.

- Obtain the speaker's authorization before recording or replaying their voice.
- Use independent, opaque 128-bit identifiers in the form
  `<type>-<32 lowercase hexadecimal characters>`, for example
  `speaker-0123456789abcdef0123456789abcdef`. The evaluator enforces this for
  dataset, case, source-group, speaker, session, device, and criteria IDs.
- Never encode a name, email, prompt, language, scenario, date, device serial,
  or filename in an ID. Keep the authorization-to-speaker mapping outside the
  manifest and report.
- Do not store transcripts, API keys, OS device labels, or absolute paths in
  the manifest or runtime trace.
- The evaluator's public case results retain only opaque case IDs and approved
  evaluation fields. WAV paths/basenames, speaker IDs, session IDs, device IDs,
  source-group IDs, and individual audio hashes are not emitted.
- Runtime diagnostics are disabled by default and accept no PCM or transcript
  payload.

## Versioned checkpoint manifest

The manifest is a JSON object with `schema_version: 1`, an opaque `dataset_id`,
and a non-empty `cases` array. Each case has exactly these fields:

```json
{
  "id": "case-0123456789abcdef0123456789abcdef",
  "path": "recordings/turn-0001.wav",
  "audio_sha256": "<64 lowercase hexadecimal characters>",
  "expected": "incomplete",
  "language": "zh-CN",
  "scenario": "sentence_internal_pause",
  "source_group": "source-0123456789abcdef0123456789abcdef",
  "speaker_id": "speaker-0123456789abcdef0123456789abcdef",
  "session_id": "session-0123456789abcdef0123456789abcdef",
  "device_id": "device-0123456789abcdef0123456789abcdef",
  "capture_surface": "electron",
  "capture_route_context": "dummy",
  "split": "holdout",
  "pause_ms": 650
}
```

The loader enforces these evidence contracts:

- `language` is `zh-CN`, `en-US`, or `ja-JP`; `expected` is `complete` or
  `incomplete`.
- `scenario` is `terminal_end`, `sentence_internal_pause`,
  `hesitation_continue`, `long_pause_continue`, `keyboard_noise`, or
  `barge_in`.
- `capture_route_context` is `dummy`, `glm`, or `gemini`. It is provenance in
  checkpoint replay, not a distinct model path: all three use the same pinned
  model and production decision threshold. Do not duplicate audio across route
  labels.
- WAV input is non-empty, at most eight seconds, mono, 16 kHz, and signed
  PCM16. Paths are relative to the manifest and cannot escape through `..`, an
  absolute path, or a symlink.
- `audio_sha256` must match the exact WAV file bytes. The loader also hashes the
  decoded PCM and rejects duplicate audio even if it was repackaged into a
  byte-different WAV. The report exposes one `dataset.audio_corpus_sha256`
  derived from the sorted PCM digests, rather than exposing per-file hashes.
- Case IDs and paths are unique. A speaker or source group cannot cross the
  calibration/holdout boundary. Tails cut from one continuous recording must
  share one `source_group`.
- The collector and human label review must confirm that `pause_ms` is an
  actual silent or speech-inactive interval in the WAV. The loader cannot
  prove that acoustic fact; it enforces arithmetic feasibility only:
  `pause_ms` must be at least the deployed `candidate_silence_ms` (currently
  300 ms), and WAV duration must be at least
  `pause_ms + minimum_speech_ms` (currently another 200 ms). It also rejects a
  WAV whose decoded PCM is shorter than the frame count claimed by its header.

Generate `audio_sha256` locally from the final WAV bytes after capture and any
authorized trimming. Changing the audio afterward intentionally invalidates the
manifest.

## Collection matrix

A complete label means the speaker intended the turn to end at the labelled
pause. An incomplete label means they intended to continue after that pause.
Record intent before inference; do not infer it from punctuation or the model's
answer.

| Scenario | Required label shape | Human action |
| --- | --- | --- |
| `terminal_end` | complete only | Finish a natural sentence and remain silent. |
| `sentence_internal_pause` | incomplete only | Pause naturally between clauses, then continue. |
| `hesitation_continue` | incomplete only | Use a filled hesitation, pause, then continue. |
| `long_pause_continue` | incomplete only | Deliberately pause longer than an ordinary clause pause, then continue. |
| `keyboard_noise` | both labels | Repeat complete and continuation cases while typing nearby. |
| `barge_in` | both labels | Begin while N.E.K.O audio is ending, then finish or continue as labelled. |

Use semantically equivalent natural prompts in each language, with varied
speaking rate, pitch, accent, and hesitation. The product owner must
pre-register the exact speaker count, a SHA-256 digest of the sorted opaque
speaker roster, device minimum, exact per-speaker scenario-by-label matrix,
confidence level, and error bounds before collecting the holdout. Freeze and
archive the manifest and criteria before running inference. Every holdout
speaker in a language must match both the frozen roster and that matrix
exactly; replacing, adding, or removing a speaker or tail makes the evidence
`blocked` rather than silently changing the stopping rule or reweighting a
speaker.

Generate the frozen evidence digests with the supported evaluator mode; do not
write a one-off manifest scanner:

```powershell
uv run python scripts/evaluate_smart_turn_v3.py `
  --manifest data/smart_turn_v3_human/pilot/manifest.json `
  --evidence-digest-output `
    data/smart_turn_v3_human/pilot/evidence-digests.json
```

This mode validates the complete manifest and every WAV, writes the exact
`manifest_sha256`, and writes `speaker_count` plus
`speaker_roster_sha256` for each language's holdout. It returns before loading
the ONNX model, so generating registration material cannot expose model
results. Copy these values into the criteria file, then archive both inputs.

For independent verification, the roster digest is defined over the unique
holdout `speaker_id` values for one language. Sort the opaque IDs in ascending
ASCII order, serialize the array as UTF-8/ASCII JSON with no whitespace and no
trailing newline, and SHA-256 those exact bytes. In Python notation, the
canonical bytes are:

```python
json.dumps(sorted(unique_speaker_ids), separators=(",", ":")).encode("ascii")
```

For two IDs, the hashed bytes therefore look exactly like
`["speaker-0123...","speaker-abcd..."]`. The supported command above is the
source of truth and avoids differences caused by newlines, locale sorting, or
pretty-printed JSON.

## Pre-registered checkpoint gate

The criteria file is separate from the manifest so observed probabilities
cannot move the goalposts. Its `schema_version: 1` payload contains an opaque
`criteria_id`, `manifest_sha256` for the complete frozen manifest,
`confidence_method: "wilson"`, a confidence level, and one language entry for
each of `zh-CN`, `en-US`, and `ja-JP`. Freeze the manifest and fill this digest
before any model inference. A later change to a case ID, audio hash, speaker
assignment, scenario, label, split, or other manifest evidence blocks the
gate, even if speaker counts and the roster itself still match.

`decision_threshold` must equal the deployed
`SmartTurnConfig().evaluation_threshold`, currently `0.5`. A different value
is rejected; the evaluator cannot be used to claim acceptance at a friendlier
non-production threshold. Each language entry pre-registers:

- exact `speaker_count`, `speaker_roster_sha256` derived from the sorted opaque
  speaker IDs, and `min_devices`;
- the exact `speaker_scenario_matrix`, including complete and incomplete counts
  for all six scenarios;
- `max_speakers_with_any_premature_split` and
  `max_speakers_with_any_missed_endpoint`;
- maximum Wilson upper bounds for the speaker premature-split and
  missed-endpoint rates.

The statistical unit is a speaker, not a correlated audio tail. For every
matrix-compliant speaker, one or more false-complete decisions across that
speaker's incomplete cases count as one speaker with a premature split. One or
more false-incomplete decisions across the speaker's complete cases count as
one speaker with a missed endpoint. Wilson intervals are calculated over those
speaker-level any-error counts. They are two-sided at `confidence_level`, using
`NormalDist().inv_cdf(0.5 + confidence_level / 2.0)`; the reported upper bound
therefore uses the `(1 + confidence_level) / 2` quantile (0.975 when
`confidence_level` is 0.95).

Raw case confusion cells, complete recall, continuation specificity,
premature-split rate, missed-endpoint rate, and balanced accuracy remain useful
descriptive diagnostics. They are named `descriptive_case_*` in the report and
are not gate confidence intervals because repeated tails from one speaker are
correlated.

No product-owner values for speaker count or allowable error have been
committed here. Without a criteria file the result is `exploratory`. Missing or
matrix-noncompliant evidence is `blocked`; adequate evidence outside an error
bound is `fail`; only a complete holdout within every bound is `pass`.
Calibration is reported separately and never participates in the gate.

The criteria shape below is intentionally a non-runnable template: replace
every angle-bracket placeholder with a JSON number or digest chosen/frozen by
the product owner. Do not derive an allowable error after seeing predictions.
Repeat the complete language object for `en-US` and `ja-JP` using their own
generated roster values.

```jsonc
{
  "schema_version": 1,
  "criteria_id": "criteria-<32 lowercase hexadecimal characters>",
  "manifest_sha256": "<copy from evidence-digests.json>",
  "decision_threshold": 0.5,
  "confidence_level": <product-owner value between 0.5 and 1.0>,
  "confidence_method": "wilson",
  "holdout": {
    "languages": {
      "zh-CN": {
        "speaker_count": <copy generated integer>,
        "speaker_roster_sha256": "<copy generated digest>",
        "min_devices": <pre-registered integer>,
        "speaker_scenario_matrix": {
          "terminal_end": {"complete": <count>, "incomplete": 0},
          "sentence_internal_pause": {"complete": 0, "incomplete": <count>},
          "hesitation_continue": {"complete": 0, "incomplete": <count>},
          "long_pause_continue": {"complete": 0, "incomplete": <count>},
          "keyboard_noise": {"complete": <count>, "incomplete": <count>},
          "barge_in": {"complete": <count>, "incomplete": <count>}
        },
        "max_speakers_with_any_premature_split": <pre-registered integer>,
        "max_speakers_with_any_missed_endpoint": <pre-registered integer>,
        "max_speaker_premature_split_rate_upper_bound": <rate from 0 to 1>,
        "max_speaker_missed_endpoint_rate_upper_bound": <rate from 0 to 1>
      },
      "en-US": {"<same required fields>": "<language-specific values>"},
      "ja-JP": {"<same required fields>": "<language-specific values>"}
    }
  }
}
```

Run checkpoint replay with the pinned model:

```powershell
uv run python scripts/prepare_voice_turn_assets.py
uv run python scripts/evaluate_smart_turn_v3.py `
  --manifest data/smart_turn_v3_human/pilot/manifest.json `
  --criteria data/smart_turn_v3_human/pilot/criteria.json `
  --output data/smart_turn_v3_human/pilot/report.json `
  --markdown-output data/smart_turn_v3_human/pilot/report.md
$LASTEXITCODE
```

The evaluator writes requested JSON/Markdown reports before returning. When a
criteria file is supplied, both `blocked` and `fail` return exit code `2`; only
`pass` returns `0`. Exploratory runs without criteria return `0` but are not an
acceptance signal.

The gate first requires the loaded manifest SHA-256 to match the digest frozen
inside the criteria. The report also binds the evidence to the manifest and
criteria SHA-256 values, the
pinned model version/SHA, and the evaluator script SHA-256. Provenance also
contains Git revision (suffixed `-dirty` when tracked or untracked changes are
present), platform, Python, NumPy, and ONNX Runtime versions. Latency is split
between calibration and holdout and separates the cold first inference from
warm measurements. These bindings make a run auditable; they do not remove the
need to archive the ignored local inputs securely.

## Legacy JSONL compatibility

`--fixture-dir` plus `--labels` remains available only to replay old unversioned
fixtures. Legacy cases have unknown speaker, session, device, and source-group
provenance, cannot use a criteria file, and always remain exploratory. For
compatibility, an old WAV longer than eight seconds is accepted and the model's
existing trailing-window behavior is preserved. This mode redacts filenames
from results but cannot satisfy the versioned evidence, deduplication, matrix,
or speaker-level gate requirements; do not use it for product-quality claims.

## Opt-in runtime diagnostics

Enable the privacy-minimized runtime trace before starting the backend. The
path must resolve under this repository's ignored `data/` directory and end in
`.jsonl`; otherwise diagnostics safely remain disabled.

```powershell
# N.E.K.O backend terminal, set before launcher.py
$env:NEKO_SMART_TURN_DIAGNOSTICS = '1'
$env:NEKO_SMART_TURN_DIAGNOSTICS_PATH = `
  'data/smart_turn_v3_human/pilot/runtime-diagnostics.jsonl'
```

Accepted opt-in values are `1`, `true`, `yes`, and `on`. If no path is set,
the default is `data/smart_turn/runtime-diagnostics.jsonl`.

Each diagnostics sink/session receives a fresh random 128-bit `run_id`. JSONL
events contain only the schema, sequence number, monotonic elapsed
milliseconds, event type, and an event-specific allowlist:

- `session_start` and `session_end`;
- `candidate` with a bounded reason;
- `evaluation` with reason, outcome, evaluation duration, a valid bounded
  probability when available, and the coordinator's actual threshold;
- `complete` with reason; and
- `failure` with bounded kind and stage.

There is no wall-clock timestamp, PCM, transcript, prompt, case/speaker/device
identifier, path, provider credential, or API key. Invalid probabilities are
omitted. Writing occurs on a daemon worker; flush/close acknowledgement is
bounded to 50 ms, and trace I/O failure does not block the voice runtime. Treat
the trace as operational sequence evidence, not proof of the user's labelled
intent; correlate it with a separate local, opaque run sheet.

## Opt-in local audio evidence

Raw SmartTurn audio evidence is a separate opt-in from the privacy-minimized
JSONL trace. Use it only after the speaker has authorized local retention of
their voice. The recorder stores completed SmartTurn turn windows as 16 kHz
mono PCM16 WAV files plus a JSONL index under the ignored `data/` tree; it
does not store transcripts, prompts, API keys, microphone names, or absolute
paths.

```powershell
# N.E.K.O backend terminal, set before launcher.py
$env:NEKO_SMART_TURN_AUDIO_EVIDENCE = '1'
$env:NEKO_SMART_TURN_AUDIO_EVIDENCE_DIR = `
  'data/smart_turn_v3_human/pilot/audio-evidence'
```

Accepted opt-in values are `1`, `true`, `yes`, and `on`. If no directory is
set, the default is `data/smart_turn/audio-evidence`. A configured directory
outside repository `data/` disables capture. Each runtime session writes to a
fresh random run directory containing `turn-0001.wav`, `turn-0002.wav`, and an
`index.jsonl` with duration, SHA-256, decision reason, probability, threshold,
and truncation metadata.

For Japanese continuation reproduction, use this sentence first. Read through
the comma with a natural 0.5-0.8 second pause, then continue without pressing
stop:

```text
今日は駅で友達と待ち合わせをして、そのあと新しい喫茶店に行きます。
```

The 2026-08-04 live Japanese pilot showed three premature completions from a
single read of this sentence. All completions were direct `candidate_pause`
decisions with high SmartTurn probabilities, so protecting only the
`strict_retry` path is insufficient. Product runtime now treats a SmartTurn
`candidate_pause` COMPLETE as provisional for
`SmartTurnConfig.candidate_complete_confirmation_seconds` (default 1.0 s).
If Silero reports resumed speech inside that window, the provisional completion
is cancelled and audio stays in the same logical turn; otherwise the completion
is published after the short confirmation delay.

## Live Electron acceptance

Start with the `dummy` ASR override. It retains the real Electron microphone,
48 kHz AudioWorklet capture, PCM conversion, WebSocket, VAD, SmartTurn, and turn
seal path while removing cloud credentials, provider/network latency, and
transcription variability from the endpointing result:

```powershell
# N.E.K.O backend terminal
uv run python scripts/prepare_voice_turn_assets.py
$env:NEKO_SMART_TURN_DIAGNOSTICS = '1'
$env:NEKO_SMART_TURN_DIAGNOSTICS_PATH = `
  'data/smart_turn_v3_human/pilot/runtime-diagnostics.jsonl'
$env:NEKO_SMART_TURN_AUDIO_EVIDENCE = '1'
$env:NEKO_SMART_TURN_AUDIO_EVIDENCE_DIR = `
  'data/smart_turn_v3_human/pilot/audio-evidence'
$env:ASR_PROVIDER = 'dummy'
uv run python launcher.py
```

```powershell
# N.E.K.O.-PC terminal
$env:NEKO_USER_DATA_DIR = '<an isolated local directory>'
npm ci
npm start
```

Use F2 or the microphone button to start and stop an actual voice session. Run
the complete pre-registered scenario/label matrix in all three languages. In a
local opaque run sheet, record the intended label and trace run, then verify:

- candidate, evaluation, completion, and final ordering with relative timing;
- premature split, missed endpoint, duplicate final, and recovery behavior;
- microphone permission denial/regrant, mute, stop/restart, device switch, and
  hot-plug behavior; and
- clean teardown with no late seal or final from the old session.

A dummy pass validates endpointing-path integration, not GLM or Gemini service
acceptance. After isolation, remove `ASR_PROVIDER` and repeat the fully
registered matrix on each supported segmented provider route separately. Never
merge routes or use a partial rerun to clear a failed or missing stratum.

## Required interpretation

Checkpoint replay answers, “How did the pinned model classify these authorized
tails?” Live Electron acceptance answers, “Did the shipped interaction path
seal the intended turn at the right time and recover correctly?” Provider-route
acceptance additionally answers whether the real service commits and returns
correctly. No one layer can substitute for the others.
