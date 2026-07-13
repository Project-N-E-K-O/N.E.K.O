import type {
  AvatarToolDefinition,
  HammerSwingEffectRecipe,
} from '../catalog';
import type { AvatarToolCommand, AvatarToolRuleContext, AvatarToolRuleHandlers } from '../interactionEngine';

export const HAMMER_SWING_EFFECT_RECIPE = {
  id: 'hammer-swing',
  kind: 'hammer-swing-v1',
  interactionLock: 'effect-lifetime',
  anchor: {
    source: 'live-pointer',
    visualMode: 'inRange',
  },
  transformOrigin: { x: 60, y: 118 },
  impactRegistration: {
    transformOrigin: { x: 80.19, y: 68 },
    translate: { x: 19.62, y: -9.01 },
    rotationDeg: 34.258,
    scale: 0.999333,
  },
  variants: {
    idle: 'primary',
    impact: 'secondary',
  },
  timeline: [
    { phase: 'windup', delayMs: 0 },
    { phase: 'swing', delayMs: 240 },
    { phase: 'impact', delayMs: 420 },
    { phase: 'recover', delayMs: 520 },
    { phase: 'idle', delayMs: 620 },
  ],
  easterEgg: {
    mode: 'easter-egg',
    scale: 5,
    anchorOffset: { x: 322.11, y: 259.27 },
  },
} as const satisfies HammerSwingEffectRecipe;

export const HAMMER_AVATAR_TOOL_DEFINITION = {
  definitionVersion: 1,
  id: 'hammer',
  label: {
    key: 'chat.toolHammer',
    fallback: '锤子',
  },
  capability: {
    desktopVisual: true,
    desktopInteraction: true,
  },
  visual: {
    initialVariant: 'primary',
    variants: {
      primary: {
        iconImagePath: '/static/icons/chat_hammer1.png',
        pointerImagePath: '/static/icons/chat_hammer1_cursor.png',
        menuOffsetX: -8,
        menuOffsetY: 4,
      },
      secondary: {
        iconImagePath: '/static/icons/chat_hammer2.png',
        pointerImagePath: '/static/icons/chat_hammer2_cursor.png',
        menuOffsetX: 1,
        menuOffsetY: -1,
      },
      tertiary: {
        iconImagePath: '/static/icons/chat_hammer1.png',
        pointerImagePath: '/static/icons/chat_hammer2_cursor.png',
        menuOffsetX: 1,
        menuOffsetY: -1,
      },
    },
    presentation: {
      inRangeVariantSource: 'primary',
      outsideVariantSource: 'outside',
      effectActiveImageKind: 'icon',
    },
    menuScale: 1.52,
    hotspotX: 50,
    hotspotY: 54,
    naturalWidth: 100,
    naturalHeight: 96,
    pointer: {
      displayWidth: 100,
      displayHeight: 96,
      displayCoordinateSpace: 'pre-scale-css-pixel',
      scale: 0.52,
      renderedAnchor: {
        x: 26,
        y: 28.08,
        coordinateSpace: 'final-css-pixel',
      },
    },
    inRange: {
      displayWidth: 136,
      displayHeight: 130,
      displayCoordinateSpace: 'pre-scale-css-pixel',
      scale: 1,
      renderedAnchor: {
        x: 50,
        y: 54,
        coordinateSpace: 'final-css-pixel',
      },
    },
  },
  sounds: [
    {
      id: 'hammer-small',
      src: '/static/sounds/avatar-tools/hammer-small.mp3',
      volume: 0.9,
    },
    {
      id: 'hammer-big',
      src: '/static/sounds/avatar-tools/hammer-big.mp3',
      volume: 0.9,
    },
  ],
  effects: [HAMMER_SWING_EFFECT_RECIPE],
  interaction: {
    kind: 'locked-impact-v1',
    actionId: 'bonk',
    touchZone: 'release',
    outsideFeedback: {
      variant: 'secondary',
      resetAfterMs: 220,
    },
    burst: {
      key: 'hammer',
      windowMs: 3200,
      rapidThreshold: 2,
      burstThreshold: 3,
      normalIntensity: 'normal',
      rapidIntensity: 'rapid',
      burstIntensity: 'burst',
    },
    touchZones: ['ear', 'head', 'face', 'body'],
    chance: {
      field: 'easterEgg',
      probability: 0.05,
      intensity: 'easter_egg',
      sound: 'hammer-big',
    },
    feedback: {
      sound: 'hammer-small',
      effect: 'hammer-swing',
    },
  },
} as const satisfies AvatarToolDefinition;

const HAMMER_INTERACTION = HAMMER_AVATAR_TOOL_DEFINITION.interaction;

export const HAMMER_BURST_WINDOW_MS = HAMMER_INTERACTION.burst.windowMs;

export function resolveHammerInteraction(tapCount: number, easterEggRoll: number) {
  const easterEgg = easterEggRoll < HAMMER_INTERACTION.chance.probability;
  return {
    actionId: HAMMER_INTERACTION.actionId,
    intensity: easterEgg
      ? HAMMER_INTERACTION.chance.intensity
      : tapCount >= HAMMER_INTERACTION.burst.burstThreshold
        ? HAMMER_INTERACTION.burst.burstIntensity
        : tapCount >= HAMMER_INTERACTION.burst.rapidThreshold
          ? HAMMER_INTERACTION.burst.rapidIntensity
          : HAMMER_INTERACTION.burst.normalIntensity,
    easterEgg,
  };
}

export function resolveHammerPointerDown(context: AvatarToolRuleContext): AvatarToolCommand {
  if (!context.hit) {
    return {
      outsideVariant: HAMMER_INTERACTION.outsideFeedback.variant,
      resetOutsideVariantAfterMs: HAMMER_INTERACTION.outsideFeedback.resetAfterMs,
    };
  }
  return {};
}

export function resolveHammerCommit(context: AvatarToolRuleContext): AvatarToolCommand {
  if (!context.hit) return {};
  const decision = resolveHammerInteraction(
    context.recordBurst(HAMMER_INTERACTION.burst.key, HAMMER_BURST_WINDOW_MS),
    context.random(),
  );
  return {
    commit: {
      toolId: HAMMER_AVATAR_TOOL_DEFINITION.id,
      actionId: HAMMER_INTERACTION.actionId,
      intensity: decision.intensity,
      touchZone: context.hit.touchZone,
      easterEgg: decision.easterEgg,
      clientX: context.clientX,
      clientY: context.clientY,
    },
    sound: decision.easterEgg ? HAMMER_INTERACTION.chance.sound : HAMMER_INTERACTION.feedback.sound,
    effect: HAMMER_INTERACTION.feedback.effect,
    ...(decision.easterEgg ? { effectMode: HAMMER_SWING_EFFECT_RECIPE.easterEgg.mode } : {}),
  };
}

export const HAMMER_AVATAR_TOOL_HANDLERS: AvatarToolRuleHandlers = {
  pointerDown: resolveHammerPointerDown,
  commit: resolveHammerCommit,
  pointerRelease: () => ({}),
};
