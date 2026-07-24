# User-driven memory evidence: implemented design record

This page describes the evidence mechanism implemented for reflections and
persona entries. Active values live in `config/memory_settings.py`; this document
explains their relationships and runtime boundaries. For the broader storage and
recall model, see [Memory System](/architecture/memory-system).

## Status and purpose

The mechanism is implemented. It gives an observation a user-driven confidence
history instead of treating every synthesized reflection as permanently true.
Positive and negative evidence decay independently, drive reflection promotion,
limit prompt growth, and eventually move persistently net-negative entries into
archives.

Evidence is not the same as explicit memory recall. The public
`POST /query_memory/{character}` path uses BM25, optional cosine similarity, time
filtering, and RRF. The vector-plus-LLM reranker in `memory/recall.py` is an
internal candidate selector for evidence signal detection; it is not a second
stage of the public recall endpoint.

## Data model and score

Non-protected reflection and persona entries can carry:

```text
reinforcement
disputation
rein_last_signal_at
disp_last_signal_at
sub_zero_days
sub_zero_last_increment_date
user_fact_reinforce_count
```

Decay is computed at read time rather than written back on every read:

```text
effective_reinforcement = reinforcement * 0.5 ^ (rein_age_days / 30)
effective_disputation   = disputation   * 0.5 ^ (disp_age_days / 180)
evidence_score          = effective_reinforcement - effective_disputation
```

The two clocks are independent: a new positive signal does not make old negative
evidence fresh, and vice versa. Missing or invalid timestamps are treated as age
zero. `disputation` is clamped at zero; `reinforcement` may become negative after
ignored feedback.

Entries sourced from the character card are `protected`. Their score is treated
as positive infinity, and they are excluded from evidence-based archival and
non-protected prompt-budget eviction.

## Initial seed

A synthesized reflection normally starts at zero, with one exception for the
highest source-fact importance:

| Maximum importance | Initial reinforcement |
|---:|---:|
| 10 | 0.8 |
| 9 | 0.6 |
| 8 | 0.4 |
| 7 | 0.2 |
| 6 or lower | 0.0 |

This seed accelerates important observations but does not itself bypass the
confirmation and promotion thresholds.

## Signal sources and weights

| Source | Effect | Weight |
|---|---|---:|
| A newly extracted user fact reinforces an existing observation | `reinforcement +=` | 0.5 |
| A newly extracted user fact negates an existing observation | `disputation +=` | 1.0 |
| User confirms a surfaced reflection | `reinforcement +=` | 1.0 |
| User denies a surfaced reflection | `disputation +=` | 1.0 |
| Negative-keyword detection plus LLM target confirmation | `disputation +=` | 1.0 |
| User ignores a surfaced reflection | `reinforcement +=` | -0.2 |

Fact-to-observation mapping is intentionally weaker on positive inference than on
direct feedback. After more than two lifetime `user_fact` reinforces, each later
reinforce also receives a 0.5 combo bonus, making the third and later inferred
reinforcements worth 1.0. The counter does not decay, while the resulting score
still does.

AI output does not directly reinforce its own observations. The AI-aware fact
extraction path can preserve useful AI/context disclosures, but those facts are
marked so they do not enter the Stage-2 evidence loop and create a self-reinforcing
cycle.

## Signal extraction pipeline

When powerful memory is enabled, the background signal loop batches user
conversation and runs:

1. Stage 1: extract new user-observation facts.
2. Build a candidate pool from active persona entries and reflections.
3. Exclude protected, suppressed, terminal, and net-negative candidates.
4. Optionally use local embeddings to pre-rank candidates; when the candidate set
   is still larger than the budget, an internal LLM reranker can reduce it.
5. Stage 2: ask the LLM whether each new fact reinforces or negates a selected
   observation.
6. Apply full-snapshot evidence events and mark only successfully processed facts.

The Stage-2 prompt is capped by the configured observation and token budgets.
Failures leave unprocessed facts eligible for a later retry instead of silently
advancing the batch marker.

Separately, post-turn processing checks the user's response to surfaced
reflections. Confirmation, denial, and ignore decisions update evidence; denial
also moves the reflection to the denied lifecycle state. A local negative-keyword
hook uses an LLM target check before applying a rebuttal signal, avoiding a raw
keyword match against every observation.

## Derived tiers and stored lifecycle

The score maps to these derived tiers:

| Score | Derived tier |
|---:|---|
| `>= 2.0` | `promoted` |
| `>= 1.0` and `< 2.0` | `confirmed` |
| `> -2.0` and `< 1.0` | `pending` |
| `<= -2.0` | `archive_candidate` |

The tier is a decision result, not always the literal stored `status` field.
With powerful memory enabled:

- a pending reflection at score 1.0 or higher becomes confirmed;
- a confirmed reflection at score 2.0 or higher enters merge-on-promote;
- merge-on-promote may create a persona entry, merge into an existing entry,
  reject the merge, or place repeated failures in a `promote_blocked` state;
- merge updates carry `merged_from_ids` so retries are idempotent and provenance
  remains visible.

Promotion LLM calls run outside the per-character lock. The final write rechecks
the stored status under the lock so a late promotion result cannot overwrite a
newer denial or archive transition.

## Archival

`archive_candidate` is descriptive; it is not the actual archive trigger. An
hourly sweep checks non-protected entries:

1. while `evidence_score < 0`, increment `sub_zero_days` at most once per calendar
   day;
2. do not erase that lifetime tally when the score later becomes non-negative;
3. at 14 accumulated days, remove the entry from the active view and append it to
   a dated, size-bounded archive shard.

Reflection and persona archives use the event journal so a crash between active
view removal and shard append can be repaired from the event snapshot on the next
startup.

## Rendering and repetition control

Evidence also controls what reaches the system prompt:

- non-protected persona content has a 2,000-token budget;
- pending plus confirmed reflections share a separate 2,000-token budget;
- higher-scoring entries win budget pressure;
- confirmed reflections at score zero or below are not rendered;
- character-card protected entries remain outside the non-protected budget.

`recent_mentions` is a separate anti-repetition mechanism. If the AI repeatedly
mentions a persona entry or confirmed reflection within its rolling window, the
entry can be suppressed from rendering. Suppression measures AI repetition; it
does not add positive or negative user evidence. Pending reflections remain
eligible for surfacing so the user can confirm or reject them.

## Powerful-memory switch

`powerful_memory_enabled` is read from `core_config.json` and defaults to true.
It is hot-read rather than cached.

When enabled, batched Stage-1/Stage-2 signal extraction, score-driven promotion,
merge decisions, correction work, and refinement loops are active.

When disabled:

- the per-turn baseline still extracts facts;
- recent compression/review, reflection synthesis, explicit recall, repetition
  checks, and surfaced-feedback checks remain available;
- pending reflections auto-confirm after 7 days and confirmed reflections
  auto-promote after another 7 days through the simple non-LLM merge path;
- that fallback promotion path uses age, not `evidence_score`.

Turning the switch back on starts signal extraction from the current cursor; it
does not bulk-replay every user message accumulated while the feature was off.

## Persistence and recovery

Evidence updates, merge rewrites, relevant reflection state changes, and archive
transitions use the event-journal `record_and_save` contract. Payloads carry full
counter snapshots rather than arithmetic deltas, making replay idempotent.

At startup the reconciler repairs unapplied covered events before one-shot
evidence/archive migrations run. The event journal is not a complete history of
all fact, reflection, or persona writes; see
[Memory event journal](./memory-event-log-rfc) for the exact handler coverage.

## Known boundaries

- Surfaced feedback is persisted before its evidence update. If the downstream
  evidence write fails after feedback persistence, that signal is logged as lost
  and is not automatically retried by an outbox.
- Topic-level evidence, clustering, and automatic topic avoidance are not
  implemented.
- Evidence is per character; it is not shared across characters.
- The memory browser does not provide per-entry score editing and does not edit
  facts, reflections, persona, journals, or archives.
- The internal Stage-2 LLM can misclassify a relationship. Indirect positive
  signals are deliberately weighted lower, but the mechanism is not a proof of
  factual truth.
- Current journals recover the evidence/lifecycle paths with registered handlers,
  not every mutation in the wider memory system.

## Current code entry points

- `config/memory_settings.py` — thresholds, weights, schedules, budgets, and model tiers
- `memory/evidence.py` — decay, score, derived status, seed, and snapshot math
- `memory/facts.py` — Stage-1 extraction and Stage-2 signal mapping
- `memory/recall.py` — internal evidence-candidate pre-rank/rerank
- `app/memory_server/signal_extraction.py` — background scheduling and signal dispatch
- `app/memory_server/post_turn.py` — surfaced feedback and per-turn gates
- `app/memory_server/evidence_loops.py` — promotion, migrations, and archive sweep
- `memory/reflection/evidence_flow.py` — reflection evidence and archival
- `memory/reflection/promotion.py` — score-driven and time-driven lifecycle paths
- `memory/reflection/promotion_merge.py` — merge-on-promote and retry/dead-letter state
- `memory/persona/facts.py` — persona evidence, merge, and archival
- `memory/persona/rendering.py` — score filters, token budgets, and prompt rendering
- `memory/event_log.py` and `memory/evidence_handlers.py` — durable event/replay path

## Non-goals

- Letting model output reinforce itself
- Cross-character confidence transfer
- A learned ranking model for truth or confidence
- User-editable evidence counters in the public UI
- Topic graphs, global clustering, or a graph database
- Treating the score as certainty rather than an auditable interaction signal
