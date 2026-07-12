# Output Contract And Danmaku Response

## Purpose

The output-contract slice keeps NEKO Live speech short, attributable, and safe before the host transports it. It also owns normal danmaku request classification and prompt construction.

## Ownership And Contracts

- `modules/danmaku_response/module.py` classifies the current public danmaku and builds an `InteractionRequest`; `core/danmaku_text_rules.py` owns its small reaction and mention classifiers without depending on the later active-topic slice.
- `adapters/output_contract_bridge.py` maps that request to plugin-owned reply metadata.
- `core/live_reply_contract.py` declares route limits and reply modes.
- `core/live_output_contract_prompt.py`, `core/live_output_quality.py`, and `core/live_output_shape.py` render, validate, and shape the final spoken line.
- `adapters/neko_dispatcher.py` is the only output boundary. The host receives opaque metadata and does not own NEKO Live wording policy.

## Data Flow And Safety

Live and sandbox input still enter `core/pipeline.py`. The pipeline applies the permission gate and `core/safety_guard.py` before dispatch. Normal danmaku responses read the current public event, sanitized viewer profile context, recent plugin output, and the configured live theme. They do not write a new store and do not bypass the audit, pipeline, or dispatcher boundaries.

The pure shaper can remove stage directions and internal-context leaks, reject unsafe or unfulfilled reply shapes, enforce a route character ceiling, and record shaping reasons in plugin metadata. The current host SDK does not expose the generated reply to the plugin before TTS, so live delivery currently relies on the injected prompt contract and opaque metadata; hard post-generation shaping is reserved for a future generic host callback. Hosting coalescing uses the target, hosting source, and stable beat identifier so duplicate delivery can collapse without merging different beats.

## Testing

Run:

```powershell
uv run pytest plugin/plugins/neko_roast/tests/test_output_contract.py -q
uv run pytest plugin/plugins/neko_roast/tests -q --maxfail=1
uv run python -m plugin.neko_plugin_cli.cli check plugin/plugins/neko_roast
```

Coverage includes reply length, fulfilled content requests, hosting coalescing, and named-target parsing.

## Limitations And Degrade Behavior

- Reply quality checks are deterministic heuristics; uncertain text falls back to a short safe line.
- Generic words are not accepted as named roast targets. A public nickname or explicit mention is required.
- The host currently treats output-contract metadata as opaque transport metadata and provides no plugin-owned post-generation or pre-TTS transform hook. Character ceilings are therefore prompt-level best effort on the live delivery path until that generic host capability exists.

To roll back this slice, remove the output-contract bridge from the dispatcher and restore the previous danmaku module registration. The EventBus, pipeline, safety guard, and viewer stores remain compatible because their public contracts are unchanged.
