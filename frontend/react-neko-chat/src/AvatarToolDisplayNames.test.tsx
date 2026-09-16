import { fireEvent, render, screen } from '@testing-library/react';
import spanish from '../../../static/locales/es.json';
import AvatarToolCreatePage from './AvatarToolCreatePage';
import { AvatarToolInteractionCanvas } from './AvatarToolEditorWorkspace';
import { AvatarToolInteractionEditorProvider } from './avatar-tools/AvatarToolInteractionEditorContext';
import type { LocalAvatarToolLimits, LocalAvatarToolV2Detail } from './avatar-tools/localTools';

const limits: LocalAvatarToolLimits = {
  maxTools: 64, maxNameChars: 60, maxMeaningChars: 100, maxChangeImages: 16,
  maxImages: 17, maxInteractions: 16, maxLinks: 32, maxDelayMs: 600000,
  maxImageBytes: 8388608, maxImagePixels: 16000000, maxAudioBytes: 5242880,
  maxAudioDurationMs: 10000, maxTotalBytes: 268435456,
};
const detail: LocalAvatarToolV2Detail = {
  recordVersion: 2, id: 'local-12345678-1234-4123-8123-123456789abc', revision: '2-100',
  name: 'Tool', changeMode: 'press-swap',
  defaultImage: { resource: 'default.png', url: '/default.png?v=1' },
  changeItems: [{ resource: 'change-000.png', url: '/change-000.png?v=1', meaning: '' }],
};

describe('avatar tool display names across editor consumers', () => {
  afterEach(() => { vi.unstubAllGlobals(); });

  it.each([
    { locale: 'es', imageName: 'Imagen de la herramienta 1', clickName: 'Clic del ratón 2' },
    { locale: 'none', imageName: 'Tool image 1', clickName: 'Mouse click 2' },
  ])('shares image names in cards, canvas, actions and validation with $locale', ({ locale, imageName, clickName }) => {
    if (locale === 'es') {
      // Use shipped Spanish name translations; leave unrelated controls in English.
      vi.stubGlobal('t', (key: string) => /(?:ImageNumber|ClickNumber|DelayNumber)$/.test(key)
        ? spanish.chat[key.replace('chat.', '') as keyof typeof spanish.chat] ?? key : key);
    }
    render(<AvatarToolInteractionEditorProvider>
      <AvatarToolInteractionCanvas limits={limits} />
      <AvatarToolCreatePage limits={limits} initialDetail={detail}
        onSpecialEnabledChange={() => undefined} onSave={async () => undefined} onCancel={() => undefined} />
    </AvatarToolInteractionEditorProvider>);
    expect(screen.getByRole('button', { name: `Edit ${imageName}` })).toBeVisible();
    expect(document.querySelector('.avatar-tool-initial-image-node')).toHaveTextContent(imageName);
    fireEvent.click(screen.getByRole('button', { name: 'Mouse click' }));
    expect(screen.getByLabelText('Interaction name')).toHaveAttribute('placeholder', clickName);
    expect(screen.getByText(clickName)).toBeVisible();
    fireEvent.change(screen.getByLabelText('Interaction name'), { target: { value: 'Custom click' } });
    expect(screen.getByText('Custom click')).toBeVisible();
    expect(screen.getAllByRole('option', { name: imageName })).toHaveLength(2);
    fireEvent.click(screen.getByRole('tab', { name: 'Tool settings' }));
    fireEvent.click(screen.getByRole('button', { name: /Edit (Tool image 2|Imagen de la herramienta 2)/ }));
    fireEvent.change(screen.getByLabelText('Image name'), { target: { value: imageName } });
    expect(screen.getByLabelText('Image name')).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getAllByRole('alert').some(alert => alert.textContent?.includes(imageName))).toBe(true);
    fireEvent.change(screen.getByLabelText('Image name'), { target: { value: 'Custom image' } });
    expect(screen.getByRole('button', { name: 'Edit Custom image' })).toBeVisible();
    fireEvent.click(screen.getByRole('tab', { name: /Interaction settings/ }));
    expect(screen.getAllByRole('option', { name: 'Custom image' })).toHaveLength(2);
  });

  it('uses the same delayed switch fallback for the canvas, inspector and errors', () => {
    render(<AvatarToolInteractionEditorProvider>
      <AvatarToolInteractionCanvas limits={limits} />
      <AvatarToolCreatePage limits={limits} initialDetail={detail}
        onSpecialEnabledChange={() => undefined} onSave={async () => undefined} onCancel={() => undefined} />
    </AvatarToolInteractionEditorProvider>);
    fireEvent.click(screen.getByRole('button', { name: 'Delayed switch' }));
    expect(screen.getByLabelText('Interaction name')).toHaveAttribute('placeholder', 'Delayed switch 1');
    expect(document.querySelector('.avatar-tool-interaction-node.is-after')).toHaveTextContent('Delayed switch 1');
    fireEvent.change(screen.getByLabelText('Interaction name'), { target: { value: 'Custom delay' } });
    expect(document.querySelector('.avatar-tool-interaction-node.is-after')).toHaveTextContent('Custom delay');
    fireEvent.change(screen.getByLabelText('Interaction name'), { target: { value: '' } });
    fireEvent.change(screen.getByLabelText('Switch to'), { target: { value: 'img-v2-change-000' } });
    fireEvent.click(screen.getByRole('tab', { name: 'Tool settings' }));
    fireEvent.click(screen.getByRole('button', { name: 'Edit Tool image 2' }));
    fireEvent.click(screen.getByRole('button', { name: 'Remove image' }));
    expect(screen.getByRole('alert')).toHaveTextContent('Delayed switch 1 · Switch to');
  });
});
