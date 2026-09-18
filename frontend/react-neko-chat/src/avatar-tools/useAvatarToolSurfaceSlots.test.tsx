import { act, renderHook } from '@testing-library/react';
import { ACTIVE_AVATAR_TOOLS_STORAGE_KEY } from '../avatarTools';
import { useAvatarToolSurfaceSlots } from './useAvatarToolSurfaceSlots';
import type { LocalAvatarToolCatalog } from './useLocalAvatarToolCatalog';

describe('useAvatarToolSurfaceSlots', () => {
  afterEach(() => window.localStorage.removeItem(ACTIVE_AVATAR_TOOLS_STORAGE_KEY));

  it('keeps mounted surfaces independent while sharing only their slot rules', async () => {
    const remove = vi.fn().mockResolvedValue(undefined);
    const catalog = {
      items: [], authoritativeLoaded: false, remove, refresh: vi.fn(),
    } as unknown as LocalAvatarToolCatalog;
    const compactClear = vi.fn();
    const fullClear = vi.fn();
    const compact = renderHook(() => useAvatarToolSurfaceSlots({
      catalog, activeToolId: 'fist', clearActiveTool: compactClear, managerOpen: false,
    }));
    const full = renderHook(() => useAvatarToolSurfaceSlots({
      catalog, activeToolId: 'fist', clearActiveTool: fullClear, managerOpen: false,
    }));

    act(() => compact.result.current.saveSlots(['lollipop']));
    expect(compact.result.current.activeToolIds).toEqual(['lollipop']);
    expect(full.result.current.activeToolIds).toEqual(['lollipop', 'fist', 'hammer']);
    expect(compactClear).toHaveBeenCalled();
    expect(fullClear).not.toHaveBeenCalled();

    await act(async () => full.result.current.deleteLocalTool('local-12345678-1234-4123-8123-123456789abc'));
    expect(remove).toHaveBeenCalledTimes(1);
    expect(full.result.current.activeToolIds).toEqual(['lollipop', 'fist', 'hammer']);
    expect(compact.result.current.activeToolIds).toEqual(['lollipop']);
    compact.unmount();
    full.unmount();
  });

  it('keeps a local slot when deletion fails and removes only that ID after success', async () => {
    const localId = 'local-12345678-1234-4123-8123-123456789abc' as const;
    window.localStorage.setItem(ACTIVE_AVATAR_TOOLS_STORAGE_KEY, JSON.stringify([localId, 'fist']));
    const remove = vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValue(undefined);
    const catalog = {
      items: [], authoritativeLoaded: false, remove, refresh: vi.fn(),
    } as unknown as LocalAvatarToolCatalog;
    const { result } = renderHook(() => useAvatarToolSurfaceSlots({
      catalog, activeToolId: null, clearActiveTool: vi.fn(), managerOpen: false,
    }));

    let failure: unknown;
    await act(async () => {
      try { await result.current.deleteLocalTool(localId); } catch (error) { failure = error; }
    });
    expect(failure).toEqual(new Error('offline'));
    expect(result.current.activeToolIds).toEqual([localId, 'fist']);
    await act(async () => result.current.deleteLocalTool(localId));
    expect(result.current.activeToolIds).toEqual(['fist']);
    expect(JSON.parse(window.localStorage.getItem(ACTIVE_AVATAR_TOOLS_STORAGE_KEY) ?? 'null')).toEqual(['fist']);
  });
});
