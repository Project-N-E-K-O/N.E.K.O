import { fireEvent, render, screen } from '@testing-library/react';
import AvatarToolCreatePage from './AvatarToolCreatePage';
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
