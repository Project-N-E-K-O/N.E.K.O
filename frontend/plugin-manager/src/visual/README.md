# Plugin manager visual baselines

These four Chromium captures protect the dashboard's existing composition and
the plugin workbench in light, dark and narrow layouts. They complement the
behavior and panel geometry tests in `src/e2e`; a snapshot alone cannot prove
that controls are reachable or that nested scrolling works.

The harness uses eight English fixture plugins, a fixed date and timezone,
explicit API responses, blocked external HTTP/WebSocket traffic, a fresh storage
context and disabled motion. Test-only Liberation Sans Regular and Bold are
served by Playwright, loaded before capture and never imported by the product.
The font files are unmodified copies distributed under `fonts/LICENSE` (SIL OFL
1.1), sourced from the `ttf-liberation` system package. This keeps captures
independent of installed CJK or display fonts; real localized layouts are
covered by the behavior tests.

Run from `frontend/plugin-manager` after installing the lockfile dependencies
and Playwright Chromium:

```sh
npm run build-only
npx playwright test --config playwright.visual.config.ts
```

The suite starts a production preview on port 4174, separate from boot tests on
4173. `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` is respected when an environment
provides its own Chromium.

For an intentional visual change, generate candidate baselines with:

```sh
npx playwright test --config playwright.visual.config.ts --update-snapshots
```

Review every changed image in `__snapshots__` before committing it. Unexpected
differences appear under `test-results/visual` as expected/actual/diff images.
The comparison allows a small rasterization tolerance (0.2% of pixels at a
0.15 color threshold); it still catches shifts in spacing, boundaries, colors
and control sizing. Keep the same Playwright version for local review and CI.
