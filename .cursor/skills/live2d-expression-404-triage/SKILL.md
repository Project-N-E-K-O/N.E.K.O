---
name: live2d-expression-404-triage
description: Diagnose and fix Live2D expression 404 errors caused by mismatch between EmotionMapping expression file paths and FileReferences.Expressions names. Use when logs show expressionXX.exp3.json 404, Failed to load expression, or temporary click effect expression playback failures.
---

# Live2D Expression 404 Triage

## Quick checks

1. Confirm error signature in console:
   - `Failed to load resource ... expressionXX.exp3.json 404`
   - `Failed to load expression: expressionXX.exp3.json`
2. Locate click/effect path:
   - `static/live2d-interaction.js` in `_playTemporaryClickEffect`
   - `static/live2d-emotion.js` in `playExpression`
3. Check whether code maps by strict equality only (`expr.File === choiceFile`).

## Root cause pattern

- `EmotionMapping.expressions` may contain basename only (for example `expression15.exp3.json`).
- `FileReferences.Expressions` often stores full relative path (for example `expressions/expression15.exp3.json`).
- Strict comparison fails, code falls back to inferred name, and runtime loads wrong path.

## Fix pattern

1. Add a shared resolver in `Live2DManager` (core file):
   - Normalize paths (slash style, leading `./` or `/`, case).
   - Match in two passes:
     - exact normalized file path
     - basename fallback
   - Return `expr.Name` from `FileReferences.Expressions`.
2. Replace duplicated file->name conversion logic with this shared resolver.
3. If resolver returns null:
   - avoid forcing native `currentModel.expression()` with guessed name;
   - fall back to file-based/manual expression path when available.

## Regression checks

- Clicking model no longer triggers repeated expression 404 logs.
- Temporary click effects still recover after timeout.
- Persistent expression re-apply flow still runs.
- Existing models with full path mapping still behave the same.
