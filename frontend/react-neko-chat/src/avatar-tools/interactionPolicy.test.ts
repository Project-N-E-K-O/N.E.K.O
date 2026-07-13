import { describe, expect, it } from 'vitest';
import {
  AVATAR_TOOL_RUNTIME_POLICY,
  AVATAR_TOOL_RANGE_EXIT_PADDING,
  AVATAR_TOOL_RANGE_PADDING,
  validateAvatarToolRuntimePolicy,
} from './interactionPolicy';

describe('avatar tool runtime policy', () => {
  it('serializes the explicit range, press, release, and forced-exit contract', () => {
    expect(JSON.parse(JSON.stringify(AVATAR_TOOL_RUNTIME_POLICY))).toEqual(AVATAR_TOOL_RUNTIME_POLICY);
    expect(AVATAR_TOOL_RUNTIME_POLICY).toMatchObject({
      policyVersion: 1,
      range: {
        geometry: {
          shape: 'ellipse',
          radiusXFromWidth: 0.3,
          radiusYFromHeight: 0.475,
          boundary: 'inclusive',
        },
        enterPadding: 100,
        exitPadding: 116,
        visualHold: {
          durationMs: 180,
          semantics: 'presentation-only',
          grantsInteractionHit: false,
        },
        bounds: { cacheTtlMs: 80, missingGraceMs: 640 },
        touchZones: {
          coordinateSpace: 'normalized-avatar-bounds',
          clampToBounds: true,
          boundary: 'inclusive',
          ear: { maxY: 0.24, leftMaxX: 0.24, rightMinX: 0.76 },
          headMaxY: 0.34,
          faceMaxY: 0.62,
          fallback: 'body',
        },
        forcedExit: {
          uiExclusion: 'immediate',
          deactivation: 'immediate',
          hostExit: 'immediate',
        },
      },
      press: {
        button: 0,
        requiresRawHit: true,
        matchingRelease: { pointerId: 'same-as-press', button: 'same-as-press' },
        move: { thresholdPx: 6, comparison: 'strictly-greater' },
      },
      release: {
        bounds: 'fresh',
        heldVisualRangeIsHit: false,
        touchZone: 'fresh-release-hit',
        uiExclusion: 'reject',
      },
    });
    expect(AVATAR_TOOL_RANGE_PADDING).toBe(100);
    expect(AVATAR_TOOL_RANGE_EXIT_PADDING).toBe(116);
  });

  it('accepts its JSON form and rejects unsupported policy versions fail-closed', () => {
    const serialized = JSON.parse(JSON.stringify(AVATAR_TOOL_RUNTIME_POLICY)) as unknown;
    expect(() => validateAvatarToolRuntimePolicy(serialized)).not.toThrow();
    const invalidVersion = {
      ...(serialized as Record<string, unknown>),
      policyVersion: 2,
    };
    expect(() => validateAvatarToolRuntimePolicy(invalidVersion)).toThrow();
  });
});
