import { afterEach, describe, expect, it, vi } from 'vitest';
import { createAvatarToolProfileHandlers } from './profileInterpreter';
import { validateAvatarToolDefinition } from './catalog';
import {
  buildLocalAvatarToolDefinition,
  createLocalAvatarTool,
  deleteLocalAvatarTool,
  fetchLocalAvatarToolDetail,
  fetchLocalAvatarTools,
  LocalAvatarToolCreateError,
  LocalAvatarToolDeleteError,
  updateLocalAvatarTool,
  type LocalAvatarToolV2Dto,
} from './localTools';

const TOOL_ID = 'local-12345678-1234-4123-8123-123456789abc' as const;
const LIMITS = {
  maxTools: 64,
  maxNameChars: 20,
  maxMeaningChars: 100,
  maxChangeImages: 16,
  maxImages: 17,
  maxInteractions: 16,
  maxLinks: 32,
  maxDelayMs: 600000,
  maxImageBytes: 8_388_608,
  maxImagePixels: 16_000_000,
  maxAudioBytes: 5_242_880,
  maxAudioDurationMs: 10_000,
  maxTotalBytes: 268_435_456,
};

function dto(overrides: Partial<LocalAvatarToolV2Dto> = {}): LocalAvatarToolV2Dto {
  return {
    id: TOOL_ID,
    recordVersion: 2, revision: '2-123',
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

  it.each([
    { recordVersion: undefined, revision: '2-123', valid: false },
    { recordVersion: 2, revision: '3-123', valid: false },
    { recordVersion: 2, revision: '100-200', valid: false },
    { recordVersion: 2, revision: '2-123', valid: true },
  ])('enforces explicit v2 list and detail revisions: %j', async ({ recordVersion, revision, valid }) => {
    const item = { ...dto(), recordVersion, revision };
    const detail = {
      recordVersion, revision, id: TOOL_ID, name: 'Feather', changeMode: 'press-swap',
      defaultImage: { resource: 'default.png', url: '/default.png?v=1' },
      changeItems: [{ resource: 'change-000.png', url: '/change-000.png?v=1', meaning: 'Change' }],
    };
    vi.stubGlobal('fetch', vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true, items: [item], limits: LIMITS })))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true, detail, limits: LIMITS }))));
    await expect.soft(fetchLocalAvatarTools()).resolves.toMatchObject({ items: valid ? [item] : [] });
    if (valid) {
      await expect(fetchLocalAvatarToolDetail(TOOL_ID)).resolves.toMatchObject(detail);
    } else {
      await expect(fetchLocalAvatarToolDetail(TOOL_ID)).rejects.toThrow('avatar_tool_detail_invalid');
    }
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
    expect(handlers.pointerDown({ ...context(0, 2), hit: null })).toEqual({});
    expect(handlers.pointerDown({ ...context(0, 2), interactionLocked: true })).toEqual({});
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

  it('rejects an explicitly malformed optional local chance sound', () => {
    const definition = buildLocalAvatarToolDefinition(dto({
      special: { probability: 0.1, imageUrl: `/user_avatar_tools/${TOOL_ID}/special.png?v=1` },
    }));
    if (definition.interaction.kind !== 'press-release' || !definition.interaction.chance) {
      throw new Error('invalid local profile');
    }

    for (const sound of ['', null, undefined]) {
      const malformed = structuredClone(definition);
      if (malformed.interaction.kind !== 'press-release' || !malformed.interaction.chance) {
        throw new Error('invalid local profile');
      }
      malformed.interaction.chance.sound = sound as never;
      expect(() => validateAvatarToolDefinition(malformed)).toThrow(/interaction\.chance\.sound/);
    }
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

  it('validates item image counts against the authoritative response limit', async () => {
    const limits = { ...LIMITS, maxChangeImages: 1 };
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      ok: true,
      items: [dto({
        changeMode: 'click-advance',
        changeUrls: ['/change-000.png?v=1', '/change-001.png?v=1'],
      })],
      limits,
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })));

    await expect(fetchLocalAvatarTools()).resolves.toEqual({ items: [], limits });
  });

  it('drops catalog items with an empty default image URL', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      ok: true,
      items: [dto({ defaultUrl: '' })],
      limits: LIMITS,
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })));

    await expect(fetchLocalAvatarTools()).resolves.toEqual({ items: [], limits: LIMITS });
  });

  it('drops catalog items whose resource URL is unversioned, fragmented, or has extra parameters', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      ok: true,
      items: [
        dto({ id: 'local-aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', defaultUrl: '/default.png' }),
        dto({ id: 'local-bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', defaultUrl: '/default.png?v=1#preview' }),
        dto({ id: 'local-cccccccc-cccc-4ccc-8ccc-cccccccccccc', defaultUrl: '/default.png?v=1&extra=1' }),
      ],
      limits: LIMITS,
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })));

    await expect(fetchLocalAvatarTools()).resolves.toEqual({ items: [], limits: LIMITS });
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
      toolId: TOOL_ID,
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
    const secondForm = fetchMock.mock.calls[1][1]?.body as FormData;
    expect(firstForm.get('tool_id')).toBe(TOOL_ID);
    expect(secondForm.get('tool_id')).toBe(TOOL_ID);
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

  it('preserves the server field and item index on create errors', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      error_code: 'image_too_large',
      field: 'change_image',
      index: 1,
    }), {
      status: 413,
      headers: { 'Content-Type': 'application/json' },
    })));

    let failure: unknown;
    try {
      await createLocalAvatarTool({
        toolId: TOOL_ID,
        name: 'Feather',
        changeMode: 'click-advance',
        defaultImage: new File(['default'], 'default.png', { type: 'image/png' }),
        changeItems: [
          { image: new File(['one'], 'one.png', { type: 'image/png' }), meaning: 'One' },
          { image: new File(['two'], 'two.png', { type: 'image/png' }), meaning: 'Two' },
        ],
      });
    } catch (cause) {
      failure = cause;
    }

    expect(failure).toBeInstanceOf(LocalAvatarToolCreateError);
    expect(failure).toMatchObject({
      message: 'image_too_large',
      field: 'change_image',
      index: 1,
    });
  });

  it('loads private edit details only from the targeted endpoint', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      ok: true,
      detail: {
        id: TOOL_ID,
        recordVersion: 2, revision: '2-200',
        name: 'Feather',
        changeMode: 'press-swap',
        defaultImage: { resource: 'default.png', url: '/default.png?v=1' },
        changeItems: [{
          resource: 'change-000.png',
          url: '/change-000.png?v=1',
          meaning: 'A gentle touch',
        }],
      },
      limits: LIMITS,
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', fetchMock);

    await expect(fetchLocalAvatarToolDetail(TOOL_ID)).resolves.toMatchObject({
      id: TOOL_ID,
      changeItems: [{ meaning: 'A gentle touch' }],
    });
    expect(fetchMock).toHaveBeenCalledWith(`/api/avatar-tools/${TOOL_ID}`, {
      credentials: 'same-origin',
      cache: 'no-store',
    });
  });

  it('puts retained resources, replacements, removals, and ordering under the same id', async () => {
    window.nekoLocalMutationSecurity = {
      getMutationHeaders: () => ({ 'X-CSRF-Token': 'token' }),
    };
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      ok: true,
      item: dto({
        name: 'Soft Feather',
        changeMode: 'click-advance',
        changeUrls: ['/change-000.png?v=2', '/change-001.png?v=2'],
      }),
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', fetchMock);
    const replacement = new File(['new'], 'new.png', { type: 'image/png' });

    await expect(updateLocalAvatarTool(TOOL_ID, {
      baseRevision: '2-200',
      name: 'Soft Feather',
      changeMode: 'click-advance',
      defaultImage: { resource: 'default.png' },
      changeItems: [
        { resource: 'change-000.png', meaning: 'Retained' },
        { file: replacement, meaning: 'Replacement' },
      ],
      special: {
        probability: 0.25,
        image: { resource: 'special.png' },
        meaning: 'Surprise',
      },
    })).resolves.toMatchObject({ id: TOOL_ID, name: 'Soft Feather' });

    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toBe(`/api/avatar-tools/${TOOL_ID}`);
    expect(options).toMatchObject({ method: 'PUT', headers: { 'X-CSRF-Token': 'token' } });
    const form = options.body as FormData;
    expect(form.get('default_resource')).toBe('default.png');
    expect(form.get('base_revision')).toBe('2-200');
    expect(form.getAll('change_resources')).toEqual(['change-000.png', '']);
    expect(form.getAll('change_meanings')).toEqual(['Retained', 'Replacement']);
    expect(form.getAll('change_images')).toEqual([replacement]);
    expect(form.has('normal_sound_resource')).toBe(false);
    expect(form.get('special_image_resource')).toBe('special.png');
    expect(form.has('special_sound_resource')).toBe(false);
  });

  it('rebuilds PUT FormData when retrying after a csrf refresh', async () => {
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
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true, item: dto() }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }));
    vi.stubGlobal('fetch', fetchMock);
    const replacement = new File(['new'], 'new.png', { type: 'image/png' });

    await updateLocalAvatarTool(TOOL_ID, {
      baseRevision: '2-200',
      name: 'Feather',
      changeMode: 'press-swap',
      defaultImage: { resource: 'default.png' },
      changeItems: [{ file: replacement, meaning: 'Replacement' }],
    });

    expect(refreshToken).toHaveBeenCalledTimes(1);
    expect(getMutationHeaders).toHaveBeenCalledTimes(2);
    const firstForm = fetchMock.mock.calls[0][1]?.body as FormData;
    const secondForm = fetchMock.mock.calls[1][1]?.body as FormData;
    expect(secondForm).not.toBe(firstForm);
    expect(secondForm.getAll('change_images')).toEqual([replacement]);
    expect(fetchMock.mock.calls[1][1]?.headers).toEqual({ 'X-CSRF-Token': 'new' });
  });

  it('deletes a local tool with mutation headers and retries csrf failure once', async () => {
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
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true, deletedId: TOOL_ID }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }));
    vi.stubGlobal('fetch', fetchMock);

    await expect(deleteLocalAvatarTool(TOOL_ID)).resolves.toBeUndefined();

    expect(refreshToken).toHaveBeenCalledTimes(1);
    expect(getMutationHeaders).toHaveBeenCalledTimes(2);
    expect(fetchMock).toHaveBeenNthCalledWith(1, `/api/avatar-tools/${TOOL_ID}`, expect.objectContaining({
      method: 'DELETE',
      credentials: 'same-origin',
      headers: { 'X-CSRF-Token': 'old' },
    }));
  });

  it('rejects malformed delete success responses', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      ok: true,
      deletedId: 'local-aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
    }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })));

    await expect(deleteLocalAvatarTool(TOOL_ID)).rejects.toEqual(
      expect.objectContaining({
        name: 'LocalAvatarToolDeleteError',
        message: 'avatar_tool_delete_failed',
      }),
    );
    await expect(deleteLocalAvatarTool('lollipop' as `local-${string}`)).rejects.toBeInstanceOf(
      LocalAvatarToolDeleteError,
    );
  });
});

describe('local avatar tool v3 persistence transport', () => {
  const graph = {
    initialImagePosition: { x: 20, y: 40 },
    initialLinks: [{ to: 'ix-click' as const, sourceSide: 'right' as const, targetSide: 'left' as const }],
    items: [{
      id: 'ix-click' as const,
      name: '',
      trigger: { kind: 'mouse-click' as const },
      actions: { press: { kind: 'keep' as const }, release: { kind: 'keep' as const } },
      editorPosition: { x: 300, y: 40 },
    }],
    links: [{
      from: 'ix-click' as const,
      to: 'ix-click' as const,
      sourceSide: 'right' as const,
      targetSide: 'right' as const,
    }],
  };
  const runtime = {
    images: [{
      id: 'img-1' as const,
      url: '/user_avatar_tools/tool/image-000.png?v=1',
      hasMeaning: false,
    }],
    initialImageId: 'img-1' as const,
    initialInteractionIds: ['ix-click' as const],
    interactions: [{
      id: 'ix-click' as const,
      trigger: { kind: 'mouse-click' as const },
      actions: { press: { kind: 'keep' as const }, release: { kind: 'keep' as const } },
    }],
    links: [{ from: 'ix-click' as const, to: 'ix-click' as const }],
  };

  it('sends one strict manifest with indexed uploads when creating v3', async () => {
    window.nekoLocalMutationSecurity = { getMutationHeaders: () => ({ 'X-CSRF-Token': 'token' }) };
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      ok: true,
      item: {
        recordVersion: 3,
        id: TOOL_ID,
        revision: '3-123',
        name: 'Flow',
        initialImageUrl: '/user_avatar_tools/tool/image-000.png?v=1',
        runtime,
      },
    }), { status: 201, headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', fetchMock);
    const image = new File(['png'], 'state.png', { type: 'image/png' });

    await expect(createLocalAvatarTool({
      recordVersion: 3,
      toolId: TOOL_ID,
      name: 'Flow',
      images: [{ id: 'img-1', name: '', image: { file: image }, meaning: '' }],
      initialImageId: 'img-1',
      imageInteractions: graph,
    })).resolves.toMatchObject({ recordVersion: 3, id: TOOL_ID });

    const request = fetchMock.mock.calls[0]?.[1] as RequestInit;
    const form = request.body as FormData;
    expect(form.get('record_version')).toBe('3');
    expect(form.getAll('uploads')).toEqual([image]);
    expect(JSON.parse(String(form.get('manifest')))).toEqual({
      recordVersion: 3,
      id: TOOL_ID,
      name: 'Flow',
      images: [{ id: 'img-1', name: '', source: { kind: 'upload', index: 0 }, meaning: '' }],
      initialImageId: 'img-1',
      imageInteractions: graph,
      interaction: {},
    });
  });

  it('reopens a v3 detail without losing names, positions, actions, or selected sides', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      ok: true,
      limits: LIMITS,
      detail: {
        recordVersion: 3,
        id: TOOL_ID,
        revision: '3-456',
        name: 'Flow',
        images: [{
          id: 'img-1',
          name: 'Idle',
          resource: 'image-000.png',
          url: '/user_avatar_tools/tool/image-000.png?v=1',
          meaning: 'waiting',
        }],
        initialImageId: 'img-1',
        imageInteractions: graph,
      },
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })));

    await expect(fetchLocalAvatarToolDetail(TOOL_ID)).resolves.toMatchObject({
      recordVersion: 3,
      images: [{ id: 'img-1', name: 'Idle', meaning: 'waiting' }],
      imageInteractions: graph,
    });
  });

  it('uses Unicode code points for the shared name limit when reopening v3', async () => {
    const legalName = '𠮷'.repeat(LIMITS.maxNameChars);
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      ok: true,
      limits: LIMITS,
      detail: {
        recordVersion: 3,
        id: TOOL_ID,
        revision: '3-456',
        name: legalName,
        images: [{
          id: 'img-1',
          name: legalName,
          resource: 'image-000.png',
          url: '/user_avatar_tools/tool/image-000.png?v=1',
          meaning: '',
        }],
        initialImageId: 'img-1',
        imageInteractions: {
          ...graph,
          items: [{ ...graph.items[0], name: legalName }],
        },
        normalSound: {
          resource: 'normal.mp3',
          url: '/user_avatar_tools/tool/normal.mp3?v=1',
        },
        special: {
          probability: 0.2,
          image: {
            resource: 'special.png',
            url: '/user_avatar_tools/tool/special.png?v=1',
          },
          meaning: 'sparkles appear',
          sound: {
            resource: 'special.mp3',
            url: '/user_avatar_tools/tool/special.mp3?v=1',
          },
        },
      },
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })));

    await expect(fetchLocalAvatarToolDetail(TOOL_ID)).resolves.toMatchObject({
      name: legalName,
      images: [{ name: legalName }],
      imageInteractions: { items: [{ name: legalName }] },
      normalSound: { resource: 'normal.mp3' },
      special: { image: { resource: 'special.png' }, sound: { resource: 'special.mp3' } },
    });
  });

  it('rejects non-canonical resource URLs returned by the detail endpoint', async () => {
    const detail = {
      recordVersion: 3,
      id: TOOL_ID,
      revision: '3-456',
      name: 'Flow',
      images: [{
        id: 'img-1',
        name: 'Idle',
        resource: 'image-000.png',
        url: '/user_avatar_tools/tool/image-000.png?v=1&extra=1',
        meaning: '',
      }],
      initialImageId: 'img-1',
      imageInteractions: graph,
    };
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      ok: true,
      limits: LIMITS,
      detail,
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })));

    await expect(fetchLocalAvatarToolDetail(TOOL_ID)).rejects.toMatchObject({
      message: 'avatar_tool_detail_invalid',
    });
  });

  it('rejects invalid optional names returned by the detail endpoint', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      ok: true,
      limits: LIMITS,
      detail: {
        recordVersion: 3,
        id: TOOL_ID,
        revision: '3-456',
        name: 'Flow',
        images: [{
          id: 'img-1',
          name: 'bad!',
          resource: 'image-000.png',
          url: '/user_avatar_tools/tool/image-000.png?v=1',
          meaning: '',
        }],
        initialImageId: 'img-1',
        imageInteractions: graph,
      },
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })));

    await expect(fetchLocalAvatarToolDetail(TOOL_ID)).rejects.toMatchObject({
      message: 'avatar_tool_detail_invalid',
    });
  });

  it('rejects a v3 detail whose image resource is not canonical', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      ok: true,
      limits: LIMITS,
      detail: {
        recordVersion: 3,
        id: TOOL_ID,
        revision: '3-456',
        name: 'Flow',
        images: [{
          id: 'img-1',
          name: '',
          resource: 'special.png',
          url: '/user_avatar_tools/tool/special.png?v=1',
          meaning: '',
        }],
        initialImageId: 'img-1',
        imageInteractions: graph,
      },
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })));

    await expect(fetchLocalAvatarToolDetail(TOOL_ID)).rejects.toMatchObject({
      message: 'avatar_tool_detail_invalid',
    });
  });

  it('rejects v3 names that collide after the editor normalization', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      ok: true,
      limits: LIMITS,
      detail: {
        recordVersion: 3,
        id: TOOL_ID,
        revision: '3-456',
        name: 'Flow',
        images: [
          {
            id: 'img-1',
            name: '  Caf\u00e9  au lait  ',
            resource: 'image-000.png',
            url: '/user_avatar_tools/tool/image-000.png?v=1',
            meaning: '',
          },
          {
            id: 'img-2',
            name: 'cafe\u0301 au   LAIT',
            resource: 'image-001.png',
            url: '/user_avatar_tools/tool/image-001.png?v=2',
            meaning: '',
          },
        ],
        initialImageId: 'img-1',
        imageInteractions: graph,
      },
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })));

    await expect(fetchLocalAvatarToolDetail(TOOL_ID)).rejects.toMatchObject({
      message: 'avatar_tool_detail_invalid',
    });
  });

  it('uses named retained resources in a v3 update manifest', async () => {
    window.nekoLocalMutationSecurity = { getMutationHeaders: () => ({ 'X-CSRF-Token': 'token' }) };
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      ok: true,
      item: {
        recordVersion: 3,
        id: TOOL_ID,
        revision: '3-789',
        name: 'Flow',
        initialImageUrl: '/user_avatar_tools/tool/image-000.png?v=1',
        runtime,
      },
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', fetchMock);

    await updateLocalAvatarTool(TOOL_ID, {
      recordVersion: 3,
      baseRevision: '3-456',
      name: 'Flow',
      images: [{ id: 'img-1', name: '', image: { resource: 'image-000.png' }, meaning: '' }],
      initialImageId: 'img-1',
      imageInteractions: graph,
    });

    const form = fetchMock.mock.calls[0]?.[1]?.body as FormData;
    expect(JSON.parse(String(form.get('manifest'))).images[0].source).toEqual({
      kind: 'resource',
      name: 'image-000.png',
    });
  });

  it('treats malformed successful create and update responses as uncertain failures', async () => {
    window.nekoLocalMutationSecurity = { getMutationHeaders: () => ({ 'X-CSRF-Token': 'token' }) };
    const image = new File(['png'], 'state.png', { type: 'image/png' });
    const input = {
      recordVersion: 3 as const,
      name: 'Flow',
      images: [{ id: 'img-1' as const, name: '', image: { file: image }, meaning: '' }],
      initialImageId: 'img-1' as const,
      imageInteractions: graph,
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true, item: null }), {
        status: 201,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response('{', {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }));
    vi.stubGlobal('fetch', fetchMock);

    await expect(createLocalAvatarTool({ ...input, toolId: TOOL_ID })).rejects.toMatchObject({
      message: 'avatar_tool_create_response_invalid',
    });
    await expect(updateLocalAvatarTool(TOOL_ID, {
      ...input,
      baseRevision: '3-456',
    })).rejects.toMatchObject({
      message: 'avatar_tool_update_response_invalid',
    });
  });
});
