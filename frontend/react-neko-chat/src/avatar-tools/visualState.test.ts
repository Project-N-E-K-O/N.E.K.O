import { describe, expect, it } from 'vitest';
import { AVAILABLE_AVATAR_TOOLS } from '../avatarTools';
import {
  createAvatarToolVariantState,
  deriveAvatarToolPresentation,
  getAvatarToolOverlayTransform,
  getAvatarToolOverlayTransformFromDefinition,
  resolveAvatarToolVisualPresentation,
} from './visualState';
import {
  AVATAR_TOOL_DEFINITIONS,
  getAvatarToolRegistration,
  type AvatarToolDefinition,
} from './catalog';

describe('avatar tool presentation', () => {
  it('initializes every catalogued tool without a hard-coded variant object', () => {
    expect(createAvatarToolVariantState()).toEqual({
      lollipop: 'primary',
      fist: 'primary',
      hammer: 'primary',
    });
  });

  it('derives the tool-specific effective variant and shared enlarged image kind', () => {
    const rangeVariants = {
      ...createAvatarToolVariantState(),
      lollipop: 'tertiary' as const,
      fist: 'secondary' as const,
      hammer: 'secondary' as const,
    };
    const outsideVariants = {
      ...createAvatarToolVariantState(),
      fist: 'tertiary' as const,
      hammer: 'secondary' as const,
    };

    expect(deriveAvatarToolPresentation({
      activeToolId: 'lollipop',
      rangeVariants,
      outsideVariants,
      overAvatarRange: false,
      overCompactZone: false,
      insideHostWindow: true,
      effectActive: false,
    })).toMatchObject({ effectiveVariant: 'tertiary', imageKind: 'pointer' });

    expect(deriveAvatarToolPresentation({
      activeToolId: 'fist',
      rangeVariants,
      outsideVariants,
      overAvatarRange: true,
      overCompactZone: false,
      insideHostWindow: true,
      effectActive: false,
    })).toMatchObject({
      effectiveVariant: 'secondary',
      withinAvatarRange: true,
      imageKind: 'icon',
    });

    expect(deriveAvatarToolPresentation({
      activeToolId: 'hammer',
      rangeVariants,
      outsideVariants,
      overAvatarRange: false,
      overCompactZone: false,
      insideHostWindow: true,
      effectActive: true,
    })).toMatchObject({ effectiveVariant: 'secondary', imageKind: 'icon' });
  });

  it('keeps compact UI overlap in pointer mode even while the pointer is over avatar bounds', () => {
    const variants = createAvatarToolVariantState();
    expect(deriveAvatarToolPresentation({
      activeToolId: 'fist',
      rangeVariants: variants,
      outsideVariants: variants,
      overAvatarRange: true,
      overCompactZone: true,
      insideHostWindow: true,
      effectActive: false,
    })).toMatchObject({ withinAvatarRange: false, imageKind: 'pointer' });
  });

  it('uses definition initial variants and presentation policy as runtime inputs', () => {
    const definitions = AVATAR_TOOL_DEFINITIONS.map(definition => definition.id === 'fist'
      ? {
        ...definition,
        visual: { ...definition.visual, initialVariant: 'tertiary' as const },
      }
      : definition);
    expect(createAvatarToolVariantState(definitions).fist).toBe('tertiary');

    const source = getAvatarToolRegistration('lollipop').definition;
    const withOutsidePolicy = {
      ...source,
      visual: {
        ...source.visual,
        presentation: {
          ...source.visual.presentation,
          outsideVariantSource: 'outside' as const,
        },
      },
    } satisfies AvatarToolDefinition;
    expect(resolveAvatarToolVisualPresentation({
      definition: withOutsidePolicy,
      rangeVariant: 'tertiary',
      outsideVariant: 'secondary',
      overAvatarRange: false,
      withinAvatarRange: false,
      effectActive: false,
    })).toEqual({ effectiveVariant: 'secondary', imageKind: 'pointer' });
  });
});

describe('avatar tool visual runtime geometry', () => {
  it('preserves the current pointer and in-range anchors for every tool', () => {
    const pointer = { x: 100, y: 100 };
    const transforms = Object.fromEntries(AVAILABLE_AVATAR_TOOLS.map(item => [item.id, {
      pointer: getAvatarToolOverlayTransform(item, true, pointer),
      inRange: getAvatarToolOverlayTransform(item, false, pointer),
    }]));

    expect(transforms).toEqual({
      lollipop: {
        pointer: 'translate3d(79.66px, 65.22px, 0)',
        inRange: 'translate3d(63.67px, 37.9px, 0)',
      },
      fist: {
        pointer: 'translate3d(78.16px, 74.24px, 0)',
        inRange: 'translate3d(61px, 54px, 0)',
      },
      hammer: {
        pointer: 'translate3d(74px, 71.92px, 0)',
        inRange: 'translate3d(50px, 46px, 0)',
      },
    });
  });

  it('uses the selected visual mode rendered anchor instead of tool-id branches', () => {
    const source = getAvatarToolRegistration('hammer').definition;
    const definition = {
      ...source,
      visual: {
        ...source.visual,
        pointer: {
          ...source.visual.pointer,
          renderedAnchor: { x: 1, y: 2, coordinateSpace: 'final-css-pixel' as const },
        },
        inRange: {
          ...source.visual.inRange,
          renderedAnchor: { x: 3, y: 4, coordinateSpace: 'final-css-pixel' as const },
        },
      },
    } satisfies AvatarToolDefinition;

    expect(getAvatarToolOverlayTransformFromDefinition(definition, true, { x: 100, y: 100 }))
      .toBe('translate3d(99px, 98px, 0)');
    expect(getAvatarToolOverlayTransformFromDefinition(definition, false, { x: 100, y: 100 }))
      .toBe('translate3d(97px, 96px, 0)');
  });

  it('subtracts a final CSS-pixel anchor exactly once without applying visual scale again', () => {
    const source = getAvatarToolRegistration('hammer').definition;
    expect(source.visual.pointer).toMatchObject({
      displayCoordinateSpace: 'pre-scale-css-pixel',
      scale: 0.52,
      renderedAnchor: { x: 26, y: 28.08, coordinateSpace: 'final-css-pixel' },
    });
    expect(getAvatarToolOverlayTransformFromDefinition(source, true, { x: 26, y: 28.08 }))
      .toBe('translate3d(0px, 0px, 0)');
  });
});
