import { useState } from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import AvatarToolItemManager from './AvatarToolItemManager';
import { AVAILABLE_COMPACT_AVATAR_TOOLS, type AvatarToolId, type AvatarToolItem } from './avatarTools';
import chatStyles from './styles.css?raw';

const LOCAL_ID = 'local-12345678-1234-4123-8123-123456789abc' as AvatarToolId;
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

describe('AvatarToolItemManager local creation', () => {
  afterEach(() => {
    delete window.nekoHost;
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
      <AvatarToolItemManager {...props} availableTools={AVAILABLE_COMPACT_AVATAR_TOOLS} />,
    );

    rerender(
      <AvatarToolItemManager
        {...props}
        availableTools={[...AVAILABLE_COMPACT_AVATAR_TOOLS, {
          id: LOCAL_ID,
          label: { kind: 'literal', value: 'My Feather' },
          iconImagePath: '/user_avatar_tools/local/default.png?v=1',
          pointerImagePath: '/user_avatar_tools/local/default.png?v=1',
        }]}
      />,
    );

    expect(document.querySelector(`[data-avatar-tool-library-id="${LOCAL_ID}"]`)).toHaveAttribute('aria-pressed', 'true');
  });

  it('keeps focus, scrolling, and close visibility inside the create surface', () => {
    expect(chatStyles).toMatch(/\.avatar-tool-create-page\s*\{[\s\S]*?padding:\s*3px/);
    expect(chatStyles).toMatch(/\.avatar-tool-manager-create-body\s*\{[\s\S]*?overflow-y:\s*hidden/);
    expect(chatStyles).toMatch(/\.avatar-tool-create-field textarea\s*\{[\s\S]*?resize:\s*none[\s\S]*?overflow-y:\s*auto/);
    expect(chatStyles).toMatch(/\.avatar-tool-create-change-list\s*\{[\s\S]*?flex:\s*1 1 164px[\s\S]*?min-height:\s*164px/);
    expect(chatStyles).toMatch(/\.avatar-tool-create-change-list:not\(\.has-multiple-items\)\s*\{[\s\S]*?grid-template-rows:\s*minmax\(0, 1fr\)/);
    expect(chatStyles).toMatch(/\.avatar-tool-create-actions\s*\{[\s\S]*?flex:\s*0 0 auto[\s\S]*?margin-top:\s*auto/);
    expect(chatStyles).toMatch(/\.avatar-tool-manager-header p\s*\{[\s\S]*?font-size:\s*13px/);
    expect(chatStyles).toMatch(/\.avatar-tool-create-field\s*\{[\s\S]*?font-size:\s*13px/);
    expect(chatStyles).toMatch(/\.avatar-tool-create-field small\s*\{[\s\S]*?font-size:\s*11px/);
    expect(chatStyles).toMatch(/\.avatar-tool-create-mode-options button\s*\{[\s\S]*?font-size:\s*13px/);
    expect(chatStyles).toMatch(/\.avatar-tool-create-file-control\s*\{[\s\S]*?grid-template-columns:\s*auto minmax\(0, 1fr\)/);
    expect(chatStyles).toMatch(/\.avatar-tool-create-change-item > \.avatar-tool-create-file-control\s*\{[\s\S]*?font-size:\s*13px/);
    expect(chatStyles).toMatch(/\.avatar-tool-manager-dialog\.is-create-view\s*\{[\s\S]*?height:\s*min\(780px, calc\(100vh - 8px\)\)/);
    expect(chatStyles).toMatch(/\.avatar-tool-manager-dialog\.is-create-view\.is-special-enabled\s*\{[\s\S]*?height:\s*min\(1040px, calc\(100vh - 8px\)\)/);
    expect(chatStyles).toMatch(/\.avatar-tool-manager-dialog\.is-create-view:not\(\.is-positioned\)\s*\{[\s\S]*?top:\s*max\(4px, calc\(50% - 390px\)\)/);
    expect(chatStyles).toMatch(/\.avatar-tool-manager-dialog\.is-create-view\.is-positioned\s*\{[\s\S]*?height:\s*var\(--avatar-tool-manager-positioned-create-height\)/);
    expect(chatStyles).toMatch(/\.avatar-tool-manager-icon-button::before\s*\{[\s\S]*?mask:\s*url\('\/static\/icons\/close_button\.png'\)/);
    expect(chatStyles).toMatch(/\.avatar-tool-create-fields\s*\{[\s\S]*?overflow-y:\s*auto[\s\S]*?scrollbar-gutter:\s*stable/);
    expect(chatStyles).toMatch(/\.avatar-tool-create-page:not\(\.has-special\) \.avatar-tool-create-fields\s*\{[\s\S]*?padding-bottom:\s*11px/);
    expect(chatStyles).toMatch(/\.avatar-tool-create-special\s*\{[\s\S]*?min-height:\s*20px/);
    expect(chatStyles).toMatch(/\.avatar-tool-create-special\.is-enabled\s*\{[\s\S]*?flex:\s*0 0 auto[\s\S]*?overflow:\s*hidden/);
    expect(chatStyles).not.toMatch(/\.avatar-tool-create-page\.has-special \.avatar-tool-create-item-meaning textarea/);
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

  it('uses the same dialog, keeps draft slots, and inserts the authoritative card before plus', async () => {
    const onSave = vi.fn();
    const onCreate = vi.fn();

    function Harness() {
      const [tools, setTools] = useState<ReadonlyArray<AvatarToolItem>>(AVAILABLE_COMPACT_AVATAR_TOOLS);
      const [activeToolIds] = useState<AvatarToolId[]>(['lollipop']);
      return (
        <AvatarToolItemManager
          open
          activeToolIds={activeToolIds}
          availableTools={tools}
          onSave={onSave}
          onCancel={() => undefined}
          createLimits={LIMITS}
          onCreate={async (input) => {
            onCreate(input);
            setTools(current => [...current, {
              id: LOCAL_ID,
              label: { kind: 'literal', value: input.name },
              iconImagePath: '/user_avatar_tools/local/default.png?v=1',
              pointerImagePath: '/user_avatar_tools/local/default.png?v=1',
            }]);
          }}
        />
      );
    }

    render(<Harness />);
    fireEvent.click(document.querySelector('[data-avatar-tool-library-id="fist"]')!);
    const dialog = screen.getByRole('dialog', { name: 'Manage tools' });
    fireEvent.click(screen.getByRole('button', { name: 'Create tool' }));
    expect(screen.getByRole('dialog', { name: 'Create custom tool' })).toBe(dialog);
    expect(dialog).toHaveClass('is-create-view');
    expect(document.querySelector('.avatar-tool-create-page img')).toBeNull();

    fireEvent.submit(document.querySelector('.avatar-tool-create-page')!);
    expect(await screen.findByRole('alert')).toHaveTextContent('Please complete all required fields.');

    fireEvent.change(screen.getByLabelText('Tool name'), { target: { value: 'My Feather' } });
    const fileInputs = document.querySelectorAll<HTMLInputElement>('input[type="file"]');
    fireEvent.change(fileInputs[0], {
      target: { files: [new File(['default'], 'default.png', { type: 'image/png' })] },
    });
    fireEvent.change(fileInputs[1], {
      target: { files: [new File(['pressed'], 'pressed.png', { type: 'image/png' })] },
    });
    fireEvent.change(document.querySelector('.avatar-tool-create-item-meaning textarea')!, {
      target: { value: 'A gentle touch' },
    });
    fireEvent.submit(document.querySelector('.avatar-tool-create-page')!);

    await waitFor(() => expect(onCreate).toHaveBeenCalledTimes(1));
    await screen.findByRole('dialog', { name: 'Manage tools' });
    const cards = Array.from(document.querySelectorAll('.avatar-tool-manager-library-card'));
    expect(cards[cards.length - 2]).toHaveTextContent('My Feather');
    expect(cards[cards.length - 1]).toHaveAttribute('data-avatar-tool-create');
    expect(screen.getByRole('button', { name: /My Feather/ })).toHaveAttribute('aria-pressed', 'false');

    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    expect(onSave).toHaveBeenCalledWith(['lollipop', 'fist']);
  });

  it('keeps the anchored dialog position when create content grows and desktop layout updates', () => {
    const desktopWindow = window as typeof window & {
      __nekoDesktopCompactLayout?: unknown;
    };
    const originalLayout = desktopWindow.__nekoDesktopCompactLayout;
    document.body.classList.add('electron-chat-window');
    desktopWindow.__nekoDesktopCompactLayout = {
      workArea: { x: 0, y: 0, width: 1280, height: 1200 },
      windowBounds: { x: 0, y: 0, width: 1280, height: 1200 },
    };

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

      const dialog = screen.getByRole('dialog', { name: 'Manage tools' });
      const libraryLeft = dialog.style.getPropertyValue('--avatar-tool-manager-left');
      const libraryTop = dialog.style.getPropertyValue('--avatar-tool-manager-top');
      expect(libraryLeft).toBe('380px');
      expect(libraryTop).toBe('208px');

      fireEvent.click(screen.getByRole('button', { name: 'Create tool' }));

      expect(dialog.style.getPropertyValue('--avatar-tool-manager-left')).toBe(libraryLeft);
      expect(dialog.style.getPropertyValue('--avatar-tool-manager-top')).toBe(libraryTop);

      fireEvent.click(screen.getByRole('checkbox', { name: 'Surprise' }));
      act(() => {
        window.dispatchEvent(new CustomEvent('neko:desktop-compact-layout-change'));
      });

      expect(dialog.style.getPropertyValue('--avatar-tool-manager-left')).toBe(libraryLeft);
      expect(dialog.style.getPropertyValue('--avatar-tool-manager-top')).toBe(libraryTop);
      expect(dialog.style.getPropertyValue('--avatar-tool-manager-positioned-create-height')).toBe('980px');
    } finally {
      document.body.classList.remove('electron-chat-window');
      desktopWindow.__nekoDesktopCompactLayout = originalLayout;
    }
  });

  it('uses desktop host pickers and keeps the optional MP3 in the create payload', async () => {
    const onCreate = vi.fn().mockResolvedValue(undefined);
    const pickImage = vi.fn()
      .mockResolvedValueOnce({
        cancelled: false,
        name: 'default.png',
        bytes: new Uint8Array([137, 80, 78, 71]).buffer,
      })
      .mockResolvedValueOnce({
        cancelled: false,
        name: 'pressed.png',
        bytes: new Uint8Array([137, 80, 78, 71]).buffer,
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
    fireEvent.change(screen.getByLabelText('Interaction description'), {
      target: { value: 'A friendly interaction' },
    });
    const fileInputs = document.querySelectorAll<HTMLInputElement>('input[type="file"]');
    fireEvent.click(fileInputs[0]);
    await waitFor(() => expect(pickImage).toHaveBeenCalledTimes(1));
    fireEvent.click(fileInputs[1]);
    await waitFor(() => expect(pickImage).toHaveBeenCalledTimes(2));
    fireEvent.click(fileInputs[2]);
    await waitFor(() => expect(pickAudio).toHaveBeenCalledTimes(1));
    expect(screen.getByText(/Played once after each successful interaction\./)).toBeInTheDocument();
    fireEvent.submit(document.querySelector('.avatar-tool-create-page')!);

    await waitFor(() => expect(onCreate).toHaveBeenCalledTimes(1));
    const payload = onCreate.mock.calls[0][0];
    expect(payload.defaultImage).toBeInstanceOf(File);
    expect(payload.defaultImage.name).toBe('default.png');
    expect(payload.changeMode).toBe('press-swap');
    expect(payload.changeItems).toHaveLength(1);
    expect(payload.changeItems[0].image).toBeInstanceOf(File);
    expect(payload.changeItems[0].image.name).toBe('pressed.png');
    expect(payload.changeItems[0].meaning).toBe('A friendly interaction');
    expect(payload.normalSound).toBeInstanceOf(File);
    expect(payload.normalSound.name).toBe('interaction.mp3');
  });

  it('shows surprise fields only when enabled and submits a selected percentage', async () => {
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
    expect(screen.getByRole('dialog')).not.toHaveClass('is-special-enabled');
    expect(screen.queryByLabelText('Trigger chance')).toBeNull();
    fireEvent.click(surpriseToggle);
    expect(screen.getByRole('dialog')).toHaveClass('is-special-enabled');
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

    fireEvent.change(screen.getByLabelText('Tool name'), { target: { value: 'Surprise Tool' } });
    fireEvent.change(screen.getByLabelText('Default image'), {
      target: { files: [new File(['default'], 'default.png', { type: 'image/png' })] },
    });
    fireEvent.change(screen.getByLabelText('Change image'), {
      target: { files: [new File(['change'], 'change.png', { type: 'image/png' })] },
    });
    fireEvent.change(document.querySelector('.avatar-tool-create-item-meaning textarea')!, {
      target: { value: 'Normal meaning' },
    });
    fireEvent.change(screen.getByLabelText('Surprise image'), {
      target: { files: [new File(['special'], 'special.png', { type: 'image/png' })] },
    });
    fireEvent.change(document.querySelector('.avatar-tool-create-special-meaning textarea')!, {
      target: { value: 'Special meaning' },
    });
    fireEvent.change(screen.getByLabelText('Surprise sound (optional)'), {
      target: { files: [new File(['sound'], 'special.mp3', { type: 'audio/mpeg' })] },
    });
    fireEvent.submit(document.querySelector('.avatar-tool-create-page')!);

    await waitFor(() => expect(onCreate).toHaveBeenCalledTimes(1));
    expect(onCreate).toHaveBeenCalledWith(expect.objectContaining({
      special: expect.objectContaining({
        probability: 0.25,
        meaning: 'Special meaning',
      }),
    }));
    expect(onCreate.mock.calls[0][0].special.image.name).toBe('special.png');
    expect(onCreate.mock.calls[0][0].special.sound.name).toBe('special.mp3');
  });

  it('keeps independent drafts for both image modes and places add inside the sequential list', () => {
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
    expect(screen.getByPlaceholderText('For example: “Ming” offers a lollipop to “Yui”, and “Yui” takes a bite.')).toBeInTheDocument();
    expect(screen.getByLabelText('Change image')).toBeInTheDocument();
    expect(screen.queryByLabelText('Change image 1')).toBeNull();
    fireEvent.change(screen.getByLabelText('Interaction description'), {
      target: { value: 'Press meaning' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Switch after clicking' }));

    const singleItemList = document.querySelector('.avatar-tool-create-change-list')!;
    expect(singleItemList).not.toHaveClass('has-multiple-items');
    expect(singleItemList).toContainElement(screen.getByRole('button', { name: '＋ Add another image' }));

    fireEvent.change(screen.getByLabelText('Interaction description for change image 1'), {
      target: { value: 'First click meaning' },
    });
    fireEvent.click(screen.getByRole('button', { name: '＋ Add another image' }));

    expect(singleItemList).toHaveClass('has-multiple-items');
    expect(screen.getByLabelText('Change image 1')).toBeInTheDocument();
    expect(screen.getByLabelText('Interaction description for change image 1')).toBeInTheDocument();
    expect(screen.getByLabelText('Change image 2')).toBeInTheDocument();
    expect(screen.getByLabelText('Interaction description for change image 2')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Switch while held' }));
    expect(screen.getByRole('button', { name: 'Switch while held' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByLabelText('Interaction description')).toHaveValue('Press meaning');
    expect(document.querySelector('.avatar-tool-create-change-list')).not.toHaveClass('has-multiple-items');
    expect(screen.queryByRole('button', { name: '＋ Add another image' })).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Switch after clicking' }));
    expect(screen.getAllByLabelText(/Interaction description for change image/)).toHaveLength(2);
    expect(screen.getByLabelText('Interaction description for change image 1')).toHaveValue('First click meaning');
  });

  it('validates and submits only the currently selected image mode', async () => {
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
    fireEvent.change(screen.getByLabelText('Tool name'), { target: { value: 'Sequence Tool' } });
    fireEvent.change(screen.getByLabelText('Default image'), {
      target: { files: [new File(['default'], 'default.png', { type: 'image/png' })] },
    });
    fireEvent.change(screen.getByLabelText('Interaction description'), {
      target: { value: 'Incomplete press draft' },
    });

    fireEvent.click(screen.getByRole('button', { name: 'Switch after clicking' }));
    fireEvent.change(screen.getByLabelText('Change image 1'), {
      target: { files: [new File(['next'], 'next.png', { type: 'image/png' })] },
    });
    fireEvent.change(screen.getByLabelText('Interaction description for change image 1'), {
      target: { value: 'First click' },
    });
    fireEvent.submit(document.querySelector('.avatar-tool-create-page')!);

    await waitFor(() => expect(onCreate).toHaveBeenCalledTimes(1));
    expect(onCreate).toHaveBeenCalledWith(expect.objectContaining({
      changeMode: 'click-advance',
      changeItems: [expect.objectContaining({ meaning: 'First click' })],
    }));
    expect(onCreate.mock.calls[0][0].changeItems[0].image.name).toBe('next.png');
  });
});
