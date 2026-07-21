import type { MouseEvent as ReactMouseEvent } from 'react';
import { i18n } from './i18n';
import {
  type AvatarToolId,
  type AvatarToolItem,
  type AvatarToolVariantId,
  resolveAvatarToolMenuIconVisual,
  withAvatarToolAssetVersion,
} from './avatarTools';

type AvatarToolQuickbarProps = {
  activeToolIds: AvatarToolId[];
  activeAvatarToolId: string | null;
  availableTools: AvatarToolItem[];
  disabled?: boolean;
  getToolVariant: (toolId: AvatarToolId) => AvatarToolVariantId;
  onToolClick: (tool: AvatarToolItem, event: ReactMouseEvent<HTMLButtonElement>) => void;
  onEditClick: (event: ReactMouseEvent<HTMLButtonElement>) => void;
};

function getToolLabel(tool: AvatarToolItem): string {
  return i18n(tool.labelKey, tool.labelFallback);
}

export default function AvatarToolQuickbar({
  activeToolIds,
  activeAvatarToolId,
  availableTools,
  disabled = false,
  getToolVariant,
  onToolClick,
  onEditClick,
}: AvatarToolQuickbarProps) {
  const availableById = new Map(availableTools.map(tool => [tool.id, tool]));
  const activeTools = activeToolIds
    .map(toolId => availableById.get(toolId))
    .filter((tool): tool is AvatarToolItem => !!tool);

  return (
    <div
      id="composer-avatar-tool-quickbar"
      className="avatar-tool-quickbar"
      role="group"
      aria-label={i18n('chat.avatarToolQuickbarAriaLabel', 'Avatar quick tools')}
      data-avatar-tool-quickbar-empty={activeTools.length === 0 ? 'true' : 'false'}
    >
      <div
        id="composer-tool-popover-compact"
        className="avatar-tool-quickbar-scroll"
      >
        {activeTools.length > 0 ? activeTools.map((tool) => {
          const label = getToolLabel(tool);
          const visual = resolveAvatarToolMenuIconVisual(tool, getToolVariant(tool.id));
          return (
            <button
              key={tool.id}
              className={`composer-icon-button avatar-tool-quickbar-button${activeAvatarToolId === tool.id ? ' is-active' : ''}`}
              type="button"
              data-avatar-tool-id={tool.id}
              aria-label={label}
              aria-pressed={activeAvatarToolId === tool.id}
              title={label}
              disabled={disabled}
              onClick={(event) => onToolClick(tool, event)}
            >
              <img
                className="composer-icon-button-image avatar-tool-quickbar-image"
                src={visual.imagePath}
                style={{
                  transform: `translate(${visual.offsetX}px, ${visual.offsetY}px) scale(${tool.menuIconScale ?? 1})`,
                }}
                alt=""
                aria-hidden="true"
              />
            </button>
          );
        }) : (
          <span className="avatar-tool-quickbar-empty">
            {i18n('chat.avatarToolQuickbarEmpty', 'No quick tools')}
          </span>
        )}
      </div>
      <button
        className="avatar-tool-quickbar-edit"
        type="button"
        aria-label={i18n('chat.avatarToolEdit', 'Edit quick tools')}
        title={i18n('chat.avatarToolEdit', 'Edit quick tools')}
        disabled={disabled}
        onClick={onEditClick}
      >
        <img
          className="avatar-tool-quickbar-edit-image"
          src={withAvatarToolAssetVersion('/static/assets/avatar-tools/ui/edit.png')}
          alt=""
          aria-hidden="true"
        />
      </button>
    </div>
  );
}
