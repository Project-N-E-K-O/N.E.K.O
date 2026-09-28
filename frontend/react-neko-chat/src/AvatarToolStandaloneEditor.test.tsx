import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import AvatarToolStandaloneEditor from './AvatarToolStandaloneEditor';
import { LocalAvatarToolRevisionConflictError, type LocalAvatarToolDetail } from './avatar-tools/localTools';

const LOCAL_ID = 'local-12345678-1234-4123-8123-123456789abc' as const;
const catalog = vi.hoisted(() => ({
  authoritativeLoaded: true,
  refreshFailed: false,
  refresh: vi.fn(),
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
  detail: vi.fn(),
  create: vi.fn(),
  update: vi.fn(),
  remove: vi.fn(),
}));

vi.mock('./avatar-tools/useLocalAvatarToolCatalog', () => ({
  useLocalAvatarToolCatalog: () => catalog,
}));

describe('AvatarToolStandaloneEditor', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.history.replaceState({}, '', '/avatar_tool_editor?mode=create');
  });

  afterEach(() => {
    document.body.classList.remove('avatar-tool-editor-page');
    vi.unstubAllGlobals();
  });

  it('renders creation in the dedicated editor page without a hidden Escape close shortcut', () => {
    const close = vi.spyOn(window, 'close').mockImplementation(() => undefined);
    render(<AvatarToolStandaloneEditor />);

    expect(screen.getByRole('dialog', { name: 'Create custom tool' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Interaction flow' })).toBeInTheDocument();
    expect(screen.getByRole('complementary', { name: 'Tool editor' })).toBeInTheDocument();
    const privacy = screen.getByText(
      'Images and sounds stay on this device. During interactions, the prompt text for the current image or surprise is sent to the model, and the tool\'s name is saved to the character\'s memory and may come up in later conversations.',
    );
    expect(privacy.closest('.avatar-tool-workspace-settings-heading')).not.toBeNull();
    expect(document.querySelector('.avatar-tool-create-fields .avatar-tool-workspace-content-note')).toBeNull();
    expect(screen.queryByText('Details')).toBeNull();
    expect(screen.getByRole('heading', { name: 'Tool editor' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Tool settings' })).toBeInTheDocument();
    expect(document.querySelector('.avatar-tool-workspace-header')).toBeNull();
    expect(document.body).toHaveClass('avatar-tool-editor-page');
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(close).not.toHaveBeenCalled();
    close.mockRestore();
  });

  it('protects an edited draft from target reuse without prompting for an untouched form', () => {
    const { unmount } = render(<AvatarToolStandaloneEditor />);
    expect(window.avatarToolEditorHasUnsavedChanges?.()).toBe(false);

    fireEvent.click(screen.getByRole('button', { name: 'Mouse click' }));
    expect(window.avatarToolEditorHasUnsavedChanges?.()).toBe(true);
    // The opener calls this after the user confirmed discarding in its own prompt.
    window.avatarToolEditorDiscardUnsavedChanges?.();
    expect(window.avatarToolEditorHasUnsavedChanges?.()).toBe(false);
    const beforeUnload = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(beforeUnload);
    expect(beforeUnload.defaultPrevented).toBe(false);
    unmount();
    expect(window.avatarToolEditorHasUnsavedChanges).toBeUndefined();
    expect(window.avatarToolEditorDiscardUnsavedChanges).toBeUndefined();
  });

  it('tracks content edits as unsaved changes', () => {
    render(<AvatarToolStandaloneEditor />);
    fireEvent.change(screen.getByRole('textbox', { name: 'Tool name' }), {
      target: { value: 'New tool' },
    });
    expect(window.avatarToolEditorHasUnsavedChanges?.()).toBe(true);
  });

  it('tracks applying a preset even though it resets the graph state', () => {
    render(<AvatarToolStandaloneEditor />);
    fireEvent.click(screen.getByRole('button', { name: 'Presets' }));
    fireEvent.click(screen.getByRole('button', { name: 'Press swap' }));
    expect(window.avatarToolEditorHasUnsavedChanges?.()).toBe(true);
  });

  it('guards reload and window close only while the draft has unsaved changes', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    const dispatchBeforeUnload = () => {
      const event = new Event('beforeunload', { cancelable: true });
      window.dispatchEvent(event);
      return event.defaultPrevented;
    };
    try {
      const { unmount } = render(<AvatarToolStandaloneEditor />);
      expect(dispatchBeforeUnload()).toBe(false);
      await expect(window.nekoBeforeWindowClose?.()).resolves.toBeUndefined();
      expect(confirm).not.toHaveBeenCalled();

      fireEvent.change(screen.getByRole('textbox', { name: 'Tool name' }), {
        target: { value: 'New tool' },
      });
      expect(dispatchBeforeUnload()).toBe(true);
      await expect(window.nekoBeforeWindowClose?.()).resolves.toEqual({ handled: true });
      expect(confirm).toHaveBeenCalledWith('You have unsaved settings, are you sure you want to leave?');
      expect(window.avatarToolEditorHasUnsavedChanges?.()).toBe(true);

      confirm.mockReturnValue(true);
      await expect(window.nekoBeforeWindowClose?.()).resolves.toBeUndefined();
      // The confirmed title-bar close must not be followed by a second browser prompt.
      expect(dispatchBeforeUnload()).toBe(false);

      fireEvent.change(screen.getByRole('textbox', { name: 'Tool name' }), {
        target: { value: 'Another tool' },
      });
      unmount();
      expect(dispatchBeforeUnload()).toBe(false);
      expect(window.nekoBeforeWindowClose).toBeUndefined();
    } finally {
      confirm.mockRestore();
    }
  });

  it('leaves closing to the title-bar hook in the Electron shell, where beforeunload would block it', async () => {
    const userAgent = vi.spyOn(window.navigator, 'userAgent', 'get').mockReturnValue(
      'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) N.E.K.O/1.0 Chrome/130.0 Electron/33.2.0 Safari/537.36',
    );
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    try {
      render(<AvatarToolStandaloneEditor />);
      fireEvent.change(screen.getByRole('textbox', { name: 'Tool name' }), {
        target: { value: 'New tool' },
      });
      const beforeUnload = new Event('beforeunload', { cancelable: true });
      window.dispatchEvent(beforeUnload);
      // Alt+F4 / 任务栏关闭 / app.quit() 不能被静默吞掉。
      expect(beforeUnload.defaultPrevented).toBe(false);
      // 标题栏关闭仍然先问。
      await expect(window.nekoBeforeWindowClose?.()).resolves.toEqual({ handled: true });
      expect(confirm).toHaveBeenCalledTimes(1);
    } finally {
      confirm.mockRestore();
      userAgent.mockRestore();
    }
  });

  describe('revision conflicts', () => {
    const flowDetail = (revision: string, name: string): LocalAvatarToolDetail => ({
      recordVersion: 3,
      id: LOCAL_ID,
      revision,
      name,
      images: [{ id: 'img-idle', name: '', resource: 'image-000.png', url: '/user_avatar_tools/local/image-000.png?v=1', meaning: '' }],
      initialImageId: 'img-idle',
      imageInteractions: {
        initialImagePosition: { x: 0, y: 0 },
        initialLinks: [{ to: 'ix-click', sourceSide: 'right', targetSide: 'left' }],
        items: [{
          id: 'ix-click', name: '', trigger: { kind: 'mouse-click' },
          actions: { press: { kind: 'keep' }, release: { kind: 'keep' } },
          editorPosition: { x: 200, y: 0 },
        }],
        links: [{ from: 'ix-click', to: 'ix-click', sourceSide: 'right', targetSide: 'right' }],
      },
    });
    const LATEST = flowDetail('3-200', 'Flow latest');
    const UNSAVED = 'You have unsaved settings, are you sure you want to leave?';
    const LOADED_NOTICE = 'This tool changed in another window. The latest version has been loaded.';

    const renderEditTarget = async () => {
      window.history.replaceState({}, '', `/avatar_tool_editor?mode=edit&toolId=${LOCAL_ID}`);
      catalog.detail.mockResolvedValue(flowDetail('3-100', 'Flow'));
      render(<AvatarToolStandaloneEditor />);
      expect(await screen.findByDisplayValue('Flow')).toBeInTheDocument();
    };

    it('asks before a save conflict replaces an edited draft and keeps it when declined', async () => {
      const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
      const close = vi.spyOn(window, 'close').mockImplementation(() => undefined);
      catalog.update.mockRejectedValue(new LocalAvatarToolRevisionConflictError(LATEST));
      try {
        await renderEditTarget();
        fireEvent.change(screen.getByRole('textbox', { name: 'Tool name' }), {
          target: { value: 'Flow edited' },
        });
        fireEvent.submit(document.querySelector('.avatar-tool-create-page')!);

        expect(await screen.findByRole('alert')).toHaveTextContent('Could not save this tool');
        expect(confirm).toHaveBeenCalledWith(UNSAVED);
        expect(screen.getByRole('textbox', { name: 'Tool name' })).toHaveValue('Flow edited');
        expect(window.avatarToolEditorHasUnsavedChanges?.()).toBe(true);
        expect(screen.queryByText(LOADED_NOTICE)).toBeNull();

        confirm.mockReturnValue(true);
        fireEvent.submit(document.querySelector('.avatar-tool-create-page')!);
        expect(await screen.findByText(LOADED_NOTICE)).toBeInTheDocument();
        expect(catalog.update.mock.calls.map(([, input]) => input.baseRevision)).toEqual(['3-100', '3-100']);
        expect(confirm).toHaveBeenCalledTimes(2);
        expect(screen.getByRole('textbox', { name: 'Tool name' })).toHaveValue('Flow latest');
        expect(window.avatarToolEditorHasUnsavedChanges?.()).toBe(false);
        expect(close).not.toHaveBeenCalled();
      } finally {
        confirm.mockRestore();
        close.mockRestore();
      }
    });

    it('loads the latest version without asking when the draft was untouched', async () => {
      const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
      catalog.update.mockRejectedValue(new LocalAvatarToolRevisionConflictError(LATEST));
      try {
        await renderEditTarget();
        fireEvent.submit(document.querySelector('.avatar-tool-create-page')!);
        expect(await screen.findByText(LOADED_NOTICE)).toBeInTheDocument();
        expect(confirm).not.toHaveBeenCalled();
        expect(screen.getByRole('textbox', { name: 'Tool name' })).toHaveValue('Flow latest');
      } finally {
        confirm.mockRestore();
      }
    });

    it('deletes with the loaded revision and handles a delete conflict like a save conflict', async () => {
      let keepDraft = true;
      const confirm = vi.spyOn(window, 'confirm').mockImplementation(message => (
        message === UNSAVED ? !keepDraft : true
      ));
      const close = vi.spyOn(window, 'close').mockImplementation(() => undefined);
      catalog.remove.mockRejectedValue(new LocalAvatarToolRevisionConflictError(LATEST));
      try {
        await renderEditTarget();
        fireEvent.change(screen.getByRole('textbox', { name: 'Tool name' }), {
          target: { value: 'Flow edited' },
        });
        fireEvent.click(screen.getByRole('button', { name: 'Delete tool' }));

        expect(await screen.findByRole('alert')).toHaveTextContent('Could not delete this tool');
        expect(catalog.remove).toHaveBeenCalledWith(LOCAL_ID, '3-100');
        expect(screen.getByRole('textbox', { name: 'Tool name' })).toHaveValue('Flow edited');

        keepDraft = false;
        fireEvent.click(screen.getByRole('button', { name: 'Delete tool' }));
        expect(await screen.findByText(LOADED_NOTICE)).toBeInTheDocument();
        expect(catalog.remove).toHaveBeenLastCalledWith(LOCAL_ID, '3-100');
        expect(screen.getByRole('textbox', { name: 'Tool name' })).toHaveValue('Flow latest');
        expect(close).not.toHaveBeenCalled();
      } finally {
        confirm.mockRestore();
        close.mockRestore();
      }
    });
  });

  it('registers as the shared editor window and applies delegated target switches itself', () => {
    const registryKey = 'neko:named-window:neko_avatar_tool_editor_singleton';
    const channels: Array<{ onmessage: ((event: MessageEvent) => void) | null; closed: boolean }> = [];
    vi.stubGlobal('BroadcastChannel', class {
      onmessage: ((event: MessageEvent) => void) | null = null;
      closed = false;
      constructor() { channels.push(this); }
      postMessage() {}
      close() { this.closed = true; }
    });
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    const focus = vi.spyOn(window, 'focus').mockImplementation(() => undefined);
    const deliver = (payload: unknown, timestamp: number) => {
      const message = {
        type: 'neko:named-window-message',
        windowName: 'neko_avatar_tool_editor_singleton',
        payload,
        timestamp,
      };
      act(() => channels[0]?.onmessage?.({ data: message } as MessageEvent));
      act(() => {
        window.dispatchEvent(new StorageEvent('storage', {
          key: 'neko:named-window-focus:neko_avatar_tool_editor_singleton',
          newValue: JSON.stringify(message),
        }));
      });
    };
    try {
      const { unmount } = render(<AvatarToolStandaloneEditor />);
      expect(JSON.parse(window.localStorage.getItem(registryKey) || '{}').timestamp).toEqual(expect.any(Number));
      expect(channels).toHaveLength(1);

      deliver({ type: 'neko:navigate-on-reuse', url: `${window.location.origin}/avatar_tool_editor?mode=create&ui_lang=ja` }, 1);
      expect(focus).toHaveBeenCalledTimes(1);
      expect(confirm).not.toHaveBeenCalled();

      fireEvent.change(screen.getByRole('textbox', { name: 'Tool name' }), {
        target: { value: 'New tool' },
      });
      deliver({
        type: 'neko:navigate-on-reuse',
        url: `${window.location.origin}/avatar_tool_editor?mode=edit&toolId=${LOCAL_ID}`,
      }, 2);
      // One request arrives over both BroadcastChannel and storage; ask once.
      expect(focus).toHaveBeenCalledTimes(2);
      expect(confirm).toHaveBeenCalledTimes(1);
      expect(window.location.search).toBe('?mode=create');
      expect(window.avatarToolEditorHasUnsavedChanges?.()).toBe(true);
      expect(screen.getByRole('textbox', { name: 'Tool name' })).toHaveValue('New tool');

      unmount();
      expect(window.localStorage.getItem(registryKey)).toBeNull();
      expect(channels[0]?.closed).toBe(true);
    } finally {
      confirm.mockRestore();
      focus.mockRestore();
    }
  });

  it('loads an existing tool directly from the shared catalog API', async () => {
    window.history.replaceState({}, '', `/avatar_tool_editor?mode=edit&toolId=${LOCAL_ID}`);
    catalog.detail.mockResolvedValue({
      id: LOCAL_ID,
      recordVersion: 2, revision: '2-200',
      name: 'My Feather',
      changeMode: 'press-swap',
      defaultImage: { resource: 'default.png', url: '/user_avatar_tools/local/default.png?v=1' },
      changeItems: [{
        resource: 'change-000.png',
        url: '/user_avatar_tools/local/change-000.png?v=1',
        meaning: 'A gentle touch',
      }],
    });

    render(<AvatarToolStandaloneEditor />);

    await waitFor(() => expect(catalog.detail).toHaveBeenCalledWith(LOCAL_ID));
    expect(await screen.findByDisplayValue('My Feather')).toBeInTheDocument();
    expect(screen.getByRole('dialog', { name: 'Edit custom tool' })).toBeInTheDocument();
    expect(screen.getByRole('complementary', { name: 'Tool editor' })).toBeInTheDocument();
  });

  it('refreshes the complete editor and window title when the app locale becomes ready', async () => {
    let localeReady = false;
    const zhTranslations: Record<string, string> = {
      'chat.avatarToolCreateTitle': '创建自定义道具',
      'chat.avatarToolWorkspaceCanvasTitle': '互动流程',
      'chat.avatarToolWorkspaceEditorTitle': '道具编辑',
      'chat.avatarToolWorkspaceSettingsTitle': '道具设置',
      'chat.avatarToolCreatePrivacy': '图片和音效仅存本机；互动时，当前图片或彩蛋对应的提示词会发送给模型，道具名称会记入角色的记忆，之后的对话中可能会用到。',
      'chat.avatarToolCreateName': '道具名称',
      'chat.avatarToolWorkspaceControls': '画布控件',
      'chat.avatarToolWorkspaceZoomIn': '放大',
      'chat.avatarToolWorkspaceZoomOut': '缩小',
      'chat.avatarToolWorkspaceFitView': '适配视图',
      'chat.avatarToolInitialImageNode': '初始图片',
      'chat.avatarToolInitialImageMissing': '尚未选择初始图片',
      'chat.avatarToolInitialImageNodeHint': '互动流程从这张图片开始',
    };
    vi.stubGlobal('safeT', (key: string, fallback: unknown) => {
      const defaultValue = typeof fallback === 'string'
        ? fallback
        : (fallback as { defaultValue?: string }).defaultValue ?? key;
      return localeReady ? zhTranslations[key] ?? defaultValue : defaultValue;
    });

    render(<AvatarToolStandaloneEditor />);
    expect(screen.getByRole('dialog', { name: 'Create custom tool' })).toBeInTheDocument();
    const initialImageNode = document.querySelector<HTMLElement>('.avatar-tool-initial-image-node');
    expect(initialImageNode).not.toBeNull();
    expect(within(initialImageNode!).getByText('Initial image')).toBeInTheDocument();

    localeReady = true;
    act(() => window.dispatchEvent(new Event('localechange')));

    expect(await screen.findByRole('dialog', { name: '创建自定义道具' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '互动流程' })).toBeInTheDocument();
    expect(screen.getByRole('complementary', { name: '道具编辑' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '道具设置' })).toBeInTheDocument();
    expect(screen.getByText('图片和音效仅存本机；互动时，当前图片或彩蛋对应的提示词会发送给模型，道具名称会记入角色的记忆，之后的对话中可能会用到。')
      .closest('.avatar-tool-workspace-settings-heading')).not.toBeNull();
    expect(screen.getByRole('textbox', { name: '道具名称' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '放大' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '缩小' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '适配视图' })).toBeInTheDocument();
    expect(within(initialImageNode!).getByText('初始图片')).toBeInTheDocument();
    expect(within(initialImageNode!).getByText('尚未选择初始图片')).toBeInTheDocument();
    expect(within(initialImageNode!).getByText('互动流程从这张图片开始')).toBeInTheDocument();
    await waitFor(() => expect(document.title).toBe('创建自定义道具 - N.E.K.O.'));
  });
});
