import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { tOrDefault, useT } from "../i18n";
import "./Live2DRightToolbar.css";

export type Live2DRightToolbarButtonId = "mic" | "screen" | "agent" | "settings" | "goodbye" | "return";

export type Live2DRightToolbarPanel = "agent" | "settings" | null;

export type Live2DSettingsToggleId = "mergeMessages" | "allowInterrupt" | "proactiveChat" | "proactiveVision";
export type Live2DAgentToggleId = "master" | "keyboard" | "mcp" | "userPlugin";

export interface Live2DSettingsState {
  mergeMessages: boolean;
  allowInterrupt: boolean;
  proactiveChat: boolean;
  proactiveVision: boolean;
}

export interface Live2DAgentState {
  statusText: string;
  master: boolean;
  keyboard: boolean;
  mcp: boolean;
  userPlugin: boolean;
  disabled: Partial<Record<Live2DAgentToggleId, boolean>>;
}

export type Live2DSettingsMenuId =
  | "live2dSettings"
  | "apiKeys"
  | "characterManage"
  | "voiceClone"
  | "memoryBrowser"
  | "steamWorkshop";

export interface Live2DRightToolbarProps {
  visible?: boolean;
  right?: number;
  bottom?: number;
  top?: number;
  isMobile?: boolean;

  micEnabled: boolean;
  screenEnabled: boolean;
  goodbyeMode: boolean;

  openPanel: Live2DRightToolbarPanel;
  onOpenPanelChange: (panel: Live2DRightToolbarPanel) => void;

  settings: Live2DSettingsState;
  onSettingsChange: (id: Live2DSettingsToggleId, next: boolean) => void;

  agent: Live2DAgentState;
  onAgentChange: (id: Live2DAgentToggleId, next: boolean) => void;

  onToggleMic: (next: boolean) => void;
  onToggleScreen: (next: boolean) => void;
  onGoodbye: () => void;
  onReturn: () => void;

  onSettingsMenuClick?: (id: Live2DSettingsMenuId) => void;
}

export function Live2DRightToolbar({
  visible = true,
  right = 460,
  bottom,
  top,
  isMobile,
  micEnabled,
  screenEnabled,
  goodbyeMode,
  openPanel,
  onOpenPanelChange,
  settings,
  onSettingsChange,
  agent,
  onAgentChange,
  onToggleMic,
  onToggleScreen,
  onGoodbye,
  onReturn,
  onSettingsMenuClick,
}: Live2DRightToolbarProps) {
  const t = useT();
  const rootRef = useRef<HTMLDivElement | null>(null);
  const [closingPanel, setClosingPanel] = useState<Exclude<Live2DRightToolbarPanel, null> | null>(null);
  const closeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const PANEL_ANIM_MS = 240;

  const containerStyle = useMemo<React.CSSProperties>(() => {
    const style: React.CSSProperties = {
      right,
    };

    if (typeof top === "number") {
      style.top = top;
    } else {
      style.bottom = typeof bottom === "number" ? bottom : 320;
    }

    return style;
  }, [right, top, bottom]);

  const startClose = useCallback(
    (panel: Exclude<Live2DRightToolbarPanel, null>) => {
      setClosingPanel(panel);
      onOpenPanelChange(null);

      if (closeTimerRef.current) {
        clearTimeout(closeTimerRef.current);
      }
      closeTimerRef.current = setTimeout(() => {
        setClosingPanel((prev) => (prev === panel ? null : prev));
        closeTimerRef.current = null;
      }, PANEL_ANIM_MS);
    },
    [onOpenPanelChange]
  );

  const togglePanel = useCallback(
    (panel: Exclude<Live2DRightToolbarPanel, null>) => {
      if (openPanel === panel) {
        startClose(panel);
        return;
      }

      // 切换：先关掉旧 panel（播放退出动画），再打开新 panel
      if (openPanel) {
        startClose(openPanel);
      }
      onOpenPanelChange(panel);
    },
    [onOpenPanelChange, openPanel, startClose]
  );

  useEffect(() => {
    return () => {
      if (closeTimerRef.current) {
        clearTimeout(closeTimerRef.current);
        closeTimerRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    const onPointerDown = (e: PointerEvent) => {
      const root = rootRef.current;
      if (!root) return;
      if (!openPanel) return;
      const target = e.target as Node | null;
      if (target && root.contains(target)) return;
      startClose(openPanel);
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [openPanel, startClose]);

  const buttons = useMemo(
    () =>
      [
        {
          id: "mic" as const,
          title: tOrDefault(t, "buttons.voiceControl", "语音控制"),
          hidden: false,
          active: micEnabled,
          onClick: () => onToggleMic(!micEnabled),
          icon: "/static/icons/mic_icon_off.png",
        },
        {
          id: "screen" as const,
          title: tOrDefault(t, "buttons.screenShare", "屏幕分享"),
          hidden: false,
          active: screenEnabled,
          onClick: () => onToggleScreen(!screenEnabled),
          icon: "/static/icons/screen_icon_off.png",
        },
        {
          id: "agent" as const,
          title: tOrDefault(t, "buttons.agentTools", "Agent工具"),
          hidden: Boolean(isMobile),
          active: openPanel === "agent",
          onClick: () => togglePanel("agent"),
          icon: "/static/icons/Agent_off.png",
          hasPanel: true,
        },
        {
          id: "settings" as const,
          title: tOrDefault(t, "buttons.settings", "设置"),
          hidden: false,
          active: openPanel === "settings",
          onClick: () => togglePanel("settings"),
          icon: "/static/icons/set_off.png",
          hasPanel: true,
        },
        {
          id: "goodbye" as const,
          title: tOrDefault(t, "buttons.leave", "请她离开"),
          hidden: Boolean(isMobile),
          active: goodbyeMode,
          onClick: onGoodbye,
          icon: "/static/icons/rest_off.png",
          hasPanel: false,
        },
      ].filter((b) => !b.hidden),
    [goodbyeMode, isMobile, micEnabled, onGoodbye, onToggleMic, onToggleScreen, openPanel, screenEnabled, t, togglePanel]
  );

  const settingsToggleRows = useMemo(
    () => [
      {
        id: "mergeMessages" as const,
        label: tOrDefault(t, "settings.toggles.mergeMessages", "合并消息"),
        checked: settings.mergeMessages,
      },
      {
        id: "allowInterrupt" as const,
        label: tOrDefault(t, "settings.toggles.allowInterrupt", "允许打断"),
        checked: settings.allowInterrupt,
      },
      {
        id: "proactiveChat" as const,
        label: tOrDefault(t, "settings.toggles.proactiveChat", "主动搭话"),
        checked: settings.proactiveChat,
      },
      {
        id: "proactiveVision" as const,
        label: tOrDefault(t, "settings.toggles.proactiveVision", "自主视觉"),
        checked: settings.proactiveVision,
      },
    ],
    [settings, t]
  );

  const agentToggleRows = useMemo(
    () => [
      {
        id: "master" as const,
        label: tOrDefault(t, "settings.toggles.agentMaster", "Agent总开关"),
        checked: agent.master,
        disabled: Boolean(agent.disabled.master),
      },
      {
        id: "keyboard" as const,
        label: tOrDefault(t, "settings.toggles.keyboardControl", "键鼠控制"),
        checked: agent.keyboard,
        disabled: Boolean(agent.disabled.keyboard),
      },
      {
        id: "mcp" as const,
        label: tOrDefault(t, "settings.toggles.mcpTools", "MCP工具"),
        checked: agent.mcp,
        disabled: Boolean(agent.disabled.mcp),
      },
      {
        id: "userPlugin" as const,
        label: tOrDefault(t, "settings.toggles.userPlugin", "用户插件"),
        checked: agent.userPlugin,
        disabled: Boolean(agent.disabled.userPlugin),
      },
    ],
    [agent, t]
  );

  if (!visible) return null;

  return (
    <div ref={rootRef} className="live2d-right-toolbar" style={containerStyle}>
      {goodbyeMode ? (
        <button
          type="button"
          className="live2d-right-toolbar__button live2d-right-toolbar__return"
          title={tOrDefault(t, "buttons.return", "请她回来")}
          onClick={onReturn}
        >
          <img className="live2d-right-toolbar__icon" src="/static/icons/rest_off.png" alt="return" />
        </button>
      ) : (
        buttons.map((b) => (
          <div key={b.id} className="live2d-right-toolbar__item">
            <button
              type="button"
              className="live2d-right-toolbar__button"
              title={b.title}
              data-active={b.active ? "true" : "false"}
              onClick={b.onClick}
            >
              <img className="live2d-right-toolbar__icon" src={b.icon} alt={b.id} />
            </button>

            {(b.id === "settings" && (openPanel === "settings" || closingPanel === "settings")) && (
              <div
                key={`settings-panel-${openPanel === "settings" ? "open" : "closing"}`}
                className={`live2d-right-toolbar__panel live2d-right-toolbar__panel--settings${
                  closingPanel === "settings" && openPanel !== "settings" ? " live2d-right-toolbar__panel--exit" : ""
                }`}
                role="menu"
              >
                {settingsToggleRows.map((x) => (
                  <label key={x.id} className="live2d-right-toolbar__row" data-disabled="false">
                    <input
                      type="checkbox"
                      className="live2d-right-toolbar__checkbox"
                      checked={x.checked}
                      onChange={(e: React.ChangeEvent<HTMLInputElement>) => onSettingsChange(x.id, e.target.checked)}
                    />
                    <span className="live2d-right-toolbar__indicator" aria-hidden="true">
                      <span className="live2d-right-toolbar__checkmark">✓</span>
                    </span>
                    <span className="live2d-right-toolbar__label">{x.label}</span>
                  </label>
                ))}

                {!isMobile && (
                  <>
                    <div className="live2d-right-toolbar__separator" />
                    <button
                      type="button"
                      className="live2d-right-toolbar__menuItem"
                      onClick={() => onSettingsMenuClick?.("live2dSettings")}
                    >
                      <span className="live2d-right-toolbar__menuItemContent">
                        <img
                          className="live2d-right-toolbar__menuIcon"
                          src="/static/icons/live2d_settings_icon.png"
                          alt={tOrDefault(t, "settings.menu.live2dSettings", "Live2D设置")}
                        />
                        {tOrDefault(t, "settings.menu.live2dSettings", "Live2D设置")}
                      </span>
                    </button>
                    <button
                      type="button"
                      className="live2d-right-toolbar__menuItem"
                      onClick={() => onSettingsMenuClick?.("apiKeys")}
                    >
                      <span className="live2d-right-toolbar__menuItemContent">
                        <img
                          className="live2d-right-toolbar__menuIcon"
                          src="/static/icons/api_key_icon.png"
                          alt={tOrDefault(t, "settings.menu.apiKeys", "API密钥")}
                        />
                        {tOrDefault(t, "settings.menu.apiKeys", "API密钥")}
                      </span>
                    </button>
                    <button
                      type="button"
                      className="live2d-right-toolbar__menuItem"
                      onClick={() => onSettingsMenuClick?.("characterManage")}
                    >
                      <span className="live2d-right-toolbar__menuItemContent">
                        <img
                          className="live2d-right-toolbar__menuIcon"
                          src="/static/icons/character_icon.png"
                          alt={tOrDefault(t, "settings.menu.characterManage", "角色管理")}
                        />
                        {tOrDefault(t, "settings.menu.characterManage", "角色管理")}
                      </span>
                    </button>
                    <button
                      type="button"
                      className="live2d-right-toolbar__menuItem"
                      onClick={() => onSettingsMenuClick?.("voiceClone")}
                    >
                      <span className="live2d-right-toolbar__menuItemContent">
                        <img
                          className="live2d-right-toolbar__menuIcon"
                          src="/static/icons/voice_clone_icon.png"
                          alt={tOrDefault(t, "settings.menu.voiceClone", "声音克隆")}
                        />
                        {tOrDefault(t, "settings.menu.voiceClone", "声音克隆")}
                      </span>
                    </button>
                    <button
                      type="button"
                      className="live2d-right-toolbar__menuItem"
                      onClick={() => onSettingsMenuClick?.("memoryBrowser")}
                    >
                      <span className="live2d-right-toolbar__menuItemContent">
                        <img
                          className="live2d-right-toolbar__menuIcon"
                          src="/static/icons/memory_icon.png"
                          alt={tOrDefault(t, "settings.menu.memoryBrowser", "记忆浏览")}
                        />
                        {tOrDefault(t, "settings.menu.memoryBrowser", "记忆浏览")}
                      </span>
                    </button>
                    <button
                      type="button"
                      className="live2d-right-toolbar__menuItem"
                      onClick={() => onSettingsMenuClick?.("steamWorkshop")}
                    >
                      <span className="live2d-right-toolbar__menuItemContent">
                        <img
                          className="live2d-right-toolbar__menuIcon"
                          src="/static/icons/Steam_icon_logo.png"
                          alt={tOrDefault(t, "settings.menu.steamWorkshop", "创意工坊")}
                        />
                        {tOrDefault(t, "settings.menu.steamWorkshop", "创意工坊")}
                      </span>
                    </button>
                  </>
                )}
              </div>
            )}

            {(b.id === "agent" && (openPanel === "agent" || closingPanel === "agent")) && (
              <div
                key={`agent-panel-${openPanel === "agent" ? "open" : "closing"}`}
                className={`live2d-right-toolbar__panel live2d-right-toolbar__panel--agent${
                  closingPanel === "agent" && openPanel !== "agent" ? " live2d-right-toolbar__panel--exit" : ""
                }`}
                role="menu"
              >
                <div id="live2d-agent-status" className="live2d-right-toolbar__status">
                  {agent.statusText}
                </div>
                {agentToggleRows.map((x) => (
                  <label
                    key={x.id}
                    className="live2d-right-toolbar__row"
                    data-disabled={x.disabled ? "true" : "false"}
                    title={x.disabled ? tOrDefault(t, "settings.toggles.checking", "查询中...") : undefined}
                  >
                    <input
                      type="checkbox"
                      className="live2d-right-toolbar__checkbox"
                      checked={x.checked}
                      disabled={x.disabled}
                      onChange={(e: React.ChangeEvent<HTMLInputElement>) => onAgentChange(x.id, e.target.checked)}
                    />
                    <span className="live2d-right-toolbar__indicator" aria-hidden="true">
                      <span className="live2d-right-toolbar__checkmark">✓</span>
                    </span>
                    <span className="live2d-right-toolbar__label">{x.label}</span>
                  </label>
                ))}
              </div>
            )}
          </div>
        ))
      )}
    </div>
  );
}
