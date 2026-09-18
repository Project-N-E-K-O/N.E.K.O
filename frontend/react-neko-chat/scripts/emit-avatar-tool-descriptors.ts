import { buildAvatarToolSelectionStatePayload } from '../src/avatar-tools/protocol';
import { buildLocalAvatarToolDefinition } from '../src/avatar-tools/localTools';
import { AVAILABLE_COMPACT_AVATAR_TOOLS } from '../src/avatarTools';

const localToolId = 'local-12345678-1234-4123-8123-123456789abc' as const;
const localDefaultUrl = `/user_avatar_tools/${localToolId}/default.png?v=cross-repo`;
const localTool = {
  id: localToolId,
  iconImagePath: localDefaultUrl,
  pointerImagePath: localDefaultUrl,
};
const localDefinition = buildLocalAvatarToolDefinition({
  id: localToolId,
  revision: '2-123',
  name: 'Cross-repo surprise fixture',
  changeMode: 'click-advance',
  defaultUrl: localDefaultUrl,
  changeUrls: [
    `/user_avatar_tools/${localToolId}/change-000.png?v=cross-repo`,
    `/user_avatar_tools/${localToolId}/change-001.png?v=cross-repo`,
  ],
  normalSoundUrl: `/user_avatar_tools/${localToolId}/normal.mp3?v=cross-repo`,
  special: {
    probability: 0.25,
    imageUrl: `/user_avatar_tools/${localToolId}/special.png?v=cross-repo`,
    soundUrl: `/user_avatar_tools/${localToolId}/special.mp3?v=cross-repo`,
  },
});
const graphToolId = 'local-aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa' as const;
const graphAsset = (name: string) => `/user_avatar_tools/${graphToolId}/${name}?v=cross-repo`;
const graphTool = {
  id: graphToolId,
  iconImagePath: graphAsset('image-000.png'),
  pointerImagePath: graphAsset('image-000.png'),
};
const graphDefinition = buildLocalAvatarToolDefinition({
  recordVersion: 3,
  id: graphToolId,
  revision: '3-456',
  name: 'Cross-repo graph fixture',
  initialImageUrl: graphAsset('image-000.png'),
  runtime: {
    images: [
      { id: 'img-a', url: graphAsset('image-000.png'), hasMeaning: true },
      { id: 'img-b', url: graphAsset('image-001.png'), hasMeaning: false },
      { id: 'img-c', url: graphAsset('image-002.png'), hasMeaning: true },
    ],
    initialImageId: 'img-a',
    initialInteractionIds: ['ix-click'],
    interactions: [
      {
        id: 'ix-click',
        trigger: { kind: 'mouse-click' },
        actions: {
          press: { kind: 'show', imageId: 'img-b' },
          release: { kind: 'show', imageId: 'img-c' },
        },
      },
      {
        id: 'ix-delay',
        trigger: { kind: 'after', delayMs: 800 },
        actions: { complete: { kind: 'show', imageId: 'img-a' } },
      },
    ],
    links: [
      { from: 'ix-click', to: 'ix-delay' },
      { from: 'ix-delay', to: 'ix-click' },
    ],
    normalSoundUrl: graphAsset('normal.mp3'),
    special: {
      probability: 0.25,
      imageUrl: graphAsset('special.png'),
      hasMeaning: true,
      soundUrl: graphAsset('special.mp3'),
    },
  },
});

const descriptors = AVAILABLE_COMPACT_AVATAR_TOOLS.map(activeTool => (
  buildAvatarToolSelectionStatePayload({
    activeTool,
    avatarRangeVariant: 'primary',
    outsideRangeVariant: 'primary',
  })
));
descriptors.push(buildAvatarToolSelectionStatePayload({
  activeTool: localTool,
  avatarRangeVariant: 'primary',
  outsideRangeVariant: 'primary',
  definition: localDefinition,
}));
descriptors.push(buildAvatarToolSelectionStatePayload({
  activeTool: graphTool,
  avatarRangeVariant: 'primary',
  outsideRangeVariant: 'primary',
  definition: graphDefinition,
}));

console.log(JSON.stringify(descriptors));
