import type { RefObject } from 'react';
import { i18n } from './i18n';
import {
  getAvatarToolRegistration,
  withAvatarToolAssetVersion,
  type BuiltInAvatarToolId,
  type AvatarToolVariantId,
} from './avatar-tools/catalog';
import type { AvatarToolInteractionPresetKind } from './avatar-tools/avatarToolInteractionEditorModel';

export const AVATAR_TOOL_PRESET_MENU_WIDTH = 386;

const AVATAR_TOOL_PRESET_KINDS: readonly AvatarToolInteractionPresetKind[] = [
  'press-swap',
  'click-advance',
  'cycle-stop',
];

const AVATAR_TOOL_PRESET_REFERENCE_TOOL_IDS: Record<
  AvatarToolInteractionPresetKind,
  BuiltInAvatarToolId
> = {
  'press-swap': 'fist',
  'click-advance': 'lollipop',
  'cycle-stop': 'rps',
};

const AVATAR_TOOL_PRESET_REFERENCE_VARIANTS: Record<
  AvatarToolInteractionPresetKind,
  readonly AvatarToolVariantId[]
> = {
  'press-swap': ['primary', 'secondary', 'primary'],
  'click-advance': ['primary', 'secondary', 'tertiary'],
  'cycle-stop': ['primary', 'secondary', 'tertiary'],
};

function PresetIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <rect x="2.5" y="3" width="6" height="5.5" rx="1.2" />
      <rect x="11.5" y="3" width="6" height="5.5" rx="1.2" />
      <rect x="2.5" y="11.5" width="6" height="5.5" rx="1.2" />
      <path d="M11.5 14.25h6M14.5 11.5V17" />
    </svg>
  );
}

function getAvatarToolDefinitionLabel(toolId: BuiltInAvatarToolId): string {
  const label = getAvatarToolRegistration(toolId).definition.label;
  return label.kind === 'literal' ? label.value : i18n(label.key, label.fallback);
}

function getPresetCopy(kind: AvatarToolInteractionPresetKind) {
  if (kind === 'press-swap') {
    return {
      label: i18n('chat.avatarToolPresetPressSwap', 'Press swap'),
      hint: i18n(
        'chat.avatarToolPresetPressSwapHint',
        'Switch images while pressing and switch back on release. Choose the images for both states after applying.',
      ),
      flow: i18n(
        'chat.avatarToolPresetPressSwapFlow',
        'Mouse click 1: Press switches the image → Release restores it',
      ),
      setup: i18n(
        'chat.avatarToolPresetPressSwapSetup',
        'In “Mouse click 1”, choose an image for “Press” and another for “Release”.',
      ),
    };
  }
  if (kind === 'click-advance') {
    return {
      label: i18n('chat.avatarToolPresetClickAdvance', 'Sequential switch'),
      hint: i18n(
        'chat.avatarToolPresetClickAdvanceHint',
        'Switch to the next image with each click, then stop after three steps. Choose an image for each step after applying.',
      ),
      flow: i18n(
        'chat.avatarToolPresetClickAdvanceFlow',
        'Mouse click 1 → Mouse click 2 → Mouse click 3 → end',
      ),
      setup: i18n(
        'chat.avatarToolPresetClickAdvanceSetup',
        'In “Mouse click 1”, “Mouse click 2”, and “Mouse click 3”, choose images 1, 2, and 3 for “Release”. The flow ends after “Mouse click 3”.',
      ),
    };
  }
  return {
    label: i18n('chat.avatarToolPresetCycleStop', 'Image cycle'),
    hint: i18n(
      'chat.avatarToolPresetCycleStopHint',
      'Cycle through images at your chosen interval. A click pauses the cycle for one interval, then it continues. Choose the images and interval after applying.',
    ),
    flow: i18n(
      'chat.avatarToolPresetCycleStopFlow',
      'Delayed switch 1 → Delayed switch 2 → Delayed switch 3 → repeat; Mouse click 1 → Delayed switch 4 → resume cycling',
    ),
    setup: i18n(
      'chat.avatarToolPresetCycleStopSetup',
      'Choose cycling images under “Switch to” in “Delayed switch 1”, “Delayed switch 2”, and “Delayed switch 3”, and choose the held image under “Release” in “Mouse click 1”. “Wait” in the first three delayed switches sets the cycling speed; “Wait” in “Delayed switch 4” sets the hold duration.',
    ),
  };
}

function AvatarToolPresetGuide({ kind, descriptionId }: {
  kind: AvatarToolInteractionPresetKind;
  descriptionId: string;
}) {
  const toolId = AVATAR_TOOL_PRESET_REFERENCE_TOOL_IDS[kind];
  const definition = getAvatarToolRegistration(toolId).definition;
  const toolLabel = getAvatarToolDefinitionLabel(toolId);
  const copy = getPresetCopy(kind);
  const variants = AVATAR_TOOL_PRESET_REFERENCE_VARIANTS[kind];

  return (
    <span id={descriptionId} className={`avatar-tool-preset-guide is-${kind}`}>
      <span className="avatar-tool-preset-guide-heading">
        <span className="avatar-tool-preset-reference-badge">{i18n(
          'chat.avatarToolPresetReferenceWith',
          'Reference: {{tool}}',
          { tool: toolLabel },
        )}</span>
        <span>{i18n('chat.avatarToolPresetFlowLabel', 'Flow example')}</span>
      </span>
      <span className="avatar-tool-preset-guide-images" aria-hidden="true">
        {variants.map((variant, index) => (
          <span key={`${variant}-${index}`}>
            <img
              src={withAvatarToolAssetVersion(definition.visual.variants[variant].iconImagePath)}
              alt=""
            />
            {index < variants.length - 1 ? (
              <span className="avatar-tool-preset-guide-arrow">→</span>
            ) : null}
          </span>
        ))}
        {kind === 'cycle-stop' ? <span className="avatar-tool-preset-guide-repeat">↻</span> : null}
      </span>
      <span className="avatar-tool-preset-guide-flow">{copy.flow}</span>
      <span className="avatar-tool-preset-guide-setup">
        <b>{i18n('chat.avatarToolPresetSetupLabel', 'Follow-up setup')}</b>
        <span>{copy.setup}</span>
      </span>
      {kind === 'cycle-stop' ? (
        <span className="avatar-tool-preset-guide-scope">{i18n(
          'chat.avatarToolPresetCycleScope',
          'This only references the cycling, click-to-hold, and delayed-resume rhythm of rock paper scissors. It does not include win/loss logic or result animation.',
        )}</span>
      ) : null}
    </span>
  );
}

type AvatarToolPresetPickerProps = {
  open: boolean;
  align: 'start' | 'end';
  pickerRef: RefObject<HTMLDivElement>;
  triggerRef: RefObject<HTMLButtonElement>;
  canApply(kind: AvatarToolInteractionPresetKind): boolean;
  onToggle(): void;
  onBlurAway(): void;
  onClose(): void;
  onApply(kind: AvatarToolInteractionPresetKind): void;
};

export function AvatarToolPresetPicker({
  open,
  align,
  pickerRef,
  triggerRef,
  canApply,
  onToggle,
  onBlurAway,
  onClose,
  onApply,
}: AvatarToolPresetPickerProps) {
  return (
    <div
      ref={pickerRef}
      className="avatar-tool-preset-picker"
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) onBlurAway();
      }}
      onKeyDown={(event) => {
        if (event.key !== 'Escape' || !open) return;
        event.preventDefault();
        event.stopPropagation();
        onClose();
      }}
    >
      <button
        ref={triggerRef}
        className="avatar-tool-preset-trigger"
        type="button"
        aria-expanded={open}
        aria-controls="avatar-tool-preset-menu"
        onClick={onToggle}
      >
        <span aria-hidden="true"><PresetIcon /></span>
        {i18n('chat.avatarToolPresetGroup', 'Presets')}
      </button>
      {open ? (
        <div
          id="avatar-tool-preset-menu"
          className={`avatar-tool-preset-menu is-${align}`}
          role="group"
          aria-label={i18n('chat.avatarToolPresetGroup', 'Presets')}
        >
          {AVATAR_TOOL_PRESET_KINDS.map((kind) => {
            const copy = getPresetCopy(kind);
            const descriptionId = `avatar-tool-preset-${kind}-guide`;
            return (
              <button
                key={kind}
                className="avatar-tool-preset-card"
                type="button"
                disabled={!canApply(kind)}
                aria-label={copy.label}
                aria-describedby={descriptionId}
                title={copy.hint}
                onClick={() => onApply(kind)}
              >
                <strong>{copy.label}</strong>
                <span>{copy.hint}</span>
                <AvatarToolPresetGuide kind={kind} descriptionId={descriptionId} />
              </button>
            );
          })}
          <p className="avatar-tool-preset-reference-notice">{i18n(
            'chat.avatarToolPresetReferenceNotice',
            'The examples only explain the flow. Applying a preset adds an editable flow without copying the reference images or choosing node images for you.',
          )}</p>
        </div>
      ) : null}
    </div>
  );
}
