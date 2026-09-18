import { useCallback, useState } from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import AvatarToolItemManager, { openAvatarToolEditorWindow } from './AvatarToolItemManager';
import { AVAILABLE_COMPACT_AVATAR_TOOLS, type AvatarToolId, type AvatarToolItem } from './avatarTools';
import { type LocalAvatarToolDetail } from './avatar-tools/localTools';
import { useAvatarToolSlotReconciliation } from './avatar-tools/useAvatarToolSlotReconciliation';
import chatStyles from './styles.css?raw';

const LOCAL_ID = 'local-12345678-1234-4123-8123-123456789abc' as const;
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
const DETAIL: LocalAvatarToolDetail = {
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
};

function pngBytes(width = 16, height = 16): Uint8Array {
  const bytes = new Uint8Array(24);
  bytes.set([137, 80, 78, 71, 13, 10, 26, 10]);
  bytes.set([0, 0, 0, 13, 73, 72, 68, 82], 8);
  const view = new DataView(bytes.buffer);
  view.setUint32(16, width, false);
  view.setUint32(20, height, false);
  return bytes;
}

function pngFile(name: string, width = 16, height = 16): File {
  return new File([pngBytes(width, height).buffer as ArrayBuffer], name, { type: 'image/png' });
}

describe('AvatarToolItemManager local creation', () => {
  afterEach(() => {
    delete window.nekoHost;
    delete window.openOrFocusWindow;
    document.body.classList.remove('electron-chat-window');
    document.body.classList.remove('neko-electron-runtime');
  });

  it('retains a persisted local slot while its catalog entry is still loading', () => {
    const activeToolIds = [LOCAL_ID];
    const props = {
      open: true,
      activeToolIds,
      onSave: vi.fn(),
      onCancel: vi.fn(),
    };
    const { rerender } = render(
      <AvatarToolItemManager
        {...props}
        availableTools={AVAILABLE_COMPACT_AVATAR_TOOLS}
        catalogAuthoritativeLoaded={false}
      />,
    );

    expect(screen.getByRole('button', { name: 'Save changes' })).toBeDisabled();
    expect(screen.getByRole('status')).toHaveTextContent('Loading custom tools');

    rerender(
      <AvatarToolItemManager
        {...props}
        availableTools={[...AVAILABLE_COMPACT_AVATAR_TOOLS, {
          id: LOCAL_ID,
          label: { kind: 'literal', value: 'My Feather' },
          iconImagePath: '/user_avatar_tools/local/default.png?v=1',
          pointerImagePath: '/user_avatar_tools/local/default.png?v=1',
        }]}
        catalogAuthoritativeLoaded
      />,
    );

    expect(document.querySelector(`[data-avatar-tool-library-id="${LOCAL_ID}"]`)).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'Save changes' })).toBeEnabled();
  });

  it('keeps a saved v3 tool visible in its slot until the user removes it', () => {
    const onSave = vi.fn();
    const v3Tool: AvatarToolItem = {
      id: LOCAL_ID,
      label: { kind: 'literal', value: 'Saved flow' },
      iconImagePath: '/user_avatar_tools/local/image-000.png?v=1',
      pointerImagePath: '/user_avatar_tools/local/image-000.png?v=1',
    };
    render(
      <AvatarToolItemManager
        open
        activeToolIds={[LOCAL_ID]}
        availableTools={[...AVAILABLE_COMPACT_AVATAR_TOOLS, v3Tool]}
        runnableToolIds={new Set(AVAILABLE_COMPACT_AVATAR_TOOLS.map(tool => tool.id))}
        onSave={onSave}
        onCancel={() => undefined}
      />,
    );

    const retainedSlot = document.querySelector(`[data-avatar-tool-drop-slot="0"][data-avatar-tool-id="${LOCAL_ID}"]`);
    expect(retainedSlot).toHaveTextContent('Saved flow');
    expect(retainedSlot).toHaveTextContent('Not yet equippable');
    expect(document.querySelector(`[data-avatar-tool-library-id="${LOCAL_ID}"]`)).toHaveAttribute('aria-pressed', 'true');

    fireEvent.click(document.querySelector('[data-avatar-tool-library-id="lollipop"]')!);
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    expect(onSave).toHaveBeenCalledWith([LOCAL_ID, 'lollipop']);
  });

  it('receives a standalone editor result while the manager is hidden and restores the manager', async () => {
    function Harness() {
      const [open, setOpen] = useState(false);
      return (
        <AvatarToolItemManager
          open={open}
          activeToolIds={[]}
          availableTools={AVAILABLE_COMPACT_AVATAR_TOOLS}
          onSave={() => undefined}
          onCancel={() => setOpen(false)}
          onExternalEditorResult={() => setOpen(true)}
        />
      );
    }

    render(<Harness />);
    expect(screen.queryByRole('dialog', { name: 'Manage tools' })).toBeNull();

    fireEvent(window, new MessageEvent('message', {
      origin: window.location.origin,
      data: {
        type: 'neko:avatar-tool-editor-result',
        action: 'created',
        toolId: LOCAL_ID,
      },
    }));

    expect(await screen.findByRole('dialog', { name: 'Manage tools' })).toBeInTheDocument();
  });

  it('does not open creation until authoritative server limits are available', () => {
    render(
      <AvatarToolItemManager
        open
        activeToolIds={[]}
        availableTools={AVAILABLE_COMPACT_AVATAR_TOOLS}
        onSave={() => undefined}
        onCancel={() => undefined}
        onCreate={async () => undefined}
        catalogAuthoritativeLoaded={false}
      />,
    );

    expect(screen.getByRole('button', { name: 'Create tool' })).toBeDisabled();
  });

  it('reorders slots by drag without crashing', () => {
    // moveSlotTool 只在拖拽路径上跑，此前没有任何用例覆盖，所以它引用一个不存在
    // 的标识符也一路绿着 —— 类型检查当时是空转的，vitest 又不做类型检查。
    const onSave = vi.fn();
    render(
      <AvatarToolItemManager
        open
        activeToolIds={['lollipop', 'fist', 'hammer'] as AvatarToolId[]}
        availableTools={AVAILABLE_COMPACT_AVATAR_TOOLS}
        onSave={onSave}
        onCancel={() => undefined}
      />,
    );

    const slots = document.querySelectorAll<HTMLElement>('[data-avatar-tool-drop-slot]');
    const source = slots[0].querySelector('.avatar-tool-manager-slot-card') as HTMLElement;
    fireEvent.pointerDown(source, { pointerType: 'mouse', button: 0, clientX: 0, clientY: 0 });
    fireEvent.pointerMove(source, { clientX: 40, clientY: 0 });
    fireEvent.pointerUp(slots[2], { clientX: 40, clientY: 0 });

    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    expect(onSave).toHaveBeenCalledTimes(1);
    expect(onSave.mock.calls[0][0]).toHaveLength(3);
  });

  it.each(['click', 'drag'])('retains an unavailable occupied slot when equipping by %s', (method) => {
    const onSave = vi.fn();
    const localTool: AvatarToolItem = {
      id: LOCAL_ID,
      label: { kind: 'literal', value: 'My Feather' },
      iconImagePath: '/user_avatar_tools/local/default.png?v=1',
      pointerImagePath: '/user_avatar_tools/local/default.png?v=1',
    };
    const props = {
      open: true,
      activeToolIds: [LOCAL_ID, 'lollipop', 'fist'] as AvatarToolId[],
      onSave,
      onCancel: vi.fn(),
    };
    const { rerender } = render(
      <AvatarToolItemManager
        {...props}
        availableTools={[...AVAILABLE_COMPACT_AVATAR_TOOLS, localTool]}
      />,
    );

    rerender(
      <AvatarToolItemManager
        {...props}
        availableTools={AVAILABLE_COMPACT_AVATAR_TOOLS}
      />,
    );
    const hammer = document.querySelector('[data-avatar-tool-library-id="hammer"]')!;
    if (method === 'click') {
      fireEvent.click(hammer);
    } else {
      const elementsFromPoint = document.elementsFromPoint;
      document.elementsFromPoint = () => [
        document.querySelector('[data-avatar-tool-drop-slot="0"]')!,
      ];
      fireEvent.pointerDown(hammer, { pointerType: 'mouse', button: 0, clientX: 0, clientY: 0 });
      fireEvent.pointerMove(hammer, { clientX: 40, clientY: 0 });
      fireEvent.pointerUp(hammer, { clientX: 40, clientY: 0 });
      document.elementsFromPoint = elementsFromPoint;
    }
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));

    expect(onSave).toHaveBeenCalledWith([LOCAL_ID, 'lollipop', 'fist']);
    const retainedSlot = document.querySelector(`[data-avatar-tool-drop-slot="0"][data-avatar-tool-id="${LOCAL_ID}"]`)!;
    expect(retainedSlot).toHaveTextContent('Temporarily unavailable');
    expect(retainedSlot).not.toHaveTextContent('Empty slot');
    expect(retainedSlot.querySelector('.avatar-tool-manager-slot-card')).toBeDisabled();
    fireEvent.click(retainedSlot.querySelector('.avatar-tool-manager-remove')!);
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    expect(onSave).toHaveBeenLastCalledWith(['lollipop', 'fist']);
  });

  it('reconciles exact parent deletion while retaining other unsaved changes in the open manager', async () => {
    const onSave = vi.fn();
    let errorCode = 'record_invalid';
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: false, error_code: errorCode }), {
      status: 404, headers: { 'Content-Type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);
    function Harness({ items }: { items: ReadonlyArray<AvatarToolItem> }) {
      const [activeToolIds, setActiveToolIds] = useState<AvatarToolId[]>([LOCAL_ID, 'fist']);
      const onConfirmedDeleted = useCallback((ids: ReadonlyArray<`local-${string}`>) => {
        setActiveToolIds(current => current.filter(id => !ids.some(deleted => deleted === id)));
      }, []);
      useAvatarToolSlotReconciliation({
        activeToolIds, authoritativeItems: items, authoritativeLoaded: true, onConfirmedDeleted,
      });
      return <>
        <output data-testid="saved-slots">{activeToolIds.join(',')}</output>
        <AvatarToolItemManager open activeToolIds={activeToolIds} availableTools={items}
          onSave={onSave} onCancel={() => undefined} />
      </>;
    }
    try {
      const { rerender } = render(<Harness items={AVAILABLE_COMPACT_AVATAR_TOOLS} />);
      await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
      fireEvent.click(document.querySelector('[data-avatar-tool-id="fist"] .avatar-tool-manager-remove')!);
      fireEvent.click(document.querySelector('[data-avatar-tool-library-id="hammer"]')!);
      rerender(<Harness items={[...AVAILABLE_COMPACT_AVATAR_TOOLS]} />);
      await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
      fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
      expect(onSave).toHaveBeenLastCalledWith([LOCAL_ID, 'hammer']);

      errorCode = 'tool_not_found';
      rerender(<Harness items={[...AVAILABLE_COMPACT_AVATAR_TOOLS]} />);
      await waitFor(() => expect(screen.getByTestId('saved-slots')).toHaveTextContent(/^fist$/));
      fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
      expect(onSave).toHaveBeenLastCalledWith(['hammer']);
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('keeps focus, scrolling, and close visibility inside the create surface', () => {
    expect(chatStyles).toMatch(/\.avatar-tool-create-page\s*\{[\s\S]*?padding:\s*3px/);
    expect(chatStyles).toMatch(/\.avatar-tool-manager-create-body\s*\{[\s\S]*?overflow-y:\s*hidden/);
    expect(chatStyles).toMatch(/\.avatar-tool-create-field textarea\s*\{[\s\S]*?resize:\s*none[\s\S]*?overflow-y:\s*auto/);
    expect(chatStyles).toMatch(/\.avatar-tool-image-grid\s*\{[\s\S]*?grid-template-columns:\s*repeat\(2, minmax\(0, 1fr\)\)/);
    expect(chatStyles).toMatch(/\.avatar-tool-image-card,\s*\.avatar-tool-image-add-card\s*\{[\s\S]*?min-height:\s*142px/);
    expect(chatStyles).toMatch(/\.avatar-tool-image-card-preview\s*\{[\s\S]*?position:\s*relative/);
    expect(chatStyles).toMatch(/\.avatar-tool-image-card-preview img\s*\{[\s\S]*?position:\s*absolute[\s\S]*?inset:\s*0[\s\S]*?width:\s*100%[\s\S]*?height:\s*100%[\s\S]*?object-fit:\s*contain/);
    expect(chatStyles).toMatch(/\.avatar-tool-image-card-initial-badge\s*\{[\s\S]*?top:\s*5px[\s\S]*?left:\s*5px[\s\S]*?width:\s*18px[\s\S]*?height:\s*18px[\s\S]*?border:\s*2px solid/);
    expect(chatStyles).toMatch(/\.avatar-tool-image-card-initial-badge::before\s*\{[\s\S]*?width:\s*8px[\s\S]*?height:\s*8px[\s\S]*?border-radius:\s*50%/);
    expect(chatStyles).toMatch(/\.avatar-tool-image-card-copy strong\s*\{[\s\S]*?font-size:\s*13px/);
    expect(chatStyles).toMatch(/\.avatar-tool-image-card-copy > span\s*\{[\s\S]*?font-size:\s*12px/);
    expect(chatStyles).toMatch(/\.avatar-tool-image-detail-replace\.avatar-tool-create-file-control\s*\{[\s\S]*?min-height:\s*36px[\s\S]*?font-size:\s*13px/);
    expect(chatStyles).toMatch(/\.avatar-tool-image-detail-remove\s*\{[\s\S]*?min-height:\s*24px[\s\S]*?font-size:\s*12px/);
    expect(chatStyles).toMatch(/\.avatar-tool-image-detail-initial-control\s*\{[\s\S]*?font-size:\s*12px/);
    expect(chatStyles).toMatch(/\.avatar-tool-image-detail-heading-actions\s*\{[\s\S]*?display:\s*inline-flex[\s\S]*?gap:\s*3px/);
    expect(chatStyles).toMatch(/\.avatar-tool-image-detail-identity strong\s*\{[\s\S]*?font-size:\s*14px/);
    expect(chatStyles).toMatch(/\.avatar-tool-image-meaning-field\.avatar-tool-create-field\s*\{[\s\S]*?font-size:\s*13px/);
    expect(chatStyles).toMatch(/\.avatar-tool-image-meaning-heading small\s*\{[\s\S]*?font-size:\s*11px/);
    expect(chatStyles).toMatch(/\.avatar-tool-create-actions\s*\{[\s\S]*?flex:\s*0 0 auto[\s\S]*?margin-top:\s*auto/);
    expect(chatStyles).toMatch(/\.avatar-tool-manager-header p\s*\{[\s\S]*?font-size:\s*13px/);
    expect(chatStyles).toMatch(/\.avatar-tool-create-field\s*\{[\s\S]*?font-size:\s*13px/);
    expect(chatStyles).toMatch(/\.avatar-tool-create-field small\s*\{[\s\S]*?font-size:\s*11px/);
    expect(chatStyles).toMatch(/\.avatar-tool-create-file-control\s*\{[\s\S]*?grid-template-columns:\s*auto minmax\(0, 1fr\)/);
    expect(chatStyles).toMatch(/\.avatar-tool-editor-workspace\s*\{[\s\S]*?inset:\s*12px[\s\S]*?display:\s*flex/);
    expect(chatStyles.match(/\.avatar-tool-editor-workspace\s*\{[^}]*\}/)?.[0]).not.toMatch(/app-region/);
    expect(chatStyles).toMatch(/\.avatar-tool-workspace-main\s*\{[\s\S]*?grid-template-columns:\s*minmax\(430px, 1fr\) minmax\(390px, 430px\)/);
    expect(chatStyles).toMatch(/\.avatar-tool-workspace-stage-heading,\s*\.avatar-tool-workspace-settings-heading\s*\{[\s\S]*?display:\s*grid[\s\S]*?gap:\s*3px/);
    expect(chatStyles).not.toMatch(/\.avatar-tool-workspace-heading > span,\s*\.avatar-tool-workspace-stage-heading p\s*\{[\s\S]*?display:\s*none/);
    expect(chatStyles).toMatch(/\.avatar-tool-workspace-canvas\s*\{[\s\S]*?min-width:\s*0[\s\S]*?min-height:\s*0/);
    expect(chatStyles).toMatch(/\.avatar-tool-workspace-settings-body\s*\{[\s\S]*?overflow:\s*hidden/);
    expect(chatStyles).toMatch(/\.avatar-tool-manager-icon-button::before\s*\{[\s\S]*?mask:\s*url\('\/static\/icons\/close_button\.png'\)/);
    expect(chatStyles).toMatch(/\.avatar-tool-create-fields\s*\{[\s\S]*?overflow-y:\s*auto[\s\S]*?scrollbar-gutter:\s*stable/);
    expect(chatStyles).toMatch(/\.avatar-tool-create-page:not\(\.has-special\) \.avatar-tool-create-fields\s*\{[\s\S]*?padding-bottom:\s*11px/);
    expect(chatStyles).toMatch(/\.avatar-tool-create-special\s*\{[\s\S]*?min-height:\s*20px/);
    expect(chatStyles).toMatch(/\.avatar-tool-create-special\.is-enabled\s*\{[\s\S]*?flex:\s*0 0 auto[\s\S]*?overflow:\s*hidden/);
    expect(chatStyles).toMatch(/\.avatar-tool-image-meaning-field textarea\s*\{[\s\S]*?min-height:\s*56px[\s\S]*?max-height:\s*96px[\s\S]*?overflow-y:\s*hidden/);
    expect(chatStyles).toMatch(/\.avatar-tool-create-special-probability\s*\{[\s\S]*?grid-template-columns:\s*auto minmax\(0, 1fr\) 34px/);
    expect(chatStyles).toMatch(/\.avatar-tool-create-special-switch\s*\{[\s\S]*?width:\s*42px[\s\S]*?height:\s*22px/);
    expect(chatStyles).toMatch(/\.avatar-tool-create-special-switch\s*\{[\s\S]*?margin-left:\s*11px/);
    expect(chatStyles).not.toMatch(/\.avatar-tool-create-special-toggle > span:first-child\s*\{[\s\S]*?margin-right:\s*auto/);
    expect(chatStyles).toMatch(/\.avatar-tool-create-special-switch::after\s*\{[\s\S]*?emotion_model_icon\.png/);
    expect(chatStyles).toMatch(/\.avatar-tool-create-special-probability input\[type='range'\]::\-webkit-slider-thumb\s*\{[\s\S]*?emotion_model_icon\.png/);
  });

  it('shows a refresh failure without removing the previous library', () => {
    render(
      <AvatarToolItemManager
        open
        activeToolIds={[]}
        availableTools={AVAILABLE_COMPACT_AVATAR_TOOLS}
        onSave={() => undefined}
        onCancel={() => undefined}
        catalogRefreshFailed
      />,
    );

    expect(screen.getByRole('alert')).toHaveTextContent('Could not refresh local tools');
    expect(screen.getByRole('button', { name: /棒棒糖/ })).toBeInTheDocument();
  });

  it('opens one edit entry for local tools and deletes only from the edit page', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    const onDelete = vi.fn().mockResolvedValue(undefined);
    const onSave = vi.fn();
    const localTool: AvatarToolItem = {
      id: LOCAL_ID,
      label: { kind: 'literal', value: 'My Feather' },
      iconImagePath: '/user_avatar_tools/local/default.png?v=1',
      pointerImagePath: '/user_avatar_tools/local/default.png?v=1',
    };

    render(
      <AvatarToolItemManager
        open
        activeToolIds={[LOCAL_ID]}
        availableTools={[...AVAILABLE_COMPACT_AVATAR_TOOLS, localTool]}
        onSave={onSave}
        onCancel={() => undefined}
        createLimits={LIMITS}
        onLoadDetail={async () => DETAIL}
        onUpdate={vi.fn()}
        onDelete={onDelete}
      />,
    );

    expect(screen.queryByRole('button', { name: /Edit 棒棒糖/ })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Delete My Feather' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Edit My Feather' }));
    await screen.findByRole('dialog', { name: 'Edit custom tool' });
    fireEvent.click(screen.getByRole('button', { name: 'Delete tool' }));
    await waitFor(() => expect(onDelete).toHaveBeenCalledWith(LOCAL_ID));
    expect(confirm).toHaveBeenCalledWith('Delete “My Feather”? This cannot be undone.');
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    expect(onSave).toHaveBeenCalledWith([]);
    confirm.mockRestore();
  });

  it('keeps the local card and draft when deletion fails', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    const onDelete = vi.fn().mockRejectedValue(new Error('failed'));
    const onSave = vi.fn();
    render(
      <AvatarToolItemManager
        open
        activeToolIds={[LOCAL_ID]}
        availableTools={[...AVAILABLE_COMPACT_AVATAR_TOOLS, {
          id: LOCAL_ID,
          label: { kind: 'literal', value: 'My Feather' },
          iconImagePath: '/user_avatar_tools/local/default.png?v=1',
          pointerImagePath: '/user_avatar_tools/local/default.png?v=1',
        }]}
        onSave={onSave}
        onCancel={() => undefined}
        createLimits={LIMITS}
        onLoadDetail={async () => DETAIL}
        onUpdate={vi.fn()}
        onDelete={onDelete}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Edit My Feather' }));
    await screen.findByRole('dialog', { name: 'Edit custom tool' });
    fireEvent.click(screen.getByRole('button', { name: 'Delete tool' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not delete this tool');
    expect(screen.getByRole('dialog', { name: 'Edit custom tool' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Back' }));
    expect(document.querySelector(`[data-avatar-tool-library-id="${LOCAL_ID}"]`)).not.toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    expect(onSave).toHaveBeenCalledWith([LOCAL_ID]);
    confirm.mockRestore();
  });

  it('keeps other unsaved slot changes when deleting a saved local tool', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    const onSave = vi.fn();
    const localTool: AvatarToolItem = {
      id: LOCAL_ID,
      label: { kind: 'literal', value: 'My Feather' },
      iconImagePath: '/user_avatar_tools/local/default.png?v=1',
      pointerImagePath: '/user_avatar_tools/local/default.png?v=1',
    };

    function Harness() {
      const [activeToolIds, setActiveToolIds] = useState<AvatarToolId[]>([LOCAL_ID, 'lollipop']);
      const [availableTools, setAvailableTools] = useState<ReadonlyArray<AvatarToolItem>>([
        ...AVAILABLE_COMPACT_AVATAR_TOOLS,
        localTool,
      ]);
      return (
        <AvatarToolItemManager
          open
          activeToolIds={activeToolIds}
          availableTools={availableTools}
          onSave={onSave}
          onCancel={() => undefined}
          createLimits={LIMITS}
          onLoadDetail={async () => DETAIL}
          onUpdate={vi.fn()}
          onDelete={async () => {
            setActiveToolIds(['lollipop']);
            setAvailableTools(AVAILABLE_COMPACT_AVATAR_TOOLS);
          }}
        />
      );
    }

    render(<Harness />);
    fireEvent.click(screen.getByRole('button', { name: 'Remove 棒棒糖' }));
    fireEvent.click(document.querySelector('[data-avatar-tool-library-id="fist"]')!);
    fireEvent.click(screen.getByRole('button', { name: 'Edit My Feather' }));
    await screen.findByRole('dialog', { name: 'Edit custom tool' });
    fireEvent.click(screen.getByRole('button', { name: 'Delete tool' }));
    await screen.findByRole('dialog', { name: 'Manage tools' });

    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    expect(onSave).toHaveBeenCalledWith(['fist']);
    confirm.mockRestore();
  });

  it('projects an existing v2 tool into equal image cards without losing retained resources', async () => {
    const detailed: LocalAvatarToolDetail = {
      ...DETAIL,
      normalSound: { resource: 'normal.mp3', url: '/user_avatar_tools/local/normal.mp3?v=1' },
      special: {
        probability: 0.2,
        image: { resource: 'special.png', url: '/user_avatar_tools/local/special.png?v=1' },
        meaning: 'A surprise appears',
        sound: { resource: 'special.mp3', url: '/user_avatar_tools/local/special.mp3?v=1' },
      },
    };
    render(
      <AvatarToolItemManager
        open
        activeToolIds={[LOCAL_ID]}
        availableTools={[...AVAILABLE_COMPACT_AVATAR_TOOLS, {
          id: LOCAL_ID,
          label: { kind: 'literal', value: 'My Feather' },
          iconImagePath: DETAIL.defaultImage.url,
          pointerImagePath: DETAIL.defaultImage.url,
        }]}
        onSave={vi.fn()}
        onCancel={() => undefined}
        createLimits={LIMITS}
        onLoadDetail={async () => detailed}
        onUpdate={vi.fn()}
        onDelete={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Edit My Feather' }));
    await screen.findByRole('dialog', { name: 'Edit custom tool' });
    expect(screen.getByLabelText('Tool name')).toHaveValue('My Feather');
    const cards = document.querySelectorAll<HTMLElement>('[data-avatar-tool-image-id]');
    expect(cards).toHaveLength(2);
    expect(cards[0]).toHaveAttribute('data-avatar-tool-image-id', 'img-v2-default');
    expect(cards[0]).toHaveAttribute('data-avatar-tool-image-initial', 'true');
    expect(cards[1]).toHaveAttribute('data-avatar-tool-image-id', 'img-v2-change-000');
    expect(cards[1]).toHaveAttribute('data-avatar-tool-image-initial', 'false');
    const initialBadge = cards[0].querySelector('.avatar-tool-image-card-initial-badge');
    expect(initialBadge).toHaveAttribute('aria-hidden', 'true');
    expect(initialBadge).toHaveAttribute('title', 'Initial image');
    expect(initialBadge).toBeEmptyDOMElement();
    expect(screen.queryByRole('button', { name: 'Initial image' })).toBeNull();
    expect(screen.queryByText('Default image')).toBeNull();
    expect(screen.queryByText('Image switching')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Edit Tool image 2' }));
    expect(screen.getByLabelText('Interaction description for tool image 2 (optional)')).toHaveValue('A gentle touch');
    expect(screen.getAllByText('Current image')).toHaveLength(1);
    expect(screen.getAllByText('Current sound')).toHaveLength(2);
  });

  it('keeps the library visible when edit details cannot be loaded', async () => {
    render(
      <AvatarToolItemManager
        open
        activeToolIds={[]}
        availableTools={[...AVAILABLE_COMPACT_AVATAR_TOOLS, {
          id: LOCAL_ID,
          label: { kind: 'literal', value: 'My Feather' },
          iconImagePath: DETAIL.defaultImage.url,
          pointerImagePath: DETAIL.defaultImage.url,
        }]}
        onSave={vi.fn()}
        onCancel={() => undefined}
        onLoadDetail={async () => { throw new Error('missing'); }}
        onUpdate={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Edit My Feather' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not open this tool');
    expect(screen.getByRole('dialog', { name: 'Manage tools' })).toBeInTheDocument();
  });

  it('opens the shared editor workspace, keeps draft slots, and returns focus to the entry', async () => {
    const onSave = vi.fn();
    const onCreate = vi.fn();

    function Harness() {
      const [tools] = useState<ReadonlyArray<AvatarToolItem>>(AVAILABLE_COMPACT_AVATAR_TOOLS);
      const [activeToolIds] = useState<AvatarToolId[]>(['lollipop']);
      return (
        <AvatarToolItemManager
          open
          activeToolIds={activeToolIds}
          availableTools={tools}
          onSave={onSave}
          onCancel={() => undefined}
          createLimits={LIMITS}
          onCreate={async (input) => { onCreate(input); }}
        />
      );
    }

    render(<Harness />);
    fireEvent.click(document.querySelector('[data-avatar-tool-library-id="fist"]')!);
    const dialog = screen.getByRole('dialog', { name: 'Manage tools' });
    const createButton = screen.getByRole('button', { name: 'Create tool' });
    fireEvent.click(createButton);
    const workspace = screen.getByRole('dialog', { name: 'Create custom tool' });
    expect(workspace).not.toBe(dialog);
    expect(workspace).toHaveClass('avatar-tool-editor-workspace');
    expect(screen.getByRole('region', { name: 'Interaction flow' })).toBeInTheDocument();
    expect(screen.getByRole('complementary', { name: 'Tool editor' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Zoom in' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Fit view' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Back' })).toHaveFocus();
    expect(document.querySelector('.avatar-tool-create-page img')).toBeNull();
    expect(screen.getByLabelText('Tool name')).toHaveAttribute(
      'placeholder',
      '1–20 characters; use letters, numbers, spaces, “-”, or “_”',
    );

    fireEvent.submit(document.querySelector('.avatar-tool-create-page')!);
    expect(await screen.findByText('Please enter a tool name.')).toHaveAttribute('role', 'alert');
    expect(screen.getByText('Add at least one tool image.')).toHaveAttribute('role', 'alert');
    expect(screen.getByText('Choose one initial image.')).toHaveAttribute('role', 'alert');

    fireEvent.change(screen.getByLabelText('Tool name'), { target: { value: 'My Feather' } });
    fireEvent.change(screen.getByLabelText('Add tool image'), {
      target: { files: [pngFile('A.png')] },
    });
    await screen.findByRole('button', { name: 'Edit Tool image 1' });
    fireEvent.change(screen.getByLabelText('Interaction description for tool image 1 (optional)'), {
      target: { value: '这是 A' },
    });
    expect(screen.getByLabelText('Interaction description for tool image 1 (optional)')).toHaveValue('这是 A');

    fireEvent.click(screen.getByRole('button', { name: 'Back' }));
    expect(await screen.findByRole('dialog', { name: 'Manage tools' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Create tool' })).toHaveFocus();
    expect(document.querySelector('[data-avatar-tool-library-id="fist"]')).toHaveAttribute('aria-pressed', 'true');
    expect(onCreate).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    expect(onSave).toHaveBeenCalledWith(['lollipop', 'fist']);
  });

  it('opens the desktop editor as a separate management page without changing the compact host', () => {
    document.body.classList.add('neko-electron-runtime');
    Object.defineProperty(window, 'i18n', {
      configurable: true,
      value: { language: 'ja', resolvedLanguage: 'ja' },
    });
    const focus = vi.fn();
    const open = vi.spyOn(window, 'open').mockReturnValue({ focus } as unknown as Window);

    try {
      render(
        <AvatarToolItemManager
          open
          activeToolIds={[]}
          availableTools={AVAILABLE_COMPACT_AVATAR_TOOLS}
          anchorRect={{
            left: 800,
            top: 900,
            right: 840,
            bottom: 940,
            width: 40,
            height: 40,
          }}
          onSave={() => undefined}
          onCancel={() => undefined}
          createLimits={LIMITS}
          onCreate={async () => undefined}
        />,
      );

      fireEvent.click(screen.getByRole('button', { name: 'Create tool' }));
      expect(screen.getByRole('dialog', { name: 'Manage tools' })).toBeInTheDocument();
      expect(screen.queryByRole('dialog', { name: 'Create custom tool' })).toBeNull();
      expect(open).toHaveBeenCalledTimes(1);
      expect(open.mock.calls[0]?.[0]).toContain('/avatar_tool_editor?mode=create');
      expect(open.mock.calls[0]?.[0]).toContain('ui_lang=ja');
      expect(open.mock.calls[0]?.[1]).toBe('_blank');
      expect(open.mock.calls[0]?.[2]).toContain('resizable=yes');
      expect(open.mock.calls[0]?.[2]).toContain('width=1280');
      expect(open.mock.calls[0]?.[2]).toContain('height=900');
      expect(focus).toHaveBeenCalledTimes(1);
    } finally {
      document.body.classList.remove('neko-electron-runtime');
      Reflect.deleteProperty(window, 'i18n');
      open.mockRestore();
    }
  });

  it('focuses the same editor target and asks the existing editor before switching targets', () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    const existing = {
      location: { href: `${window.location.origin}/avatar_tool_editor?mode=create&ui_lang=ja` },
      avatarToolEditorHasUnsavedChanges: vi.fn().mockReturnValue(true),
      focus: vi.fn(),
    } as unknown as Window;
    window.openOrFocusWindow = vi.fn((url, _name, _features, options) => {
      const mayNavigate = options?.shouldNavigateOnReuse;
      expect(mayNavigate?.(existing, `${window.location.origin}/avatar_tool_editor?mode=create`)).toBe(false);
      expect(existing.avatarToolEditorHasUnsavedChanges).not.toHaveBeenCalled();
      expect(mayNavigate?.(existing, url)).toBe(false);
      expect(existing.avatarToolEditorHasUnsavedChanges).toHaveBeenCalledTimes(1);
      expect(confirm).toHaveBeenCalledTimes(1);
      confirm.mockReturnValue(true);
      expect(mayNavigate?.(existing, url)).toBe(true);
      return existing;
    });
    expect(openAvatarToolEditorWindow('edit', LOCAL_ID)).toBe(existing);
    expect(window.openOrFocusWindow).toHaveBeenCalledWith(
      expect.stringContaining(`mode=edit&toolId=${LOCAL_ID}`),
      'neko_avatar_tool_editor_singleton',
      expect.any(String),
      expect.objectContaining({ navigateOnReuse: true }),
    );
    expect(existing.focus).toHaveBeenCalled();
    confirm.mockRestore();
  });

  it('keeps the inline Web fallback when the shared chat template only has its static class', () => {
    document.body.classList.add('electron-chat-window');
    const open = vi.spyOn(window, 'open');

    try {
      render(
        <AvatarToolItemManager
          open
          activeToolIds={[]}
          availableTools={AVAILABLE_COMPACT_AVATAR_TOOLS}
          onSave={() => undefined}
          onCancel={() => undefined}
          createLimits={LIMITS}
          onCreate={async () => undefined}
        />,
      );

      fireEvent.click(screen.getByRole('button', { name: 'Create tool' }));
      expect(screen.getByRole('dialog', { name: 'Create custom tool' })).toBeInTheDocument();
      expect(open).not.toHaveBeenCalled();
    } finally {
      open.mockRestore();
    }
  });

  it('reuses desktop host pickers for same-level images and optional audio', async () => {
    const onCreate = vi.fn().mockResolvedValue(undefined);
    const pickImage = vi.fn()
      .mockResolvedValueOnce({
        cancelled: false,
        name: 'A.png',
        bytes: pngBytes().buffer,
      })
      .mockResolvedValueOnce({
        cancelled: false,
        name: 'B.png',
        bytes: pngBytes().buffer,
      });
    const pickAudio = vi.fn().mockResolvedValue({
      cancelled: false,
      name: 'interaction.mp3',
      bytes: new Uint8Array([73, 68, 51]).buffer,
    });
    window.nekoHost = { pickImage, pickAudio };

    render(
      <AvatarToolItemManager
        open
        activeToolIds={[]}
        availableTools={AVAILABLE_COMPACT_AVATAR_TOOLS}
        onSave={() => undefined}
        onCancel={() => undefined}
        createLimits={LIMITS}
        onCreate={onCreate}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Create tool' }));
    fireEvent.change(screen.getByLabelText('Tool name'), { target: { value: 'My Tool' } });
    fireEvent.click(screen.getByLabelText('Add tool image'));
    await waitFor(() => expect(pickImage).toHaveBeenCalledTimes(1));
    await screen.findByRole('button', { name: 'Edit Tool image 1' });
    fireEvent.change(screen.getByLabelText('Interaction description for tool image 1 (optional)'), {
      target: { value: '  A friendly\r\ninteraction  ' },
    });
    fireEvent.click(screen.getByLabelText('Add tool image'));
    await waitFor(() => expect(pickImage).toHaveBeenCalledTimes(2));
    await screen.findByRole('button', { name: 'Edit Tool image 2' });
    fireEvent.click(screen.getByLabelText('Interaction sound (optional)'));
    await waitFor(() => expect(pickAudio).toHaveBeenCalledTimes(1));
    expect(screen.getByText(/Played once when an interaction succeeds\./)).toBeInTheDocument();
    fireEvent.submit(document.querySelector('.avatar-tool-create-page')!);

    expect(await screen.findByText('Fix the interaction flow before saving.')).toHaveAttribute('role', 'alert');
    expect(screen.getByText('Connect the initial image to at least one interaction.')).toBeVisible();
    fireEvent.click(screen.getByRole('tab', { name: 'Tool settings' }));
    expect(document.querySelectorAll('[data-avatar-tool-image-id]')).toHaveLength(2);
    expect(onCreate).not.toHaveBeenCalled();
  });

  it('shows surprise fields only when enabled and keeps their draft values', async () => {
    const onCreate = vi.fn().mockResolvedValue(undefined);
    render(
      <AvatarToolItemManager
        open
        activeToolIds={[]}
        availableTools={AVAILABLE_COMPACT_AVATAR_TOOLS}
        onSave={() => undefined}
        onCancel={() => undefined}
        createLimits={LIMITS}
        userName="Ming"
        assistantName="Yui"
        onCreate={onCreate}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Create tool' }));
    const surpriseToggle = screen.getByRole('checkbox', { name: 'Surprise' });
    expect(screen.getByRole('dialog')).toHaveClass('avatar-tool-editor-workspace');
    expect(screen.queryByLabelText('Trigger chance')).toBeNull();
    fireEvent.click(surpriseToggle);
    expect(screen.getByRole('dialog')).toHaveClass('avatar-tool-editor-workspace');
    const probability = screen.getByRole('slider', { name: /Trigger chance/ });
    expect(probability).toHaveAttribute('min', '1');
    expect(probability).toHaveAttribute('max', '100');
    expect(document.querySelector('.avatar-tool-create-special input[type="number"]')).toBeNull();
    expect(document.querySelector('.avatar-tool-create-special-meaning span')).toHaveTextContent('Interaction description');
    expect(document.querySelector('.avatar-tool-create-special-meaning textarea')).toHaveAttribute(
      'placeholder',
      expect.stringContaining('reward drops'),
    );
    fireEvent.change(probability, { target: { value: '25' } });
    expect(screen.getByText('25%')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Surprise image'), {
      target: { files: [pngFile('special.png')] },
    });
    fireEvent.change(document.querySelector('.avatar-tool-create-special-meaning textarea')!, {
      target: { value: 'Special meaning' },
    });
    fireEvent.change(screen.getByLabelText('Surprise sound (optional)'), {
      target: { files: [new File(['sound'], 'special.mp3', { type: 'audio/mpeg' })] },
    });
    expect(await screen.findByText('special.png')).toBeInTheDocument();
    expect(screen.getByText('special.mp3')).toBeInTheDocument();
    expect(document.querySelector('.avatar-tool-create-special-meaning textarea')).toHaveValue('Special meaning');
    expect(onCreate).not.toHaveBeenCalled();
  });

  it('manages equal image cards with stable IDs, one initial image, and compact description summaries', async () => {
    render(
      <AvatarToolItemManager
        open
        activeToolIds={[]}
        availableTools={AVAILABLE_COMPACT_AVATAR_TOOLS}
        onSave={() => undefined}
        onCancel={() => undefined}
        createLimits={LIMITS}
        userName="Ming"
        assistantName="Yui"
        onCreate={async () => undefined}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Create tool' }));
    const add = async (file: File) => {
      const previousCount = document.querySelectorAll('[data-avatar-tool-image-id]').length;
      fireEvent.change(screen.getByLabelText('Add tool image'), { target: { files: [file] } });
      await waitFor(() => expect(document.querySelectorAll('[data-avatar-tool-image-id]')).toHaveLength(previousCount + 1));
    };

    await add(pngFile('A.png'));
    const firstId = document.querySelector('[data-avatar-tool-image-id]')?.getAttribute('data-avatar-tool-image-id');
    fireEvent.change(screen.getByLabelText('Interaction description for tool image 1 (optional)'), {
      target: { value: '这是 A' },
    });
    await add(pngFile('B.png'));
    await add(pngFile('C.png'));
    fireEvent.change(screen.getByLabelText('Interaction description for tool image 3 (optional)'), {
      target: { value: '这是 C' },
    });
    const cards = Array.from(document.querySelectorAll<HTMLElement>('[data-avatar-tool-image-id]'));
    expect(cards).toHaveLength(3);
    expect(new Set(cards.map(card => card.dataset.avatarToolImageId))).toHaveProperty('size', 3);
    expect(cards[0]).toHaveAttribute('data-avatar-tool-image-initial', 'true');
    expect(cards[0]).toHaveTextContent('这是 A');
    expect(cards[1]).toHaveTextContent('No interaction description');
    expect(cards[2]).toHaveTextContent('这是 C');
    expect(screen.getByLabelText('Interaction description for tool image 3 (optional)')).toHaveValue('这是 C');
    fireEvent.click(screen.getByRole('button', { name: 'Edit Tool image 1' }));
    expect(screen.getByLabelText('Interaction description for tool image 1 (optional)')).toHaveValue('这是 A');
    fireEvent.click(screen.getByRole('button', { name: 'Edit Tool image 2' }));
    expect(screen.getByLabelText('Interaction description for tool image 2 (optional)')).toHaveValue('');
    expect(screen.queryByText('Default image')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Switch while held' })).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Edit Tool image 2' }));
    fireEvent.click(screen.getByRole('radio', { name: 'Initial image' }));
    expect(cards[1]).toHaveAttribute('data-avatar-tool-image-initial', 'true');
    expect(screen.getByTitle('B.png')).toBeVisible();

    const secondId = cards[1].dataset.avatarToolImageId;
    fireEvent.change(screen.getByLabelText('Change image: Tool image 2'), {
      target: { files: [pngFile('B-replaced.png')] },
    });
    await waitFor(() => expect(cards[1]).toHaveAttribute('data-avatar-tool-image-id', secondId));
    expect(await screen.findByTitle('B-replaced.png')).toBeVisible();
    expect(cards[1]).toHaveAttribute('data-avatar-tool-image-id', secondId);
    expect(cards[0]).toHaveAttribute('data-avatar-tool-image-id', firstId);

    fireEvent.click(screen.getByRole('button', { name: 'Remove image' }));
    expect(screen.getByText('Choose another initial image before removing this one.')).toHaveAttribute('role', 'alert');
    fireEvent.click(screen.getByRole('button', { name: 'Edit Tool image 3' }));
    fireEvent.click(screen.getByRole('radio', { name: 'Initial image' }));
    fireEvent.click(screen.getByRole('button', { name: 'Edit Tool image 2' }));
    fireEvent.click(screen.getByRole('button', { name: 'Remove image' }));
    expect(document.querySelectorAll('[data-avatar-tool-image-id]')).toHaveLength(2);
  });

  it('rejects unsupported tool-name characters without clearing the form', () => {
    const onCreate = vi.fn().mockResolvedValue(undefined);
    render(
      <AvatarToolItemManager
        open
        activeToolIds={[]}
        availableTools={AVAILABLE_COMPACT_AVATAR_TOOLS}
        onSave={() => undefined}
        onCancel={() => undefined}
        createLimits={LIMITS}
        onCreate={onCreate}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Create tool' }));
    const nameInput = screen.getByLabelText('Tool name');
    expect(nameInput).not.toHaveAttribute('maxlength');
    fireEvent.change(nameInput, { target: { value: 'Feather!' } });
    fireEvent.submit(document.querySelector('.avatar-tool-create-page')!);

    expect(screen.getByText('Use letters, numbers, spaces, “-”, or “_” in the tool name.')).toHaveAttribute(
      'role',
      'alert',
    );
    expect(nameInput).toHaveValue('Feather!');
    expect(onCreate).not.toHaveBeenCalled();
  });

  it('rejects invalid and over-pixel PNG files before adding an image card', async () => {
    const onCreate = vi.fn();
    render(
      <AvatarToolItemManager
        open
        activeToolIds={[]}
        availableTools={AVAILABLE_COMPACT_AVATAR_TOOLS}
        onSave={() => undefined}
        onCancel={() => undefined}
        createLimits={LIMITS}
        onCreate={onCreate}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Create tool' }));
    fireEvent.change(screen.getByLabelText('Add tool image'), {
      target: { files: [pngFile('huge.png', 5000, 5000)] },
    });
    expect(await screen.findByText(/no more than 16000000 total pixels/)).toHaveAttribute('role', 'alert');
    expect(document.querySelectorAll('[data-avatar-tool-image-id]')).toHaveLength(0);

    fireEvent.change(screen.getByLabelText('Add tool image'), {
      target: { files: [new File(['not png'], 'broken.png', { type: 'image/png' })] },
    });
    expect(await screen.findByText('This image cannot be used. Please choose another PNG.')).toHaveAttribute('role', 'alert');
    expect(onCreate).not.toHaveBeenCalled();
  });

  it('allows a phase-5 v3 tool to be equipped and edited', async () => {
    const managementTool: AvatarToolItem = {
      id: LOCAL_ID,
      label: { kind: 'literal', value: 'Flow' },
      iconImagePath: '/user_avatar_tools/local/image-000.png?v=1',
      pointerImagePath: '/user_avatar_tools/local/image-000.png?v=1',
    };
    const detail: LocalAvatarToolDetail = {
      recordVersion: 3,
      id: LOCAL_ID,
      revision: '3-100',
      name: 'Flow',
      images: [{ id: 'img-idle', name: '', resource: 'image-000.png', url: managementTool.iconImagePath, meaning: '' }],
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
    };
    const onLoadDetail = vi.fn().mockResolvedValue(detail);
    const onSave = vi.fn();
    render(
      <AvatarToolItemManager
        open
        activeToolIds={[]}
        availableTools={[...AVAILABLE_COMPACT_AVATAR_TOOLS, managementTool]}
        runnableToolIds={new Set([...AVAILABLE_COMPACT_AVATAR_TOOLS.map(tool => tool.id), LOCAL_ID])}
        onSave={onSave}
        onCancel={() => undefined}
        createLimits={LIMITS}
        onLoadDetail={onLoadDetail}
        onUpdate={async () => undefined}
      />,
    );

    expect(screen.queryByText('Not yet equippable')).toBeNull();
    const libraryCard = document.querySelector(`[data-avatar-tool-library-id="${LOCAL_ID}"]`);
    expect(libraryCard).toBeEnabled();
    fireEvent.click(libraryCard!);
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    expect(onSave).toHaveBeenCalledWith([LOCAL_ID]);
    fireEvent.click(screen.getByRole('button', { name: 'Edit Flow' }));
    await waitFor(() => expect(onLoadDetail).toHaveBeenCalledWith(LOCAL_ID));
    expect(await screen.findByRole('dialog', { name: 'Edit custom tool' })).toBeVisible();
  });
});
