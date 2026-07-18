# Contributing

Project N.E.K.O. accepts focused code, documentation, translation, testing, and content-tooling contributions.

## Workflow

1. Fork and create a focused branch from current `main`.
2. Read `.agent/rules/neko-guide.md` and any matching `.agent/skills/*/SKILL.md`.
3. Set up Python 3.11 with `uv sync`; build frontends with the repository scripts.
4. Make a minimal, symmetric change in the owning module.
5. Run targeted tests, then the relevant lint/build checks.
6. Open a PR describing behavior, risk, and validation.

All Python commands use `uv run`. User-visible i18n changes update all eight locale files together.

## PR gates

- Changes to Python under `app/`, `main_logic/`, or `memory/` require the regression-report section expected by `scripts/check_pr_report.py`.
- A PR with more than 20 counted files requires a non-empty no-split rationale. New files, recognized test files, and synchronized i18n locale groups are excluded by that gate's rules.
- Static analysis and plugin tests are defined by current workflows, not by an old checklist.

Read [Testing](./testing), [Code Style](./code-style), [Documentation Maintenance](./documentation), and [Nuitka Packaging](./nuitka-packaging) before changing their areas.

Maintainer-only content tools have their own safety contracts. The [natural-expression candidate miner](./natural-expression-candidate-miner) accepts only explicitly supplied local data and never promotes runtime rules automatically.

## Reports and community

Use [GitHub Issues](https://github.com/Project-N-E-K-O/N.E.K.O/issues) for reproducible bugs and scoped proposals. Include environment, exact revision/artifact, expected/actual behavior, sanitized logs, and minimal reproduction.

Contributions are governed by the repository's current `LICENSE`; do not infer additional distribution promises from this documentation.
