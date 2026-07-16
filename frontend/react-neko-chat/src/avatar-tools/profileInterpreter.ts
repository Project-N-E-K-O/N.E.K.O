import type {
  AvatarToolDefinition,
  AvatarToolInteractionProfile,
} from './catalog';
import type {
  AvatarToolCommand,
  AvatarToolInteractionCommit,
  AvatarToolRuleContext,
  AvatarToolRuleHandlers,
} from './interaction';

function createCommit(
  definition: AvatarToolDefinition,
  context: AvatarToolRuleContext,
  facts: Record<string, unknown>,
): AvatarToolInteractionCommit {
  return {
    toolId: definition.id,
    ...facts,
    clientX: context.clientX,
    clientY: context.clientY,
  } as AvatarToolInteractionCommit;
}

function createProgressiveReleaseHandlers(
  definition: AvatarToolDefinition,
  profile: Extract<AvatarToolInteractionProfile, { kind: 'progressive-release-v1' }>,
): AvatarToolRuleHandlers {
  return {
    pointerDown: () => ({}),
    commit: (context: AvatarToolRuleContext): AvatarToolCommand => {
      if (!context.hit) return {};
      const stage = profile.stages.find(candidate => candidate.variant === context.rangeVariant);
      if (!stage) return {};
      const intensity = stage.variant === profile.burst.variant
        ? context.recordBurst(profile.burst.key, profile.burst.windowMs) >= profile.burst.threshold
          ? profile.burst.thresholdIntensity
          : profile.burst.belowThresholdIntensity
        : stage.intensity;
      return {
        commit: createCommit(definition, context, {
          actionId: stage.actionId,
          intensity,
        }),
        ...(stage.nextVariant ? { rangeVariant: stage.nextVariant } : {}),
        sound: profile.feedback.sound,
        ...(stage.variant === profile.feedback.effectVariant
          ? { effect: profile.feedback.effect }
          : {}),
      };
    },
    pointerRelease: () => ({}),
  };
}

function createPressReleaseHandlers(
  definition: AvatarToolDefinition,
  profile: Extract<AvatarToolInteractionProfile, { kind: 'press-release-v1' }>,
): AvatarToolRuleHandlers {
  return {
    pointerDown: () => ({
      ...profile.pointerDown,
      pressFeedback: 'until-pointer-release',
    }),
    commit: (context: AvatarToolRuleContext): AvatarToolCommand => {
      if (!context.hit || !profile.touchZones.includes(context.hit.touchZone)) return {};
      const intensity = context.recordBurst(profile.burst.key, profile.burst.windowMs)
        >= profile.burst.rapidThreshold
        ? profile.burst.rapidIntensity
        : profile.burst.normalIntensity;
      const chanceHit = context.random() < profile.chance.probability;
      return {
        commit: createCommit(definition, context, {
          actionId: profile.actionId,
          intensity,
          touchZone: context.hit.touchZone,
          [profile.chance.field]: chanceHit,
        }),
        ...(chanceHit ? {
          sound: profile.chance.sound,
          effect: profile.chance.effect,
        } : {}),
      };
    },
    pointerRelease: () => ({ ...profile.pointerRelease }),
  };
}

function createLockedImpactHandlers(
  definition: AvatarToolDefinition,
  profile: Extract<AvatarToolInteractionProfile, { kind: 'locked-impact-v1' }>,
): AvatarToolRuleHandlers {
  return {
    pointerDown: (context: AvatarToolRuleContext) => context.hit ? {} : {
      outsideVariant: profile.outsideFeedback.variant,
      resetOutsideVariantAfterMs: profile.outsideFeedback.resetAfterMs,
    },
    commit: (context: AvatarToolRuleContext): AvatarToolCommand => {
      if (!context.hit || !profile.touchZones.includes(context.hit.touchZone)) return {};
      const tapCount = context.recordBurst(profile.burst.key, profile.burst.windowMs);
      const chanceHit = context.random() < profile.chance.probability;
      const intensity = chanceHit
        ? profile.chance.intensity
        : tapCount >= profile.burst.burstThreshold
          ? profile.burst.burstIntensity
          : tapCount >= profile.burst.rapidThreshold
            ? profile.burst.rapidIntensity
            : profile.burst.normalIntensity;
      const effect = definition.effects.find(
        candidate => candidate.id === profile.feedback.effect,
      );
      const effectMode = chanceHit && effect?.kind === 'hammer-swing-v1'
        ? effect.easterEgg.mode
        : '';
      return {
        commit: createCommit(definition, context, {
          actionId: profile.actionId,
          intensity,
          touchZone: context.hit.touchZone,
          [profile.chance.field]: chanceHit,
        }),
        sound: chanceHit ? profile.chance.sound : profile.feedback.sound,
        effect: profile.feedback.effect,
        ...(effectMode ? { effectMode } : {}),
      };
    },
    pointerRelease: () => ({}),
  };
}

export function createAvatarToolProfileHandlers(
  definition: AvatarToolDefinition,
): AvatarToolRuleHandlers {
  const profile = definition.interaction;
  if (profile.kind === 'progressive-release-v1') {
    return createProgressiveReleaseHandlers(definition, profile);
  }
  if (profile.kind === 'press-release-v1') {
    return createPressReleaseHandlers(definition, profile);
  }
  return createLockedImpactHandlers(definition, profile);
}
