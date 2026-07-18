# NEKO Live Console Design QA

Date: 2026-07-13  
Scope: `plugin/plugins/neko_roast` only

This record captures the acceptance criteria used for the streamer-first console refactor. Screenshot artifacts are intentionally not referenced by local absolute paths so the document remains portable across workstations.

## Viewports and states

- Desktop: 1265 x 856, covering ready, live, connection error, account modal, and room modal states.
- Narrow panel: 390 x 844 iframe viewport; measured body width and scroll width were both 390 px.

## Comparison evidence

- The implementation preserves the selected prototype hierarchy: setup summary, one primary runtime control block, and one session summary block.
- The production panel reuses the Hosted UI kit tokens, tabs, cards, badges, alerts, and modal components instead of introducing a parallel visual system.
- Routine live work stays on Console. Interaction details, viewer data, detailed settings, and developer tools remain in their dedicated views.
- No horizontal overflow was observed at desktop or 390 px narrow width.

## Interaction checks

- Account management opens as a modal with platform selection and the existing Bilibili/Douyin account controls.
- Room switching opens as a modal with the existing room value, platform guidance, lookup, and confirmation action.
- Ready state exposes diagnostics and start listening.
- Live state exposes pause and stop listening.
- Error state shows the connection error while keeping retry and diagnostics available.
- Console, Interaction, Viewers, and Settings remain the normal top-level tabs; Developer Tools appears only when developer mode is enabled.
- Developer Tools separates identity lookup, simulated live events, and recent sandbox results into internal subpages.

## Fix history during QA

- Removed the retained legacy console tree so it is no longer constructed during every render.
- Corrected Hosted UI callback return types and the compatibility surface type name.
- Restored activity-level settings and solo-stream readiness details that were initially hidden by the console compaction.
- Updated the subtitle and ready-state labels across all eight locales.
- Final browser tab reported no console errors.

Final result: passed.
