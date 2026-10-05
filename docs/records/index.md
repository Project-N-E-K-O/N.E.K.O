# Project Records

This section collects implementation rationale and dated evidence that are useful to maintainers but are not the primary user or API documentation.

## Record collections

- [Design and implementation records](/design/) — decisions, constraints, implemented RFCs, compatibility notes, and scoped proposals.
- [Runtime benchmarks](/benchmarks/) — dated memory and lifecycle measurements with their test conditions.
- [Plugin SDK change notes](/changelog/) — migration-oriented notes for significant plugin API additions or changes.

## Incident records

- [2026-09-27: Qwen screen-comment repetition](/records/2026-09-27-qwen-screen-chat-repetition) — Chinese-only investigation snapshot; local scoped fix validated, incident remains open pending deployment and recovery verification.

## How to read records

The current code, tests, public guides, and API reference take precedence. A record may explain why a behavior exists without guaranteeing that every implementation detail is still current.

Before using a record as implementation authority:

1. check its status and date;
2. follow its code and test references;
3. compare them with the current branch;
4. confirm future work through an accepted issue or maintained project board.

See [Documentation Maintenance](/contributing/documentation) for ownership, translation, and status rules.
