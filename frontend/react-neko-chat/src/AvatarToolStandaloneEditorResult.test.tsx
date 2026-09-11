import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import AvatarToolStandaloneEditor from './AvatarToolStandaloneEditor';

const LOCAL_ID = 'local-12345678-1234-4123-8123-123456789abc' as const;
const catalog = vi.hoisted(() => ({
  limits: {
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
  },
  authoritativeLoaded: true,
  refreshFailed: false,
  refresh: vi.fn(),
  detail: vi.fn(),
  create: vi.fn(),
  update: vi.fn(),
  remove: vi.fn(),
}));

vi.mock('./avatar-tools/useLocalAvatarToolCatalog', () => ({
  useLocalAvatarToolCatalog: () => catalog,
}));

vi.mock('./AvatarToolCreatePage', () => ({
  default: ({ onSave }: { onSave(input: { toolId: typeof LOCAL_ID }): Promise<void> }) => (
    <button type="button" onClick={() => void onSave({ toolId: LOCAL_ID })}>
      Save fixture
    </button>
  ),
}));

describe('AvatarToolStandaloneEditor result lifecycle', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    catalog.create.mockResolvedValue(undefined);
    window.history.replaceState({}, '', '/avatar_tool_editor?mode=create');
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('reports the saved tool, restores its opener, and closes only the editor window', async () => {
    const postMessage = vi.fn();
    const focus = vi.fn();
    Object.defineProperty(window, 'opener', {
      configurable: true,
      value: { postMessage, focus, closed: false },
    });
    const close = vi.spyOn(window, 'close').mockImplementation(() => undefined);
    const historyBack = vi.spyOn(window.history, 'back').mockImplementation(() => undefined);

    render(<AvatarToolStandaloneEditor />);
    fireEvent.click(screen.getByRole('button', { name: 'Save fixture' }));

    await waitFor(() => expect(catalog.create).toHaveBeenCalledTimes(1));
    expect(postMessage).toHaveBeenCalledWith({
      type: 'neko:avatar-tool-editor-result',
      action: 'created',
      toolId: LOCAL_ID,
    }, window.location.origin);
    expect(focus).toHaveBeenCalledTimes(1);
    expect(close).toHaveBeenCalledTimes(1);
    expect(historyBack).not.toHaveBeenCalled();
  });
});
