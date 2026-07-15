import { describe, expect, it, vi } from 'vitest';
import {
  AVATAR_TOOL_RANGE_EXIT_PADDING,
  AVATAR_TOOL_RANGE_PADDING,
  AVATAR_TOOL_RUNTIME_POLICY,
  classifyAvatarTouchZone,
  getAvatarRangeHit,
  isPointInsideAvatarBounds,
  isPointerOverAvatarToolUi,
  normalizeAvatarToolBounds,
  resolveAvatarToolCommit,
  resolveAvatarToolPointerDown,
  resolveAvatarToolPointerRelease,
  validateAvatarToolRuntimePolicy,
  type AvatarToolRuleContext,
  type AvatarToolRuntimePolicy,
} from './interaction';

describe('avatar tool runtime policy', () => {
  it('serializes the explicit range, press, release, and forced-exit contract', () => {
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
const bounds = { left: 100, right: 200, top: 100, bottom: 200, width: 100, height: 100 };

describe('avatar tool hit testing', () => {
  it('normalizes finite positive bounds and rejects invalid input', () => {
    expect(normalizeAvatarToolBounds({ left: 10, top: 20, width: 30, height: 40 })).toEqual(expect.objectContaining({
      right: 40,
      bottom: 60,
      centerX: 25,
      centerY: 40,
    }));
    expect(normalizeAvatarToolBounds({ left: 0, top: 0, width: 0, height: 20 })).toBeNull();
  });

  it('uses the shared ellipse and derives touch zones', () => {
    expect(getAvatarRangeHit(150, 150, [bounds], 0)?.touchZone).toBe('face');
    expect(getAvatarRangeHit(20, 20, [bounds], 0)).toBeNull();
    expect(classifyAvatarTouchZone(bounds, 110, 110)).toBe('ear');
    expect(classifyAvatarTouchZone(bounds, 150, 125)).toBe('head');
    expect(classifyAvatarTouchZone(bounds, 150, 185)).toBe('body');
  });

  it('drives ellipse geometry and touch-zone thresholds from the supplied policy', () => {
    expect(isPointInsideAvatarBounds(bounds, 180, 150, 0)).toBe(true);
    expect(getAvatarRangeHit(185, 150, [bounds], 0)).toBeNull();

    const policy = JSON.parse(JSON.stringify(AVATAR_TOOL_RUNTIME_POLICY)) as AvatarToolRuntimePolicy;
    policy.range.geometry.radiusXFromWidth = 0.5;
    policy.range.touchZones.ear.maxY = 0.05;
    validateAvatarToolRuntimePolicy(policy);

    expect(getAvatarRangeHit(185, 150, [bounds], 0, policy)).not.toBeNull();
    expect(classifyAvatarTouchZone(bounds, 110, 110, policy)).toBe('head');
  });

  it('excludes window drag, resize, and tutorial shield surfaces from tool interaction', () => {
    const dragHandle = document.createElement('div');
    dragHandle.id = 'react-chat-window-drag-handle';
    const resizeEdge = document.createElement('div');
    resizeEdge.className = 'react-chat-resize-edge';
    const tutorialShield = document.createElement('div');
    tutorialShield.id = 'yui-guide-standalone-interaction-shield';

    expect(isPointerOverAvatarToolUi(dragHandle)).toBe(true);
    expect(isPointerOverAvatarToolUi(resizeEdge)).toBe(true);
    expect(isPointerOverAvatarToolUi(tutorialShield)).toBe(true);
  });

  it('excludes the full chat window without excluding the whole compact window', () => {
    const fullWindow = document.createElement('section');
    fullWindow.className = 'chat-window chat-surface-mode-full';
    const fullMessage = document.createElement('p');
    fullWindow.appendChild(fullMessage);
    const compactWindow = document.createElement('section');
    compactWindow.className = 'chat-window chat-surface-mode-compact';

    expect(isPointerOverAvatarToolUi(fullMessage)).toBe(true);
    expect(isPointerOverAvatarToolUi(compactWindow)).toBe(false);
  });
});


function context(overrides: Partial<AvatarToolRuleContext> = {}): AvatarToolRuleContext {
  return {
    toolId: 'lollipop',
    clientX: 120,
    clientY: 140,
    hit: {
      bounds: { left: 100, right: 200, top: 100, bottom: 200, width: 100, height: 100 },
      touchZone: 'face',
    },
    rangeVariant: 'primary',
    outsideVariant: 'primary',
    interactionLocked: false,
    recordBurst: vi.fn(() => 1),
    random: vi.fn(() => 0.9),
    ...overrides,
  };
}

describe('avatar tool runtime rules', () => {
  it('keeps lollipop semantics isolated from touch-zone and reward fields', () => {
    expect(resolveAvatarToolPointerDown(context())).toEqual({});
    const command = resolveAvatarToolCommit(context());
    expect(command.commit).toEqual(expect.objectContaining({ toolId: 'lollipop', actionId: 'offer' }));
    expect(command.commit).not.toHaveProperty('touchZone');
    expect(command.commit).not.toHaveProperty('rewardDrop');
    expect(command.rangeVariant).toBe('secondary');
  });

  it('does not commit lollipop outside the avatar range', () => {
    expect(resolveAvatarToolCommit(context({ hit: null }))).toEqual({});
  });

  it('fails closed when lollipop receives a variant without a declared stage', () => {
    expect(resolveAvatarToolCommit(context({
      rangeVariant: 'missing' as AvatarToolRuleContext['rangeVariant'],
    }))).toEqual({});
  });

  it('keeps fist reward and touch-zone facts in the fist module', () => {
    expect(resolveAvatarToolPointerDown(context({ toolId: 'fist' }))).toEqual({
      rangeVariant: 'secondary',
      outsideVariant: 'secondary',
      pressFeedback: 'until-pointer-release',
    });
    const command = resolveAvatarToolCommit(context({
      toolId: 'fist',
      random: () => 0.1,
      recordBurst: () => 4,
    }));
    expect(command.commit).toEqual(expect.objectContaining({
      toolId: 'fist',
      actionId: 'poke',
      intensity: 'rapid',
      rewardDrop: true,
      touchZone: 'face',
    }));
    expect(command.effect).toBe('reward-drops');
    expect(resolveAvatarToolPointerRelease('fist')).toEqual({
      rangeVariant: 'primary',
      outsideVariant: 'primary',
    });
  });

  it('turns an outside hammer click into local feedback only', () => {
    const command = resolveAvatarToolPointerDown(context({ toolId: 'hammer', hit: null }));
    expect(command.commit).toBeUndefined();
    expect(command.outsideVariant).toBe('secondary');
    expect(command.resetOutsideVariantAfterMs).toBe(220);
  });

  it('applies interaction locks generically and preserves hammer-only easter eggs', () => {
    expect(resolveAvatarToolCommit(context({ toolId: 'lollipop', interactionLocked: true }))).toEqual({});
    expect(resolveAvatarToolPointerDown(context({ toolId: 'hammer' }))).toEqual({});
    const command = resolveAvatarToolCommit(context({ toolId: 'hammer', random: () => 0.01 }));
    expect(command.commit).toEqual(expect.objectContaining({
      toolId: 'hammer',
      actionId: 'bonk',
      intensity: 'easter_egg',
      easterEgg: true,
    }));
    expect(command.effect).toBe('hammer-swing');
    expect(command.effectMode).toBe('easter-egg');
    expect(command).not.toHaveProperty('hammerSwing');
  });
});
