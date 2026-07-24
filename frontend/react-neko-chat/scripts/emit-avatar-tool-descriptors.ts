import { buildAvatarToolSelectionStatePayload } from '../src/avatar-tools/protocol';
import { AVAILABLE_COMPACT_AVATAR_TOOLS } from '../src/avatarTools';

console.log(JSON.stringify(AVAILABLE_COMPACT_AVATAR_TOOLS.map(activeTool => (
  buildAvatarToolSelectionStatePayload({
    activeTool,
    avatarRangeVariant: 'primary',
    outsideRangeVariant: 'primary',
  })
))));
