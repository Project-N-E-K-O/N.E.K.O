import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import AvatarToolCreatePage from './AvatarToolCreatePage';
import { AvatarToolInteractionCanvas } from './AvatarToolEditorWorkspace';
import { AvatarToolInteractionEditorProvider } from './avatar-tools/AvatarToolInteractionEditorContext';
import type { LocalAvatarToolDetail, LocalAvatarToolLimits } from './avatar-tools/localTools';
import { validateAvatarToolPng } from './avatar-tools/avatarToolImageFile';

const LIMITS: LocalAvatarToolLimits = {
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
  id: 'local-12345678-1234-4123-8123-123456789abc',
  revision: '2-100',
  name: 'Loop',
  changeMode: 'press-swap',
  defaultImage: { resource: 'default.png', url: '/default.png' },
  changeItems: [{
    resource: 'change-000.png',
    url: '/change-000.png',
    meaning: '变化图片',
  }],
};

describe('AvatarToolCreatePage stage 2 image references', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('saves an opened v2 tool as a complete ordinary v3 update', async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <AvatarToolInteractionEditorProvider>
        <AvatarToolCreatePage
          limits={LIMITS}
          initialDetail={DETAIL}
          onSpecialEnabledChange={() => undefined}
          onSave={onSave}
          onDelete={async () => undefined}
          onCancel={() => undefined}
        />
      </AvatarToolInteractionEditorProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    const input = onSave.mock.calls[0][0];
    expect(input).toMatchObject({
      recordVersion: 3,
      baseRevision: '2-100',
      initialImageId: 'img-v2-default',
      images: [
        expect.objectContaining({ id: 'img-v2-default', meaning: '' }),
        expect.objectContaining({ id: 'img-v2-change-000', meaning: '变化图片' }),
      ],
      imageInteractions: {
        initialLinks: [expect.objectContaining({ to: 'ix-v2-press-swap' })],
        items: [expect.objectContaining({
          id: 'ix-v2-press-swap',
          trigger: { kind: 'mouse-click' },
          actions: {
            press: { kind: 'show', imageId: 'img-v2-change-000' },
            release: { kind: 'show', imageId: 'img-v2-default' },
          },
        })],
      },
    });
    expect(input).not.toHaveProperty('changeMode');
    expect(input).not.toHaveProperty('presetId');
  });

  it('preserves every v2 image, description, sound, and special while saving click-advance as v3', async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    const detail: LocalAvatarToolDetail = {
      ...DETAIL,
      revision: '2-200',
      changeMode: 'click-advance',
      changeItems: [
        { resource: 'change-000.png', url: '/change-000.png', meaning: '第一张' },
        { resource: 'change-001.png', url: '/change-001.png', meaning: '' },
        { resource: 'change-002.png', url: '/change-002.png', meaning: '第三张' },
      ],
      normalSound: { resource: 'normal.mp3', url: '/normal.mp3' },
      special: {
        probability: 0.25,
        image: { resource: 'special.png', url: '/special.png' },
        meaning: '彩蛋描述',
        sound: { resource: 'special.mp3', url: '/special.mp3' },
      },
    };
    render(
      <AvatarToolInteractionEditorProvider>
        <AvatarToolCreatePage
          limits={LIMITS}
          initialDetail={detail}
          onSpecialEnabledChange={() => undefined}
          onSave={onSave}
          onDelete={async () => undefined}
          onCancel={() => undefined}
        />
      </AvatarToolInteractionEditorProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    const input = onSave.mock.calls[0][0];

    expect(input).toMatchObject({
      recordVersion: 3,
      baseRevision: '2-200',
      images: [
        expect.objectContaining({ id: 'img-v2-default', image: { resource: 'default.png', url: '/default.png' }, meaning: '' }),
        expect.objectContaining({ id: 'img-v2-change-000', image: { resource: 'change-000.png', url: '/change-000.png' }, meaning: '第一张' }),
        expect.objectContaining({ id: 'img-v2-change-001', image: { resource: 'change-001.png', url: '/change-001.png' }, meaning: '' }),
        expect.objectContaining({ id: 'img-v2-change-002', image: { resource: 'change-002.png', url: '/change-002.png' }, meaning: '第三张' }),
      ],
      imageInteractions: {
        initialLinks: [expect.objectContaining({ to: 'ix-v2-click-advance-000' })],
        items: [
          expect.objectContaining({
            id: 'ix-v2-click-advance-000',
            actions: { press: { kind: 'keep' }, release: { kind: 'show', imageId: 'img-v2-change-000' } },
          }),
          expect.objectContaining({
            id: 'ix-v2-click-advance-001',
            actions: { press: { kind: 'keep' }, release: { kind: 'show', imageId: 'img-v2-change-001' } },
          }),
          expect.objectContaining({
            id: 'ix-v2-click-advance-002',
            actions: { press: { kind: 'keep' }, release: { kind: 'show', imageId: 'img-v2-change-002' } },
          }),
        ],
        links: [
          expect.objectContaining({ from: 'ix-v2-click-advance-000', to: 'ix-v2-click-advance-001' }),
          expect.objectContaining({ from: 'ix-v2-click-advance-001', to: 'ix-v2-click-advance-002' }),
        ],
      },
      normalSound: { resource: 'normal.mp3', url: '/normal.mp3' },
      special: {
        probability: 0.25,
        image: { resource: 'special.png', url: '/special.png' },
        meaning: '彩蛋描述',
        sound: { resource: 'special.mp3', url: '/special.mp3' },
      },
    });
    expect(input.imageInteractions.items[2].id).toBe('ix-v2-click-advance-002');
    expect(input.imageInteractions.links).toHaveLength(2);
    expect(input).not.toHaveProperty('changeMode');
    expect(input).not.toHaveProperty('presetId');
  });

  it('keeps the edited form in memory when saving fails', async () => {
    const onSave = vi.fn().mockRejectedValue(new Error('save_failed'));
    render(
      <AvatarToolInteractionEditorProvider>
        <AvatarToolCreatePage
          limits={LIMITS}
          initialDetail={DETAIL}
          onSpecialEnabledChange={() => undefined}
          onSave={onSave}
          onDelete={async () => undefined}
          onCancel={() => undefined}
        />
      </AvatarToolInteractionEditorProvider>,
    );

    const nameInput = screen.getByLabelText('Tool name');
    fireEvent.change(nameInput, { target: { value: 'Unsaved edit' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));

    await screen.findByText('Could not save this tool. Please try again.');
    expect(onSave).toHaveBeenCalledTimes(1);
    expect(nameInput).toHaveValue('Unsaved edit');
  });

  it('discards an edited v2 draft without saving when the user cancels', () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    const onCancel = vi.fn();
    render(
      <AvatarToolInteractionEditorProvider>
        <AvatarToolCreatePage
          limits={LIMITS}
          initialDetail={DETAIL}
          onSpecialEnabledChange={() => undefined}
          onSave={onSave}
          onDelete={async () => undefined}
          onCancel={onCancel}
        />
      </AvatarToolInteractionEditorProvider>,
    );

    fireEvent.change(screen.getByLabelText('Tool name'), { target: { value: 'Edited in memory' } });
    fireEvent.click(screen.getByRole('button', { name: 'Back' }));

    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onSave).not.toHaveBeenCalled();
  });

  it('saves an applied preset as a standard v3 graph without template identity', async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    const detail: LocalAvatarToolDetail = {
      recordVersion: 3,
      id: DETAIL.id,
      revision: '3-100',
      name: 'Flow',
      images: [
        { id: 'img-a', name: '', resource: 'image-000.png', url: '/a.png', meaning: '' },
        { id: 'img-b', name: '', resource: 'image-001.png', url: '/b.png', meaning: 'B' },
        { id: 'img-c', name: '', resource: 'image-002.png', url: '/c.png', meaning: 'C' },
      ],
      initialImageId: 'img-a',
      imageInteractions: {
        initialImagePosition: { x: 0, y: 0 },
        initialLinks: [{ to: 'ix-old', sourceSide: 'right', targetSide: 'left' }],
        items: [{
          id: 'ix-old',
          name: '',
          trigger: { kind: 'mouse-click' },
          actions: { press: { kind: 'keep' }, release: { kind: 'keep' } },
          editorPosition: { x: 280, y: 0 },
        }],
        links: [{ from: 'ix-old', to: 'ix-old', sourceSide: 'right', targetSide: 'right' }],
      },
    };
    render(
      <AvatarToolInteractionEditorProvider>
        <AvatarToolInteractionCanvas limits={LIMITS} />
        <AvatarToolCreatePage
          limits={LIMITS}
          initialDetail={detail}
          onSpecialEnabledChange={() => undefined}
          onSave={onSave}
          onDelete={async () => undefined}
          onCancel={() => undefined}
        />
      </AvatarToolInteractionEditorProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Presets' }));
    fireEvent.click(screen.getByRole('button', { name: 'Image cycle' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    const input = onSave.mock.calls[0][0];
    expect(input.recordVersion).toBe(3);
    expect(input.imageInteractions.items).toHaveLength(5);
    expect(input.imageInteractions.items.map((item: { actions: unknown }) => item.actions)).toEqual([
      { complete: { kind: 'keep' } },
      { complete: { kind: 'keep' } },
      { complete: { kind: 'keep' } },
      { press: { kind: 'keep' }, release: { kind: 'keep' } },
      { complete: { kind: 'keep' } },
    ]);
    expect(input.imageInteractions.initialLinks).toHaveLength(1);
    expect(input.imageInteractions.links).toHaveLength(8);
    expect(input).not.toHaveProperty('changeMode');
    expect(input).not.toHaveProperty('presetId');
  });

  it('applies the configured byte limit before accepting a PNG', async () => {
    const bytes = new Uint8Array(24);
    bytes.set([137, 80, 78, 71, 13, 10, 26, 10]);
    bytes.set([0, 0, 0, 13, 73, 72, 68, 82], 8);
    const view = new DataView(bytes.buffer);
    view.setUint32(16, 16, false);
    view.setUint32(20, 16, false);
    const file = new File([bytes.buffer as ArrayBuffer], 'image.png', { type: 'image/png' });

    await expect(validateAvatarToolPng(file, {
      maxImageBytes: 23,
      maxImagePixels: 16_000_000,
    })).resolves.toBe('too-large');
  });

  it('shows compact interaction descriptions on cards and keeps the selected image settings editable', () => {
    render(
      <AvatarToolInteractionEditorProvider>
        <AvatarToolCreatePage
        limits={LIMITS}
        initialDetail={DETAIL}
        onSpecialEnabledChange={() => undefined}
        onSave={async () => undefined}
        onDelete={async () => undefined}
        onCancel={() => undefined}
        />
      </AvatarToolInteractionEditorProvider>,
    );

    expect(screen.getByText('变化图片')).toBeVisible();
    expect(screen.getByText('Choose an initial image.')).toBeVisible();
    expect(document.querySelector('[data-avatar-tool-image-id="img-v2-default"]')).not.toHaveTextContent('default.png');
    expect(document.querySelector('[data-avatar-tool-image-id="img-v2-change-000"]')).not.toHaveTextContent('change-000.png');

    fireEvent.click(screen.getByRole('button', { name: 'Edit Tool image 2' }));

    const imageName = screen.getByLabelText('Image name');
    expect(imageName).toHaveAttribute('placeholder', 'Tool image 2');
    expect(imageName.closest('label')).toHaveAttribute('title', 'Click the name to rename');
    expect(imageName.closest('label')?.querySelector('.avatar-tool-editable-name-icon'))
      .toHaveAttribute('src', '/static/icons/edit.png');
    fireEvent.change(imageName, { target: { value: 'Open palm' } });
    expect(screen.getByRole('button', { name: 'Edit Open palm' })).toBeVisible();

    const description = screen.getByLabelText('Interaction description for tool image 2 (optional)');
    expect(description).toHaveValue('变化图片');
    expect(description).toHaveStyle({ overflowY: 'hidden' });
    expect(document.querySelector('[data-avatar-tool-image-id="img-v2-change-000"] .avatar-tool-image-card-copy')).toHaveTextContent('变化图片');
    expect(screen.getByRole('radio', { name: 'Initial image' })).not.toBeChecked();
    expect(screen.getByText('Change image')).toBeVisible();
    expect(screen.getByTitle('change-000.png')).toBeVisible();
    const selectedImagePreview = screen.getByAltText('Open palm');
    Object.defineProperties(selectedImagePreview, {
      naturalWidth: { configurable: true, value: 240 },
      naturalHeight: { configurable: true, value: 180 },
    });
    fireEvent.load(selectedImagePreview);
    expect(screen.getByText('240 px · 180 px')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Remove image' })).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Image actions' })).toBeNull();
  });

  it('names every interaction reference and blocks a dangling image deletion', () => {
    render(
      <AvatarToolInteractionEditorProvider>
        <AvatarToolCreatePage
        limits={LIMITS}
        initialDetail={DETAIL}
        imageReferences={{
          'img-v2-change-000': ['鼠标点击 1 · 松开时', '经过 800ms · 目标图片'],
        }}
        onSpecialEnabledChange={() => undefined}
        onSave={async () => undefined}
        onDelete={async () => undefined}
        onCancel={() => undefined}
        />
      </AvatarToolInteractionEditorProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Edit Tool image 2' }));
    fireEvent.click(screen.getByRole('button', { name: 'Remove image' }));

    expect(screen.getByRole('alert')).toHaveTextContent('鼠标点击 1 · 松开时');
    expect(screen.getByRole('alert')).toHaveTextContent('经过 800ms · 目标图片');
    expect(document.querySelector('[data-avatar-tool-image-id="img-v2-change-000"]')).toBeInTheDocument();
  });

  it('shows the shared optional-name rule while the image name is edited', () => {
    render(
      <AvatarToolInteractionEditorProvider>
        <AvatarToolCreatePage
          limits={LIMITS}
          initialDetail={DETAIL}
          onSpecialEnabledChange={() => undefined}
          onSave={async () => undefined}
          onDelete={async () => undefined}
          onCancel={() => undefined}
        />
      </AvatarToolInteractionEditorProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Edit Tool image 2' }));
    const imageName = screen.getByLabelText('Image name');
    fireEvent.change(imageName, { target: { value: 'bad!' } });
    expect(screen.getByRole('alert')).toHaveTextContent('Use letters, numbers, spaces');

    fireEvent.change(imageName, { target: { value: '𠮷'.repeat(20) } });
    expect(screen.queryByText(/The name must be no more than/)).toBeNull();

    fireEvent.change(imageName, { target: { value: '𠮷'.repeat(21) } });
    expect(screen.getByRole('alert')).toHaveTextContent('The name must be no more than 20 characters.');
  });

  it('rejects a tool name already used by a built-in or another custom tool', () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <AvatarToolInteractionEditorProvider>
        <AvatarToolCreatePage
          limits={LIMITS}
          existingToolNames={['Hammer', 'Another custom tool']}
          onSpecialEnabledChange={() => undefined}
          onSave={onSave}
          onCancel={() => undefined}
        />
      </AvatarToolInteractionEditorProvider>,
    );

    fireEvent.change(screen.getByLabelText('Tool name'), { target: { value: '  hammer  ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save tool' }));

    expect(screen.getByText('“hammer” is already used by another tool. Choose a different name.'))
      .toBeVisible();
    expect(onSave).not.toHaveBeenCalled();
  });

  it('clears both Web audio inputs so the same MP3 can be selected again', () => {
    render(
      <AvatarToolInteractionEditorProvider>
        <AvatarToolCreatePage
        limits={LIMITS}
        onSpecialEnabledChange={() => undefined}
        onSave={async () => undefined}
        onCancel={() => undefined}
        />
      </AvatarToolInteractionEditorProvider>,
    );
    const audio = new File(['mp3'], 'tap.mp3', { type: 'audio/mpeg' });
    const normalInput = screen.getByLabelText('Interaction sound (optional)') as HTMLInputElement;

    fireEvent.change(normalInput, { target: { files: [audio] } });
    expect(normalInput.value).toBe('');
    expect(screen.getByText('tap.mp3')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Remove' }));
    fireEvent.change(normalInput, { target: { files: [audio] } });
    expect(normalInput.value).toBe('');
    expect(screen.getByText('tap.mp3')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Remove' }));

    fireEvent.click(screen.getByRole('checkbox', { name: 'Surprise' }));
    const specialInput = screen.getByLabelText('Surprise sound (optional)') as HTMLInputElement;
    fireEvent.change(specialInput, { target: { files: [audio] } });
    expect(specialInput.value).toBe('');
    expect(screen.getByText('tap.mp3')).toBeVisible();
  });
});
