import type { AvatarToolImageId } from './avatarToolEditorModel';

export type AvatarToolInteractionId = `ix-${string}`;
export type AvatarToolInteractionLinkId = `link-${string}`;
export type AvatarToolConnectionSide = 'top' | 'right' | 'bottom' | 'left';

export type AvatarToolConnectionSides = {
  sourceSide: AvatarToolConnectionSide;
  targetSide: AvatarToolConnectionSide;
};

export type AvatarToolImageAction =
  | { kind: 'keep' }
  | { kind: 'show'; imageId: AvatarToolImageId };

type AvatarToolInteractionBase = {
  id: AvatarToolInteractionId;
  name?: string;
  position: { x: number; y: number };
};

export type AvatarToolClickInteractionDraft = AvatarToolInteractionBase & {
  kind: 'mouse-click';
  press: AvatarToolImageAction;
  release: AvatarToolImageAction;
};

export type AvatarToolDelayInteractionDraft = AvatarToolInteractionBase & {
  kind: 'after';
  delayMs: string;
  complete: AvatarToolImageAction | null;
};

export type AvatarToolInteractionDraft =
  | AvatarToolClickInteractionDraft
  | AvatarToolDelayInteractionDraft;

export type AvatarToolInteractionLinkDraft = {
  id: AvatarToolInteractionLinkId;
  from: AvatarToolInteractionId;
  to: AvatarToolInteractionId;
  sourceSide: AvatarToolConnectionSide;
  targetSide: AvatarToolConnectionSide;
};

export type AvatarToolInteractionEditorState = {
  items: AvatarToolInteractionDraft[];
  links: AvatarToolInteractionLinkDraft[];
  initialImageTargetIds: AvatarToolInteractionId[];
  initialImageLinkSides: Partial<Record<AvatarToolInteractionId, AvatarToolConnectionSides>>;
  initialImagePosition: { x: number; y: number };
  selectedInteractionId: AvatarToolInteractionId | null;
  selectedLinkId: AvatarToolInteractionLinkId | null;
  selectedInitialLinkTargetId: AvatarToolInteractionId | null;
};

export type AvatarToolInteractionPresetKind = 'press-swap' | 'click-advance' | 'cycle-stop';

export type AvatarToolInteractionPresetRequirements = {
  interactionCount: number;
  totalLinkCount: number;
};

export type AvatarToolInteractionValidationCode =
  | 'initial-connection-required'
  | 'duplicate-name'
  | 'name-too-long'
  | 'name-invalid'
  | 'action-image-missing'
  | 'delay-invalid'
  | 'delay-image-missing'
  | 'link-endpoint-missing'
  | 'duplicate-link'
  | 'unreachable'
  | 'ambiguous-click'
  | 'ambiguous-delay';

export type AvatarToolInteractionValidationIssue = {
  key: string;
  code: AvatarToolInteractionValidationCode;
  interactionId?: AvatarToolInteractionId;
  linkId?: AvatarToolInteractionLinkId;
  field?: 'name' | 'press' | 'release' | 'delayMs' | 'complete' | 'initialConnection' | 'connection';
  waitingAfterId?: AvatarToolInteractionId;
  delayMs?: number;
  maxNameChars?: number;
};

export type AvatarToolInteractionEditorAction =
  | { type: 'reset'; state: AvatarToolInteractionEditorState }
  | { type: 'add'; interaction: AvatarToolInteractionDraft }
  | { type: 'select-interaction'; interactionId: AvatarToolInteractionId | null }
  | { type: 'select-link'; linkId: AvatarToolInteractionLinkId | null }
  | { type: 'select-initial-link'; interactionId: AvatarToolInteractionId | null }
  | { type: 'move'; interactionId: AvatarToolInteractionId; position: { x: number; y: number } }
  | { type: 'move-initial-image'; position: { x: number; y: number } }
  | { type: 'update-name'; interactionId: AvatarToolInteractionId; name: string }
  | { type: 'update-click-action'; interactionId: AvatarToolInteractionId; timing: 'press' | 'release'; action: AvatarToolImageAction }
  | { type: 'update-delay'; interactionId: AvatarToolInteractionId; delayMs: string }
  | { type: 'update-delay-action'; interactionId: AvatarToolInteractionId; action: AvatarToolImageAction | null }
  | {
    type: 'connect-initial-image';
    interactionId: AvatarToolInteractionId;
    sourceSide: AvatarToolConnectionSide;
    targetSide: AvatarToolConnectionSide;
  }
  | { type: 'remove-initial-link'; interactionId: AvatarToolInteractionId }
  | { type: 'connect'; link: AvatarToolInteractionLinkDraft }
  | { type: 'remove-link'; linkId: AvatarToolInteractionLinkId }
  | { type: 'remove-interaction'; interactionId: AvatarToolInteractionId }
  | { type: 'duplicate-interaction'; sourceId: AvatarToolInteractionId; duplicate: AvatarToolInteractionDraft };
