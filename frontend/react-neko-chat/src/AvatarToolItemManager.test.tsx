import { useState } from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
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
    expect(chatStyles).toMatch(/\.avatar-tool-create-change-list\s*\{[\s\S]*?flex:\s*1 1 120px[\s\S]*?min-height:\s*120px/);
    expect(chatStyles).toMatch(/\.avatar-tool-create-change-list:not\(\.has-multiple-items\)\s*\{[\s\S]*?grid-template-rows:\s*minmax\(0, 1fr\)/);
    expect(chatStyles).toMatch(/\.avatar-tool-create-actions\s*\{[\s\S]*?flex:\s*0 0 auto[\s\S]*?margin-top:\s*auto/);
    expect(chatStyles).toMatch(/\.avatar-tool-manager-header p\s*\{[\s\S]*?font-size:\s*13px/);
    expect(chatStyles).toMatch(/\.avatar-tool-create-field\s*\{[\s\S]*?font-size:\s*13px/);
    expect(chatStyles).toMatch(/\.avatar-tool-create-field small\s*\{[\s\S]*?font-size:\s*11px/);
    expect(chatStyles).toMatch(/\.avatar-tool-create-mode-options button\s*\{[\s\S]*?font-size:\s*13px/);
    expect(chatStyles).toMatch(/\.avatar-tool-create-file-control\s*\{[\s\S]*?grid-template-columns:\s*auto minmax\(0, 1fr\)/);
    expect(chatStyles).toMatch(/\.avatar-tool-manager-icon-button::before\s*\{[\s\S]*?mask:\s*url\('\/static\/icons\/close_button\.png'\)/);
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
    fireEvent.change(screen.getByLabelText('Interaction description'), {
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

  it('uses the desktop host image picker without changing the existing create payload', async () => {
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
    window.nekoHost = { pickImage };

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
