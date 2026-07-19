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
  profile: Extract<AvatarToolInteractionProfile, { kind: 'progressive-release' }>,
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
  profile: Extract<AvatarToolInteractionProfile, { kind: 'press-release' }>,
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
  profile: Extract<AvatarToolInteractionProfile, { kind: 'locked-impact' }>,
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
      const effectMode = chanceHit && effect?.kind === 'hammer-swing'
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

function createRoundChoiceHandlers(
  profile: Extract<AvatarToolInteractionProfile, { kind: 'round-choice' }>,
): AvatarToolRuleHandlers {
  return {
    pointerDown: (context: AvatarToolRuleContext) => context.hit && !context.interactionLocked ? {
      rangeVariant: context.visibleVariant,
      roundChoiceCycle: 'pause',
    } : {},
    commit: (context: AvatarToolRuleContext): AvatarToolCommand => {
      if (!context.hit) return {};
      const userChoice = profile.choices.find(choice => choice.variant === context.visibleVariant);
      if (!userChoice) return {};
      const choiceIndex = Math.min(
        profile.choices.length - 1,
        Math.max(0, Math.floor(context.random() * profile.choices.length)),
      );
      const avatarChoice = profile.choices[choiceIndex];
      return {
        rangeVariant: context.visibleVariant,
        roundChoiceCycle: 'confirm',
        roundChoiceHoldMs: profile.confirmation.holdMs,
        roundChoiceUserGesture: userChoice.gesture,
        roundChoiceUserVariant: userChoice.variant,
        roundChoiceAvatarGesture: avatarChoice.gesture,
        roundChoiceAvatarVariant: avatarChoice.variant,
        sound: profile.confirmation.sound,
      };
    },
    pointerRelease: () => ({}),
  };
}

export function createAvatarToolProfileHandlers(
  definition: AvatarToolDefinition,
): AvatarToolRuleHandlers {
  const profile = definition.interaction;
  if (profile.kind === 'progressive-release') {
    return createProgressiveReleaseHandlers(definition, profile);
  }
  if (profile.kind === 'press-release') {
    return createPressReleaseHandlers(definition, profile);
  }
  if (profile.kind === 'round-choice') {
    return createRoundChoiceHandlers(profile);
  }
  return createLockedImpactHandlers(definition, profile);
}
