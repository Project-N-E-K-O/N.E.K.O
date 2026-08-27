import { afterEach } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';
import { ACTIVE_AVATAR_TOOLS_STORAGE_KEY } from '../avatarTools';
import { useLocalAvatarToolCatalog } from './useLocalAvatarToolCatalog';

describe('useLocalAvatarToolCatalog failure handling', () => {
  afterEach(() => {
    window.localStorage.removeItem(ACTIVE_AVATAR_TOOLS_STORAGE_KEY);
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    vi.unstubAllGlobals();
  });

  it('keeps the previous snapshot and persisted local slot when GET fails', async () => {
    const stored = '["local-12345678-1234-4123-8123-123456789abc"]';
    window.localStorage.setItem(ACTIVE_AVATAR_TOOLS_STORAGE_KEY, stored);
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));

    const { result } = renderHook(() => useLocalAvatarToolCatalog());
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));

    expect(result.current.authoritativeLoaded).toBe(false);
    expect(result.current.registry.items.map(item => item.id)).toEqual(['lollipop', 'fist', 'hammer', 'rps']);
    expect(window.localStorage.getItem(ACTIVE_AVATAR_TOOLS_STORAGE_KEY)).toBe(stored);
  });

  it('retries when the surface becomes active after an initial failure', async () => {
    const fetchMock = vi.fn()
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        ok: true,
        items: [],
        limits: LIMITS,
      }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', fetchMock);

    const { result } = renderHook(() => useLocalAvatarToolCatalog());
    await waitFor(() => expect(result.current.refreshFailed).toBe(true));

    act(() => window.dispatchEvent(new Event('focus')));
    await waitFor(() => expect(result.current.authoritativeLoaded).toBe(true));
    expect(result.current.refreshFailed).toBe(false);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('skips one definition that fails validation without dropping valid local tools', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      ok: true,
      items: [
        {
          id: 'local-aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
          revision: '2-100',
          name: 'Unsafe',
          changeMode: 'press-swap',
          defaultUrl: 'https://example.com/default.png',
          changeUrls: ['/change-000.png'],
        },
        {
          id: 'local-12345678-1234-4123-8123-123456789abc',
          revision: '2-101',
          name: 'Feather',
          changeMode: 'press-swap',
          defaultUrl: '/default.png',
          changeUrls: ['/change-000.png'],
        },
      ],
      limits: LIMITS,
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })));

    const { result } = renderHook(() => useLocalAvatarToolCatalog());
    await waitFor(() => expect(result.current.authoritativeLoaded).toBe(true));

    expect(result.current.registry.items.map(item => item.id)).toEqual([
      'lollipop',
      'fist',
      'hammer',
      'rps',
      'local-12345678-1234-4123-8123-123456789abc',
    ]);
  });

  it('keeps a successful POST successful and publishes its item when the following GET fails', async () => {
    const createdItem = {
      id: 'local-12345678-1234-4123-8123-123456789abc' as const,
      revision: '2-100',
      name: 'Feather',
      changeMode: 'press-swap',
      defaultUrl: '/default.png?v=1',
      changeUrls: ['/change-000.png?v=1'],
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        ok: true,
        items: [],
        limits: LIMITS,
      }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true, item: createdItem }), {
        status: 201,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockRejectedValueOnce(new Error('refresh offline'));
    vi.stubGlobal('fetch', fetchMock);

    const { result } = renderHook(() => useLocalAvatarToolCatalog());
    await waitFor(() => expect(result.current.authoritativeLoaded).toBe(true));

    await act(async () => {
      await expect(result.current.create({
        toolId: createdItem.id,
        name: 'Feather',
        changeMode: 'press-swap',
        defaultImage: new File(['default'], 'default.png', { type: 'image/png' }),
        changeItems: [{
          image: new File(['pressed'], 'pressed.png', { type: 'image/png' }),
          meaning: 'A gentle touch',
        }],
      })).resolves.toBeUndefined();
    });

    expect(result.current.registry.items.map(item => item.id)).toContain(createdItem.id);
    expect(result.current.refreshFailed).toBe(true);
  });

  it('treats a lost POST response as successful when the authoritative refresh contains its stable id', async () => {
    const createdItem = {
      id: 'local-12345678-1234-4123-8123-123456789abc' as const,
      revision: '2-100',
      name: 'Feather',
      changeMode: 'press-swap',
      defaultUrl: '/default.png?v=1',
      changeUrls: ['/change-000.png?v=1'],
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true, items: [], limits: LIMITS }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockRejectedValueOnce(new TypeError('connection reset'))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true, items: [createdItem], limits: LIMITS }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true, item: createdItem }), {
        status: 201,
        headers: { 'Content-Type': 'application/json' },
      }));
    vi.stubGlobal('fetch', fetchMock);
    const { result } = renderHook(() => useLocalAvatarToolCatalog());
    await waitFor(() => expect(result.current.authoritativeLoaded).toBe(true));

    await act(async () => {
      await expect(result.current.create({
        toolId: createdItem.id,
        name: 'Feather',
        changeMode: 'press-swap',
        defaultImage: new File(['default'], 'default.png', { type: 'image/png' }),
        changeItems: [{
          image: new File(['pressed'], 'pressed.png', { type: 'image/png' }),
          meaning: 'A gentle touch',
        }],
      })).resolves.toBeUndefined();
    });

    expect(result.current.registry.has(createdItem.id)).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(4);
    expect(fetchMock.mock.calls[3]?.[1]).toMatchObject({ method: 'POST' });
  });

  it('keeps creation uncertain when the stable id belongs to different content', async () => {
    const toolId = 'local-12345678-1234-4123-8123-123456789abc' as const;
    const existingItem = {
      id: toolId,
      revision: '2-100',
      name: 'Old feather',
      changeMode: 'press-swap',
      defaultUrl: '/default.png?v=old',
      changeUrls: ['/change-000.png?v=old'],
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true, items: [], limits: LIMITS }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockRejectedValueOnce(new TypeError('connection reset'))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true, items: [existingItem], limits: LIMITS }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        ok: false,
        error_code: 'tool_id_conflict',
      }), {
        status: 409,
        headers: { 'Content-Type': 'application/json' },
      }));
    vi.stubGlobal('fetch', fetchMock);
    const { result } = renderHook(() => useLocalAvatarToolCatalog());
    await waitFor(() => expect(result.current.authoritativeLoaded).toBe(true));

    await act(async () => {
      await expect(result.current.create({
        toolId,
        name: 'Changed feather',
        changeMode: 'press-swap',
        defaultImage: new File(['changed'], 'default.png', { type: 'image/png' }),
        changeItems: [{
          image: new File(['changed'], 'pressed.png', { type: 'image/png' }),
          meaning: 'A different touch',
        }],
      })).rejects.toMatchObject({ message: 'tool_id_conflict' });
    });

    expect(fetchMock).toHaveBeenCalledTimes(4);
  });

  it('does not treat a mismatched stable-id creation conflict as a lost response', async () => {
    const toolId = 'local-12345678-1234-4123-8123-123456789abc' as const;
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true, items: [], limits: LIMITS }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        ok: false,
        error_code: 'tool_id_conflict',
      }), {
        status: 409,
        headers: { 'Content-Type': 'application/json' },
      }));
    vi.stubGlobal('fetch', fetchMock);
    const { result } = renderHook(() => useLocalAvatarToolCatalog());
    await waitFor(() => expect(result.current.authoritativeLoaded).toBe(true));

    await act(async () => {
      await expect(result.current.create({
        toolId,
        name: 'Changed feather',
        changeMode: 'press-swap',
        defaultImage: new File(['changed'], 'default.png', { type: 'image/png' }),
        changeItems: [{
          image: new File(['changed'], 'pressed.png', { type: 'image/png' }),
          meaning: 'A different touch',
        }],
      })).rejects.toMatchObject({ message: 'tool_id_conflict' });
    });

    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('replaces the same registry id immediately after PUT when the follow-up GET fails', async () => {
    const toolId = 'local-12345678-1234-4123-8123-123456789abc' as const;
    const oldItem = {
      id: toolId,
      revision: '100-200',
      name: 'Feather',
      changeMode: 'press-swap',
      defaultUrl: '/default.png?v=1',
      changeUrls: ['/change-000.png?v=1'],
    };
    const updatedItem = {
      ...oldItem,
      revision: '120-300',
      name: 'Soft Feather',
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true, items: [oldItem], limits: LIMITS }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true, item: updatedItem }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockRejectedValueOnce(new Error('refresh offline'));
    vi.stubGlobal('fetch', fetchMock);
    const { result } = renderHook(() => useLocalAvatarToolCatalog());
    await waitFor(() => expect(result.current.registry.has(toolId)).toBe(true));

    await act(async () => {
      await result.current.update(toolId, {
        baseRevision: '100-200',
        name: 'Soft Feather',
        changeMode: 'press-swap',
        defaultImage: { resource: 'default.png' },
        changeItems: [{ resource: 'change-000.png', meaning: 'A gentle touch' }],
      });
    });

    const definition = result.current.registry.getRegistration(toolId).definition;
    expect(definition?.label).toEqual({ kind: 'literal', value: 'Soft Feather' });
    expect(definition?.visual.variants.primary.iconImagePath).toBe('/default.png?v=1');
    expect(result.current.refreshFailed).toBe(true);
  });

  it('treats a lost PUT response as successful only when the authoritative revision and fields changed', async () => {
    const toolId = 'local-12345678-1234-4123-8123-123456789abc' as const;
    const oldItem = {
      id: toolId,
      revision: '100-200',
      name: 'Feather',
      changeMode: 'press-swap',
      defaultUrl: '/default.png?v=1',
      changeUrls: ['/change-000.png?v=1'],
    };
    const updatedItem = {
      ...oldItem,
      revision: '120-300',
      name: 'Soft Feather',
    };
    const listResponse = (items: unknown[]) => new Response(JSON.stringify({ ok: true, items, limits: LIMITS }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(listResponse([oldItem]))
      .mockRejectedValueOnce(new TypeError('connection reset'))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        ok: true,
        detail: {
          id: toolId,
          revision: '120-300',
          name: 'Soft Feather',
          changeMode: 'press-swap',
          defaultImage: { resource: 'default.png', url: updatedItem.defaultUrl },
          changeItems: [{
            resource: 'change-000.png',
            url: updatedItem.changeUrls[0],
            meaning: 'A gentle touch',
          }],
        },
      }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
      .mockResolvedValueOnce(listResponse([updatedItem]));
    vi.stubGlobal('fetch', fetchMock);
    const { result } = renderHook(() => useLocalAvatarToolCatalog());
    await waitFor(() => expect(result.current.registry.has(toolId)).toBe(true));

    await act(async () => {
      await expect(result.current.update(toolId, {
        baseRevision: '100-200',
        name: 'Soft Feather',
        changeMode: 'press-swap',
        defaultImage: { resource: 'default.png', url: oldItem.defaultUrl },
        changeItems: [{ resource: 'change-000.png', url: oldItem.changeUrls[0], meaning: 'A gentle touch' }],
      })).resolves.toBeUndefined();
    });

    expect(result.current.registry.getRegistration(toolId).definition.label).toEqual({
      kind: 'literal',
      value: 'Soft Feather',
    });
    expect(fetchMock).toHaveBeenCalledTimes(4);
  });

  it('keeps a newer catalog revision published after confirming a lost PUT', async () => {
    const toolId = 'local-12345678-1234-4123-8123-123456789abc' as const;
    const oldItem = {
      id: toolId,
      revision: '100-200',
      name: 'Feather',
      changeMode: 'press-swap',
      defaultUrl: '/default.png?v=old',
      changeUrls: ['/change-000.png?v=old'],
    };
    const submittedItem = {
      ...oldItem,
      revision: '120-300',
      name: 'Soft Feather',
    };
    const newestItem = {
      ...submittedItem,
      revision: '130-400',
      name: 'Newest feather',
      defaultUrl: '/default.png?v=newest',
      changeUrls: ['/change-000.png?v=newest'],
    };
    const listResponse = (items: unknown[]) => new Response(JSON.stringify({ ok: true, items, limits: LIMITS }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(listResponse([oldItem]))
      .mockRejectedValueOnce(new TypeError('connection reset'))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        ok: true,
        detail: {
          id: toolId,
          revision: submittedItem.revision,
          name: submittedItem.name,
          changeMode: 'press-swap',
          defaultImage: { resource: 'default.png', url: submittedItem.defaultUrl },
          changeItems: [{
            resource: 'change-000.png',
            url: submittedItem.changeUrls[0],
            meaning: 'A gentle touch',
          }],
        },
      }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
      .mockResolvedValueOnce(listResponse([newestItem]));
    vi.stubGlobal('fetch', fetchMock);
    const { result } = renderHook(() => useLocalAvatarToolCatalog());
    await waitFor(() => expect(result.current.registry.has(toolId)).toBe(true));

    await act(async () => {
      await expect(result.current.update(toolId, {
        baseRevision: oldItem.revision,
        name: submittedItem.name,
        changeMode: 'press-swap',
        defaultImage: { resource: 'default.png', url: oldItem.defaultUrl },
        changeItems: [{
          resource: 'change-000.png',
          url: oldItem.changeUrls[0],
          meaning: 'A gentle touch',
        }],
      })).resolves.toBeUndefined();
    });

    const definition = result.current.registry.getRegistration(toolId).definition;
    expect(definition.label).toEqual({ kind: 'literal', value: 'Newest feather' });
    expect(definition.visual.variants.primary.iconImagePath).toBe(newestItem.defaultUrl);
  });

  it('does not infer a lost retained-resource PUT succeeded when an asset changed elsewhere', async () => {
    const toolId = 'local-12345678-1234-4123-8123-123456789abc' as const;
    const oldItem = {
      id: toolId,
      revision: '100-200',
      name: 'Feather',
      changeMode: 'press-swap',
      defaultUrl: '/default.png?v=old-default',
      changeUrls: ['/change-000.png?v=old-change'],
    };
    const listResponse = (items: unknown[]) => new Response(JSON.stringify({ ok: true, items, limits: LIMITS }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(listResponse([oldItem]))
      .mockRejectedValueOnce(new TypeError('connection reset'))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        ok: true,
        detail: {
          id: toolId,
          revision: '120-300',
          name: 'Soft Feather',
          changeMode: 'press-swap',
          defaultImage: { resource: 'default.png', url: oldItem.defaultUrl },
          changeItems: [{
            resource: 'change-000.png',
            url: '/change-000.png?v=changed-elsewhere',
            meaning: 'A gentle touch',
          }],
        },
      }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
      .mockResolvedValueOnce(listResponse([oldItem]));
    vi.stubGlobal('fetch', fetchMock);
    const { result } = renderHook(() => useLocalAvatarToolCatalog());
    await waitFor(() => expect(result.current.registry.has(toolId)).toBe(true));

    await act(async () => {
      await expect(result.current.update(toolId, {
        baseRevision: '100-200',
        name: 'Soft Feather',
        changeMode: 'press-swap',
        defaultImage: { resource: 'default.png', url: oldItem.defaultUrl },
        changeItems: [{
          resource: 'change-000.png',
          url: oldItem.changeUrls[0],
          meaning: 'A gentle touch',
        }],
      })).rejects.toThrow('connection reset');
    });
  });

  it('returns the latest detail after refreshing an edit revision conflict', async () => {
    const toolId = 'local-12345678-1234-4123-8123-123456789abc' as const;
    const oldItem = {
      id: toolId,
      revision: '100-200',
      name: 'Feather',
      changeMode: 'press-swap',
      defaultUrl: '/default.png?v=1',
      changeUrls: ['/change-000.png?v=1'],
    };
    const conflictDetail = {
      id: toolId,
      revision: '120-300',
      name: 'Changed elsewhere',
      changeMode: 'press-swap',
      defaultImage: { resource: 'default.png', url: '/default.png?v=2' },
      changeItems: [{
        resource: 'change-000.png',
        url: '/change-000.png?v=2',
        meaning: 'Changed elsewhere',
      }],
    };
    const latestDetail = {
      ...conflictDetail,
      revision: '130-400',
      name: 'Changed again',
      defaultImage: { resource: 'default.png', url: '/default.png?v=3' },
      changeItems: [{
        resource: 'change-000.png',
        url: '/change-000.png?v=3',
        meaning: 'Changed again',
      }],
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true, items: [oldItem], limits: LIMITS }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ error_code: 'tool_revision_conflict' }), {
        status: 409,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true, detail: conflictDetail }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true, items: [{
        ...oldItem,
        revision: latestDetail.revision,
        name: latestDetail.name,
        defaultUrl: latestDetail.defaultImage.url,
        changeUrls: latestDetail.changeItems.map(item => item.url),
      }], limits: LIMITS }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true, detail: latestDetail }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }));
    vi.stubGlobal('fetch', fetchMock);
    const { result } = renderHook(() => useLocalAvatarToolCatalog());
    await waitFor(() => expect(result.current.registry.has(toolId)).toBe(true));

    await act(async () => {
      await expect(result.current.update(toolId, {
        baseRevision: '100-200',
        name: 'My pending change',
        changeMode: 'press-swap',
        defaultImage: { resource: 'default.png' },
        changeItems: [{ resource: 'change-000.png', meaning: 'My pending change' }],
      })).rejects.toMatchObject({ currentDetail: latestDetail });
    });
  });

  it('does not infer a lost replacement-file PUT succeeded from matching text fields', async () => {
    const toolId = 'local-12345678-1234-4123-8123-123456789abc' as const;
    const oldItem = {
      id: toolId,
      revision: '100-200',
      name: 'Feather',
      changeMode: 'press-swap',
      defaultUrl: '/default.png?v=1',
      changeUrls: ['/change-000.png?v=1'],
    };
    const listResponse = new Response(JSON.stringify({ ok: true, items: [oldItem], limits: LIMITS }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(listResponse)
      .mockRejectedValueOnce(new TypeError('connection reset'))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        ok: true,
        detail: {
          id: toolId,
          revision: '120-300',
          name: 'Soft Feather',
          changeMode: 'press-swap',
          defaultImage: { resource: 'default.png', url: '/default.png?v=2' },
          changeItems: [{
            resource: 'change-000.png',
            url: '/change-000.png?v=2',
            meaning: 'A gentle touch',
          }],
        },
      }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true, items: [oldItem], limits: LIMITS }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }));
    vi.stubGlobal('fetch', fetchMock);
    const { result } = renderHook(() => useLocalAvatarToolCatalog());
    await waitFor(() => expect(result.current.registry.has(toolId)).toBe(true));

    await act(async () => {
      await expect(result.current.update(toolId, {
        baseRevision: '100-200',
        name: 'Soft Feather',
        changeMode: 'press-swap',
        defaultImage: { resource: 'default.png' },
        changeItems: [{
          file: new File(['replacement'], 'replacement.png', { type: 'image/png' }),
          meaning: 'A gentle touch',
        }],
      })).rejects.toThrow('connection reset');
    });
  });

  it('ignores a pre-create GET and confirms the created item with a newer GET', async () => {
    const createdItem = {
      id: 'local-12345678-1234-4123-8123-123456789abc' as const,
      revision: '2-100',
      name: 'Feather',
      changeMode: 'press-swap',
      defaultUrl: '/default.png?v=1',
      changeUrls: ['/change-000.png?v=1'],
    };
    let resolveStaleGet!: (response: Response) => void;
    const staleGet = new Promise<Response>(resolve => { resolveStaleGet = resolve; });
    let getCount = 0;
    const listResponse = (items: unknown[]) => new Response(JSON.stringify({ ok: true, items, limits: LIMITS }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === 'POST') {
        return new Response(JSON.stringify({ ok: true, item: createdItem }), {
          status: 201,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      getCount += 1;
      if (getCount === 1) return listResponse([]);
      if (getCount === 2) return staleGet;
      return listResponse([createdItem]);
    });
    vi.stubGlobal('fetch', fetchMock);
    const { result } = renderHook(() => useLocalAvatarToolCatalog());
    await waitFor(() => expect(result.current.authoritativeLoaded).toBe(true));

    act(() => window.dispatchEvent(new Event('focus')));
    await waitFor(() => expect(getCount).toBe(2));
    let createPromise!: Promise<void>;
    act(() => {
      createPromise = result.current.create({
        toolId: createdItem.id,
        name: 'Feather',
        changeMode: 'press-swap',
        defaultImage: new File(['default'], 'default.png', { type: 'image/png' }),
        changeItems: [{
          image: new File(['pressed'], 'pressed.png', { type: 'image/png' }),
          meaning: 'A gentle touch',
        }],
      });
    });
    await waitFor(() => expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(true));
    resolveStaleGet(listResponse([]));
    await act(async () => { await createPromise; });

    expect(getCount).toBe(3);
    expect(result.current.registry.has(createdItem.id)).toBe(true);
  });

  it('removes a deleted item immediately and keeps deletion successful if the follow-up GET fails', async () => {
    const localItem = {
      id: 'local-12345678-1234-4123-8123-123456789abc',
      revision: '2-100',
      name: 'Feather',
      changeMode: 'press-swap',
      defaultUrl: '/default.png?v=1',
      changeUrls: ['/change-000.png?v=1'],
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true, items: [localItem], limits: LIMITS }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true, deletedId: localItem.id }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockRejectedValueOnce(new Error('refresh offline'));
    vi.stubGlobal('fetch', fetchMock);
    const { result } = renderHook(() => useLocalAvatarToolCatalog());
    await waitFor(() => expect(result.current.registry.has(localItem.id)).toBe(true));

    await act(async () => {
      await expect(result.current.remove(localItem.id as `local-${string}`)).resolves.toBeUndefined();
    });

    expect(result.current.registry.has(localItem.id)).toBe(false);
    expect(result.current.refreshFailed).toBe(true);
  });

  it('ignores a pre-delete GET and confirms deletion with a newer GET', async () => {
    const localItem = {
      id: 'local-12345678-1234-4123-8123-123456789abc',
      revision: '2-100',
      name: 'Feather',
      changeMode: 'press-swap',
      defaultUrl: '/default.png?v=1',
      changeUrls: ['/change-000.png?v=1'],
    };
    let resolveStaleGet!: (response: Response) => void;
    const staleGet = new Promise<Response>(resolve => { resolveStaleGet = resolve; });
    let getCount = 0;
    const listResponse = (items: unknown[]) => new Response(JSON.stringify({ ok: true, items, limits: LIMITS }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === 'DELETE') {
        return new Response(JSON.stringify({ ok: true, deletedId: localItem.id }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      getCount += 1;
      if (getCount === 1) return listResponse([localItem]);
      if (getCount === 2) return staleGet;
      return listResponse([]);
    });
    vi.stubGlobal('fetch', fetchMock);
    const { result } = renderHook(() => useLocalAvatarToolCatalog());
    await waitFor(() => expect(result.current.registry.has(localItem.id)).toBe(true));

    act(() => window.dispatchEvent(new Event('focus')));
    await waitFor(() => expect(getCount).toBe(2));
    let removePromise!: Promise<void>;
    act(() => { removePromise = result.current.remove(localItem.id as `local-${string}`); });
    await waitFor(() => expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'DELETE')).toBe(true));
    resolveStaleGet(listResponse([localItem]));
    await act(async () => { await removePromise; });

    expect(getCount).toBe(3);
    expect(result.current.registry.has(localItem.id)).toBe(false);
  });

  it('refreshes the actual catalog after a failed delete', async () => {
    const localItem = {
      id: 'local-12345678-1234-4123-8123-123456789abc',
      revision: '2-100',
      name: 'Feather',
      changeMode: 'press-swap',
      defaultUrl: '/default.png?v=1',
      changeUrls: ['/change-000.png?v=1'],
    };
    const listResponse = () => new Response(JSON.stringify({ ok: true, items: [localItem], limits: LIMITS }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(listResponse())
      .mockResolvedValueOnce(new Response(JSON.stringify({ error_code: 'tool_delete_failed' }), {
        status: 500,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(listResponse());
    vi.stubGlobal('fetch', fetchMock);
    const { result } = renderHook(() => useLocalAvatarToolCatalog());
    await waitFor(() => expect(result.current.registry.has(localItem.id)).toBe(true));

    await act(async () => {
      await expect(result.current.remove(localItem.id as `local-${string}`)).rejects.toThrow('tool_delete_failed');
    });

    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(result.current.registry.has(localItem.id)).toBe(true);
  });

  it('confirms an uncertain delete only when the detail endpoint proves the tool is gone', async () => {
    const localItem = {
      id: 'local-12345678-1234-4123-8123-123456789abc',
      revision: '2-100',
      name: 'Feather',
      changeMode: 'press-swap',
      defaultUrl: '/default.png?v=1',
      changeUrls: ['/change-000.png?v=1'],
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true, items: [localItem], limits: LIMITS }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ error_code: 'avatar_tool_delete_failed' }), {
        status: 500,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true, items: [], limits: LIMITS }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
      // 列表缺席还不够：要一个明确的 tool_not_found 才算删掉了。
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: false, error_code: 'tool_not_found' }), {
        status: 404,
        headers: { 'Content-Type': 'application/json' },
      }));
    vi.stubGlobal('fetch', fetchMock);
    const { result } = renderHook(() => useLocalAvatarToolCatalog());
    await waitFor(() => expect(result.current.registry.has(localItem.id)).toBe(true));

    await act(async () => {
      await expect(result.current.remove(localItem.id as `local-${string}`)).resolves.toBeUndefined();
    });

    expect(result.current.registry.has(localItem.id)).toBe(false);
  });

  it('rejects an uncertain delete when the tool is merely quarantined out of the list', async () => {
    const localItem = {
      id: 'local-12345678-1234-4123-8123-123456789abc',
      revision: '2-100',
      name: 'Feather',
      changeMode: 'press-swap',
      defaultUrl: '/default.png?v=1',
      changeUrls: ['/change-000.png?v=1'],
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true, items: [localItem], limits: LIMITS }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ error_code: 'avatar_tool_delete_failed' }), {
        status: 500,
        headers: { 'Content-Type': 'application/json' },
      }))
      // 被隔离的道具同样不在列表里，但它还在磁盘上 —— 不能当成删除成功。
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true, items: [], limits: LIMITS }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: false, error_code: 'record_invalid' }), {
        status: 404,
        headers: { 'Content-Type': 'application/json' },
      }));
    vi.stubGlobal('fetch', fetchMock);
    const { result } = renderHook(() => useLocalAvatarToolCatalog());
    await waitFor(() => expect(result.current.registry.has(localItem.id)).toBe(true));

    await act(async () => {
      await expect(result.current.remove(localItem.id as `local-${string}`)).rejects.toThrow();
    });
  });

  it('refreshes while hidden when the desktop bridge requests local invalidation', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true, items: [], limits: LIMITS }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true, items: [], limits: LIMITS }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }));
    vi.stubGlobal('fetch', fetchMock);
    const { result } = renderHook(() => useLocalAvatarToolCatalog());
    await waitFor(() => expect(result.current.authoritativeLoaded).toBe(true));
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' });

    act(() => window.dispatchEvent(new Event('neko:refresh-local-avatar-tools')));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
  });

  it('queues a fresh catalog fetch when desktop invalidation arrives during an older fetch', async () => {
    const oldItem = {
      id: 'local-12345678-1234-4123-8123-123456789abc',
      revision: '100-200',
      name: 'Old feather',
      changeMode: 'press-swap',
      defaultUrl: '/default.png?v=old',
      changeUrls: ['/change-000.png?v=old'],
    };
    const newItem = {
      ...oldItem,
      revision: '120-300',
      name: 'New feather',
      defaultUrl: '/default.png?v=new',
      changeUrls: ['/change-000.png?v=new'],
    };
    let resolveOldFetch!: (response: Response) => void;
    const oldFetch = new Promise<Response>((resolve) => { resolveOldFetch = resolve; });
    const listResponse = (items: unknown[]) => new Response(JSON.stringify({ ok: true, items, limits: LIMITS }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
    const fetchMock = vi.fn()
      .mockReturnValueOnce(oldFetch)
      .mockResolvedValueOnce(listResponse([newItem]));
    vi.stubGlobal('fetch', fetchMock);
    const { result } = renderHook(() => useLocalAvatarToolCatalog());
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    act(() => window.dispatchEvent(new Event('neko:refresh-local-avatar-tools')));
    resolveOldFetch(listResponse([oldItem]));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(
      result.current.registry.getRegistration(newItem.id as `local-${string}`).definition.label,
    ).toEqual({ kind: 'literal', value: 'New feather' }));
  });

  it('queues a fresh catalog fetch when focus arrives during an older fetch', async () => {
    const oldItem = {
      id: 'local-12345678-1234-4123-8123-123456789abc',
      revision: '100-200',
      name: 'Old feather',
      changeMode: 'press-swap',
      defaultUrl: '/default.png?v=old',
      changeUrls: ['/change-000.png?v=old'],
    };
    const newItem = {
      ...oldItem,
      revision: '120-300',
      name: 'New feather',
      defaultUrl: '/default.png?v=new',
      changeUrls: ['/change-000.png?v=new'],
    };
    let resolveOldFetch!: (response: Response) => void;
    const oldFetch = new Promise<Response>((resolve) => { resolveOldFetch = resolve; });
    const listResponse = (items: unknown[]) => new Response(JSON.stringify({ ok: true, items, limits: LIMITS }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
    const fetchMock = vi.fn()
      .mockReturnValueOnce(oldFetch)
      .mockResolvedValueOnce(listResponse([newItem]));
    vi.stubGlobal('fetch', fetchMock);
    const { result } = renderHook(() => useLocalAvatarToolCatalog());
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    act(() => window.dispatchEvent(new Event('focus')));
    resolveOldFetch(listResponse([oldItem]));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(
      result.current.registry.getRegistration(newItem.id as `local-${string}`).definition.label,
    ).toEqual({ kind: 'literal', value: 'New feather' }));
  });
});

const LIMITS = {
  maxTools: 64,
  maxNameChars: 20,
  maxMeaningChars: 100,
  maxChangeImages: 16,
  maxImageBytes: 8_388_608,
  maxImagePixels: 16_000_000,
  maxAudioBytes: 5_242_880,
  maxAudioDurationMs: 10_000,
  maxTotalBytes: 268_435_456,
};
