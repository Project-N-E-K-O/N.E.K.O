# Natural-expression candidate miner

`scripts/natural_expression_candidate_miner.py` is a maintainer-only, offline
discovery tool. It counts repeated assistant phrases in a local corpus that a
maintainer explicitly names and writes **pending review candidates**. It does not
generate regular expressions, replacements, or runtime rules.

The repository's user-facing conversation export is not a stable machine-readable
message corpus for this purpose, so the miner uses the deliberately small JSONL
contract below rather than depending on an internal database or UI export shape.

## Input contract

Pass one local JSONL file with `--input`. Each non-empty line must be one JSON
object with:

- `role`: required string. Only the exact value `assistant` is analyzed; all
  other roles are validated and ignored.
- `content`: required string.
- `lang`: required on assistant records unless `--language` is supplied.
- `conversation_id`: optional string. It is accepted only to make separately
  exported conversations easier to combine; it is never copied to the output.

Supported language tags are `en`, `es`, `pt`, `ru`, `ja`, `ko`, `zh`, `zh-CN`,
and `zh-TW`, plus common locale aliases such as `pt-BR`, `ja-JP`, and `ko-KR`.
`--language` overrides every record's `lang`. There is no automatic language
detection or guessing.

Synthetic example:

```jsonl
{"role":"system","content":"Synthetic instruction","lang":"en","conversation_id":"demo-a"}
{"role":"user","content":"Synthetic question","lang":"en","conversation_id":"demo-a"}
{"role":"assistant","content":"A quiet lantern lit the path.","lang":"en","conversation_id":"demo-a"}
```

The tool never scans a directory, database, default chat-history location, or
user profile. Prepare the JSONL file yourself and pass its exact path.

## Run it

All project Python commands use `uv run`:

```bash
uv run python scripts/natural_expression_candidate_miner.py \
  --input C:/maintainer/corpora/synthetic-or-consented.jsonl \
  --output C:/maintainer/review/natural-expression-candidates.json
```

Useful controls:

- `--threshold 3`: minimum total occurrences. The default starts at three.
- `--word-ngram-min` / `--word-ngram-max`: Unicode word n-grams for English,
  Spanish, Portuguese, and Russian. Defaults to 2–5 words.
- `--cjk-ngram-min` / `--cjk-ngram-max`: character-fragment n-grams for Chinese
  and Japanese, plus the character side of Korean's hybrid strategy. Defaults
  to 4–8 characters.
- `--min-length`: minimum non-space character length.
- `--exclude-covered`: omit a candidate only when every mined occurrence falls
  inside a current curated `SLOP_RULES` match. Partially covered candidates stay
  visible with `covered_by_rule_ids` for review.
- `--debug-candidates`: explicitly print candidate phrases. This can expose
  assistant text and is off by default.

Korean uses both Unicode word n-grams and Hangul character fragments: normal
space-delimited prose benefits from word boundaries, while repeated compounds
and onomatopoeia can still be discovered without a morphological analyzer.
CJK punctuation creates hard boundaries, so candidates never join text from
opposite sides of `。`, `！`, `？`, `，`, or related punctuation.

Fenced code, inline code, URLs, numeric-only material, and obvious placeholder
templates are excluded. System and user content never enters candidate counts.
Coverage checks run curated patterns against the original assistant message so
runtime punctuation, normalization, and line boundaries are preserved; matches
overlapping runtime-protected code or URLs are ignored. Original message context
is retained only in memory and is never written to the report.
Candidate spans are compared with complete runtime matches, so a pattern longer
than an individual n-gram can still be recognized.

## Review artifact

The output is deterministic JSON with no timestamp or random identifier. Given
identical input bytes, arguments, and rule-table version, repeated runs produce
identical output bytes. Candidates are stably sorted by language, message count,
occurrence count, and phrase.

Each candidate contains only:

```json
{
  "covered_by_rule_ids": [],
  "language": "en",
  "message_count": 3,
  "normalized_phrase": "a quiet lantern",
  "occurrence_count": 4,
  "phrase": "A quiet lantern",
  "status": "pending"
}
```

The top-level `schema_version` is
`natural-expression-candidates/v1`, and the artifact shape is intentionally
incompatible with the runtime rule schema. It has no `find`, `replace`, or
activation field and cannot be loaded by `utils.slop_filter`.

Default terminal output contains only input/output paths, aggregate counts, and
language names. Candidate phrases are written only to the explicit output file.
That file can contain fragments of assistant replies and must be handled with the
same care as conversation data. It intentionally contains no full context, user
content, system content, or conversation identifiers.

## Manual review and promotion

1. Review each pending phrase for false positives, genre-specific language, and
   whether repetition across messages is actually undesirable.
2. Check `covered_by_rule_ids`; it records rules covering at least one
   occurrence. A partially covered phrase remains pending, while a fully covered
   phrase normally needs no new runtime rule.
3. Reject, merge, or refine candidates manually outside this artifact.
4. In a separate maintainer change, manually add a curated, language-specific
   pattern and deterministic replacement pool to
   `config/prompts/prompts_slop.py`, with focused synthetic tests.

The miner never writes `config/prompts/prompts_slop.py`, never turns a candidate
into a regex, never calls an LLM or third-party API, never learns during runtime,
and never enables a rule. Promotion is always an explicit human maintenance
decision.
