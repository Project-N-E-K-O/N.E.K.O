# Documentation Maintenance

Project N.E.K.O. documentation is part of the product contract. Keep it close to the code it explains, state what kind of document it is, and avoid copying volatile facts into multiple pages.

## Documentation layers

| Layer | Purpose | Location |
| --- | --- | --- |
| User and developer guides | Supported setup and common workflows | `guide/`, `config/`, `deployment/`, `frontend/`, `plugins/` |
| Architecture and API reference | Current code-backed contracts | `architecture/`, `modules/`, `api/` |
| Contributor rules | Repository-wide development and validation rules | `contributing/` |
| Project records | Design decisions, benchmark snapshots, and SDK change notes | `design/`, `benchmarks/`, `changelog/` |
| Component-owned docs | Detailed documentation maintained with one component | For example `plugin/plugins/neko_live/docs/` |

The [Project Records](/records/) section is evidence and context, not a substitute for current code, tests, or accepted issues.

## Canonical-source rule

Choose one canonical page for each fact:

- behavior and public contracts belong in the owning guide, architecture page, or API page;
- repository process belongs in `contributing/`;
- implementation rationale and dated measurements belong in project records;
- component-specific workflows belong beside that component;
- active future work belongs in accepted issues or the maintained project board.

Link to the canonical page instead of copying command lists, provider tables, version claims, or roadmap promises. A translated page mirrors the canonical meaning; it does not become an independent specification.

## Status language

Every plan or record must make its authority clear near the beginning:

- **Current contract** — behavior that current code and tests enforce.
- **Implemented design record** — rationale for shipped behavior; later code may supersede details.
- **Proposal** — not approved or implemented merely because the file exists.
- **Historical snapshot** — dated evidence that must not be read as current behavior.
- **Deprecated** — retained only for migration or archaeology.

Avoid vague labels such as “phase 2 soon” or “future version” without an owner and authoritative tracking link.

## Languages

The documentation site has English, Simplified Chinese, and Japanese navigation. Not every project record is translated. The locale switcher falls back to the locale home when a matching page does not exist.

When changing a mirrored guide:

1. update all existing mirrors in the same change;
2. preserve code identifiers, paths, placeholders, and warning meaning;
3. do not invent a translated page solely to hide missing coverage;
4. note intentional language-only records in the nearest index.

The runtime UI's eight-locale rule is separate and remains mandatory for user-visible locale keys.

`docs/README_en.md`, `docs/README_ja.md`, and `docs/README_ru.md` are repository README translations linked from the root README. They are intentionally excluded from the VitePress build and should not be used as site navigation pages.

`docs/zh-CN/guide/openclaw_guide*.md` and its adjacent assets are localized runtime content served by the application. Keep their paths stable and keep the page files excluded from VitePress; the public integration contract belongs in the Agent and plugin documentation.

## Links and paths

- Use site-root links such as `/plugins/quick-start` for VitePress pages.
- Use repository-relative links only when the target is outside the documentation site.
- Keep established routes stable. If a move is necessary, add a redirect or compatibility page and update inbound references.
- Do not link generated files, local worktrees, temporary reports, private logs, or unmerged PR branches as permanent documentation.

## Review checklist

Before submitting documentation changes:

1. confirm the described behavior against current code and tests;
2. remove secrets, raw user content, machine-specific paths, and temporary evidence;
3. check all existing language mirrors;
4. run the documentation build from `docs/` with `npm ci` and `npm run build`;
5. run relevant code checks when the documentation changes a public contract or example;
6. ensure the PR is focused and does not stack unrelated documentation work.
