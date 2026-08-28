import {
  useRef,
  useState,
  type FormEvent,
  type MouseEvent as ReactMouseEvent,
} from 'react';
import { i18n } from './i18n';
import {
  LocalAvatarToolCreateError,
  createLocalAvatarToolId,
  type CreateLocalAvatarToolInput,
  type LocalAvatarToolChangeMode,
  type LocalAvatarToolDetail,
  type LocalAvatarToolLimits,
  type UpdateLocalAvatarToolInput,
} from './avatar-tools/localTools';

type AvatarToolCreatePageProps = {
  limits: LocalAvatarToolLimits | null;
  userName?: string;
  assistantName?: string;
  initialDetail?: LocalAvatarToolDetail;
  notice?: string;
  onSpecialEnabledChange(enabled: boolean): void;
  onSave(input: CreateLocalAvatarToolInput | UpdateLocalAvatarToolInput): Promise<void>;
  onDelete?(): Promise<void>;
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
  imageResource?: string;
  imageUrl?: string;
  meaning: string;
};

type FieldErrors = Record<string, string>;

const NAME_ALLOWED_PATTERN = /^[\p{L}\p{M}\p{N} _-]+$/u;

function normalizeToolName(value: string): string {
  return value.normalize('NFC').trim().replace(/ +/g, ' ');
}

function normalizeMeaning(value: string): string {
  return value.replace(/\r\n?/g, '\n').trim();
}

function characterCount(value: string): number {
  return Array.from(value).length;
}

function FieldError({ message }: { message?: string }) {
  return message ? <small className="avatar-tool-create-field-error" role="alert">{message}</small> : null;
}

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
  initialDetail,
  notice = '',
  onSpecialEnabledChange,
  onSave,
  onDelete,
  onCancel,
}: AvatarToolCreatePageProps) {
  const editing = !!initialDetail;
  const creationToolIdRef = useRef<ReturnType<typeof createLocalAvatarToolId> | null>(null);
  if (!editing && !creationToolIdRef.current) creationToolIdRef.current = createLocalAvatarToolId();
  const initialChangeItems = initialDetail?.changeItems.map((item, index) => ({
    id: index,
    image: null,
    imageResource: item.resource,
    imageUrl: item.url,
    meaning: item.meaning,
  }));
  const nextItemIdRef = useRef((initialChangeItems?.length ?? 0) + 2);
  const createFieldsRef = useRef<HTMLDivElement | null>(null);
  const [name, setName] = useState(initialDetail?.name ?? '');
  const [changeMode, setChangeMode] = useState<LocalAvatarToolChangeMode>(initialDetail?.changeMode ?? 'press-swap');
  const [defaultImage, setDefaultImage] = useState<File | null>(null);
  const [defaultImageResource] = useState(initialDetail?.defaultImage.resource);
  const [defaultImageUrl] = useState(initialDetail?.defaultImage.url);
  const [normalSound, setNormalSound] = useState<File | null>(null);
  const [normalSoundResource, setNormalSoundResource] = useState(initialDetail?.normalSound?.resource);
  const [normalSoundUrl, setNormalSoundUrl] = useState(initialDetail?.normalSound?.url);
  const [specialEnabled, setSpecialEnabled] = useState(!!initialDetail?.special);
  const [specialProbabilityPercent, setSpecialProbabilityPercent] = useState(
    Math.round((initialDetail?.special?.probability ?? 0.1) * 100),
  );
  const [specialImage, setSpecialImage] = useState<File | null>(null);
  const [specialImageResource] = useState(initialDetail?.special?.image.resource);
  const [specialImageUrl] = useState(initialDetail?.special?.image.url);
  const [specialMeaning, setSpecialMeaning] = useState(initialDetail?.special?.meaning ?? '');
  const [specialSound, setSpecialSound] = useState<File | null>(null);
  const [specialSoundResource, setSpecialSoundResource] = useState(initialDetail?.special?.sound?.resource);
  const [specialSoundUrl, setSpecialSoundUrl] = useState(initialDetail?.special?.sound?.url);
  const [changeItemsByMode, setChangeItemsByMode] = useState<Record<LocalAvatarToolChangeMode, ChangeItemDraft[]>>({
    'press-swap': initialDetail?.changeMode === 'press-swap' && initialChangeItems
      ? initialChangeItems
      : [{ id: 0, image: null, meaning: '' }],
    'click-advance': initialDetail?.changeMode === 'click-advance' && initialChangeItems
      ? initialChangeItems
      : [{ id: 1, image: null, meaning: '' }],
  });
  const [submitting, setSubmitting] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState('');
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const changeItems = changeItemsByMode[changeMode];
  const busy = submitting || deleting;
  const meaningExample = i18n(
    'chat.avatarToolCreateImageMeaningPlaceholder',
    'For example: “{{user}}” brings a lollipop to “{{character}}”, and “{{character}}” takes a bite.',
    {
      user: userName.trim() || i18n('chat.avatarToolCreateExampleUser', 'the user'),
      character: assistantName.trim() || i18n('chat.avatarToolCreateExampleCharacter', 'the character'),
    },
  );
  const specialMeaningExample = i18n(
    'chat.avatarToolCreateSpecialMeaningPlaceholder',
    'For example: “{{user}}” lightly taps “{{character}}” with a cat paw and a reward drops.',
    {
      user: userName.trim() || i18n('chat.avatarToolCreateExampleUser', 'the user'),
      character: assistantName.trim() || i18n('chat.avatarToolCreateExampleCharacter', 'the character'),
    },
  );

  const clearFieldError = (key: string) => {
    setFieldErrors((current) => {
      if (!current[key]) return current;
      const next = { ...current };
      delete next[key];
      return next;
    });
  };

  const setFieldError = (key: string, message: string) => {
    setFieldErrors((current) => ({ ...current, [key]: message }));
  };

  const showFieldErrors = (next: FieldErrors) => {
    setFieldErrors(next);
    const firstKey = Object.keys(next)[0];
    if (!firstKey) return;
    window.requestAnimationFrame(() => {
      const field = Array.from(
        createFieldsRef.current?.querySelectorAll<HTMLElement>('[data-error-key]') ?? [],
      ).find(candidate => candidate.dataset.errorKey === firstKey);
      if (!field) return;
      if (typeof field.scrollIntoView === 'function') {
        field.scrollIntoView({ block: 'nearest' });
      }
      const focusTarget = field.matches('input, textarea, button')
        ? field
        : field.querySelector<HTMLElement>('input, textarea, button');
      focusTarget?.focus();
    });
  };

  const updateChangeItem = (id: number, patch: Partial<Omit<ChangeItemDraft, 'id'>>) => {
    setChangeItemsByMode(current => ({
      ...current,
      [changeMode]: current[changeMode].map(item => item.id === id ? { ...item, ...patch } : item),
    }));
  };

  const pickImageWithDesktopHost = async (
    event: ReactMouseEvent<HTMLInputElement>,
    title: string,
    errorKey: string,
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
      clearFieldError(errorKey);
      setError('');
    } catch {
      setFieldError(
        errorKey,
        i18n(
          'chat.avatarToolCreateImageInvalidError',
          'This image cannot be used. Please choose another PNG.',
        ),
      );
      setError('');
    }
  };

  const pickAudioWithDesktopHost = async (
    event: ReactMouseEvent<HTMLInputElement>,
    title: string,
    setFile: (file: File) => void,
    errorKey: string,
  ) => {
    const picker = window.nekoHost?.pickAudio;
    if (!picker) return;

    event.preventDefault();
    const input = event.currentTarget;
    try {
      const result = await picker({
        title,
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
      setFile(file);
      clearFieldError(errorKey);
      setError('');
    } catch {
      setFieldError(
        errorKey,
        i18n('chat.avatarToolCreateAudioPickError', 'Could not open this MP3. Please try another file.'),
      );
      setError('');
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
    clearFieldError(`change_image:${id}`);
    clearFieldError(`change_meaning:${id}`);
    setError('');
  };

  const changeImageRequired = (index: number) => changeMode === 'click-advance'
    ? i18n('chat.avatarToolCreateChangeImageRequiredNumber', 'Please choose change image {{number}}.', {
      number: String(index + 1),
    })
    : i18n('chat.avatarToolCreateChangeImageRequired', 'Please choose a change image.');

  const changeMeaningRequired = (index: number) => changeMode === 'click-advance'
    ? i18n('chat.avatarToolCreateMeaningRequiredNumber', 'Please describe change image {{number}}.', {
      number: String(index + 1),
    })
    : i18n('chat.avatarToolCreateMeaningRequired', 'Please enter an interaction description.');

  const meaningLengthError = () => i18n(
    'chat.avatarToolCreateMeaningLengthError',
    'The interaction description must be 1–{{count}} characters.',
    { count: String(limits?.maxMeaningChars ?? 100) },
  );

  const meaningInvalidError = () => i18n(
    'chat.avatarToolCreateMeaningInvalidError',
    'The interaction description contains unsupported characters.',
  );

  const validateMeaning = (value: string, requiredError: string): string => {
    const normalized = normalizeMeaning(value);
    if (!normalized) return requiredError;
    if (characterCount(normalized) > (limits?.maxMeaningChars ?? 100)) return meaningLengthError();
    return '';
  };

  const fieldKeyFromCreateError = (cause: LocalAvatarToolCreateError): string | null => {
    if (cause.field === 'change_image' || cause.field === 'change_meaning') {
      const item = cause.index === undefined ? undefined : changeItems[cause.index];
      return item ? `${cause.field}:${item.id}` : null;
    }
    return cause.field ?? null;
  };

  const messageFromCreateError = (cause: LocalAvatarToolCreateError): string => {
    if (cause.message === 'name_required') {
      return i18n('chat.avatarToolCreateNameRequired', 'Please enter a tool name.');
    }
    if (cause.message === 'name_too_long') {
      return i18n('chat.avatarToolCreateNameLengthError', 'The tool name must be 1–{{count}} characters.', {
        count: String(limits?.maxNameChars ?? 20),
      });
    }
    if (cause.message === 'name_invalid') {
      return i18n(
        'chat.avatarToolCreateNameInvalidError',
        'Use letters, numbers, spaces, “-”, or “_” in the tool name.',
      );
    }
    if (cause.field === 'change_meaning' || cause.field === 'special_meaning') {
      if (cause.message.endsWith('_required')) {
        return cause.field === 'special_meaning'
          ? i18n('chat.avatarToolCreateMeaningRequired', 'Please enter an interaction description.')
          : changeMeaningRequired(cause.index ?? 0);
      }
      if (cause.message.endsWith('_too_long')) {
        return meaningLengthError();
      }
      return meaningInvalidError();
    }
    if (cause.field === 'default_image' || cause.field === 'change_image' || cause.field === 'special_image') {
      if (cause.message.endsWith('_required')) {
        if (cause.field === 'default_image') {
          return i18n('chat.avatarToolCreateDefaultImageRequired', 'Please choose a default image.');
        }
        if (cause.field === 'special_image') {
          return i18n('chat.avatarToolCreateSpecialImageRequired', 'Please choose a surprise image.');
        }
        return changeImageRequired(cause.index ?? 0);
      }
      if (cause.message === 'image_too_large') {
        return i18n('chat.avatarToolCreateImageSizeError', 'The image must be no larger than {{size}}.', {
          size: formatLimit(limits?.maxImageBytes),
        });
      }
      return i18n(
        'chat.avatarToolCreateImageInvalidError',
        'This image cannot be used. Please choose another PNG.',
      );
    }
    if (cause.field === 'normal_sound' || cause.field === 'special_sound') {
      if (cause.message.endsWith('too_large')) {
        return i18n('chat.avatarToolCreateAudioSizeError', 'The MP3 must be no larger than {{size}}.', {
          size: formatLimit(limits?.maxAudioBytes),
        });
      }
      if (cause.message.endsWith('too_long')) {
        return i18n('chat.avatarToolCreateAudioDurationError', 'The MP3 must be no longer than {{seconds}} seconds.', {
          seconds: String(Math.round((limits?.maxAudioDurationMs ?? 10_000) / 1000)),
        });
      }
      return i18n('chat.avatarToolCreateAudioInvalidError', 'This sound cannot be used. Please choose another MP3.');
    }
    if (cause.field === 'special_probability') {
      return i18n('chat.avatarToolCreateSpecialProbabilityInvalid', 'Choose a trigger chance from 1% to 100%.');
    }
    return i18n('chat.avatarToolCreateSaveError', 'Could not save this tool. Please try again.');
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    const nextErrors: FieldErrors = {};
    const normalizedName = normalizeToolName(name);
    const nameLength = characterCount(normalizedName);
    const maximumNameLength = limits?.maxNameChars ?? 20;
    if (!normalizedName) {
      nextErrors.name = i18n('chat.avatarToolCreateNameRequired', 'Please enter a tool name.');
    } else if (nameLength > maximumNameLength) {
      nextErrors.name = i18n(
        'chat.avatarToolCreateNameLengthError',
        'The tool name must be 1–{{count}} characters.',
        { count: String(maximumNameLength) },
      );
    } else if (!NAME_ALLOWED_PATTERN.test(normalizedName)) {
      nextErrors.name = i18n(
        'chat.avatarToolCreateNameInvalidError',
        'Use letters, numbers, spaces, “-”, or “_” in the tool name.',
      );
    }
    if (!defaultImage && !defaultImageResource) {
      nextErrors.default_image = i18n(
        'chat.avatarToolCreateDefaultImageRequired',
        'Please choose a default image.',
      );
    }
    changeItems.forEach((item, index) => {
      if (!item.image && !item.imageResource) nextErrors[`change_image:${item.id}`] = changeImageRequired(index);
      const meaningError = validateMeaning(item.meaning, changeMeaningRequired(index));
      if (meaningError) nextErrors[`change_meaning:${item.id}`] = meaningError;
    });
    if (specialEnabled) {
      if (!specialImage && !specialImageResource) {
        nextErrors.special_image = i18n(
          'chat.avatarToolCreateSpecialImageRequired',
          'Please choose a surprise image.',
        );
      }
      const meaningError = validateMeaning(
        specialMeaning,
        i18n('chat.avatarToolCreateMeaningRequired', 'Please enter an interaction description.'),
      );
      if (meaningError) nextErrors.special_meaning = meaningError;
    }
    if (Object.keys(nextErrors).length) {
      showFieldErrors(nextErrors);
      return;
    }
    setSubmitting(true);
    setError('');
    setFieldErrors({});
    try {
      if (editing) {
        await onSave({
          baseRevision: initialDetail!.revision,
          name: normalizedName,
          changeMode,
          defaultImage: defaultImage
            ? { file: defaultImage }
            : { resource: defaultImageResource, url: defaultImageUrl },
          changeItems: changeItems.map(item => ({
            ...(item.image ? { file: item.image } : { resource: item.imageResource, url: item.imageUrl }),
            meaning: normalizeMeaning(item.meaning),
          })),
          ...((normalSound || normalSoundResource) ? {
            normalSound: normalSound
              ? { file: normalSound }
              : { resource: normalSoundResource, url: normalSoundUrl },
          } : {}),
          ...(specialEnabled ? {
            special: {
              probability: specialProbabilityPercent / 100,
              image: specialImage
                ? { file: specialImage }
                : { resource: specialImageResource, url: specialImageUrl },
              meaning: normalizeMeaning(specialMeaning),
              ...((specialSound || specialSoundResource) ? {
                sound: specialSound
                  ? { file: specialSound }
                  : { resource: specialSoundResource, url: specialSoundUrl },
              } : {}),
            },
          } : {}),
        } satisfies UpdateLocalAvatarToolInput);
      } else {
        await onSave({
          toolId: creationToolIdRef.current!,
          name: normalizedName,
          changeMode,
          defaultImage: defaultImage!,
          changeItems: changeItems.map(item => ({
            image: item.image!,
            meaning: normalizeMeaning(item.meaning),
          })),
          ...(normalSound ? { normalSound } : {}),
          ...(specialEnabled ? {
            special: {
              probability: specialProbabilityPercent / 100,
              image: specialImage!,
              meaning: normalizeMeaning(specialMeaning),
              ...(specialSound ? { sound: specialSound } : {}),
            },
          } : {}),
        } satisfies CreateLocalAvatarToolInput);
      }
    } catch (cause) {
      if (cause instanceof LocalAvatarToolCreateError) {
        const key = fieldKeyFromCreateError(cause);
        if (key) {
          showFieldErrors({ [key]: messageFromCreateError(cause) });
        } else if (cause.message === 'tool_limit_reached') {
          setError(i18n('chat.avatarToolCreateToolLimitError', 'The custom tool library is full.'));
        } else if (cause.message === 'storage_limit_reached') {
          setError(i18n('chat.avatarToolCreateStorageLimitError', 'There is not enough space for another custom tool.'));
        } else {
          setError(messageFromCreateError(cause));
        }
      } else {
        setError(i18n('chat.avatarToolCreateSaveError', 'Could not save this tool. Please try again.'));
      }
    } finally {
      setSubmitting(false);
    }
  };

  const removeNormalSound = () => {
    setNormalSound(null);
    setNormalSoundResource(undefined);
    setNormalSoundUrl(undefined);
    clearFieldError('normal_sound');
  };

  const removeSpecialSound = () => {
    setSpecialSound(null);
    setSpecialSoundResource(undefined);
    setSpecialSoundUrl(undefined);
    clearFieldError('special_sound');
  };

  const deleteTool = async () => {
    if (!onDelete || busy) return;
    if (!window.confirm(i18n(
      'chat.avatarToolDeleteConfirm',
      'Delete “{{name}}”? This cannot be undone.',
      { name: initialDetail?.name ?? name },
    ))) return;
    setDeleting(true);
    setError('');
    try {
      await onDelete();
    } catch {
      setError(i18n('chat.avatarToolDeleteError', 'Could not delete this tool. Please try again.'));
    } finally {
      setDeleting(false);
    }
  };

  return (
    <form className={`avatar-tool-create-page${specialEnabled ? ' has-special' : ''}`} noValidate onSubmit={submit}>
      <div className="avatar-tool-create-fields" ref={createFieldsRef}>
        <p
          className={error ? 'avatar-tool-create-error' : 'avatar-tool-create-privacy'}
          {...(error ? { role: 'alert' } : {})}
        >
          {error
            ? error
            : i18n(
              'chat.avatarToolCreatePrivacy',
              'Images and sounds stay on this device; during interactions, the name and matching description are sent to the model.',
            )}
        </p>
        {notice ? (
          <p id="avatar-tool-manager-notice" className="avatar-tool-manager-notice" role="status">
            {notice}
          </p>
        ) : null}
        <label className="avatar-tool-create-field" data-error-key="name">
          <span>{i18n('chat.avatarToolCreateName', 'Tool name')}</span>
          <input
            value={name}
            aria-label={i18n('chat.avatarToolCreateName', 'Tool name')}
            aria-invalid={fieldErrors.name ? 'true' : undefined}
            disabled={busy}
            onChange={(event) => {
              setName(event.target.value);
              clearFieldError('name');
            }}
            placeholder={i18n(
              'chat.avatarToolCreateNamePlaceholder',
              '1–{{count}} characters; use letters, numbers, spaces, “-”, or “_”',
              { count: String(limits?.maxNameChars ?? 20) },
            )}
          />
          <FieldError message={fieldErrors.name} />
        </label>
        <div className="avatar-tool-create-field" data-error-key="default_image">
        <span>{i18n('chat.avatarToolCreateDefaultImage', 'Default image')}</span>
        <label
          className={`avatar-tool-create-file-control${busy ? ' is-disabled' : ''}`}
          aria-invalid={fieldErrors.default_image ? 'true' : undefined}
        >
          <input
            className="avatar-tool-create-file-input"
            type="file"
            accept="image/png,.png"
            aria-label={i18n('chat.avatarToolCreateDefaultImage', 'Default image')}
            disabled={busy}
            onClick={(event) => {
              void pickImageWithDesktopHost(
                event,
                i18n('chat.avatarToolCreateDefaultImage', 'Default image'),
                'default_image',
                setDefaultImage,
              );
            }}
            onChange={(event) => {
              setDefaultImage(event.target.files?.[0] ?? null);
              clearFieldError('default_image');
            }}
          />
          <span className="avatar-tool-create-file-button">
            {editing
              ? i18n('chat.avatarToolUpdateChooseImage', 'Change image')
              : i18n('chat.avatarToolCreateChooseImage', 'Choose image')}
          </span>
          <span className={`avatar-tool-create-file-name${defaultImage || defaultImageResource ? ' has-file' : ''}`}>
            {defaultImage?.name
              ?? (defaultImageResource
                ? i18n('chat.avatarToolUpdateCurrentImage', 'Current image')
                : i18n('chat.avatarToolCreateNoImage', 'No image selected'))}
          </span>
        </label>
        <small>
          {i18n('chat.avatarToolCreateDefaultImageHint', 'Shown until an image change is triggered; it grows when entering the character interaction area.')}
          {limits ? ` ${i18n('chat.avatarToolCreateImageLimit', 'PNG, up to {{size}} per image', { size: formatLimit(limits.maxImageBytes) })}` : ''}
        </small>
        <FieldError message={fieldErrors.default_image} />
        </div>

      <fieldset className="avatar-tool-create-mode" disabled={busy}>
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
                      disabled={busy || index === 0}
                      aria-label={i18n('chat.avatarToolCreateMoveUp', 'Move image up')}
                      onClick={() => moveChangeItem(index, -1)}
                    >↑</button>
                    <button
                      type="button"
                      disabled={busy || index === changeItems.length - 1}
                      aria-label={i18n('chat.avatarToolCreateMoveDown', 'Move image down')}
                      onClick={() => moveChangeItem(index, 1)}
                    >↓</button>
                    <button
                      type="button"
                      disabled={busy || changeItems.length === 1}
                      aria-label={i18n('chat.avatarToolCreateRemoveImage', 'Remove image')}
                      onClick={() => removeChangeItem(item.id)}
                    >×</button>
                  </div>
                ) : null}
              </div>
              <label
                className={`avatar-tool-create-file-control${busy ? ' is-disabled' : ''}`}
                data-error-key={`change_image:${item.id}`}
                aria-invalid={fieldErrors[`change_image:${item.id}`] ? 'true' : undefined}
              >
                <input
                  className="avatar-tool-create-file-input"
                  type="file"
                  accept="image/png,.png"
                  aria-label={imageTitle}
                  disabled={busy}
                  onClick={(event) => {
                    void pickImageWithDesktopHost(
                      event,
                      imageTitle,
                      `change_image:${item.id}`,
                      image => updateChangeItem(item.id, { image }),
                    );
                  }}
                  onChange={(event) => {
                    updateChangeItem(item.id, { image: event.target.files?.[0] ?? null });
                    clearFieldError(`change_image:${item.id}`);
                  }}
                />
                <span className="avatar-tool-create-file-button">
                  {editing
                    ? i18n('chat.avatarToolUpdateChooseImage', 'Change image')
                    : i18n('chat.avatarToolCreateChooseImage', 'Choose image')}
                </span>
                <span className={`avatar-tool-create-file-name${item.image || item.imageResource ? ' has-file' : ''}`}>
                  {item.image?.name
                    ?? (item.imageResource
                      ? i18n('chat.avatarToolUpdateCurrentImage', 'Current image')
                      : i18n('chat.avatarToolCreateNoImage', 'No image selected'))}
                </span>
              </label>
              <FieldError message={fieldErrors[`change_image:${item.id}`]} />
              <label
                className="avatar-tool-create-field avatar-tool-create-item-meaning"
                data-error-key={`change_meaning:${item.id}`}
              >
                <span>{i18n('chat.avatarToolCreateImageMeaning', 'Interaction description')}</span>
                <textarea
                  value={item.meaning}
                  aria-label={changeMode === 'press-swap'
                    ? i18n('chat.avatarToolCreateImageMeaning', 'Interaction description')
                    : i18n('chat.avatarToolCreateImageMeaningNumber', 'Interaction description for change image {{number}}', {
                      number: String(index + 1),
                    })}
                  aria-invalid={fieldErrors[`change_meaning:${item.id}`] ? 'true' : undefined}
                  disabled={busy}
                  onChange={(event) => {
                    updateChangeItem(item.id, { meaning: event.target.value });
                    clearFieldError(`change_meaning:${item.id}`);
                  }}
                  placeholder={meaningExample}
                  rows={3}
                />
                <FieldError message={fieldErrors[`change_meaning:${item.id}`]} />
              </label>
            </section>
          );
        })}
        {changeMode === 'click-advance' ? (
          <button
            className="avatar-tool-create-add-image"
            type="button"
            disabled={busy || changeItems.length >= (limits?.maxChangeImages ?? 16)}
            onClick={addChangeItem}
          >
            {i18n('chat.avatarToolCreateAddImage', '＋ Add another image')}
          </button>
        ) : null}
      </div>

      <div className="avatar-tool-create-field avatar-tool-create-audio-field" data-error-key="normal_sound">
        <span>{i18n('chat.avatarToolCreateNormalSound', 'Interaction sound (optional)')}</span>
        <div className="avatar-tool-create-file-row">
        <label className={`avatar-tool-create-file-control${busy ? ' is-disabled' : ''}`}>
          <input
            className="avatar-tool-create-file-input"
            type="file"
            accept="audio/mpeg,.mp3"
            aria-label={i18n('chat.avatarToolCreateNormalSound', 'Interaction sound (optional)')}
            disabled={busy}
            onClick={(event) => {
              void pickAudioWithDesktopHost(
                event,
                i18n('chat.avatarToolCreateNormalSound', 'Interaction sound (optional)'),
                setNormalSound,
                'normal_sound',
              );
            }}
            onChange={(event) => {
              const file = event.target.files?.[0] ?? null;
              setNormalSound(file);
              clearFieldError('normal_sound');
            }}
          />
          <span className="avatar-tool-create-file-button">
            {editing
              ? i18n('chat.avatarToolUpdateChooseAudio', 'Change MP3')
              : i18n('chat.avatarToolCreateChooseAudio', 'Choose MP3')}
          </span>
          <span className={`avatar-tool-create-file-name${normalSound || normalSoundResource ? ' has-file' : ''}`}>
            {normalSound?.name
              ?? (normalSoundResource
                ? i18n('chat.avatarToolUpdateCurrentAudio', 'Current sound')
                : i18n('chat.avatarToolCreateNoAudio', 'No sound selected'))}
          </span>
        </label>
        {editing && (normalSound || normalSoundResource) ? (
          <button
            className="avatar-tool-create-remove-file"
            type="button"
            disabled={busy}
            onClick={removeNormalSound}
          >
            {i18n('chat.avatarToolUpdateRemoveAudio', 'Remove')}
          </button>
        ) : null}
        </div>
        <small>
          {i18n('chat.avatarToolCreateNormalSoundHint', 'Played once when an interaction succeeds.')}
          {limits ? ` ${i18n('chat.avatarToolCreateAudioLimit', 'MP3, up to {{size}} and {{seconds}} seconds', {
            size: formatLimit(limits.maxAudioBytes),
            seconds: String(Math.round(limits.maxAudioDurationMs / 1000)),
          })}` : ''}
        </small>
        <FieldError message={fieldErrors.normal_sound} />
      </div>

      <section className={`avatar-tool-create-special${specialEnabled ? ' is-enabled' : ''}`}>
        <label className="avatar-tool-create-special-toggle">
          <span>{i18n('chat.avatarToolCreateSpecial', 'Surprise')}</span>
          <input
            type="checkbox"
            checked={specialEnabled}
            disabled={busy}
            onChange={(event) => {
              const enabled = event.target.checked;
              setSpecialEnabled(enabled);
              onSpecialEnabledChange(enabled);
              if (enabled) {
                window.requestAnimationFrame(() => {
                  const fields = createFieldsRef.current;
                  if (fields && fields.scrollHeight > fields.clientHeight) {
                    fields.scrollTop = fields.scrollHeight;
                  }
                });
              } else {
                setFieldErrors((current) => Object.fromEntries(
                  Object.entries(current).filter(([key]) => !key.startsWith('special_')),
                ));
              }
              setError('');
            }}
          />
          <span className="avatar-tool-create-special-switch" aria-hidden="true" />
        </label>

        {specialEnabled ? (
          <div className="avatar-tool-create-special-fields">
            <label
              className="avatar-tool-create-field avatar-tool-create-special-probability"
              data-error-key="special_probability"
            >
              <span>{i18n('chat.avatarToolCreateSpecialProbability', 'Trigger chance')}</span>
              <input
                type="range"
                min="1"
                max="100"
                step="1"
                value={specialProbabilityPercent}
                aria-valuetext={`${specialProbabilityPercent}%`}
                disabled={busy}
                onChange={(event) => {
                  setSpecialProbabilityPercent(Number(event.target.value));
                  clearFieldError('special_probability');
                }}
              />
              <strong>{specialProbabilityPercent}%</strong>
            </label>
            <FieldError message={fieldErrors.special_probability} />

            <div className="avatar-tool-create-field" data-error-key="special_image">
              <span>{i18n('chat.avatarToolCreateSpecialImage', 'Surprise image')}</span>
              <label
                className={`avatar-tool-create-file-control${busy ? ' is-disabled' : ''}`}
                aria-invalid={fieldErrors.special_image ? 'true' : undefined}
              >
                <input
                  className="avatar-tool-create-file-input"
                  type="file"
                  accept="image/png,.png"
                  aria-label={i18n('chat.avatarToolCreateSpecialImage', 'Surprise image')}
                  disabled={busy}
                  onClick={(event) => {
                    void pickImageWithDesktopHost(
                      event,
                      i18n('chat.avatarToolCreateSpecialImage', 'Surprise image'),
                      'special_image',
                      setSpecialImage,
                    );
                  }}
                  onChange={(event) => {
                    setSpecialImage(event.target.files?.[0] ?? null);
                    clearFieldError('special_image');
                  }}
                />
                <span className="avatar-tool-create-file-button">
                  {editing
                    ? i18n('chat.avatarToolUpdateChooseImage', 'Change image')
                    : i18n('chat.avatarToolCreateChooseImage', 'Choose image')}
                </span>
                <span className={`avatar-tool-create-file-name${specialImage || specialImageResource ? ' has-file' : ''}`}>
                  {specialImage?.name
                    ?? (specialImageResource
                      ? i18n('chat.avatarToolUpdateCurrentImage', 'Current image')
                      : i18n('chat.avatarToolCreateNoImage', 'No image selected'))}
                </span>
              </label>
              <FieldError message={fieldErrors.special_image} />
            </div>

            <label
              className="avatar-tool-create-field avatar-tool-create-special-meaning"
              data-error-key="special_meaning"
            >
              <span>{i18n('chat.avatarToolCreateImageMeaning', 'Interaction description')}</span>
              <textarea
                value={specialMeaning}
                aria-invalid={fieldErrors.special_meaning ? 'true' : undefined}
                disabled={busy}
                onChange={(event) => {
                  setSpecialMeaning(event.target.value);
                  clearFieldError('special_meaning');
                }}
                placeholder={specialMeaningExample}
                rows={3}
              />
              <FieldError message={fieldErrors.special_meaning} />
            </label>

            <div className="avatar-tool-create-field" data-error-key="special_sound">
              <span>{i18n('chat.avatarToolCreateSpecialSound', 'Surprise sound (optional)')}</span>
              <div className="avatar-tool-create-file-row">
              <label className={`avatar-tool-create-file-control${busy ? ' is-disabled' : ''}`}>
                <input
                  className="avatar-tool-create-file-input"
                  type="file"
                  accept="audio/mpeg,.mp3"
                  aria-label={i18n('chat.avatarToolCreateSpecialSound', 'Surprise sound (optional)')}
                  disabled={busy}
                  onClick={(event) => {
                    void pickAudioWithDesktopHost(
                      event,
                      i18n('chat.avatarToolCreateSpecialSound', 'Surprise sound (optional)'),
                      setSpecialSound,
                      'special_sound',
                    );
                  }}
                  onChange={(event) => {
                    setSpecialSound(event.target.files?.[0] ?? null);
                    clearFieldError('special_sound');
                  }}
                />
                <span className="avatar-tool-create-file-button">
                  {editing
                    ? i18n('chat.avatarToolUpdateChooseAudio', 'Change MP3')
                    : i18n('chat.avatarToolCreateChooseAudio', 'Choose MP3')}
                </span>
                <span className={`avatar-tool-create-file-name${specialSound || specialSoundResource ? ' has-file' : ''}`}>
                  {specialSound?.name
                    ?? (specialSoundResource
                      ? i18n('chat.avatarToolUpdateCurrentAudio', 'Current sound')
                      : i18n('chat.avatarToolCreateNoAudio', 'No sound selected'))}
                </span>
              </label>
              {editing && (specialSound || specialSoundResource) ? (
                <button
                  className="avatar-tool-create-remove-file"
                  type="button"
                  disabled={busy}
                  onClick={removeSpecialSound}
                >
                  {i18n('chat.avatarToolUpdateRemoveAudio', 'Remove')}
                </button>
              ) : null}
              </div>
              <FieldError message={fieldErrors.special_sound} />
            </div>
          </div>
        ) : null}
      </section>
      </div>

      <div className={`avatar-tool-manager-actions avatar-tool-create-actions${editing ? ' is-editing' : ''}`}>
        {editing && onDelete ? (
          <button
            className="avatar-tool-manager-action danger"
            type="button"
            disabled={busy}
            onClick={() => void deleteTool()}
          >
            {deleting
              ? i18n('chat.avatarToolUpdateDeleting', 'Deleting…')
              : i18n('chat.avatarToolUpdateDelete', 'Delete tool')}
          </button>
        ) : null}
        <div className="avatar-tool-create-action-group">
          <button className="avatar-tool-manager-action secondary" type="button" disabled={busy} onClick={onCancel}>
            {i18n('chat.avatarToolCreateBack', 'Back')}
          </button>
          <button className="avatar-tool-manager-action primary" type="submit" disabled={busy}>
            {submitting
              ? i18n('chat.avatarToolCreateSaving', 'Saving…')
              : editing
                ? i18n('chat.avatarToolUpdateSave', 'Save changes')
                : i18n('chat.avatarToolCreateSave', 'Save tool')}
          </button>
        </div>
      </div>
    </form>
  );
}
