import { afterEach, describe, expect, it, vi } from 'vitest';
import { createAvatarToolProfileHandlers } from './profileInterpreter';
import { validateAvatarToolDefinition } from './catalog';
import {
  buildLocalAvatarToolDefinition,
  createLocalAvatarTool,
  fetchLocalAvatarTools,
  type LocalAvatarToolDto,
} from './localTools';

const TOOL_ID = 'local-12345678-1234-4123-8123-123456789abc' as const;
const LIMITS = {
  maxTools: 64,
  maxNameChars: 80,
  maxMeaningChars: 1200,
  maxChangeImages: 16,
  maxImageBytes: 8_388_608,
  maxImagePixels: 16_000_000,
  maxTotalBytes: 268_435_456,
};

function dto(overrides: Partial<LocalAvatarToolDto> = {}): LocalAvatarToolDto {
  return {
    id: TOOL_ID,
    name: 'Feather',
    changeMode: 'press-swap',
    defaultUrl: '/default.png?v=1',
    changeUrls: ['/change-000.png?v=1'],
    ...overrides,
  };
}

function context(imageFrameIndex: number, imageFrameCount: number) {
  return {
    toolId: TOOL_ID,
    clientX: 10,
    clientY: 20,
    hit: {
      touchZone: 'head' as const,
      bounds: { left: 0, right: 100, top: 0, bottom: 100, width: 100, height: 100 },
    },
    visibleVariant: 'primary' as const,
    rangeVariant: 'primary' as const,
    outsideVariant: 'primary' as const,
    imageFrameIndex,
    imageFrameCount,
    interactionLocked: false,
    recordBurst: () => 1,
    random: () => { throw new Error('RNG must not run'); },
  };
}

describe('local avatar tool image change modes', () => {
  afterEach(() => {
    delete window.nekoLocalMutationSecurity;
    vi.unstubAllGlobals();
  });

  it('builds a strict press-swap v2 definition and reports the only change item', () => {
    const definition = buildLocalAvatarToolDefinition(dto());

    expect(() => validateAvatarToolDefinition(definition)).not.toThrow();
    expect(definition.label).toEqual({ kind: 'literal', value: 'Feather' });
    expect(definition.sounds).toEqual([]);
    expect(definition.effects).toEqual([]);
    expect(definition.visual.frames?.map(frame => frame.pointerImagePath)).toEqual([
      '/default.png?v=1',
      '/change-000.png?v=1',
    ]);
    if (definition.interaction.kind !== 'press-release') throw new Error('invalid local profile');
    expect(definition.interaction.imageChange).toEqual({ kind: 'press-swap' });

    const handlers = createAvatarToolProfileHandlers(definition);
    expect(handlers.pointerDown(context(0, 2))).toEqual({
      imageFrameIndex: 1,
      pressFeedback: 'until-pointer-release',
    });
    expect(handlers.commit(context(1, 2))).toEqual({
      commit: {
        toolId: TOOL_ID,
        actionId: 'interact',
        intensity: 'normal',
        touchZone: 'head',
        changeIndex: 0,
        clientX: 10,
        clientY: 20,
      },
    });
    expect(handlers.pointerRelease()).toEqual({ imageFrameIndex: 0 });
  });

  it('advances ordered images only to the final frame and never loops', () => {
    const definition = buildLocalAvatarToolDefinition(dto({
      changeMode: 'click-advance',
      changeUrls: ['/change-000.png?v=1', '/change-001.png?v=1'],
    }));
    const handlers = createAvatarToolProfileHandlers(definition);

    expect(handlers.pointerDown(context(0, 3))).toEqual({});
    expect(handlers.commit(context(0, 3))).toMatchObject({
      imageFrameIndex: 1,
      commit: { changeIndex: 0 },
    });
    expect(handlers.commit(context(1, 3))).toMatchObject({
      imageFrameIndex: 2,
      commit: { changeIndex: 1 },
    });
    expect(handlers.commit(context(2, 3))).toMatchObject({
      imageFrameIndex: 2,
      commit: { changeIndex: 1 },
    });
    expect(handlers.pointerRelease()).toEqual({});
  });

  it('keeps a valid authoritative item when another DTO is malformed', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      ok: true,
      items: [
        { id: 'bad', name: 'Bad', changeMode: 'press-swap', defaultUrl: '', changeUrls: [] },
        dto(),
      ],
      limits: LIMITS,
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })));

    await expect(fetchLocalAvatarTools()).resolves.toEqual({
      items: [dto()],
      limits: LIMITS,
    });
  });

  it('posts ordered image/meaning pairs and retries csrf failure exactly once', async () => {
    const getMutationHeaders = vi.fn()
      .mockResolvedValueOnce({ 'X-CSRF-Token': 'old' })
      .mockResolvedValueOnce({ 'X-CSRF-Token': 'new' });
    const refreshToken = vi.fn().mockResolvedValue(undefined);
    window.nekoLocalMutationSecurity = { getMutationHeaders, refreshToken };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ error_code: 'csrf_validation_failed' }), {
        status: 403,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true, item: dto({
        changeMode: 'click-advance',
        changeUrls: ['/change-000.png?v=1', '/change-001.png?v=1'],
      }) }), {
        status: 201,
        headers: { 'Content-Type': 'application/json' },
      }));
    vi.stubGlobal('fetch', fetchMock);

    const result = await createLocalAvatarTool({
      name: 'Feather',
      changeMode: 'click-advance',
      defaultImage: new File(['default'], 'default.png', { type: 'image/png' }),
      changeItems: [
        { image: new File(['first'], 'first.png', { type: 'image/png' }), meaning: 'First meaning' },
        { image: new File(['second'], 'second.png', { type: 'image/png' }), meaning: 'Second meaning' },
      ],
    });

    expect(result?.changeMode).toBe('click-advance');
    expect(refreshToken).toHaveBeenCalledTimes(1);
    expect(getMutationHeaders).toHaveBeenCalledTimes(2);
    const firstForm = fetchMock.mock.calls[0][1]?.body as FormData;
    expect(firstForm.get('change_mode')).toBe('click-advance');
    expect(firstForm.getAll('change_images')).toHaveLength(2);
    expect(firstForm.getAll('change_meanings')).toEqual(['First meaning', 'Second meaning']);
  });
});
