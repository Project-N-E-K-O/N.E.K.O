import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
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
      'Images and sounds stay on this device; during interactions, the current image or surprise prompt is sent to the model.',
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

  it('loads an existing tool directly from the shared catalog API', async () => {
    window.history.replaceState({}, '', `/avatar_tool_editor?mode=edit&toolId=${LOCAL_ID}`);
    catalog.detail.mockResolvedValue({
      id: LOCAL_ID,
      revision: '100-200',
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
      'chat.avatarToolCreatePrivacy': '图片和音效仅存本机；互动时，当前图片或彩蛋的提示词会发送给模型。',
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
    expect(screen.getByText('图片和音效仅存本机；互动时，当前图片或彩蛋的提示词会发送给模型。')
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
