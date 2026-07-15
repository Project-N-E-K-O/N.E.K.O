# Pipeline Split

The completed pipeline split keeps `RoastPipeline` as the public entrypoint and moves route,
request, viewer, session, dispatch, and result helpers into focused
`pipeline_*` modules.

## Contracts

- Feature code should continue importing `RoastPipeline` from
  `core.pipeline`.
- Pipeline helpers use the shared contracts from `core.contracts`.
- The split preserves the existing skipped/failed/pushed result statuses.

## Safety And Degrade

- Missing identity modules degrade to a legacy Bilibili identity adapter.
- Timeline recording is best-effort and never blocks event handling.
- Dispatch failures stay inside pipeline result objects instead of escaping
  into the live listener.

## Tests

The pipeline facade and its focused helpers must remain importable without
requiring provider, hosting, or UI modules to initialize first. Future changes
may extend the helper modules, but must keep the public pipeline facade
compatible.
