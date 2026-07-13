import { withAvatarToolAssetVersion } from '../avatarTools';
import type {
  AvatarToolDefinitionId,
  AvatarToolDefinition,
  AvatarToolEffectRecipe,
  AvatarToolInteractionProfile,
} from './catalog';
import {
  desktopAvatarToolContractSchema,
  desktopAvatarToolAssetPathSchema,
  type DesktopAvatarToolContract,
  type DesktopAvatarToolInteraction,
  type DesktopAvatarToolVisual,
} from './desktopContractSchema';
import { getAvatarToolRegistration } from './catalog';
import {
  AVATAR_TOOL_RUNTIME_POLICY,
  avatarToolRuntimePolicySchema,
  type AvatarToolRuntimePolicy,
} from './interactionPolicy';

function projectAssetPath(path: string): string {
  return desktopAvatarToolAssetPathSchema.parse(withAvatarToolAssetVersion(path, '0'));
}

function projectVisual(definition: AvatarToolDefinition): DesktopAvatarToolVisual {
  const { visual } = definition;
  const projectVariant = (variant: keyof typeof visual.variants) => ({
    iconImagePath: projectAssetPath(visual.variants[variant].iconImagePath),
    pointerImagePath: projectAssetPath(visual.variants[variant].pointerImagePath),
  });
  const projectMode = (mode: 'pointer' | 'inRange') => ({
    displayWidth: visual[mode].displayWidth,
    displayHeight: visual[mode].displayHeight,
    displayCoordinateSpace: visual[mode].displayCoordinateSpace,
    scale: visual[mode].scale,
    renderedAnchor: {
      x: visual[mode].renderedAnchor.x,
      y: visual[mode].renderedAnchor.y,
      coordinateSpace: visual[mode].renderedAnchor.coordinateSpace,
    },
  });
  return {
    initialVariant: visual.initialVariant,
    variants: {
      primary: projectVariant('primary'),
      secondary: projectVariant('secondary'),
      tertiary: projectVariant('tertiary'),
    },
    presentation: {
      inRangeVariantSource: visual.presentation.inRangeVariantSource,
      outsideVariantSource: visual.presentation.outsideVariantSource,
      effectActiveImageKind: visual.presentation.effectActiveImageKind,
    },
    hotspotX: visual.hotspotX,
    hotspotY: visual.hotspotY,
    naturalWidth: visual.naturalWidth,
    naturalHeight: visual.naturalHeight,
    pointer: projectMode('pointer'),
    inRange: projectMode('inRange'),
  };
}

function projectEffect(effect: AvatarToolEffectRecipe) {
  if (effect.kind === 'fixed-particles-v1') {
    return {
      id: effect.id,
      kind: effect.kind,
      interactionLock: effect.interactionLock,
      lifetimeMs: effect.lifetimeMs,
      glyph: effect.glyph,
      particles: effect.particles.map(particle => ({
        offsetX: particle.offsetX,
        offsetY: particle.offsetY,
        driftX: particle.driftX,
        driftY: particle.driftY,
        scale: particle.scale,
        delayMs: particle.delayMs,
      })),
    };
  }
  if (effect.kind === 'random-scatter-v1') {
    const projectRange = (range: { min: number; range: number }) => ({
      min: range.min,
      range: range.range,
    });
    return {
      id: effect.id,
      kind: effect.kind,
      interactionLock: effect.interactionLock,
      assetPath: projectAssetPath(effect.assetPath),
      count: effect.count,
      lifetimeMs: effect.lifetimeMs,
      angleDeg: projectRange(effect.angleDeg),
      distance: projectRange(effect.distance),
      offsetX: projectRange(effect.offsetX),
      offsetY: projectRange(effect.offsetY),
      rotation: projectRange(effect.rotation),
      scale: projectRange(effect.scale),
      delayMs: projectRange(effect.delayMs),
    };
  }
  return {
    id: effect.id,
    kind: effect.kind,
    interactionLock: effect.interactionLock,
    anchor: {
      source: effect.anchor.source,
      visualMode: effect.anchor.visualMode,
    },
    transformOrigin: {
      x: effect.transformOrigin.x,
      y: effect.transformOrigin.y,
    },
    impactRegistration: {
      transformOrigin: {
        x: effect.impactRegistration.transformOrigin.x,
        y: effect.impactRegistration.transformOrigin.y,
      },
      translate: {
        x: effect.impactRegistration.translate.x,
        y: effect.impactRegistration.translate.y,
      },
      rotationDeg: effect.impactRegistration.rotationDeg,
      scale: effect.impactRegistration.scale,
    },
    variants: {
      idle: effect.variants.idle,
      impact: effect.variants.impact,
    },
    timeline: effect.timeline.map(entry => ({ phase: entry.phase, delayMs: entry.delayMs })),
    easterEgg: {
      mode: effect.easterEgg.mode,
      scale: effect.easterEgg.scale,
      anchorOffset: {
        x: effect.easterEgg.anchorOffset.x,
        y: effect.easterEgg.anchorOffset.y,
      },
    },
  };
}

function projectProfile(profile: AvatarToolInteractionProfile) {
  if (profile.kind === 'progressive-release-v1') {
    return {
      kind: profile.kind,
      stages: profile.stages.map(stage => ({
        variant: stage.variant,
        actionId: stage.actionId,
        intensity: stage.intensity,
        nextVariant: stage.nextVariant,
      })),
      burst: {
        variant: profile.burst.variant,
        windowMs: profile.burst.windowMs,
        threshold: profile.burst.threshold,
        belowThresholdIntensity: profile.burst.belowThresholdIntensity,
        thresholdIntensity: profile.burst.thresholdIntensity,
      },
      feedback: {
        sound: profile.feedback.sound,
        effect: profile.feedback.effect,
        effectVariant: profile.feedback.effectVariant,
      },
    };
  }
  if (profile.kind === 'press-release-v1') {
    return {
      kind: profile.kind,
      actionId: profile.actionId,
      pointerDown: {
        rangeVariant: profile.pointerDown.rangeVariant,
        outsideVariant: profile.pointerDown.outsideVariant,
      },
      pointerRelease: {
        rangeVariant: profile.pointerRelease.rangeVariant,
        outsideVariant: profile.pointerRelease.outsideVariant,
      },
      burst: {
        windowMs: profile.burst.windowMs,
        rapidThreshold: profile.burst.rapidThreshold,
        normalIntensity: profile.burst.normalIntensity,
        rapidIntensity: profile.burst.rapidIntensity,
      },
      touchZone: profile.touchZone,
      touchZones: [...profile.touchZones],
      chance: {
        field: profile.chance.field,
        probability: profile.chance.probability,
        sound: profile.chance.sound,
        effect: profile.chance.effect,
      },
    };
  }
  return {
    kind: profile.kind,
    actionId: profile.actionId,
    touchZone: profile.touchZone,
    outsideFeedback: {
      variant: profile.outsideFeedback.variant,
      resetAfterMs: profile.outsideFeedback.resetAfterMs,
    },
    burst: {
      windowMs: profile.burst.windowMs,
      rapidThreshold: profile.burst.rapidThreshold,
      burstThreshold: profile.burst.burstThreshold,
      normalIntensity: profile.burst.normalIntensity,
      rapidIntensity: profile.burst.rapidIntensity,
      burstIntensity: profile.burst.burstIntensity,
    },
    touchZones: [...profile.touchZones],
    chance: {
      field: profile.chance.field,
      probability: profile.chance.probability,
      intensity: profile.chance.intensity,
      sound: profile.chance.sound,
    },
    feedback: {
      sound: profile.feedback.sound,
      effect: profile.feedback.effect,
    },
  };
}

function getReferencedResourceIds(profile: AvatarToolInteractionProfile) {
  if (profile.kind === 'progressive-release-v1') {
    return { sounds: new Set([profile.feedback.sound]), effects: new Set([profile.feedback.effect]) };
  }
  if (profile.kind === 'press-release-v1') {
    return { sounds: new Set([profile.chance.sound]), effects: new Set([profile.chance.effect]) };
  }
  return {
    sounds: new Set([profile.chance.sound, profile.feedback.sound]),
    effects: new Set([profile.feedback.effect]),
  };
}

function projectInteraction(definition: AvatarToolDefinition): DesktopAvatarToolInteraction {
  const references = getReferencedResourceIds(definition.interaction);
  return {
    profile: projectProfile(definition.interaction),
    sounds: definition.sounds
      .filter(sound => references.sounds.has(sound.id))
      .map(sound => ({
        id: sound.id,
        src: projectAssetPath(sound.src),
        volume: sound.volume,
      })),
    effects: definition.effects
      .filter(effect => references.effects.has(effect.id))
      .map(projectEffect),
  };
}

export function projectDesktopAvatarToolContract(
  definition: AvatarToolDefinition | null,
  runtimePolicy: AvatarToolRuntimePolicy = AVATAR_TOOL_RUNTIME_POLICY,
): DesktopAvatarToolContract {
  if (definition === null) {
    return desktopAvatarToolContractSchema.parse({
      wireVersion: 1,
      definition: null,
      runtimePolicy: null,
    });
  }
  const { desktopVisual, desktopInteraction } = definition.capability;
  return desktopAvatarToolContractSchema.parse({
    wireVersion: 1,
    definition: {
      definitionVersion: 1,
      id: definition.id,
      capability: {
        desktopVisual,
        desktopInteraction,
      },
      visual: desktopVisual ? projectVisual(definition) : null,
      interaction: desktopInteraction ? projectInteraction(definition) : null,
    },
    runtimePolicy: desktopVisual ? avatarToolRuntimePolicySchema.parse(runtimePolicy) : null,
  });
}

export function buildDesktopAvatarToolContract(
  toolId: AvatarToolDefinitionId | null,
): DesktopAvatarToolContract {
  return projectDesktopAvatarToolContract(
    toolId === null ? null : getAvatarToolRegistration(toolId).definition,
  );
}
