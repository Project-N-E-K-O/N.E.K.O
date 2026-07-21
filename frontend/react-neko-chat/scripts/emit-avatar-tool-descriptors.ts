import { buildAvatarToolSelectionStatePayload } from '../src/avatar-tools/protocol';
import { AVAILABLE_AVATAR_TOOLS } from '../src/avatarTools';

console.log(JSON.stringify(AVAILABLE_AVATAR_TOOLS.map(activeTool => (
  buildAvatarToolSelectionStatePayload({
    activeTool,
    avatarRangeVariant: 'primary',
    outsideRangeVariant: 'primary',
  })
))));
