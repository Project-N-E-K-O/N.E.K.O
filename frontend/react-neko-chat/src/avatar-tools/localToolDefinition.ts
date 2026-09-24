import type { AvatarToolDefinition, RandomScatterEffectRecipe } from './catalog';
import type { LocalAvatarToolDto } from './localToolTypes';

export const LOCAL_AVATAR_TOOL_SPECIAL_SCATTER_EFFECT_RECIPE = {
  id: 'special-scatter',
  kind: 'random-scatter',
  interactionLock: 'none',
  assetPath: '',
  count: 5,
  lifetimeMs: 920,
  angleDeg: { min: -150, range: 120 },
  distance: { min: 72, range: 52 },
  offsetX: { min: -24, range: 48 },
  offsetY: { min: -36, range: 24 },
  rotation: { min: -135, range: 270 },
  scale: { min: 0.72, range: 0.46 },
  delayMs: { min: 0, range: 160 },
} as const satisfies RandomScatterEffectRecipe;

export function buildLocalAvatarToolDefinition(item: LocalAvatarToolDto): AvatarToolDefinition {
  if (item.recordVersion === 3) {
    const initial = item.runtime.images.find(image => image.id === item.runtime.initialImageId)!;
    const initialVariant = {
      iconImagePath: initial.url,
      pointerImagePath: initial.url,
      menuOffsetX: 0,
      menuOffsetY: 0,
    };
    const frames = item.runtime.images.map(image => ({
      iconImagePath: image.url,
      pointerImagePath: image.url,
      menuOffsetX: 0,
      menuOffsetY: 0,
    }));
    const normalSound = item.runtime.normalSoundUrl ? {
      id: 'normal-feedback',
      src: item.runtime.normalSoundUrl,
      volume: 0.9,
    } : null;
    const specialSound = item.runtime.special?.soundUrl ? {
      id: 'special-feedback',
      src: item.runtime.special.soundUrl,
      volume: 0.9,
    } : null;
    const specialEffect = item.runtime.special ? {
      ...LOCAL_AVATAR_TOOL_SPECIAL_SCATTER_EFFECT_RECIPE,
      assetPath: item.runtime.special.imageUrl,
    } : null;
    return {
      definitionVersion: 3,
      id: item.id,
      label: { kind: 'literal', value: item.name },
      capability: { desktopVisual: true, desktopInteraction: true },
      visual: {
        initialVariant: 'primary',
        variants: { primary: initialVariant, secondary: initialVariant, tertiary: initialVariant },
        frames,
        presentation: {
          inRangeVariantSource: 'range',
          outsideVariantSource: 'outside',
          effectActiveImageKind: 'pointer',
        },
        menuScale: 1,
        hotspotX: 40,
        hotspotY: 40,
        naturalWidth: 80,
        naturalHeight: 80,
        pointer: {
          displayWidth: 80,
          displayHeight: 80,
          displayCoordinateSpace: 'pre-scale-css-pixel',
          scale: 0.62,
          renderedAnchor: { x: 24.8, y: 24.8, coordinateSpace: 'final-css-pixel' },
        },
        inRange: {
          displayWidth: 80,
          displayHeight: 80,
          displayCoordinateSpace: 'pre-scale-css-pixel',
          scale: 1,
          renderedAnchor: { x: 40, y: 40, coordinateSpace: 'final-css-pixel' },
        },
      },
      sounds: [normalSound, specialSound].filter((sound): sound is NonNullable<typeof sound> => !!sound),
      effects: specialEffect ? [specialEffect] : [],
      interaction: {
        kind: 'custom-graph',
        revision: item.revision,
        images: item.runtime.images.map((image, frameIndex) => ({
          id: image.id,
          frameIndex,
          hasMeaning: image.hasMeaning,
        })),
        initialImageId: item.runtime.initialImageId,
        initialInteractionIds: [...item.runtime.initialInteractionIds],
        interactions: item.runtime.interactions.map(interaction => ({
          id: interaction.id,
          trigger: interaction.trigger,
          actions: interaction.actions,
        })),
        links: item.runtime.links.map(link => ({ ...link })),
        burst: {
          key: item.id,
          windowMs: 1800,
          rapidThreshold: 3,
          normalIntensity: 'normal',
          rapidIntensity: 'rapid',
        },
        touchZone: 'release',
        touchZones: ['ear', 'head', 'face', 'body'],
        ...(normalSound ? { feedback: { sound: normalSound.id } } : {}),
        ...(item.runtime.special ? {
          chance: {
            field: 'specialTriggered',
            probability: item.runtime.special.probability,
            effect: LOCAL_AVATAR_TOOL_SPECIAL_SCATTER_EFFECT_RECIPE.id,
            ...(specialSound ? { sound: specialSound.id } : {}),
          },
        } : {}),
      },
    };
  }
  const defaultVariant = {
    iconImagePath: item.defaultUrl,
    pointerImagePath: item.defaultUrl,
    menuOffsetX: 0,
    menuOffsetY: 0,
  };
  const frames = [item.defaultUrl, ...item.changeUrls].map(path => ({
    iconImagePath: path,
    pointerImagePath: path,
    menuOffsetX: 0,
    menuOffsetY: 0,
  }));
  const normalSound = item.normalSoundUrl ? {
    id: 'normal-feedback',
    src: item.normalSoundUrl,
    volume: 0.9,
  } : null;
  const specialSound = item.special?.soundUrl ? {
    id: 'special-feedback',
    src: item.special.soundUrl,
    volume: 0.9,
  } : null;
  const specialEffect = item.special ? {
    ...LOCAL_AVATAR_TOOL_SPECIAL_SCATTER_EFFECT_RECIPE,
    assetPath: item.special.imageUrl,
  } : null;
  return {
    definitionVersion: 2,
    id: item.id,
    label: { kind: 'literal', value: item.name },
    capability: { desktopVisual: true, desktopInteraction: true },
    visual: {
      initialVariant: 'primary',
      variants: { primary: defaultVariant, secondary: defaultVariant, tertiary: defaultVariant },
      frames,
      presentation: {
        inRangeVariantSource: 'range',
        outsideVariantSource: 'outside',
        effectActiveImageKind: 'pointer',
      },
      menuScale: 1,
      hotspotX: 40,
      hotspotY: 40,
      naturalWidth: 80,
      naturalHeight: 80,
      pointer: {
        displayWidth: 80,
        displayHeight: 80,
        displayCoordinateSpace: 'pre-scale-css-pixel',
        scale: 0.62,
        renderedAnchor: { x: 24.8, y: 24.8, coordinateSpace: 'final-css-pixel' },
      },
      inRange: {
        displayWidth: 80,
        displayHeight: 80,
        displayCoordinateSpace: 'pre-scale-css-pixel',
        scale: 1,
        renderedAnchor: { x: 40, y: 40, coordinateSpace: 'final-css-pixel' },
      },
    },
    sounds: [normalSound, specialSound].filter((sound): sound is NonNullable<typeof sound> => !!sound),
    effects: specialEffect ? [specialEffect] : [],
    interaction: {
      kind: 'press-release',
      revision: item.revision,
      actionId: 'interact',
      imageChange: { kind: item.changeMode },
      burst: {
        key: item.id,
        windowMs: 1800,
        rapidThreshold: 3,
        normalIntensity: 'normal',
        rapidIntensity: 'rapid',
      },
      touchZone: 'release',
      touchZones: ['ear', 'head', 'face', 'body'],
      ...(normalSound ? { feedback: { sound: normalSound.id } } : {}),
      ...(item.special ? {
        chance: {
          field: 'specialTriggered',
          probability: item.special.probability,
          effect: LOCAL_AVATAR_TOOL_SPECIAL_SCATTER_EFFECT_RECIPE.id,
          ...(specialSound ? { sound: specialSound.id } : {}),
        },
      } : {}),
    },
  };
}
