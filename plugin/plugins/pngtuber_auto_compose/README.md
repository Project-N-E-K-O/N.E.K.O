# PNGTuber Auto Compose

PNGTuber Auto Compose is the N.E.K.O-side control panel for automatic PNGTuber asset composition.

The first version focuses on the host-side shell:

- Hosted TSX panel for uploading a reference image.
- SQLite job, artifact, and workflow-run records under the plugin data directory.
- ComfyUI reachability check.
- Base candidate generation, explicit candidate selection, background removal, local talking-state generation, simple package generation, and N.E.K.O import.

The current minimum pipeline is:

```text
source_reference
-> base_candidate[]
-> selected_candidate
-> native_base_image
-> state_variant_talking
-> model_package
-> installed_model
```

Advanced composition steps remain workflow hooks:

- expression candidates
- layered asset decomposition
- QA and automatic downgrade
- model-driven mouth/blink variants

The first talking-state implementation is a deterministic local mouth patch so the package/import flow can be validated end to end. It is designed to be replaced by a ComfyUI local-edit workflow without changing artifact roles or package semantics.

## Core layout

- `core/store.py`: durable job, artifact, and workflow-run persistence.
- `core/comfyui_client.py`: small async wrapper around ComfyUI HTTP endpoints.
- `core/pipeline.py`: stable service boundary used by routers and UI actions.
- `workflow_registry.py`: declarative workflow spec loader for `workflows/*.json`.

## Workflow bindings

Workflow bindings live in `workflows/*.json`. Each spec describes one pipeline step:

- `id`, `name`, `stage`, `status`
- `engine`: `comfyui` or `plugin`
- `graph_template`: the future ComfyUI prompt graph template
- `inputs` and `outputs`
- `quality_gates`
- `depends_on` and `next`

The plugin exposes them through:

- `list_workflows`
- `get_workflow`

Current planned chain:

```text
base_reference_transfer
-> remove_background
-> generate_talking
-> package_native
-> import_to_neko

optional:
remove_background
-> qwen_expression_patch
-> package_native

advanced:
remove_background
-> see_through_layers
-> package_native
```
