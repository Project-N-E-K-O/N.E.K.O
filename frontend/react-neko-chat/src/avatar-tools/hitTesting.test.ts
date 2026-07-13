import { describe, expect, it } from 'vitest';
import {
  classifyAvatarTouchZone,
  getAvatarRangeHit,
  isPointInsideAvatarBounds,
  isPointerOverAvatarToolUi,
  normalizeAvatarToolBounds,
} from './hitTesting';
import {
  AVATAR_TOOL_RUNTIME_POLICY,
  type AvatarToolRuntimePolicy,
  validateAvatarToolRuntimePolicy,
} from './interactionPolicy';

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
