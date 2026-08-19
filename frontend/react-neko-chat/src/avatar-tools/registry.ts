import {
  AVATAR_TOOL_REGISTRY,
  registerAvatarTool,
  validateAvatarToolDefinition,
  type AvatarToolDefinition,
  type AvatarToolEffectId,
  type AvatarToolEffectRecipe,
  type AvatarToolId,
  type AvatarToolRegistration,
  type AvatarToolSoundId,
  type AvatarToolSoundResource,
} from './catalog';

export type AvatarToolLabel =
  | { kind: 'i18n'; key: string; fallback: string }
  | { kind: 'literal'; value: string };

export type AvatarToolItem = {
  id: AvatarToolId;
  label: AvatarToolLabel;
  iconImagePath: string;
  iconImagePathAlt?: string;
  iconImagePathAlt2?: string;
  menuIconScale?: number;
  menuIconOffsetX?: number;
  menuIconOffsetY?: number;
  menuIconOffsetXAlt?: number;
  menuIconOffsetYAlt?: number;
  menuIconOffsetXAlt2?: number;
  menuIconOffsetYAlt2?: number;
  managerIconVisual?: AvatarToolDefinition['visual']['managerIcon'];
  pointerImagePath: string;
  pointerImagePathAlt?: string;
  pointerImagePathAlt2?: string;
  pointerHotspotX?: number;
  pointerHotspotY?: number;
  pointerNaturalWidth?: number;
  pointerNaturalHeight?: number;
  pointerDisplayWidth?: number;
  pointerDisplayHeight?: number;
};

export function projectAvatarToolDefinitionToItem(definition: AvatarToolDefinition): AvatarToolItem {
  const { primary, secondary, tertiary } = definition.visual.variants;
  return {
    id: definition.id,
    label: definition.label,
    iconImagePath: primary.iconImagePath,
    ...(secondary.iconImagePath !== primary.iconImagePath ? { iconImagePathAlt: secondary.iconImagePath } : {}),
    ...(tertiary.iconImagePath !== primary.iconImagePath ? { iconImagePathAlt2: tertiary.iconImagePath } : {}),
    pointerImagePath: primary.pointerImagePath,
    ...(secondary.pointerImagePath !== primary.pointerImagePath ? { pointerImagePathAlt: secondary.pointerImagePath } : {}),
    ...(tertiary.pointerImagePath !== secondary.pointerImagePath ? { pointerImagePathAlt2: tertiary.pointerImagePath } : {}),
    ...(definition.visual.menuScale !== 1 ? { menuIconScale: definition.visual.menuScale } : {}),
    ...(primary.menuOffsetX !== 0 ? { menuIconOffsetX: primary.menuOffsetX } : {}),
    ...(primary.menuOffsetY !== 0 ? { menuIconOffsetY: primary.menuOffsetY } : {}),
    ...(secondary.menuOffsetX !== primary.menuOffsetX ? { menuIconOffsetXAlt: secondary.menuOffsetX } : {}),
    ...(secondary.menuOffsetY !== primary.menuOffsetY ? { menuIconOffsetYAlt: secondary.menuOffsetY } : {}),
    ...(tertiary.menuOffsetX !== secondary.menuOffsetX ? { menuIconOffsetXAlt2: tertiary.menuOffsetX } : {}),
    ...(tertiary.menuOffsetY !== secondary.menuOffsetY ? { menuIconOffsetYAlt2: tertiary.menuOffsetY } : {}),
    ...(definition.visual.managerIcon ? { managerIconVisual: definition.visual.managerIcon } : {}),
    pointerHotspotX: definition.visual.hotspotX,
    pointerHotspotY: definition.visual.hotspotY,
    pointerNaturalWidth: definition.visual.naturalWidth,
    pointerNaturalHeight: definition.visual.naturalHeight,
    pointerDisplayWidth: definition.visual.pointer.displayWidth,
    pointerDisplayHeight: definition.visual.pointer.displayHeight,
  };
}

export type AvatarToolRegistrySnapshot = {
  readonly registrations: ReadonlyArray<AvatarToolRegistration>;
  readonly definitions: ReadonlyArray<AvatarToolDefinition>;
  readonly items: ReadonlyArray<AvatarToolItem>;
  readonly validIds: ReadonlySet<AvatarToolId>;
  has(toolId: unknown): toolId is AvatarToolId;
  getRegistration(toolId: AvatarToolId): AvatarToolRegistration;
  getSound(toolId: AvatarToolId, soundId: AvatarToolSoundId): AvatarToolSoundResource;
  getEffect(toolId: AvatarToolId, effectId: AvatarToolEffectId): AvatarToolEffectRecipe;
};

export function createAvatarToolRegistrySnapshot(
  localDefinitions: ReadonlyArray<AvatarToolDefinition> = [],
): AvatarToolRegistrySnapshot {
  const registrations: AvatarToolRegistration[] = [...AVATAR_TOOL_REGISTRY];
  localDefinitions.forEach((definition) => {
    validateAvatarToolDefinition(definition);
    registrations.push(registerAvatarTool(definition));
  });
  const byId = new Map<AvatarToolId, AvatarToolRegistration>();
  registrations.forEach((registration) => {
    if (byId.has(registration.definition.id)) {
      throw new Error(`Duplicate avatar tool definition: ${registration.definition.id}`);
    }
    byId.set(registration.definition.id, registration);
  });
  const definitions = registrations.map(registration => registration.definition);
  const items = definitions.map(projectAvatarToolDefinitionToItem);
  const validIds = new Set(byId.keys());
  return Object.freeze({
    registrations: Object.freeze(registrations),
    definitions: Object.freeze(definitions),
    items: Object.freeze(items),
    validIds,
    has(toolId: unknown): toolId is AvatarToolId {
      return typeof toolId === 'string' && byId.has(toolId as AvatarToolId);
    },
    getRegistration(toolId: AvatarToolId) {
      const registration = byId.get(toolId);
      if (!registration) throw new Error(`Unsupported avatar tool: ${toolId}`);
      return registration;
    },
    getSound(toolId: AvatarToolId, soundId: AvatarToolSoundId) {
      const sound = byId.get(toolId)?.definition.sounds.find(resource => resource.id === soundId);
      if (!sound) throw new Error(`Unsupported avatar tool sound: ${toolId}/${soundId}`);
      return sound;
    },
    getEffect(toolId: AvatarToolId, effectId: AvatarToolEffectId) {
      const effect = byId.get(toolId)?.definition.effects.find(resource => resource.id === effectId);
      if (!effect) throw new Error(`Unsupported avatar tool effect: ${toolId}/${effectId}`);
      return effect;
    },
  });
}

export const BUILT_IN_AVATAR_TOOL_REGISTRY = createAvatarToolRegistrySnapshot();
