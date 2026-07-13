import { describe, expect, it, vi } from 'vitest';
import {
  resolveAvatarToolCommit,
  resolveAvatarToolPointerDown,
  resolveAvatarToolPointerRelease,
  type AvatarToolRuleContext,
} from './interactionEngine';

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
