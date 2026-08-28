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

console.log(JSON.stringify(descriptors));
