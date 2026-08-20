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
  maxAudioBytes: 5_242_880,
  maxAudioDurationMs: 10_000,
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

function context(
  imageFrameIndex: number,
  imageFrameCount: number,
  random: () => number = () => { throw new Error('RNG must not run'); },
) {
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
    random,
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

  it('adds one scoped feedback sound only when the DTO contains an MP3 URL', () => {
    const definition = buildLocalAvatarToolDefinition(dto({
      normalSoundUrl: `/user_avatar_tools/${TOOL_ID}/normal.mp3?v=1`,
    }));

    expect(() => validateAvatarToolDefinition(definition)).not.toThrow();
    expect(definition.sounds).toEqual([{
      id: 'normal-feedback',
      src: `/user_avatar_tools/${TOOL_ID}/normal.mp3?v=1`,
      volume: 0.9,
    }]);
    if (definition.interaction.kind !== 'press-release') throw new Error('invalid local profile');
    expect(definition.interaction.feedback).toEqual({ sound: 'normal-feedback' });
    expect(createAvatarToolProfileHandlers(definition).commit(context(1, 2))).toMatchObject({
      sound: 'normal-feedback',
      commit: { changeIndex: 0 },
    });
  });

  it('uses the shared chance pipeline for explicit miss and hit feedback', () => {
    const definition = buildLocalAvatarToolDefinition(dto({
      normalSoundUrl: `/user_avatar_tools/${TOOL_ID}/normal.mp3?v=1`,
      special: {
        probability: 0.1,
        imageUrl: `/user_avatar_tools/${TOOL_ID}/special.png?v=1`,
        soundUrl: `/user_avatar_tools/${TOOL_ID}/special.mp3?v=1`,
      },
    }));

    expect(() => validateAvatarToolDefinition(definition)).not.toThrow();
    expect(definition.sounds.map(sound => sound.id)).toEqual(['normal-feedback', 'special-feedback']);
    expect(definition.effects).toEqual([expect.objectContaining({
      id: 'special-scatter',
      kind: 'random-scatter',
      assetPath: `/user_avatar_tools/${TOOL_ID}/special.png?v=1`,
    })]);
    const handlers = createAvatarToolProfileHandlers(definition);
    expect(handlers.commit(context(1, 2, () => 0.9))).toMatchObject({
      sound: 'normal-feedback',
      commit: { specialTriggered: false, changeIndex: 0 },
    });
    expect(handlers.commit(context(1, 2, () => 0.01))).toMatchObject({
      sound: 'special-feedback',
      effect: 'special-scatter',
      commit: { specialTriggered: true, changeIndex: 0 },
    });
  });

  it('rejects zero-probability and non-canonical local chance definitions', () => {
    const zeroProbability = buildLocalAvatarToolDefinition(dto({
      special: { probability: 0.1, imageUrl: `/user_avatar_tools/${TOOL_ID}/special.png?v=1` },
    }));
    const wrongField = buildLocalAvatarToolDefinition(dto({
      special: { probability: 0.1, imageUrl: `/user_avatar_tools/${TOOL_ID}/special.png?v=1` },
    }));
    if (zeroProbability.interaction.kind !== 'press-release' || !zeroProbability.interaction.chance) {
      throw new Error('invalid local profile');
    }
    if (wrongField.interaction.kind !== 'press-release' || !wrongField.interaction.chance) {
      throw new Error('invalid local profile');
    }
    zeroProbability.interaction.chance.probability = 0;
    wrongField.interaction.chance.field = 'otherTriggered';

    expect(() => validateAvatarToolDefinition(zeroProbability)).toThrow(/greater than zero/);
    expect(() => validateAvatarToolDefinition(wrongField)).toThrow(/must be specialTriggered/);
  });

  it('falls back to normal sound on a special hit and remains silent when neither sound exists', () => {
    const withFallback = buildLocalAvatarToolDefinition(dto({
      normalSoundUrl: `/user_avatar_tools/${TOOL_ID}/normal.mp3?v=1`,
      special: {
        probability: 1,
        imageUrl: `/user_avatar_tools/${TOOL_ID}/special.png?v=1`,
      },
    }));
    const silent = buildLocalAvatarToolDefinition(dto({
      special: {
        probability: 1,
        imageUrl: `/user_avatar_tools/${TOOL_ID}/special.png?v=1`,
      },
    }));

    expect(createAvatarToolProfileHandlers(withFallback).commit(context(1, 2, () => 0))).toMatchObject({
      sound: 'normal-feedback',
      effect: 'special-scatter',
      commit: { specialTriggered: true },
    });
    expect(createAvatarToolProfileHandlers(silent).commit(context(1, 2, () => 0))).toEqual({
      commit: expect.objectContaining({ specialTriggered: true }),
      effect: 'special-scatter',
    });
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
      normalSound: new File(['sound'], 'interaction.mp3', { type: 'audio/mpeg' }),
      special: {
        probability: 0.1,
        image: new File(['special'], 'special.png', { type: 'image/png' }),
        meaning: 'Surprise meaning',
        sound: new File(['special-sound'], 'special.mp3', { type: 'audio/mpeg' }),
      },
    });

    expect(result?.changeMode).toBe('click-advance');
    expect(refreshToken).toHaveBeenCalledTimes(1);
    expect(getMutationHeaders).toHaveBeenCalledTimes(2);
    const firstForm = fetchMock.mock.calls[0][1]?.body as FormData;
    expect(firstForm.get('change_mode')).toBe('click-advance');
    expect(firstForm.getAll('change_images')).toHaveLength(2);
    expect(firstForm.getAll('change_meanings')).toEqual(['First meaning', 'Second meaning']);
    expect(firstForm.get('normal_sound')).toBeInstanceOf(File);
    expect((firstForm.get('normal_sound') as File).name).toBe('interaction.mp3');
    expect(firstForm.get('special_probability')).toBe('0.1');
    expect((firstForm.get('special_image') as File).name).toBe('special.png');
    expect(firstForm.get('special_meaning')).toBe('Surprise meaning');
    expect((firstForm.get('special_sound') as File).name).toBe('special.mp3');
  });
});
