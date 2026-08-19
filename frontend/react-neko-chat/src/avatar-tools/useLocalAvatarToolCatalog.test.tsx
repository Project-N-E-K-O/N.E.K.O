import { afterEach } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';
import { ACTIVE_AVATAR_TOOLS_STORAGE_KEY } from '../avatarTools';
import { useLocalAvatarToolCatalog } from './useLocalAvatarToolCatalog';

describe('useLocalAvatarToolCatalog failure handling', () => {
  afterEach(() => {
    window.localStorage.removeItem(ACTIVE_AVATAR_TOOLS_STORAGE_KEY);
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
});

const LIMITS = {
  maxTools: 64,
  maxNameChars: 80,
  maxMeaningChars: 1200,
  maxChangeImages: 16,
  maxImageBytes: 8_388_608,
  maxImagePixels: 16_000_000,
  maxTotalBytes: 268_435_456,
};
