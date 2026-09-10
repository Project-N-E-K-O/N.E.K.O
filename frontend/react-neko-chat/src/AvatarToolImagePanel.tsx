import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent as ReactMouseEvent,
} from 'react';
import { i18n } from './i18n';
import type { LocalAvatarToolLimits } from './avatar-tools/localTools';
import type {
  AvatarToolImageDraft,
  AvatarToolImageId,
} from './avatar-tools/avatarToolEditorModel';

type AvatarToolImagePanelProps = {
  limits: LocalAvatarToolLimits | null;
  images: readonly AvatarToolImageDraft[];
  initialImageId: AvatarToolImageId | null;
  selectedImageId: AvatarToolImageId | null;
  maximumImages: number;
  busy: boolean;
  fieldErrors: Readonly<Record<string, string>>;
  meaningPlaceholder: string;
  onSelectImage(imageId: AvatarToolImageId): void;
  onChooseInitialImage(imageId: AvatarToolImageId): void;
  onRemoveImage(imageId: AvatarToolImageId): void;
  onAddImage(file: File): void;
  onOpenAddImagePicker(event: ReactMouseEvent<HTMLInputElement>, title: string): void;
  onReplaceImage(imageId: AvatarToolImageId, file: File): void;
  onOpenReplaceImagePicker(
    event: ReactMouseEvent<HTMLInputElement>,
    imageId: AvatarToolImageId,
    title: string,
  ): void;
  onUpdateName(imageId: AvatarToolImageId, name: string): void;
  onUpdateMeaning(imageId: AvatarToolImageId, meaning: string): void;
};

type AvatarToolImageDimensions = {
  width: number;
  height: number;
};

function characterCount(value: string): number {
  return Array.from(value).length;
}

function formatLimit(bytes: number | undefined): string {
  if (!bytes) return '';
  return `${Math.round(bytes / (1024 * 1024))} MB`;
}

function fileNameFromResource(resource?: string): string {
  return resource?.replace(/\\/g, '/').split('/').pop() ?? '';
}

function imageDefaultName(index: number): string {
  return i18n('chat.avatarToolCreateToolImageNumber', 'Tool image {{number}}', {
    number: String(index + 1),
  });
}

function imageDisplayName(image: AvatarToolImageDraft, index: number): string {
  return image.name?.trim() || imageDefaultName(index);
}

function fitMeaningTextarea(textarea: HTMLTextAreaElement): void {
  const minimumHeight = 56;
  const maximumHeight = 96;
  textarea.style.height = '0px';
  const contentHeight = textarea.scrollHeight;
  textarea.style.height = `${Math.min(Math.max(contentHeight, minimumHeight), maximumHeight)}px`;
  textarea.style.overflowY = contentHeight > maximumHeight ? 'auto' : 'hidden';
}

function ImageFieldError({ message }: { message?: string }) {
  return message ? <small className="avatar-tool-create-field-error" role="alert">{message}</small> : null;
}

function ImagePreview({
  image,
  alt,
  onDimensionsChange,
}: {
  image: AvatarToolImageDraft;
  alt: string;
  onDimensionsChange(imageId: AvatarToolImageId, dimensions: AvatarToolImageDimensions | null): void;
}) {
  const [objectUrl, setObjectUrl] = useState('');

  useEffect(() => {
    onDimensionsChange(image.id, null);
    if (!image.image || typeof URL.createObjectURL !== 'function') {
      setObjectUrl('');
      return undefined;
    }
    const nextUrl = URL.createObjectURL(image.image);
    setObjectUrl(nextUrl);
    return () => URL.revokeObjectURL(nextUrl);
  }, [image.id, image.image, image.imageUrl, onDimensionsChange]);

  const source = objectUrl || image.imageUrl;
  return source ? (
    <img
      src={source}
      alt={alt}
      onLoad={(event) => {
        const { naturalWidth: width, naturalHeight: height } = event.currentTarget;
        onDimensionsChange(image.id, width > 0 && height > 0 ? { width, height } : null);
      }}
      onError={() => onDimensionsChange(image.id, null)}
    />
  ) : <span aria-hidden="true">＋</span>;
}

export default function AvatarToolImagePanel({
  limits,
  images,
  initialImageId,
  selectedImageId,
  maximumImages,
  busy,
  fieldErrors,
  meaningPlaceholder,
  onSelectImage,
  onChooseInitialImage,
  onRemoveImage,
  onAddImage,
  onOpenAddImagePicker,
  onReplaceImage,
  onOpenReplaceImagePicker,
  onUpdateName,
  onUpdateMeaning,
}: AvatarToolImagePanelProps) {
  const imageMeaningTextareaRef = useRef<HTMLTextAreaElement | null>(null);
  const [imageDimensions, setImageDimensions] = useState<Readonly<Partial<Record<
    AvatarToolImageId,
    AvatarToolImageDimensions
  >>>>({});
  const updateImageDimensions = useCallback((
    imageId: AvatarToolImageId,
    dimensions: AvatarToolImageDimensions | null,
  ) => {
    setImageDimensions((current) => {
      const previous = current[imageId];
      if (!dimensions) {
        if (!previous) return current;
        const next = { ...current };
        delete next[imageId];
        return next;
      }
      if (previous?.width === dimensions.width && previous.height === dimensions.height) return current;
      return { ...current, [imageId]: dimensions };
    });
  }, []);
  const selectedImage = useMemo(
    () => images.find(image => image.id === selectedImageId) ?? null,
    [images, selectedImageId],
  );
  const selectedImageIndex = selectedImage
    ? images.findIndex(image => image.id === selectedImage.id)
    : -1;
  const selectedImageDefaultName = selectedImage ? imageDefaultName(selectedImageIndex) : '';
  const selectedImageLabel = selectedImage
    ? imageDisplayName(selectedImage, selectedImageIndex)
    : '';
  const selectedImageFileName = selectedImage
    ? selectedImage.image?.name
      || fileNameFromResource(selectedImage.imageResource)
      || i18n('chat.avatarToolUpdateCurrentImage', 'Current image')
    : '';
  const selectedImageDimensions = selectedImage ? imageDimensions[selectedImage.id] : undefined;

  useLayoutEffect(() => {
    if (imageMeaningTextareaRef.current) fitMeaningTextarea(imageMeaningTextareaRef.current);
  }, [meaningPlaceholder, selectedImageId, selectedImage?.meaning]);

  return (
    <section className="avatar-tool-image-library" data-error-key="images">
      <header className="avatar-tool-image-library-heading">
        <div>
          <strong>{i18n('chat.avatarToolCreateToolImages', 'Tool images')}</strong>
          <small>{i18n(
            'chat.avatarToolCreateToolImagesHint',
            'Choose an initial image.',
          )}</small>
        </div>
        <span>{images.length}/{maximumImages}</span>
      </header>
      <ImageFieldError message={fieldErrors.images} />
      <ImageFieldError message={fieldErrors.initial_image} />

      <div className="avatar-tool-image-grid" role="list">
        {images.map((image, index) => {
          const imageLabel = imageDisplayName(image, index);
          const initial = image.id === initialImageId;
          const selected = image.id === selectedImageId;
          const meaning = image.meaning.replace(/\r\n?/g, '\n').trim();
          return (
            <article
              className={`avatar-tool-image-card${selected ? ' is-selected' : ''}${initial ? ' is-initial' : ''}`}
              key={image.id}
              role="listitem"
              data-avatar-tool-image-id={image.id}
              data-avatar-tool-image-initial={initial ? 'true' : 'false'}
            >
              <button
                className="avatar-tool-image-card-select"
                type="button"
                aria-label={i18n('chat.avatarToolCreateEditToolImage', 'Edit {{image}}', { image: imageLabel })}
                aria-pressed={selected}
                disabled={busy}
                onClick={() => onSelectImage(image.id)}
              >
                <span className="avatar-tool-image-card-preview">
                  <ImagePreview
                    image={image}
                    alt={imageLabel}
                    onDimensionsChange={updateImageDimensions}
                  />
                  {initial ? (
                    <span
                      className="avatar-tool-image-card-initial-badge"
                      aria-hidden="true"
                      title={i18n('chat.avatarToolCreateInitialImage', 'Initial image')}
                    />
                  ) : null}
                </span>
                <span className="avatar-tool-image-card-copy">
                  <strong>{imageLabel}</strong>
                  <span className={meaning ? '' : 'is-empty'}>
                    {meaning || i18n('chat.avatarToolCreateNoImageMeaning', 'No interaction description')}
                  </span>
                </span>
              </button>
            </article>
          );
        })}

        {images.length < maximumImages ? (
          <label className={`avatar-tool-image-add-card${busy ? ' is-disabled' : ''}`} role="listitem">
            <input
              type="file"
              accept="image/png,.png"
              aria-label={i18n('chat.avatarToolCreateAddToolImage', 'Add tool image')}
              disabled={busy}
              onClick={(event) => onOpenAddImagePicker(
                event,
                i18n('chat.avatarToolCreateAddToolImage', 'Add tool image'),
              )}
              onChange={(event) => {
                const file = event.target.files?.[0];
                event.target.value = '';
                if (file) onAddImage(file);
              }}
            />
            <span aria-hidden="true">＋</span>
            <strong>{i18n('chat.avatarToolCreateAddToolImage', 'Add tool image')}</strong>
            <small>{limits ? i18n(
              'chat.avatarToolCreateImageLimit',
              'PNG, up to {{size}} per image',
              { size: formatLimit(limits.maxImageBytes) },
            ) : 'PNG'}</small>
          </label>
        ) : null}
      </div>

      {selectedImage ? (
        <section
          className="avatar-tool-image-detail"
          data-error-key={`image_meaning:${selectedImage.id}`}
          data-avatar-tool-selected-image-id={selectedImage.id}
        >
          <div className="avatar-tool-image-detail-heading">
            <label
              className="avatar-tool-image-detail-identity avatar-tool-editable-name"
              title={i18n('chat.avatarToolEditableNameHint', 'Click the name to rename')}
              data-error-key={`image_name:${selectedImage.id}`}
            >
              <input
                value={selectedImage.name ?? ''}
                aria-label={i18n('chat.avatarToolImageName', 'Image name')}
                aria-invalid={fieldErrors[`image_name:${selectedImage.id}`] ? 'true' : undefined}
                disabled={busy}
                onChange={event => onUpdateName(selectedImage.id, event.target.value)}
                placeholder={selectedImageDefaultName}
              />
              <img
                className="avatar-tool-editable-name-icon"
                src="/static/icons/edit.png"
                alt=""
                aria-hidden="true"
              />
            </label>
            <div className="avatar-tool-image-detail-heading-actions">
              <label className="avatar-tool-image-detail-initial-control">
                <input
                  type="radio"
                  name="avatar-tool-initial-image"
                  checked={selectedImage.id === initialImageId}
                  disabled={busy}
                  onChange={() => onChooseInitialImage(selectedImage.id)}
                />
                <span>{i18n('chat.avatarToolCreateInitialImage', 'Initial image')}</span>
              </label>
              <button
                className="avatar-tool-image-detail-remove"
                type="button"
                disabled={busy}
                onClick={() => onRemoveImage(selectedImage.id)}
              >
                {i18n('chat.avatarToolCreateRemoveImage', 'Remove image')}
              </button>
            </div>
          </div>
          <ImageFieldError message={fieldErrors[`image_name:${selectedImage.id}`]} />
          <ImageFieldError message={fieldErrors[`image_remove:${selectedImage.id}`]} />
          <label
            className={`avatar-tool-create-file-control avatar-tool-image-detail-replace${busy ? ' is-disabled' : ''}`}
            data-error-key={`image_file:${selectedImage.id}`}
            aria-invalid={fieldErrors[`image_file:${selectedImage.id}`] ? 'true' : undefined}
          >
            <input
              className="avatar-tool-create-file-input"
              type="file"
              accept="image/png,.png"
              aria-label={`${i18n('chat.avatarToolUpdateChooseImage', 'Change image')}: ${selectedImageLabel}`}
              disabled={busy}
              onClick={(event) => onOpenReplaceImagePicker(
                event,
                selectedImage.id,
                `${i18n('chat.avatarToolUpdateChooseImage', 'Change image')}: ${selectedImageLabel}`,
              )}
              onChange={(event) => {
                const file = event.target.files?.[0];
                event.target.value = '';
                if (file) onReplaceImage(selectedImage.id, file);
              }}
            />
            <span className="avatar-tool-create-file-button">
              {i18n('chat.avatarToolUpdateChooseImage', 'Change image')}
            </span>
            <span className="avatar-tool-create-file-name has-file" title={selectedImageFileName}>
              {selectedImageFileName}
            </span>
            {selectedImageDimensions ? (
              <span className="avatar-tool-image-detail-dimensions">
                {selectedImageDimensions.width} px · {selectedImageDimensions.height} px
              </span>
            ) : null}
          </label>
          <ImageFieldError message={fieldErrors[`image_file:${selectedImage.id}`]} />
          <label className="avatar-tool-create-field avatar-tool-image-meaning-field">
            <span className="avatar-tool-image-meaning-heading">
              <span>{i18n('chat.avatarToolCreateImageMeaningOptional', 'Interaction description (optional)')}</span>
              <small>{characterCount(selectedImage.meaning)}/{limits?.maxMeaningChars ?? 100}</small>
            </span>
            <textarea
              ref={imageMeaningTextareaRef}
              value={selectedImage.meaning}
              aria-label={i18n(
                'chat.avatarToolCreateImageMeaningForImage',
                'Interaction description for tool image {{number}} (optional)',
                { number: String(selectedImageIndex + 1) },
              )}
              aria-invalid={fieldErrors[`image_meaning:${selectedImage.id}`] ? 'true' : undefined}
              disabled={busy}
              onChange={(event) => {
                onUpdateMeaning(selectedImage.id, event.target.value);
                fitMeaningTextarea(event.currentTarget);
              }}
              placeholder={meaningPlaceholder}
              rows={2}
            />
          </label>
          <ImageFieldError message={fieldErrors[`image_meaning:${selectedImage.id}`]} />
        </section>
      ) : null}
    </section>
  );
}
