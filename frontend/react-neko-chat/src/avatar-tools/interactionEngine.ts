import type {
  AvatarToolVariantId,
  AvatarToolDefinitionId,
  AvatarToolDefinitionSound,
  AvatarToolDefinitionEffect,
} from './catalog';
import { getAvatarToolRegistration } from './catalog';
import type { AvatarRangeHit } from './hitTesting';
import type { AvatarInteractionPayload } from './interactionContract';

type AvatarToolInteractionCommitFromPayload<Payload extends AvatarInteractionPayload> =
  Payload extends AvatarInteractionPayload
    ? Omit<Payload, 'interactionId' | 'target' | 'pointer' | 'timestamp'> & {
      clientX: number;
      clientY: number;
      timestamp?: number;
    }
    : never;

export type AvatarToolInteractionCommit =
  AvatarToolInteractionCommitFromPayload<AvatarInteractionPayload>;

export type AvatarToolSound = AvatarToolDefinitionSound;
export type AvatarToolEffect = AvatarToolDefinitionEffect;

export type AvatarToolCommand = {
  commit?: AvatarToolInteractionCommit;
  rangeVariant?: AvatarToolVariantId;
  outsideVariant?: AvatarToolVariantId;
  sound?: AvatarToolSound;
  effect?: AvatarToolEffect;
  effectMode?: string;
  pressFeedback?: 'until-pointer-release';
  resetOutsideVariantAfterMs?: number;
};

export type AvatarToolRuleContext = {
  toolId: AvatarToolDefinitionId;
  clientX: number;
  clientY: number;
  hit: AvatarRangeHit | null;
  rangeVariant: AvatarToolVariantId;
  outsideVariant: AvatarToolVariantId;
  interactionLocked: boolean;
  recordBurst(key: string, windowMs: number): number;
  random(): number;
};

export type AvatarToolRule = (context: AvatarToolRuleContext) => AvatarToolCommand;

export type AvatarToolRuleHandlers = {
  pointerDown: AvatarToolRule;
  commit: AvatarToolRule;
  pointerRelease: () => AvatarToolCommand;
};

export function resolveAvatarToolPointerDown(context: AvatarToolRuleContext): AvatarToolCommand {
  return getAvatarToolRegistration(context.toolId).handlers.pointerDown(context);
}

export function resolveAvatarToolCommit(context: AvatarToolRuleContext): AvatarToolCommand {
  if (context.interactionLocked) return {};
  return getAvatarToolRegistration(context.toolId).handlers.commit(context);
}

export function resolveAvatarToolPointerRelease(toolId: AvatarToolDefinitionId): AvatarToolCommand {
  return getAvatarToolRegistration(toolId).handlers.pointerRelease();
}
