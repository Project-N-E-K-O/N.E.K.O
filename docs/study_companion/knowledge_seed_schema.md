# Study Companion Knowledge Seed Schema

This document defines the canonical knowledge graph seed shape. Math is the
reference structure, but every subject should use the same topic and edge
contract so retrieval, diagnosis, practice planning, and UI graph layers can
share one pipeline.

## Topic Contract

Every topic should include these fields:

```json
{
  "id": "college_derivative_extrema",
  "name": "Derivative extrema",
  "subject": "math",
  "stage": "college",
  "chapter": "Calculus",
  "unit": "Differential calculus",
  "aliases": ["extrema with derivatives"],
  "skills": ["differentiate", "find critical points", "test monotonicity"],
  "question_types": ["extrema calculation", "optimization modeling"],
  "examples": [{"prompt": "Find local extrema of f(x)."}],
  "typical_misconceptions": ["A stationary point is not always an extremum."],
  "prerequisites": [],
  "related": []
}
```

Required scalar fields:

- `id`
- `name`
- `subject`
- `stage`
- `chapter`
- `unit`

Required list fields:

- `skills`
- `question_types`
- `examples`
- `typical_misconceptions`
- `prerequisites`
- `related`

## Edge Contract

Object edges should be preferred over legacy string references:

```json
{
  "id": "college_critical_points",
  "relation": "procedure_step",
  "priority": "core",
  "context": "practice",
  "confidence": 0.9,
  "reason": "Extrema problems usually differentiate first, then find critical points.",
  "use_cases": ["learning_path", "hint_generation"]
}
```

Supported semantic relations:

- `prerequisite`: what must be learned first
- `confusable`: concepts that are easy to mix up
- `procedure_step`: next step in a solving or analysis workflow
- `application`: typical use case
- `extends`: follow-up extension
- `co_occurs`: useful review companion

Compatibility relations still accepted during migration:

- `nearby`
- `next`
- `related`
- `similar`
- `compare`
- `supports`
- `analogy`

## Edge Metadata

`priority` values:

- `core`
- `useful`
- `optional`

`context` values:

- `diagnosis`
- `explanation`
- `practice`
- `review`

`confidence` must be a number between `0.0` and `1.0`.

## Relation Coverage Report

The validator reports subject-level minimum relation coverage so seed authors
can see which subjects still have too few semantic edges for reliable graph
retrieval and learning-path construction.

During the static library migration, this report is a quality signal only. Low
coverage should be reviewed and fixed before expanding a subject, but it should
not be treated as a fatal validator issue until the migrated static library is
complete enough to make the coverage gate stable.

The minimum relation coverage standard applies to these subjects:

- `math`
- `physics`
- `chemistry`
- `biology`
- `english`
- `computer_science`
- `chinese`
- `economics`
- `geography`
- `history`
- `politics`

## Subject Templates

Math canonical template:

- fields: `id`, `name`, `subject`, `stage`, `chapter`, `unit`, `aliases`,
  `skills`, `question_types`, `examples`, `typical_misconceptions`,
  `prerequisites`, `related`
- subject value: `math`
- prerequisites: object edges with `relation: prerequisite`
- related: object edges with `relation`, `priority`, `context`, `confidence`,
  `reason`, and `use_cases`
- core relations: `prerequisite`, `procedure_step`, `confusable`,
  `application`, `extends`, `co_occurs`
- procedure_step: identify concept -> recall prerequisite -> choose method ->
  execute calculation or proof -> verify result
- confusable: definition vs theorem, necessary condition vs sufficient
  condition, formula use vs derivation
- application: diagnosis, hint generation, learning paths, review planning,
  practice generation
- extends: cross-topic methods, higher-stage generalizations, modeling tasks

Math is the canonical subject template. When adding or expanding any other
subject, migrate its topics and relationships to the Math field and edge
format above: keep the same required topic fields, use object edges instead of
legacy string references, and express semantic links through the supported
relation names plus metadata. Subject-specific wording can change, but the
field names, edge metadata, and relation contract should stay aligned with
Math.

Physics mechanics:

- prerequisites: force analysis, velocity and acceleration
- procedure_step: choose object -> force analysis -> coordinate system -> equation -> solve
- confusable: velocity direction vs acceleration direction, action vs reaction force
- application: dynamics calculation, connected bodies, inclined planes
- extends: work-energy relation, momentum conservation

Chemistry reaction principles:

- prerequisites: valence, electron transfer
- procedure_step: assign valence -> find increase/decrease -> balance electrons -> balance equation
- confusable: oxidizing agent vs reducing agent, oxidized vs reduced
- application: ionic equations, electrochemistry
- extends: galvanic cells, electrolytic cells

Biology genetics:

- prerequisites: gene, allele, meiosis
- procedure_step: parent genotype -> gametes -> Punnett square -> phenotype ratio
- confusable: genotype vs phenotype, dominant vs recessive
- application: genetic probability
- extends: independent assortment, sex-linked inheritance

English reading:

- prerequisites: vocabulary, complex sentences, paragraph structure
- procedure_step: read stem -> locate topic sentence -> remove detail distractors -> infer main idea
- confusable: main idea vs detail question, attitude vs inference question
- application: reading comprehension, cloze
- extends: argumentative structure, expository structure

Computer science data structures:

- prerequisites: array, pointer/reference, complexity
- procedure_step: identify operation -> choose structure -> analyze complexity -> implement
- confusable: array vs linked list, stack vs queue, BFS vs DFS
- application: traversal, shortest path, indexing
- extends: graphs, trees, algorithms

## Model Context Rule

Full seed JSON is a data source, not prompt input. Model-facing payloads should
be built from a local subgraph:

- focus topics: 1-3
- max depth: 2
- max nodes: 20
- max edges: 30
- relation budgets:
  - prerequisite: 5
  - procedure_step: 6
  - confusable: 4
  - application: 4
  - extends: 3
  - co_occurs: 3
