import type { AvatarToolDefinition, RandomScatterEffectRecipe } from '../catalog';
import type { AvatarToolCommand, AvatarToolRuleContext, AvatarToolRuleHandlers } from '../interactionEngine';

export const FIST_REWARD_DROP_EFFECT_RECIPE = {
  id: 'reward-drops',
  kind: 'random-scatter-v1',
  interactionLock: 'none',
  assetPath: '/static/icons/cat_moneny.png',
  count: 3,
  lifetimeMs: 920,
  angleDeg: { min: -140, range: 100 },
  distance: { min: 76, range: 42 },
  offsetX: { min: -22, range: 28 },
  offsetY: { min: -33, range: 18 },
  rotation: { min: -120, range: 240 },
  scale: { min: 0.82, range: 0.38 },
  delayMs: { min: 0, range: 140 },
} as const satisfies RandomScatterEffectRecipe;

export const FIST_AVATAR_TOOL_DEFINITION = {
  definitionVersion: 1,
  id: 'fist',
  label: {
    key: 'chat.toolFist',
    fallback: '猫爪',
  },
  capability: {
    desktopVisual: true,
    desktopInteraction: true,
  },
  visual: {
    initialVariant: 'primary',
    variants: {
      primary: {
        iconImagePath: '/static/icons/cat_claw1.png',
        pointerImagePath: '/static/icons/cat_claw1_cursor.png',
        menuOffsetX: 0,
        menuOffsetY: 0,
      },
      secondary: {
        iconImagePath: '/static/icons/cat_claw2.png',
        pointerImagePath: '/static/icons/cat_claw2_cursor.png',
        menuOffsetX: 0,
        menuOffsetY: 0,
      },
      tertiary: {
        iconImagePath: '/static/icons/cat_claw1.png',
        pointerImagePath: '/static/icons/cat_claw2_cursor.png',
        menuOffsetX: 0,
        menuOffsetY: 0,
      },
    },
    presentation: {
      inRangeVariantSource: 'range',
      outsideVariantSource: 'outside',
      effectActiveImageKind: 'icon',
    },
    menuScale: 1,
    hotspotX: 39,
    hotspotY: 46,
    naturalWidth: 78,
    naturalHeight: 80,
    pointer: {
      displayWidth: 78,
      displayHeight: 80,
      displayCoordinateSpace: 'pre-scale-css-pixel',
      scale: 0.56,
      renderedAnchor: {
        x: 21.84,
        y: 25.76,
        coordinateSpace: 'final-css-pixel',
      },
    },
    inRange: {
      displayWidth: 78,
      displayHeight: 80,
      displayCoordinateSpace: 'pre-scale-css-pixel',
      scale: 1,
      renderedAnchor: {
        x: 39,
        y: 46,
        coordinateSpace: 'final-css-pixel',
      },
    },
  },
  sounds: [
    {
      id: 'coin-drop',
      src: '/static/sounds/avatar-tools/coin-drop.mp3',
      volume: 0.9,
    },
  ],
  effects: [FIST_REWARD_DROP_EFFECT_RECIPE],
  interaction: {
    kind: 'press-release-v1',
    actionId: 'poke',
    pointerDown: {
      rangeVariant: 'secondary',
      outsideVariant: 'secondary',
    },
    pointerRelease: {
      rangeVariant: 'primary',
      outsideVariant: 'primary',
    },
    burst: {
      key: 'fist',
      windowMs: 1400,
      rapidThreshold: 4,
      normalIntensity: 'normal',
      rapidIntensity: 'rapid',
    },
    touchZone: 'release',
    touchZones: ['ear', 'head', 'face', 'body'],
    chance: {
      field: 'rewardDrop',
      probability: 0.25,
      sound: 'coin-drop',
      effect: 'reward-drops',
    },
  },
} as const satisfies AvatarToolDefinition;

const FIST_INTERACTION = FIST_AVATAR_TOOL_DEFINITION.interaction;

export const FIST_BURST_WINDOW_MS = FIST_INTERACTION.burst.windowMs;

export function resolveFistInteraction(tapCount: number, rewardRoll: number) {
  return {
    actionId: FIST_INTERACTION.actionId,
    intensity: tapCount >= FIST_INTERACTION.burst.rapidThreshold
      ? FIST_INTERACTION.burst.rapidIntensity
      : FIST_INTERACTION.burst.normalIntensity,
    rewardDrop: rewardRoll < FIST_INTERACTION.chance.probability,
  };
}

export function resolveFistPointerDown(): AvatarToolCommand {
  return { ...FIST_INTERACTION.pointerDown, pressFeedback: 'until-pointer-release' };
}

export function resolveFistCommit(context: AvatarToolRuleContext): AvatarToolCommand {
  if (!context.hit) return {};
  const decision = resolveFistInteraction(
    context.recordBurst(FIST_INTERACTION.burst.key, FIST_BURST_WINDOW_MS),
    context.random(),
  );
  return {
    commit: {
      toolId: FIST_AVATAR_TOOL_DEFINITION.id,
      actionId: FIST_INTERACTION.actionId,
      intensity: decision.intensity,
      touchZone: context.hit.touchZone,
      rewardDrop: decision.rewardDrop,
      clientX: context.clientX,
      clientY: context.clientY,
    },
    ...(decision.rewardDrop ? {
      sound: FIST_INTERACTION.chance.sound,
      effect: FIST_INTERACTION.chance.effect,
    } : {}),
  };
}

export function resolveFistPointerRelease(): AvatarToolCommand {
  return { ...FIST_INTERACTION.pointerRelease };
}

export const FIST_AVATAR_TOOL_HANDLERS: AvatarToolRuleHandlers = {
  pointerDown: resolveFistPointerDown,
  commit: resolveFistCommit,
  pointerRelease: resolveFistPointerRelease,
};
