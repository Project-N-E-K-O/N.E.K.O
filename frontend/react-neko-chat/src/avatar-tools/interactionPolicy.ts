import { z } from 'zod';


const finiteNumberSchema = z.number().finite();
const nonNegativeNumberSchema = finiteNumberSchema.nonnegative();
const positiveNumberSchema = finiteNumberSchema.positive();
const ratioSchema = positiveNumberSchema.max(1);

const touchZonesSchema = z.object({
  coordinateSpace: z.literal('normalized-avatar-bounds'),
  clampToBounds: z.literal(true),
  boundary: z.literal('inclusive'),
  ear: z.object({
    maxY: ratioSchema,
    leftMaxX: ratioSchema,
    rightMinX: ratioSchema,
  }).strict(),
  headMaxY: ratioSchema,
  faceMaxY: ratioSchema,
  fallback: z.literal('body'),
}).strict().superRefine((touchZones, context) => {
  if (touchZones.ear.maxY > touchZones.headMaxY || touchZones.headMaxY > touchZones.faceMaxY) {
    context.addIssue({ code: z.ZodIssueCode.custom, message: 'touch-zone Y thresholds must be ordered' });
  }
  if (touchZones.ear.leftMaxX >= touchZones.ear.rightMinX) {
    context.addIssue({
      code: z.ZodIssueCode.custom,
      path: ['ear'],
      message: 'ear X thresholds must leave a center region',
    });
  }
});

const rangeSchema = z.object({
  geometry: z.object({
    shape: z.literal('ellipse'),
    radiusXFromWidth: ratioSchema,
    radiusYFromHeight: ratioSchema,
    boundary: z.literal('inclusive'),
  }).strict(),
  enterPadding: nonNegativeNumberSchema,
  exitPadding: nonNegativeNumberSchema,
  visualHold: z.object({
    durationMs: nonNegativeNumberSchema,
    semantics: z.literal('presentation-only'),
    grantsInteractionHit: z.literal(false),
  }).strict(),
  bounds: z.object({
    cacheTtlMs: nonNegativeNumberSchema,
    missingGraceMs: nonNegativeNumberSchema,
  }).strict(),
  touchZones: touchZonesSchema,
  forcedExit: z.object({
    uiExclusion: z.literal('immediate'),
    deactivation: z.literal('immediate'),
    hostExit: z.literal('immediate'),
  }).strict(),
}).strict().superRefine((range, context) => {
  if (range.exitPadding < range.enterPadding) {
    context.addIssue({
      code: z.ZodIssueCode.custom,
      path: ['exitPadding'],
      message: 'range.exitPadding must be at least range.enterPadding',
    });
  }
});

export const avatarToolRuntimePolicySchema = z.object({
  policyVersion: z.literal(1),
  range: rangeSchema,
  press: z.object({
    button: z.literal(0),
    requiresRawHit: z.literal(true),
    matchingRelease: z.object({
      pointerId: z.literal('same-as-press'),
      button: z.literal('same-as-press'),
    }).strict(),
    move: z.object({
      thresholdPx: positiveNumberSchema,
      comparison: z.literal('strictly-greater'),
    }).strict(),
  }).strict(),
  release: z.object({
    bounds: z.literal('fresh'),
    heldVisualRangeIsHit: z.literal(false),
    touchZone: z.literal('fresh-release-hit'),
    uiExclusion: z.literal('reject'),
  }).strict(),
}).strict();

export type AvatarToolRuntimePolicy = z.infer<typeof avatarToolRuntimePolicySchema>;

export function validateAvatarToolRuntimePolicy(policy: unknown): asserts policy is AvatarToolRuntimePolicy {
  avatarToolRuntimePolicySchema.parse(policy);
}

export const AVATAR_TOOL_RUNTIME_POLICY = {
  policyVersion: 1,
  range: {
    geometry: {
      shape: 'ellipse',
      radiusXFromWidth: 0.3,
      radiusYFromHeight: 0.475,
      boundary: 'inclusive',
    },
    enterPadding: 100,
    exitPadding: 116,
    visualHold: {
      durationMs: 180,
      semantics: 'presentation-only',
      grantsInteractionHit: false,
    },
    bounds: {
      cacheTtlMs: 80,
      missingGraceMs: 640,
    },
    touchZones: {
      coordinateSpace: 'normalized-avatar-bounds',
      clampToBounds: true,
      boundary: 'inclusive',
      ear: {
        maxY: 0.24,
        leftMaxX: 0.24,
        rightMinX: 0.76,
      },
      headMaxY: 0.34,
      faceMaxY: 0.62,
      fallback: 'body',
    },
    forcedExit: {
      uiExclusion: 'immediate',
      deactivation: 'immediate',
      hostExit: 'immediate',
    },
  },
  press: {
    button: 0,
    requiresRawHit: true,
    matchingRelease: {
      pointerId: 'same-as-press',
      button: 'same-as-press',
    },
    move: {
      thresholdPx: 6,
      comparison: 'strictly-greater',
    },
  },
  release: {
    bounds: 'fresh',
    heldVisualRangeIsHit: false,
    touchZone: 'fresh-release-hit',
    uiExclusion: 'reject',
  },
} as const satisfies AvatarToolRuntimePolicy;

validateAvatarToolRuntimePolicy(AVATAR_TOOL_RUNTIME_POLICY);

export const AVATAR_TOOL_RANGE_PADDING = AVATAR_TOOL_RUNTIME_POLICY.range.enterPadding;
export const AVATAR_TOOL_RANGE_EXIT_PADDING = AVATAR_TOOL_RUNTIME_POLICY.range.exitPadding;
