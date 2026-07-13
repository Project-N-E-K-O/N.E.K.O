import { describe, expect, it } from 'vitest';
import { AVAILABLE_AVATAR_TOOLS } from '../avatarTools';
import {
  buildAvatarInteractionPayload,
  buildAvatarToolDescriptorStatePayload,
  buildAvatarToolStatePayload,
  getAvatarToolStatePayloadKey,
} from './payload';

describe('avatar tool payload builders', () => {
  it('keeps tool-specific facts on their owning payload', () => {
    const fist = buildAvatarInteractionPayload({
      toolId: 'fist', actionId: 'poke', clientX: 1, clientY: 2, touchZone: 'head', rewardDrop: true,
    });
    const hammer = buildAvatarInteractionPayload({
      toolId: 'hammer', actionId: 'bonk', clientX: 3, clientY: 4, easterEgg: true,
    });
    expect(fist).toEqual(expect.objectContaining({ touchZone: 'head', rewardDrop: true }));
    expect(fist).not.toHaveProperty('easterEgg');
    expect(hammer).toEqual(expect.objectContaining({ easterEgg: true }));
    expect(hammer).not.toHaveProperty('rewardDrop');
  });

  it('fails closed when runtime facts do not match the canonical tool payload', () => {
    const invalidCommit = {
      toolId: 'hammer',
      actionId: 'poke',
      clientX: 3,
      clientY: 4,
      rewardDrop: true,
    } as unknown as Parameters<typeof buildAvatarInteractionPayload>[0];

    expect(() => buildAvatarInteractionPayload(invalidCommit)).toThrow();
  });

  it('deduplicates state payloads independently of timestamps', () => {
    const base = { active: false, toolId: null, tool: null, timestamp: 1 } as const;
    expect(getAvatarToolStatePayloadKey(base)).toBe(getAvatarToolStatePayloadKey({ ...base, timestamp: 99 }));
  });

  it('keeps the single-window pointer state lightweight', () => {
    const tool = AVAILABLE_AVATAR_TOOLS.find(item => item.id === 'fist')!;
    const payload = buildAvatarToolStatePayload({
      activeTool: tool,
      variant: 'primary',
      avatarRangeVariant: 'primary',
      outsideRangeVariant: 'primary',
      imageKind: 'pointer',
      withinAvatarRange: false,
      overCompactZone: false,
      insideHostWindow: false,
      pointer: { x: 10, y: 20 },
    });

    expect(payload).not.toHaveProperty('desktopContract');
  });

  it('builds a desktop handoff with descriptor facts but no live pointer state', () => {
    const tool = AVAILABLE_AVATAR_TOOLS.find(item => item.id === 'hammer')!;
    const payload = buildAvatarToolDescriptorStatePayload({ activeTool: tool });

    expect(Object.keys(payload).sort()).toEqual([
      'active',
      'avatarRangeVariant',
      'desktopContract',
      'outsideRangeVariant',
      'timestamp',
      'toolId',
    ]);
    expect(payload).toEqual(expect.objectContaining({
      active: true,
      toolId: 'hammer',
      avatarRangeVariant: 'primary',
      outsideRangeVariant: 'primary',
      desktopContract: expect.objectContaining({
        wireVersion: 1,
        definition: expect.objectContaining({ id: 'hammer' }),
      }),
    }));
    expect(payload).not.toHaveProperty('tool');
  });

  it('publishes an inactive desktop handoff without active variants or a page visual descriptor', () => {
    const payload = buildAvatarToolDescriptorStatePayload({ activeTool: null });

    expect(Object.keys(payload).sort()).toEqual([
      'active',
      'desktopContract',
      'timestamp',
      'toolId',
    ]);
    expect(payload).toMatchObject({
      active: false,
      toolId: null,
      desktopContract: { wireVersion: 1, definition: null, runtimePolicy: null },
    });
    expect(payload).not.toHaveProperty('tool');
    expect(payload).not.toHaveProperty('avatarRangeVariant');
    expect(payload).not.toHaveProperty('outsideRangeVariant');
  });
});
