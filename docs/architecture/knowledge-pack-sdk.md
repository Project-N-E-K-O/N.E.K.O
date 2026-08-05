# Knowledge Pack SDK

N.E.K.O. knowledge packs are data-only `.neko-knowledge.json` files. They do
not use `plugin.toml` and cannot declare code entry points, dependencies,
permissions, prompts, UI, storage paths, ranking rules, or matching logic.
The plugin toolchain is a transport and developer-experience reference only.

## Format and compatibility

`schema_version` identifies the knowledge-pack protocol independently from the
N.E.K.O. application and plugin SDK versions. Version 1 readers reject unknown
fields instead of silently ignoring behavior they do not understand. A future
incompatible format must use a new schema version.

The machine-readable contract is
`knowledge/schemas/knowledge-pack-v1.schema.json` (JSON Schema 2020-12). The
runtime parser in `knowledge.packs` remains authoritative for installation;
the schema exists for editors and external CI.

Each entry uses the same five knowledge fields:

| Field | Purpose |
|---|---|
| `title` | Canonical display and lookup name |
| `terms` | Explicit `alias` and fixed-phrase `recognition` terms |
| `tags` | Non-source classification labels |
| `summary` | Short answer-oriented description |
| `content` | Supporting facts and usage context |

The installer supplies the `source:community.<pack_id>` tag. Pack entries must
not declare their own `source:*` tag.

## Collections and safety

A pack targeting a new `collection_id` must include
`collection.display_name`. A pack targeting an existing collection may omit
that object. Collection identifiers contain only lowercase letters, digits,
dots, underscores, and hyphens; they cannot use platform-reserved or built-in
identifiers. `pack_id` and `collection_id` share one portable rule: 1–64
unpadded lowercase letters, digits, dots, underscores, or hyphens; the first
and last character must be alphanumeric, and Windows-reserved stems such as
`nul`, `com1`, and `lpt9` are rejected.

Community collections use an application-managed database path. Packs cannot
choose a path or override collection policy. Automatic conversation context is
off by default and, when a user enables it, uses N.E.K.O.'s fixed safe policy.
Knowledge is public reference material, never user or character memory.

## Validation

Use Python 3.11 and the project environment:

```powershell
uv run --python 3.11 python scripts/validate_knowledge_pack.py examples/knowledge-packs/minimal.neko-knowledge.json
```

Normal validation accepts readable, formatted JSON. Before publishing, use:

```powershell
uv run --python 3.11 python scripts/validate_knowledge_pack.py --strict dist/example.neko-knowledge.json
```

Strict mode additionally requires canonical bytes: UTF-8, object keys sorted,
compact JSON separators, no ASCII escaping, no `NaN`/`Infinity` constants, and
exactly one final LF. This
makes the artifact SHA-256 reproducible. Exit code `0` means valid, `2` means
invalid (including strict warnings), and `1` means the file could not be read
or the validator failed internally. Diagnostics identify fields but never
print entry content.

The current limits are 10 MiB per file, 10,000 entries, 100 values per term
role, 100 tags per entry, 500 characters for a title, 4,000 for a summary,
300 characters for each term or tag, and 80,000 for entry content. Source name,
homepage, and license metadata describe provenance; publishers remain
responsible for redistribution rights and attribution.

## Local SDK workflow

Load and validate a pack with `knowledge.api.load_pack`, then open a service
with `knowledge.api.open_knowledge` and install it through
`KnowledgeService.install_pack`. Updates reuse the same `pack_id`; removal uses
`KnowledgeService.remove_pack`. These service calls own collection registration,
database transactions, rollback, and cache refresh. Third-party code must not
import `knowledge.engine` or write SQLite and registry files directly.

The minimal example is intentionally CC0-compatible demonstration content. It
is readable source material and therefore passes normal validation, but it is
not canonical publishing output. Plugin-market authentication, downloading,
and subscription UI are separate integration layers; they ultimately call the
same service installation API rather than implementing another installer.
