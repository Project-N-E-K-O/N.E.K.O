# Built-in knowledge domains

The built-in domain package is a trusted composition layer above the generic
knowledge engine. It does not change the five-field entry contract, community
pack format, database schema, or the default behavior of `knowledge.api`.

## Composition

`knowledge.builtin.open_builtin_knowledge(root)` opens two project-owned
collection specifications:

- `meme`: conversational matching and response policy for public meme
  knowledge;
- `corpora`: conservative reference matching and explicit random-material
  routes for the bundled Corpora subset.

The generic `knowledge.api.open_knowledge(root)` continues to open no implicit
domains. This keeps the SDK source-independent and lets application code opt
into built-in behavior explicitly.

## Data boundary

The `corpora` domain bundles 229 selected records from `dariusk/corpora`, pinned to commit
`cf30ca27ab176b63623af1ddcfa2447ac07305ba` and distributed under CC0 1.0.
The JSONL file is integrity-checked before import. Import replaces only the
`source:corpora` slice and is idempotent when the installed content matches.

The meme domain bundles no CHIME dataset and performs no network access. It
provides only trusted matching, response, source-display, and conflict-routing
policy. Existing local meme entries or separately distributed data packs can
use that policy without moving their data into the built-in artifact.

No Moegirlpedia, Geng8, or other remote acquisition adapter is included. Those
sources remain isolated from the local query and card-delivery path.

## Runtime use

Application lifecycle code can import Corpora off the request path, then open
the built-in service:

```python
from knowledge.builtin import open_builtin_knowledge
from knowledge.corpora import import_bundled_corpora

import_bundled_corpora(config_manager.knowledge_dir)
service = open_builtin_knowledge(config_manager.knowledge_dir)
```

The synchronous importer is deliberately small and network-free. Async hosts
should call it through their existing thread-offload lifecycle rather than from
an async request handler.

Built-in knowledge remains separate from `memory/` and does not modify user or
character memory files. The built-in domain package itself declares only
composition policy; runtime wiring lives in `main_logic/`, the management
routes, and the plugin-market transport.
