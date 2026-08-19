import {
  useRef,
  useState,
  type FormEvent,
  type MouseEvent as ReactMouseEvent,
} from 'react';
import { i18n } from './i18n';
import type {
  CreateLocalAvatarToolInput,
  LocalAvatarToolChangeMode,
  LocalAvatarToolLimits,
} from './avatar-tools/localTools';

type AvatarToolCreatePageProps = {
  limits: LocalAvatarToolLimits | null;
  userName?: string;
  assistantName?: string;
  onCreate(input: CreateLocalAvatarToolInput): Promise<void>;
  onCancel(): void;
};

type HostFilePickerResult = {
  cancelled?: boolean;
  error?: string;
  name?: string;
  bytes?: ArrayBuffer | ArrayBufferView;
};

type ChangeItemDraft = {
  id: number;
  image: File | null;
  meaning: string;
};

declare global {
  interface Window {
    nekoHost?: {
      pickImage?: (options?: { title?: string; maxBytes?: number }) => Promise<HostFilePickerResult>;
      pickAudio?: (options?: { title?: string; maxBytes?: number }) => Promise<HostFilePickerResult>;
    };
  }
}

function formatLimit(bytes: number | undefined): string {
  if (!bytes) return '';
  return `${Math.round(bytes / (1024 * 1024))} MB`;
}

export default function AvatarToolCreatePage({
  limits,
  userName = '',
  assistantName = '',
  onCreate,
  onCancel,
}: AvatarToolCreatePageProps) {
  const nextItemIdRef = useRef(2);
  const [name, setName] = useState('');
  const [changeMode, setChangeMode] = useState<LocalAvatarToolChangeMode>('press-swap');
  const [defaultImage, setDefaultImage] = useState<File | null>(null);
  const [normalSound, setNormalSound] = useState<File | null>(null);
  const [changeItemsByMode, setChangeItemsByMode] = useState<Record<LocalAvatarToolChangeMode, ChangeItemDraft[]>>({
    'press-swap': [{ id: 0, image: null, meaning: '' }],
    'click-advance': [{ id: 1, image: null, meaning: '' }],
  });
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [audioError, setAudioError] = useState('');
  const changeItems = changeItemsByMode[changeMode];
  const meaningExample = i18n(
    'chat.avatarToolCreateImageMeaningPlaceholder',
    'For example: “{{user}}” offers a lollipop to “{{character}}”, and “{{character}}” takes a bite.',
    {
      user: userName.trim() || i18n('chat.avatarToolCreateExampleUser', 'the user'),
      character: assistantName.trim() || i18n('chat.avatarToolCreateExampleCharacter', 'the character'),
    },
  );

  const updateChangeItem = (id: number, patch: Partial<Omit<ChangeItemDraft, 'id'>>) => {
    setChangeItemsByMode(current => ({
      ...current,
      [changeMode]: current[changeMode].map(item => item.id === id ? { ...item, ...patch } : item),
    }));
  };

  const pickImageWithDesktopHost = async (
    event: ReactMouseEvent<HTMLInputElement>,
    title: string,
    setFile: (file: File) => void,
  ) => {
    const picker = window.nekoHost?.pickImage;
    if (!picker) return;

    event.preventDefault();
    const input = event.currentTarget;
    try {
      const result = await picker({ title, maxBytes: limits?.maxImageBytes });
      if (result.cancelled) return;
      if (result.error || !result.name || !result.bytes) throw new Error(result.error || 'image_picker_failed');

      const sourceBytes = result.bytes instanceof ArrayBuffer
        ? new Uint8Array(result.bytes)
        : new Uint8Array(result.bytes.buffer, result.bytes.byteOffset, result.bytes.byteLength);
      const ownedBytes = new Uint8Array(sourceBytes.byteLength);
      ownedBytes.set(sourceBytes);
      const file = new File([ownedBytes.buffer as ArrayBuffer], result.name, { type: 'image/png' });
      try {
        const transfer = new DataTransfer();
        transfer.items.add(file);
        input.files = transfer.files;
      } catch {
        // File 已进入 React 状态即可保存；这里仅用于让 Chromium 原生控件显示文件名。
      }
      setFile(file);
      setError('');
    } catch {
      setError(i18n('chat.avatarToolCreateImagePickError', 'Could not open this PNG image. Please try another file.'));
    }
  };

  const pickAudioWithDesktopHost = async (event: ReactMouseEvent<HTMLInputElement>) => {
    const picker = window.nekoHost?.pickAudio;
    if (!picker) return;

    event.preventDefault();
    const input = event.currentTarget;
    try {
      const result = await picker({
        title: i18n('chat.avatarToolCreateNormalSound', 'Interaction sound (optional)'),
        maxBytes: limits?.maxAudioBytes,
      });
      if (result.cancelled) return;
      if (result.error || !result.name || !result.bytes) throw new Error(result.error || 'audio_picker_failed');

      const sourceBytes = result.bytes instanceof ArrayBuffer
        ? new Uint8Array(result.bytes)
        : new Uint8Array(result.bytes.buffer, result.bytes.byteOffset, result.bytes.byteLength);
      const ownedBytes = new Uint8Array(sourceBytes.byteLength);
      ownedBytes.set(sourceBytes);
      const file = new File([ownedBytes.buffer as ArrayBuffer], result.name, { type: 'audio/mpeg' });
      try {
        const transfer = new DataTransfer();
        transfer.items.add(file);
        input.files = transfer.files;
      } catch {
        // File 已进入 React 状态即可保存；这里只同步 Chromium 原生控件的文件名。
      }
      setNormalSound(file);
      setAudioError('');
    } catch {
      setAudioError(i18n('chat.avatarToolCreateAudioPickError', 'Could not open this MP3. Please try another file.'));
    }
  };

  const chooseMode = (nextMode: LocalAvatarToolChangeMode) => {
    if (nextMode === changeMode) return;
    setChangeMode(nextMode);
    setError('');
  };

  const addChangeItem = () => {
    const maximum = limits?.maxChangeImages ?? 16;
    if (changeItems.length >= maximum) return;
    const id = nextItemIdRef.current++;
    setChangeItemsByMode(current => ({
      ...current,
      'click-advance': [...current['click-advance'], { id, image: null, meaning: '' }],
    }));
    setError('');
  };

  const moveChangeItem = (index: number, offset: -1 | 1) => {
    const target = index + offset;
    if (target < 0 || target >= changeItems.length) return;
    setChangeItemsByMode((current) => {
      const next = [...current['click-advance']];
      [next[index], next[target]] = [next[target], next[index]];
      return { ...current, 'click-advance': next };
    });
  };

  const removeChangeItem = (id: number) => {
    if (changeItems.length <= 1) return;
    setChangeItemsByMode(current => ({
      ...current,
      'click-advance': current['click-advance'].filter(item => item.id !== id),
    }));
    setError('');
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (submitting) return;
    const completeItems = changeItems.every(item => item.image && item.meaning.trim());
    if (
      !name.trim()
      || !defaultImage
      || !completeItems
      || (changeMode === 'press-swap' && changeItems.length !== 1)
    ) {
      setError(i18n('chat.avatarToolCreateRequiredError', 'Please complete all required fields.'));
      return;
    }
    setSubmitting(true);
    setError('');
    try {
      await onCreate({
        name,
        changeMode,
        defaultImage,
        changeItems: changeItems.map(item => ({
          image: item.image!,
          meaning: item.meaning,
        })),
        ...(normalSound ? { normalSound } : {}),
      });
    } catch (cause) {
      const code = cause instanceof Error ? cause.message : '';
      if (code === 'audio_too_large') {
        setAudioError(i18n('chat.avatarToolCreateAudioSizeError', 'The MP3 must be no larger than {{size}}.', {
          size: formatLimit(limits?.maxAudioBytes),
        }));
      } else if (code === 'audio_too_long') {
        setAudioError(i18n('chat.avatarToolCreateAudioDurationError', 'The MP3 must be no longer than {{seconds}} seconds.', {
          seconds: String(Math.round((limits?.maxAudioDurationMs ?? 10_000) / 1000)),
        }));
      } else if (code.startsWith('audio_')) {
        setAudioError(i18n('chat.avatarToolCreateAudioInvalidError', 'This file is not a valid MP3 with playable audio.'));
      } else {
        setError(i18n('chat.avatarToolCreateSaveError', 'Could not save this tool. Check the fields and try again.'));
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form className="avatar-tool-create-page" onSubmit={submit}>
      <p className="avatar-tool-create-privacy">
          {i18n(
            'chat.avatarToolCreatePrivacy',
            'Images and sounds stay on this device; during interactions, the name and matching description are sent to the model.',
          )}
      </p>
      <label className="avatar-tool-create-field">
        <span>{i18n('chat.avatarToolCreateName', 'Tool name')}</span>
        <input
          value={name}
          maxLength={limits?.maxNameChars}
          disabled={submitting}
          onChange={event => setName(event.target.value)}
          required
        />
      </label>
      <div className="avatar-tool-create-field">
        <span>{i18n('chat.avatarToolCreateDefaultImage', 'Default image')}</span>
        <label className={`avatar-tool-create-file-control${submitting ? ' is-disabled' : ''}`}>
          <input
            className="avatar-tool-create-file-input"
            type="file"
            accept="image/png,.png"
            aria-label={i18n('chat.avatarToolCreateDefaultImage', 'Default image')}
            disabled={submitting}
            onClick={(event) => {
              void pickImageWithDesktopHost(
                event,
                i18n('chat.avatarToolCreateDefaultImage', 'Default image'),
                setDefaultImage,
              );
            }}
            onChange={event => setDefaultImage(event.target.files?.[0] ?? null)}
            required
          />
          <span className="avatar-tool-create-file-button">
            {i18n('chat.avatarToolCreateChooseImage', 'Choose image')}
          </span>
          <span className={`avatar-tool-create-file-name${defaultImage ? ' has-file' : ''}`}>
            {defaultImage?.name ?? i18n('chat.avatarToolCreateNoImage', 'No image selected')}
          </span>
        </label>
        <small>
          {i18n('chat.avatarToolCreateDefaultImageHint', 'Shown until an image change is triggered; entering the character interaction area only changes its size.')}
          {limits ? ` ${i18n('chat.avatarToolCreateImageLimit', 'PNG, up to {{size}} per image', { size: formatLimit(limits.maxImageBytes) })}` : ''}
        </small>
      </div>

      <fieldset className="avatar-tool-create-mode" disabled={submitting}>
        <legend>{i18n('chat.avatarToolCreateChangeMode', 'Image switching')}</legend>
        <div className="avatar-tool-create-mode-options">
          <button
            type="button"
            aria-pressed={changeMode === 'press-swap'}
            onClick={() => chooseMode('press-swap')}
          >
            {i18n('chat.avatarToolCreateModePressSwap', 'Switch while held')}
          </button>
          <button
            type="button"
            aria-pressed={changeMode === 'click-advance'}
            onClick={() => chooseMode('click-advance')}
          >
            {i18n('chat.avatarToolCreateModeClickAdvance', 'Switch after clicking')}
          </button>
        </div>
      </fieldset>

      <div className={`avatar-tool-create-change-list${changeItems.length > 1 ? ' has-multiple-items' : ''}`}>
        {changeItems.map((item, index) => {
          const imageTitle = changeMode === 'press-swap'
            ? i18n('chat.avatarToolCreateChangeImage', 'Change image')
            : i18n('chat.avatarToolCreateChangeImageNumber', 'Change image {{number}}', {
              number: String(index + 1),
            });
          return (
            <section className="avatar-tool-create-change-item" key={item.id}>
              <div className="avatar-tool-create-change-heading">
                <strong>{imageTitle}</strong>
                {changeMode === 'click-advance' ? (
                  <div className="avatar-tool-create-change-controls">
                    <button
                      type="button"
                      disabled={submitting || index === 0}
                      aria-label={i18n('chat.avatarToolCreateMoveUp', 'Move image up')}
                      onClick={() => moveChangeItem(index, -1)}
                    >↑</button>
                    <button
                      type="button"
                      disabled={submitting || index === changeItems.length - 1}
                      aria-label={i18n('chat.avatarToolCreateMoveDown', 'Move image down')}
                      onClick={() => moveChangeItem(index, 1)}
                    >↓</button>
                    <button
                      type="button"
                      disabled={submitting || changeItems.length === 1}
                      aria-label={i18n('chat.avatarToolCreateRemoveImage', 'Remove image')}
                      onClick={() => removeChangeItem(item.id)}
                    >×</button>
                  </div>
                ) : null}
              </div>
              <label className={`avatar-tool-create-file-control${submitting ? ' is-disabled' : ''}`}>
                <input
                  className="avatar-tool-create-file-input"
                  type="file"
                  accept="image/png,.png"
                  aria-label={imageTitle}
                  disabled={submitting}
                  onClick={(event) => {
                    void pickImageWithDesktopHost(
                      event,
                      imageTitle,
                      image => updateChangeItem(item.id, { image }),
                    );
                  }}
                  onChange={event => updateChangeItem(item.id, { image: event.target.files?.[0] ?? null })}
                  required
                />
                <span className="avatar-tool-create-file-button">
                  {i18n('chat.avatarToolCreateChooseImage', 'Choose image')}
                </span>
                <span className={`avatar-tool-create-file-name${item.image ? ' has-file' : ''}`}>
                  {item.image?.name ?? i18n('chat.avatarToolCreateNoImage', 'No image selected')}
                </span>
              </label>
              <label className="avatar-tool-create-field avatar-tool-create-item-meaning">
                <span>{i18n('chat.avatarToolCreateImageMeaning', 'Interaction description')}</span>
                <textarea
                  value={item.meaning}
                  aria-label={changeMode === 'press-swap'
                    ? i18n('chat.avatarToolCreateImageMeaning', 'Interaction description')
                    : i18n('chat.avatarToolCreateImageMeaningNumber', 'Interaction description for change image {{number}}', {
                      number: String(index + 1),
                    })}
                  maxLength={limits?.maxMeaningChars}
                  disabled={submitting}
                  onChange={event => updateChangeItem(item.id, { meaning: event.target.value })}
                  placeholder={meaningExample}
                  required
                  rows={3}
                />
              </label>
            </section>
          );
        })}
        {changeMode === 'click-advance' ? (
          <button
            className="avatar-tool-create-add-image"
            type="button"
            disabled={submitting || changeItems.length >= (limits?.maxChangeImages ?? 16)}
            onClick={addChangeItem}
          >
            {i18n('chat.avatarToolCreateAddImage', '＋ Add another image')}
          </button>
        ) : null}
      </div>

      <div className="avatar-tool-create-field avatar-tool-create-audio-field">
        <span>{i18n('chat.avatarToolCreateNormalSound', 'Interaction sound (optional)')}</span>
        <label className={`avatar-tool-create-file-control${submitting ? ' is-disabled' : ''}`}>
          <input
            className="avatar-tool-create-file-input"
            type="file"
            accept="audio/mpeg,.mp3"
            aria-label={i18n('chat.avatarToolCreateNormalSound', 'Interaction sound (optional)')}
            disabled={submitting}
            onClick={(event) => { void pickAudioWithDesktopHost(event); }}
            onChange={(event) => {
              const file = event.target.files?.[0] ?? null;
              setNormalSound(file);
              setAudioError('');
            }}
          />
          <span className="avatar-tool-create-file-button">
            {i18n('chat.avatarToolCreateChooseAudio', 'Choose MP3')}
          </span>
          <span className={`avatar-tool-create-file-name${normalSound ? ' has-file' : ''}`}>
            {normalSound?.name ?? i18n('chat.avatarToolCreateNoAudio', 'No sound selected')}
          </span>
        </label>
        <small>
          {i18n('chat.avatarToolCreateNormalSoundHint', 'Played once after each successful interaction.')}
          {limits ? ` ${i18n('chat.avatarToolCreateAudioLimit', 'MP3, up to {{size}} and {{seconds}} seconds', {
            size: formatLimit(limits.maxAudioBytes),
            seconds: String(Math.round(limits.maxAudioDurationMs / 1000)),
          })}` : ''}
        </small>
        {audioError ? <small className="avatar-tool-create-audio-error" role="alert">{audioError}</small> : null}
      </div>

      {error ? <p className="avatar-tool-create-error" role="alert">{error}</p> : null}
      <div className="avatar-tool-manager-actions avatar-tool-create-actions">
        <button className="avatar-tool-manager-action secondary" type="button" disabled={submitting} onClick={onCancel}>
          {i18n('chat.avatarToolCreateBack', 'Back')}
        </button>
        <button className="avatar-tool-manager-action primary" type="submit" disabled={submitting}>
          {submitting
            ? i18n('chat.avatarToolCreateSaving', 'Saving…')
            : i18n('chat.avatarToolCreateSave', 'Save tool')}
        </button>
      </div>
    </form>
  );
}
