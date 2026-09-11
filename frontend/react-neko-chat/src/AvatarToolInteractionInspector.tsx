import { useEffect, useState } from 'react';
import { i18n } from './i18n';
import {
  duplicateAvatarToolInteractionDraft,
  getAvatarToolInteractionOrdinal,
  type AvatarToolInteractionDraft,
  type AvatarToolInteractionId,
  type AvatarToolInteractionValidationIssue,
  type AvatarToolImageAction,
} from './avatar-tools/avatarToolInteractionEditorModel';
import { useAvatarToolInteractionEditor } from './avatar-tools/AvatarToolInteractionEditorContext';
import type { AvatarToolImageDraft, AvatarToolImageId } from './avatar-tools/avatarToolEditorModel';
import {
  findDuplicateAvatarToolNameIds,
  getAvatarToolNameValidationError,
} from './avatar-tools/avatarToolNames';

type AvatarToolInteractionInspectorProps = {
  images: AvatarToolImageDraft[];
  busy?: boolean;
  maxDelayMs: number;
  maxNameChars: number;
};

function defaultInteractionLabel(
  state: ReturnType<typeof useAvatarToolInteractionEditor>['state'],
  item: AvatarToolInteractionDraft,
): string {
  const number = getAvatarToolInteractionOrdinal(state, item.id);
  return item.kind === 'mouse-click'
    ? i18n('chat.avatarToolInteractionClickNumber', 'Mouse click {{number}}', { number: String(number) })
    : i18n('chat.avatarToolInteractionDelayNumber', 'Delay {{number}}', { number: String(number) });
}

function interactionLabel(
  state: ReturnType<typeof useAvatarToolInteractionEditor>['state'],
  interactionId: AvatarToolInteractionId,
): string {
  const item = state.items.find(candidate => candidate.id === interactionId);
  if (!item) return i18n('chat.avatarToolInteractionUnknown', 'Unknown interaction');
  return item.name?.trim() || defaultInteractionLabel(state, item);
}

function imageLabel(image: AvatarToolImageDraft, number: number): string {
  return image.name?.trim() || i18n('chat.avatarToolInteractionImageNumber', 'Tool image {{number}}', {
    number: String(number),
  });
}

function waitingPositionLabel(
  state: ReturnType<typeof useAvatarToolInteractionEditor>['state'],
  waitingAfterId?: AvatarToolInteractionId,
): string {
  return waitingAfterId
    ? i18n('chat.avatarToolInteractionAfterLabel', 'after {{interaction}}', {
      interaction: interactionLabel(state, waitingAfterId),
    })
    : i18n('chat.avatarToolInteractionStartPosition', 'after the initial image appears');
}

export function formatAvatarToolInteractionIssue(
  state: ReturnType<typeof useAvatarToolInteractionEditor>['state'],
  issue: AvatarToolInteractionValidationIssue,
): string {
  const label = issue.interactionId
    ? interactionLabel(state, issue.interactionId)
    : i18n('chat.avatarToolInteractionFlow', 'Interaction flow');
  switch (issue.code) {
    case 'initial-connection-required':
      return i18n(
        'chat.avatarToolInitialConnectionRequired',
        'Connect the initial image to at least one interaction.',
      );
    case 'duplicate-name':
      return i18n(
        'chat.avatarToolInteractionNameDuplicate',
        '“{{name}}” is already used by another interaction. Choose a different name.',
        { name: label },
      );
    case 'name-too-long':
      return i18n(
        'chat.avatarToolEditableNameLengthError',
        'The name must be no more than {{count}} characters.',
        { count: String(issue.maxNameChars ?? '') },
      );
    case 'name-invalid':
      return i18n(
        'chat.avatarToolEditableNameInvalidError',
        'Use letters, numbers, spaces, “-”, or “_” in the name.',
      );
    case 'action-image-missing':
      return i18n('chat.avatarToolInteractionActionImageMissing', '{{interaction}} uses an image that is no longer available.', {
        interaction: label,
      });
    case 'delay-invalid':
      return i18n('chat.avatarToolInteractionDelayInvalid', '{{interaction}} needs a positive wait time.', {
        interaction: label,
      });
    case 'delay-image-missing':
      return i18n('chat.avatarToolInteractionDelayImageMissing', 'Choose whether {{interaction}} keeps the current image or shows another image.', {
        interaction: label,
      });
    case 'link-endpoint-missing':
      return i18n('chat.avatarToolInteractionLinkMissing', 'A connection points to an interaction that no longer exists.');
    case 'duplicate-link':
      return i18n('chat.avatarToolInteractionDuplicateLink', 'The same two interactions are connected more than once.');
    case 'unreachable':
      return i18n('chat.avatarToolInteractionUnreachable', '{{interaction}} cannot be reached from the initial image.', {
        interaction: label,
      });
    case 'ambiguous-click':
      return i18n(
        'chat.avatarToolInteractionAmbiguousClick',
        '{{interaction}} conflicts with another mouse click {{position}}.',
        { interaction: label, position: waitingPositionLabel(state, issue.waitingAfterId) },
      );
    case 'ambiguous-delay':
      return i18n(
        'chat.avatarToolInteractionAmbiguousDelay',
        '{{interaction}} conflicts with another {{delay}} ms delay {{position}}.',
        {
          interaction: label,
          delay: String(issue.delayMs ?? ''),
          position: waitingPositionLabel(state, issue.waitingAfterId),
        },
      );
    default:
      return i18n('chat.avatarToolInteractionInvalid', 'This interaction needs attention.');
  }
}

function ImageActionSelect({
  label,
  action,
  images,
  invalid,
  disabled,
  onChange,
}: {
  label: string;
  action: AvatarToolImageAction;
  images: AvatarToolImageDraft[];
  invalid: boolean;
  disabled: boolean;
  onChange(action: AvatarToolImageAction): void;
}) {
  const selectedImage = action.kind === 'show'
    ? images.find(image => image.id === action.imageId) ?? null
    : null;
  const selectedImageNumber = selectedImage
    ? images.findIndex(image => image.id === selectedImage.id) + 1
    : 0;
  return (
    <label className="avatar-tool-interaction-field">
      <span>{label}</span>
      <div className="avatar-tool-interaction-image-choice">
        <InteractionImagePreview image={selectedImage} imageNumber={selectedImageNumber} />
        <select
          value={action.kind === 'keep' ? 'keep' : action.imageId}
          aria-invalid={invalid ? 'true' : undefined}
          disabled={disabled}
          onChange={(event) => onChange(event.target.value === 'keep'
            ? { kind: 'keep' }
            : { kind: 'show', imageId: event.target.value as AvatarToolImageId })}
        >
          <option value="keep">{i18n('chat.avatarToolInteractionKeepImage', 'Keep image')}</option>
          {images.map((image, index) => (
            <option key={image.id} value={image.id}>
              {imageLabel(image, index + 1)}{image.meaning.trim() ? ` — ${image.meaning.trim()}` : ''}
            </option>
          ))}
        </select>
      </div>
    </label>
  );
}

function InteractionImagePreview({
  image,
  imageNumber,
}: {
  image: AvatarToolImageDraft | null;
  imageNumber: number;
}) {
  const [objectUrl, setObjectUrl] = useState('');

  useEffect(() => {
    if (!image?.image || typeof URL.createObjectURL !== 'function') {
      setObjectUrl('');
      return undefined;
    }
    const nextUrl = URL.createObjectURL(image.image);
    setObjectUrl(nextUrl);
    return () => URL.revokeObjectURL(nextUrl);
  }, [image?.image]);

  const source = objectUrl || image?.imageUrl;
  const previewLabel = image
    ? imageLabel(image, imageNumber)
    : i18n('chat.avatarToolInteractionKeepImage', 'Keep image');

  return (
    <span className={`avatar-tool-interaction-image-preview${source ? ' has-image' : ''}`}>
      {source ? (
        <img src={source} alt={previewLabel} />
      ) : (
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <rect x="5" y="7" width="12" height="10" rx="2" />
          <path d="M8 7V5h11v10h-2" />
        </svg>
      )}
    </span>
  );
}

function InteractionErrorSummary() {
  const { state, dispatch, issues } = useAvatarToolInteractionEditor();
  if (issues.length === 0) return null;
  return (
    <section className="avatar-tool-interaction-error-summary" role="alert">
      <strong>{i18n(
        'chat.avatarToolInteractionErrorSummary',
        'Interaction issues: {{count}}',
        { count: String(issues.length) },
      )}</strong>
      <ul>
        {issues.map((issue) => {
          const label = formatAvatarToolInteractionIssue(state, issue);
          return (
            <li key={issue.key}>
              {issue.interactionId || issue.linkId ? (
                <button type="button" onClick={() => {
                  if (issue.interactionId) {
                    dispatch({ type: 'select-interaction', interactionId: issue.interactionId });
                  } else if (issue.linkId) {
                    dispatch({ type: 'select-link', linkId: issue.linkId });
                  }
                }}>
                  {label}
                </button>
              ) : <span>{label}</span>}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function FieldIssue({
  item,
  field,
}: {
  item: AvatarToolInteractionDraft;
  field: AvatarToolInteractionValidationIssue['field'];
}) {
  const { state, issues } = useAvatarToolInteractionEditor();
  const issue = issues.find(candidate => candidate.interactionId === item.id && candidate.field === field);
  return issue
    ? <small className="avatar-tool-interaction-field-error">{formatAvatarToolInteractionIssue(state, issue)}</small>
    : null;
}

export default function AvatarToolInteractionInspector({
  images,
  busy = false,
  maxDelayMs,
  maxNameChars,
}: AvatarToolInteractionInspectorProps) {
  const { state, dispatch, issues } = useAvatarToolInteractionEditor();
  const selectedItem = state.items.find(item => item.id === state.selectedInteractionId) ?? null;
  const selectedLink = state.links.find(link => link.id === state.selectedLinkId) ?? null;
  const selectedInitialTarget = state.selectedInitialLinkTargetId
    ? state.items.find(item => item.id === state.selectedInitialLinkTargetId) ?? null
    : null;

  if (selectedInitialTarget) {
    return (
      <div className="avatar-tool-interaction-inspector">
        <InteractionErrorSummary />
        <section className="avatar-tool-interaction-inspector-card">
          <div className="avatar-tool-interaction-inspector-heading">
            <div>
              <span>{i18n('chat.avatarToolInitialConnection', 'Initial image connection')}</span>
              <strong>{i18n('chat.avatarToolInteractionConnectionFromTo', '{{from}} → {{to}}', {
                from: i18n('chat.avatarToolInitialImageNode', 'Initial image'),
                to: interactionLabel(state, selectedInitialTarget.id),
              })}</strong>
            </div>
            <button
              className="avatar-tool-interaction-danger-action"
              type="button"
              disabled={busy}
              onClick={() => dispatch({
                type: 'remove-initial-link',
                interactionId: selectedInitialTarget.id,
              })}
            >
              {i18n('chat.avatarToolInteractionRemoveConnection', 'Remove connection')}
            </button>
          </div>
          <p>{i18n(
            'chat.avatarToolInitialConnectionHint',
            'When the tool starts, this interaction is available while the initial image is shown.',
          )}</p>
        </section>
      </div>
    );
  }

  if (selectedLink) {
    const connectionIssue = issues.find(issue => issue.linkId === selectedLink.id);
    return (
      <div className="avatar-tool-interaction-inspector">
        <InteractionErrorSummary />
        <section className="avatar-tool-interaction-inspector-card">
          <div className="avatar-tool-interaction-inspector-heading">
            <div>
              <span>{i18n('chat.avatarToolInteractionConnection', 'Connection')}</span>
              <strong>{i18n('chat.avatarToolInteractionConnectionFromTo', '{{from}} → {{to}}', {
                from: interactionLabel(state, selectedLink.from),
                to: interactionLabel(state, selectedLink.to),
              })}</strong>
            </div>
            <button
              className="avatar-tool-interaction-danger-action"
              type="button"
              disabled={busy}
              onClick={() => dispatch({ type: 'remove-link', linkId: selectedLink.id })}
            >
              {i18n('chat.avatarToolInteractionRemoveConnection', 'Remove connection')}
            </button>
          </div>
          <p>{i18n(
            'chat.avatarToolInteractionConnectionHint',
            'After the first interaction completes, the tool waits for the connected interaction.',
          )}</p>
          {connectionIssue ? (
            <small className="avatar-tool-interaction-field-error">
              {formatAvatarToolInteractionIssue(state, connectionIssue)}
            </small>
          ) : null}
        </section>
      </div>
    );
  }

  if (!selectedItem) {
    return (
      <div className="avatar-tool-interaction-inspector">
        <InteractionErrorSummary />
        <div className="avatar-tool-interaction-inspector-empty">
          <span aria-hidden="true">
            <svg viewBox="0 0 24 24">
              <rect x="4" y="5" width="16" height="14" rx="3" />
              <path d="M8 9h8M8 13h5" />
            </svg>
          </span>
          <strong>{i18n('chat.avatarToolInteractionSelectTitle', 'Select an interaction')}</strong>
          <p>{i18n(
            'chat.avatarToolInteractionSelectHint',
            'Add or select a complete interaction on the canvas to edit it here.',
          )}</p>
        </div>
      </div>
    );
  }

  const defaultTitle = defaultInteractionLabel(state, selectedItem);
  const duplicateNameIds = findDuplicateAvatarToolNameIds(
    state.items,
    item => item.name?.trim() || defaultInteractionLabel(state, item),
  );
  const duplicateName = duplicateNameIds.has(selectedItem.id);
  const nameValidationError = getAvatarToolNameValidationError(
    selectedItem.name ?? '',
    maxNameChars,
  );
  const nameError = nameValidationError === 'too-long'
    ? i18n(
      'chat.avatarToolEditableNameLengthError',
      'The name must be no more than {{count}} characters.',
      { count: String(maxNameChars) },
    )
    : nameValidationError === 'invalid'
      ? i18n(
        'chat.avatarToolEditableNameInvalidError',
        'Use letters, numbers, spaces, “-”, or “_” in the name.',
      )
      : duplicateName
        ? i18n(
          'chat.avatarToolInteractionNameDuplicate',
          '“{{name}}” is already used by another interaction. Choose a different name.',
          { name: selectedItem.name?.trim() || defaultTitle },
        )
        : '';
  const delayCompleteAction = selectedItem.kind === 'after' ? selectedItem.complete : null;
  const delayTargetImage = delayCompleteAction?.kind === 'show'
    ? images.find(image => image.id === delayCompleteAction.imageId) ?? null
    : null;
  const delayTargetImageNumber = delayTargetImage
    ? images.findIndex(image => image.id === delayTargetImage.id) + 1
    : 0;
  const nonFieldIssue = issues.find(issue => issue.interactionId === selectedItem.id && !issue.field);
  return (
    <div className="avatar-tool-interaction-inspector">
      <InteractionErrorSummary />
      <section className="avatar-tool-interaction-inspector-card" data-error-key={`interaction:${selectedItem.id}`}>
        <div className="avatar-tool-interaction-inspector-heading">
          <div>
            <span>{selectedItem.kind === 'mouse-click'
              ? i18n('chat.avatarToolInteractionMouseClick', 'Mouse click')
              : i18n('chat.avatarToolInteractionAfterTime', 'Delayed switch')}</span>
            <label
              className="avatar-tool-editable-name"
              title={i18n('chat.avatarToolEditableNameHint', 'Click the name to rename')}
            >
              <input
                className="avatar-tool-interaction-name-input"
                value={selectedItem.name ?? ''}
                aria-label={i18n('chat.avatarToolInteractionName', 'Interaction name')}
                aria-invalid={nameError ? 'true' : undefined}
                disabled={busy}
                onChange={event => dispatch({
                  type: 'update-name',
                  interactionId: selectedItem.id,
                  name: event.target.value,
                })}
                placeholder={defaultTitle}
              />
              <img
                className="avatar-tool-editable-name-icon"
                src="/static/icons/edit.png"
                alt=""
                aria-hidden="true"
              />
            </label>
            {nameError ? (
              <small className="avatar-tool-interaction-field-error">
                {nameError}
              </small>
            ) : null}
          </div>
          <div className="avatar-tool-interaction-inspector-actions">
            <button
              type="button"
              disabled={busy}
              onClick={() => dispatch({
                type: 'duplicate-interaction',
                sourceId: selectedItem.id,
                duplicate: duplicateAvatarToolInteractionDraft(selectedItem, state.items),
              })}
            >
              {i18n('chat.avatarToolInteractionDuplicate', 'Duplicate')}
            </button>
            <button
              className="avatar-tool-interaction-danger-action"
              type="button"
              disabled={busy}
              onClick={() => dispatch({ type: 'remove-interaction', interactionId: selectedItem.id })}
            >
              {i18n('chat.avatarToolInteractionRemove', 'Remove')}
            </button>
          </div>
        </div>

        {selectedItem.kind === 'mouse-click' ? (
          <div className="avatar-tool-interaction-fields">
            <ImageActionSelect
              label={i18n('chat.avatarToolInteractionPressTiming', 'Press')}
              action={selectedItem.press}
              images={images}
              invalid={issues.some(issue => issue.interactionId === selectedItem.id && issue.field === 'press')}
              disabled={busy}
              onChange={action => dispatch({
                type: 'update-click-action',
                interactionId: selectedItem.id,
                timing: 'press',
                action,
              })}
            />
            <FieldIssue item={selectedItem} field="press" />
            <ImageActionSelect
              label={i18n('chat.avatarToolInteractionReleaseTiming', 'Release')}
              action={selectedItem.release}
              images={images}
              invalid={issues.some(issue => issue.interactionId === selectedItem.id && issue.field === 'release')}
              disabled={busy}
              onChange={action => dispatch({
                type: 'update-click-action',
                interactionId: selectedItem.id,
                timing: 'release',
                action,
              })}
            />
            <FieldIssue item={selectedItem} field="release" />
            <p>{i18n(
              'chat.avatarToolInteractionClickHint',
              'Press and release are two image moments inside the same mouse click. Connections leave the complete click.',
            )}</p>
          </div>
        ) : (
          <div className="avatar-tool-interaction-fields">
            <label className="avatar-tool-interaction-field">
              <span>{i18n('chat.avatarToolInteractionDelayDuration', 'Wait time')}</span>
              <div className="avatar-tool-interaction-number-input">
                <input
                  type="number"
                  min="1"
                  max={maxDelayMs}
                  step="1"
                  inputMode="numeric"
                  aria-label={i18n('chat.avatarToolInteractionDelayDuration', 'Wait time')}
                  value={selectedItem.delayMs}
                  aria-invalid={issues.some(issue => issue.interactionId === selectedItem.id && issue.field === 'delayMs') ? 'true' : undefined}
                  disabled={busy}
                  onChange={event => dispatch({
                    type: 'update-delay',
                    interactionId: selectedItem.id,
                    delayMs: event.target.value,
                  })}
                />
                <span>{i18n('chat.avatarToolInteractionMilliseconds', 'ms')}</span>
              </div>
            </label>
            <FieldIssue item={selectedItem} field="delayMs" />
            <label className="avatar-tool-interaction-field">
              <span>{i18n('chat.avatarToolInteractionTargetImage', 'Switch to')}</span>
              <div className="avatar-tool-interaction-image-choice">
                <InteractionImagePreview
                  image={delayTargetImage}
                  imageNumber={delayTargetImageNumber}
                />
                <select
                  value={delayCompleteAction?.kind === 'keep'
                    ? 'keep'
                    : delayCompleteAction?.imageId ?? ''}
                  aria-invalid={issues.some(issue => issue.interactionId === selectedItem.id && issue.field === 'complete') ? 'true' : undefined}
                  disabled={busy}
                  onChange={event => dispatch({
                    type: 'update-delay-action',
                    interactionId: selectedItem.id,
                    action: event.target.value === 'keep'
                      ? { kind: 'keep' }
                      : event.target.value
                        ? { kind: 'show', imageId: event.target.value as AvatarToolImageId }
                        : null,
                  })}
                >
                  <option value="">{i18n('chat.avatarToolInteractionChooseImage', 'Choose an image')}</option>
                  <option value="keep">{i18n('chat.avatarToolInteractionKeepImage', 'Keep image')}</option>
                  {images.map((image, index) => (
                    <option key={image.id} value={image.id}>
                      {imageLabel(image, index + 1)}{image.meaning.trim() ? ` — ${image.meaning.trim()}` : ''}
                    </option>
                  ))}
                </select>
              </div>
            </label>
            <FieldIssue item={selectedItem} field="complete" />
          </div>
        )}
        {nonFieldIssue ? (
          <small className="avatar-tool-interaction-field-error">
            {formatAvatarToolInteractionIssue(state, nonFieldIssue)}
          </small>
        ) : null}
      </section>
    </div>
  );
}
