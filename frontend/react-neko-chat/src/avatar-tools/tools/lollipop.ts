import type {
  AvatarToolVariantId,
  AvatarToolDefinition,
  FixedParticleEffectRecipe,
} from '../catalog';
import type { AvatarToolCommand, AvatarToolRuleContext, AvatarToolRuleHandlers } from '../interactionEngine';

export const LOLLIPOP_HEART_EFFECT_RECIPE = {
  id: 'hearts',
  kind: 'fixed-particles-v1',
  interactionLock: 'none',
  lifetimeMs: 2100,
  glyph: '*',
  particles: [
    { offsetX: -12, offsetY: -26, driftX: -26, driftY: -124, scale: 0.92, delayMs: 0 },
    { offsetX: 10, offsetY: -20, driftX: 24, driftY: -138, scale: 1.06, delayMs: 110 },
    { offsetX: -4, offsetY: -40, driftX: -18, driftY: -154, scale: 0.84, delayMs: 190 },
  ],
} as const satisfies FixedParticleEffectRecipe;

export const LOLLIPOP_AVATAR_TOOL_DEFINITION = {
  definitionVersion: 1,
  id: 'lollipop',
  label: {
    key: 'chat.toolLollipop',
    fallback: '棒棒糖',
  },
  capability: {
    desktopVisual: true,
    desktopInteraction: true,
  },
  visual: {
    initialVariant: 'primary',
    variants: {
      primary: {
        iconImagePath: '/static/icons/chat_sugar1.png',
        pointerImagePath: '/static/icons/chat_sugar1_cursor.png',
        menuOffsetX: 0,
        menuOffsetY: 0,
      },
      secondary: {
        iconImagePath: '/static/icons/chat_sugar2.png',
        pointerImagePath: '/static/icons/chat_sugar2_cursor.png',
        menuOffsetX: 0,
        menuOffsetY: 0,
      },
      tertiary: {
        iconImagePath: '/static/icons/chat_sugar3.png',
        pointerImagePath: '/static/icons/chat_sugar2_cursor.png',
        menuOffsetX: 0,
        menuOffsetY: 0,
      },
    },
    presentation: {
      inRangeVariantSource: 'range',
      outsideVariantSource: 'range',
      effectActiveImageKind: 'icon',
    },
    menuScale: 1.18,
    hotspotX: 27,
    hotspotY: 46,
    naturalWidth: 55,
    naturalHeight: 80,
    pointer: {
      displayWidth: 74,
      displayHeight: 108,
      displayCoordinateSpace: 'pre-scale-css-pixel',
      scale: 0.56,
      renderedAnchor: {
        x: 20.34327272727273,
        y: 34.776,
        coordinateSpace: 'final-css-pixel',
      },
    },
    inRange: {
      displayWidth: 74,
      displayHeight: 108,
      displayCoordinateSpace: 'pre-scale-css-pixel',
      scale: 1,
      renderedAnchor: {
        x: 36.32727272727273,
        y: 62.1,
        coordinateSpace: 'final-css-pixel',
      },
    },
  },
  sounds: [
    {
      id: 'lollipop-bite',
      src: '/static/sounds/avatar-tools/lollipop-bite.mp3',
      volume: 0.9,
    },
  ],
  effects: [LOLLIPOP_HEART_EFFECT_RECIPE],
  interaction: {
    kind: 'progressive-release-v1',
    stages: [
      { variant: 'primary', actionId: 'offer', intensity: 'normal', nextVariant: 'secondary' },
      { variant: 'secondary', actionId: 'tease', intensity: 'normal', nextVariant: 'tertiary' },
      { variant: 'tertiary', actionId: 'tap_soft', intensity: 'rapid', nextVariant: null },
    ],
    burst: {
      key: 'lollipop',
      variant: 'tertiary',
      windowMs: 1800,
      threshold: 4,
      belowThresholdIntensity: 'rapid',
      thresholdIntensity: 'burst',
    },
    feedback: {
      sound: 'lollipop-bite',
      effect: 'hearts',
      effectVariant: 'tertiary',
    },
  },
} as const satisfies AvatarToolDefinition;

const LOLLIPOP_INTERACTION = LOLLIPOP_AVATAR_TOOL_DEFINITION.interaction;

export const LOLLIPOP_BURST_WINDOW_MS = LOLLIPOP_INTERACTION.burst.windowMs;

function isLollipopFeedbackVariant(variant: AvatarToolVariantId): boolean {
  return variant === LOLLIPOP_INTERACTION.feedback.effectVariant;
}

export function resolveLollipopInteraction(currentVariant: AvatarToolVariantId, tapCount: number) {
  const stage = LOLLIPOP_INTERACTION.stages.find(candidate => candidate.variant === currentVariant);
  if (!stage) return null;
  switch (stage.variant) {
    case 'primary':
      return {
        actionId: stage.actionId,
        intensity: stage.intensity,
        nextVariant: stage.nextVariant,
        hearts: isLollipopFeedbackVariant(stage.variant),
      };
    case 'secondary':
      return {
        actionId: stage.actionId,
        intensity: stage.intensity,
        nextVariant: stage.nextVariant,
        hearts: isLollipopFeedbackVariant(stage.variant),
      };
    case 'tertiary':
      return {
        actionId: stage.actionId,
        intensity: tapCount >= LOLLIPOP_INTERACTION.burst.threshold
          ? LOLLIPOP_INTERACTION.burst.thresholdIntensity
          : LOLLIPOP_INTERACTION.burst.belowThresholdIntensity,
        nextVariant: stage.nextVariant,
        hearts: isLollipopFeedbackVariant(stage.variant),
      };
  }
}

export function resolveLollipopCommit(context: AvatarToolRuleContext): AvatarToolCommand {
  if (!context.hit) return {};
  const tapCount = context.rangeVariant === LOLLIPOP_INTERACTION.burst.variant
    ? context.recordBurst(LOLLIPOP_INTERACTION.burst.key, LOLLIPOP_BURST_WINDOW_MS)
    : 0;
  const decision = resolveLollipopInteraction(context.rangeVariant, tapCount);
  if (!decision) return {};
  const feedback: Omit<AvatarToolCommand, 'commit'> = {
    ...(decision.nextVariant ? { rangeVariant: decision.nextVariant } : {}),
    sound: LOLLIPOP_INTERACTION.feedback.sound,
    ...(decision.hearts ? { effect: LOLLIPOP_INTERACTION.feedback.effect } : {}),
  };
  switch (decision.actionId) {
    case LOLLIPOP_INTERACTION.stages[0].actionId:
      return {
        ...feedback,
        commit: {
          toolId: LOLLIPOP_AVATAR_TOOL_DEFINITION.id,
          actionId: decision.actionId,
          intensity: decision.intensity,
          clientX: context.clientX,
          clientY: context.clientY,
        },
      };
    case LOLLIPOP_INTERACTION.stages[1].actionId:
      return {
        ...feedback,
        commit: {
          toolId: LOLLIPOP_AVATAR_TOOL_DEFINITION.id,
          actionId: decision.actionId,
          intensity: decision.intensity,
          clientX: context.clientX,
          clientY: context.clientY,
        },
      };
    case LOLLIPOP_INTERACTION.stages[2].actionId:
      return {
        ...feedback,
        commit: {
          toolId: LOLLIPOP_AVATAR_TOOL_DEFINITION.id,
          actionId: decision.actionId,
          intensity: decision.intensity,
          clientX: context.clientX,
          clientY: context.clientY,
        },
      };
  }
}

export const LOLLIPOP_AVATAR_TOOL_HANDLERS: AvatarToolRuleHandlers = {
  pointerDown: () => ({}),
  commit: resolveLollipopCommit,
  pointerRelease: () => ({}),
};
