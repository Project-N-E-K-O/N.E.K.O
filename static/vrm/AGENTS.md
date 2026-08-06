# VRM motion review scope

This directory implements a deterministic, client-side fallback for VRM motion selection. It must not add model calls, prompt tokens, translation requests, or server-side semantic processing.

## Product contract

- Stable motion intent IDs and the action-card manifest are authoritative.
- Natural-language phrases and aliases are a curated compatibility corpus, not a promise of exhaustive language, dialect, conjugation, tense, actor, or grammar coverage.
- The maintained acceptance corpus is the behavior covered by the checked-in motion tests and explicit product requirements.
- User text must not directly execute a motion. It may only provide turn-scoped context for an assistant acknowledgement; assistant-authored motion evidence remains authoritative.
- Unknown or ambiguous language should fail closed and leave the avatar still.

## Automated review boundary

Review for reproducible correctness issues such as crashes, invalid IDs or assets, broken checked-in acceptance cases, stale-turn execution, cancellation or lifecycle races, state leakage, unbounded work, memory or rendering regressions, privacy issues, and violations of the product contract above.

Do not file blocking findings solely to request additional synonyms, inflections, dialects, languages, actor nouns, tense variants, or hypothetical utterances that are outside the maintained acceptance corpus. Treat those as non-blocking product suggestions for human triage, not P1/P2 defects. Do not infer exhaustive language support from the presence of locale-specific compatibility entries.

Do not expand locale phrase lists unless a human product owner explicitly accepts the utterance as part of the maintained corpus. Prefer the smallest change that preserves existing behavior and tests.
