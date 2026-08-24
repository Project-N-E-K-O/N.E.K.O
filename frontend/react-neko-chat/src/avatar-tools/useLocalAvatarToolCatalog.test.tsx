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
          name: 'Unsafe',
          changeMode: 'press-swap',
          defaultUrl: 'https://example.com/default.png',
          changeUrls: ['/change-000.png'],
        },
        {
          id: 'local-12345678-1234-4123-8123-123456789abc',
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
      id: 'local-12345678-1234-4123-8123-123456789abc',
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

  it('replaces the same registry id immediately after PUT when the follow-up GET fails', async () => {
    const toolId = 'local-12345678-1234-4123-8123-123456789abc' as const;
    const oldItem = {
      id: toolId,
      name: 'Feather',
      changeMode: 'press-swap',
      defaultUrl: '/default.png?v=1',
      changeUrls: ['/change-000.png?v=1'],
    };
    const updatedItem = {
      ...oldItem,
      name: 'Soft Feather',
      defaultUrl: '/default.png?v=2',
      changeUrls: ['/change-000.png?v=2'],
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
    expect(definition?.visual.variants.primary.iconImagePath).toBe('/default.png?v=2');
    expect(result.current.refreshFailed).toBe(true);
  });

  it('treats a lost PUT response as successful only when the authoritative revision and fields changed', async () => {
    const toolId = 'local-12345678-1234-4123-8123-123456789abc' as const;
    const oldItem = {
      id: toolId,
      name: 'Feather',
      changeMode: 'press-swap',
      defaultUrl: '/default.png?v=1',
      changeUrls: ['/change-000.png?v=1'],
    };
    const updatedItem = {
      ...oldItem,
      name: 'Soft Feather',
      defaultUrl: '/default.png?v=2',
      changeUrls: ['/change-000.png?v=2'],
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
        defaultImage: { resource: 'default.png' },
        changeItems: [{ resource: 'change-000.png', meaning: 'A gentle touch' }],
      })).resolves.toBeUndefined();
    });

    expect(result.current.registry.getRegistration(toolId).definition.label).toEqual({
      kind: 'literal',
      value: 'Soft Feather',
    });
    expect(fetchMock).toHaveBeenCalledTimes(4);
  });

  it('does not infer a lost replacement-file PUT succeeded from matching text fields', async () => {
    const toolId = 'local-12345678-1234-4123-8123-123456789abc' as const;
    const oldItem = {
      id: toolId,
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
      id: 'local-12345678-1234-4123-8123-123456789abc',
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

  it('treats an uncertain delete as successful when the authoritative refresh shows it absent', async () => {
    const localItem = {
      id: 'local-12345678-1234-4123-8123-123456789abc',
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
      }));
    vi.stubGlobal('fetch', fetchMock);
    const { result } = renderHook(() => useLocalAvatarToolCatalog());
    await waitFor(() => expect(result.current.registry.has(localItem.id)).toBe(true));

    await act(async () => {
      await expect(result.current.remove(localItem.id as `local-${string}`)).resolves.toBeUndefined();
    });

    expect(result.current.registry.has(localItem.id)).toBe(false);
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
