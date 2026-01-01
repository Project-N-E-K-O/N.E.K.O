import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { tOrDefault, useT } from "../i18n";
import "./Live2DRightToolbar.css";

export type Live2DRightToolbarButtonId = "mic" | "screen" | "agent" | "settings" | "goodbye" | "return";

export interface Live2DRightToolbarProps {
  visible?: boolean;
  right?: number;
  bottom?: number;
  top?: number;
  isMobile?: boolean;
  initialActive?: Partial<Record<Exclude<Live2DRightToolbarButtonId, "return">, boolean>>;
  onActiveChange?: (state: {
    mic: boolean;
    screen: boolean;
    goodbye: boolean;
  }) => void;
  dispatchToWindow?: boolean;
}

function dispatchLive2DEvent(name: string, detail?: any) {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent(name, { detail }));
}

export function Live2DRightToolbar({
  visible = true,
  right = 460,
  bottom,
  top,
  isMobile,
  initialActive,
  onActiveChange,
  dispatchToWindow = true,
}: Live2DRightToolbarProps) {
  const t = useT();
  const rootRef = useRef<HTMLDivElement | null>(null);
  const [openPanel, setOpenPanel] = useState<"agent" | "settings" | null>(null);
  const [closingPanel, setClosingPanel] = useState<"agent" | "settings" | null>(null);
  const closeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const PANEL_ANIM_MS = 240;

  const [micActive, setMicActive] = useState(Boolean(initialActive?.mic));
  const [screenActive, setScreenActive] = useState(Boolean(initialActive?.screen));
  const [goodbyeMode, setGoodbyeMode] = useState(Boolean(initialActive?.goodbye));

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

  const emitActiveChange = useCallback(
    (next: { mic: boolean; screen: boolean; goodbye: boolean }) => {
      onActiveChange?.(next);
    },
    [onActiveChange]
  );

  const handleToggle = useCallback(
    (id: "mic" | "screen") => {
      if (id === "mic") {
        setMicActive((prev) => {
          const next = !prev;
          emitActiveChange({ mic: next, screen: screenActive, goodbye: goodbyeMode });
          if (dispatchToWindow) dispatchLive2DEvent("live2d-mic-toggle", { active: next });
          return next;
        });
        return;
      }

      if (id === "screen") {
        setScreenActive((prev) => {
          const next = !prev;
          emitActiveChange({ mic: micActive, screen: next, goodbye: goodbyeMode });
          if (dispatchToWindow) dispatchLive2DEvent("live2d-screen-toggle", { active: next });
          return next;
        });
      }
    },
    [dispatchToWindow, emitActiveChange, goodbyeMode, micActive, screenActive]
  );

  const handleClick = useCallback(
    (id: "agent" | "settings") => {
      if (!dispatchToWindow) return;
      if (id === "agent") dispatchLive2DEvent("live2d-agent-click");
      if (id === "settings") dispatchLive2DEvent("live2d-settings-click");
    },
    [dispatchToWindow]
  );

  const startClose = useCallback(
    (panel: "agent" | "settings", reason?: "outside" | "toggle") => {
      setClosingPanel(panel);
      setOpenPanel(null);

      if (closeTimerRef.current) {
        clearTimeout(closeTimerRef.current);
      }
      closeTimerRef.current = setTimeout(() => {
        setClosingPanel((prev) => (prev === panel ? null : prev));
        closeTimerRef.current = null;
      }, PANEL_ANIM_MS);

      if (panel === "agent" && dispatchToWindow) {
        dispatchLive2DEvent("live2d-agent-popup-closed", { reason });
      }
    },
    [PANEL_ANIM_MS, dispatchToWindow]
  );

  const startOpen = useCallback(
    (panel: "agent" | "settings") => {
      // 切换到新 panel 前，让旧 panel 走退出动画
      if (openPanel && openPanel !== panel) {
        startClose(openPanel, "toggle");
      }

      // 兼容旧逻辑：agent 面板打开事件
      if (panel === "agent" && dispatchToWindow) {
        dispatchLive2DEvent("live2d-agent-popup-opening");
      }

      setOpenPanel(panel);
    },
    [dispatchToWindow, openPanel, startClose]
  );

  const closePanels = useCallback(
    (reason?: "outside" | "toggle") => {
      if (!openPanel) return;
      startClose(openPanel, reason);
    },
    [openPanel, startClose]
  );

  const togglePanel = useCallback(
    (panel: "agent" | "settings") => {
      if (openPanel === panel) {
        startClose(panel, "toggle");
        return;
      }
      startOpen(panel);
    },
    [openPanel, startClose, startOpen]
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
    const onPointerDown = (e: MouseEvent) => {
      const root = rootRef.current;
      if (!root) return;
      if (!openPanel) return;
      const target = e.target as Node | null;
      if (target && root.contains(target)) return;
      closePanels("outside");
    };
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, [closePanels, openPanel]);

  const handleGoodbye = useCallback(() => {
    setGoodbyeMode(true);
    emitActiveChange({ mic: micActive, screen: screenActive, goodbye: true });
    if (dispatchToWindow) dispatchLive2DEvent("live2d-goodbye-click");
  }, [dispatchToWindow, emitActiveChange, micActive, screenActive]);

  const handleReturn = useCallback(() => {
    setGoodbyeMode(false);
    emitActiveChange({ mic: micActive, screen: screenActive, goodbye: false });
    if (dispatchToWindow) dispatchLive2DEvent("live2d-return-click");
  }, [dispatchToWindow, emitActiveChange, micActive, screenActive]);

  const buttons = useMemo(
    () =>
      [
        {
          id: "mic" as const,
          title: tOrDefault(t, "buttons.voiceControl", "语音控制"),
          hidden: false,
          active: micActive,
          onClick: () => handleToggle("mic"),
          icon: "/static/icons/mic_icon_off.png",
        },
        {
          id: "screen" as const,
          title: tOrDefault(t, "buttons.screenShare", "屏幕分享"),
          hidden: false,
          active: screenActive,
          onClick: () => handleToggle("screen"),
          icon: "/static/icons/screen_icon_off.png",
        },
        {
          id: "agent" as const,
          title: tOrDefault(t, "buttons.agentTools", "Agent工具"),
          hidden: Boolean(isMobile),
          active: false,
          onClick: () => {
            handleClick("agent");
            togglePanel("agent");
          },
          icon: "/static/icons/Agent_off.png",
          hasPanel: true,
        },
        {
          id: "settings" as const,
          title: tOrDefault(t, "buttons.settings", "设置"),
          hidden: false,
          active: false,
          onClick: () => {
            handleClick("settings");
            togglePanel("settings");
          },
          icon: "/static/icons/set_off.png",
          hasPanel: true,
        },
        {
          id: "goodbye" as const,
          title: tOrDefault(t, "buttons.leave", "请她离开"),
          hidden: Boolean(isMobile),
          active: goodbyeMode,
          onClick: handleGoodbye,
          icon: "/static/icons/rest_off.png",
          hasPanel: false,
        },
      ].filter((b) => !b.hidden),
    [goodbyeMode, handleClick, handleGoodbye, handleToggle, isMobile, micActive, screenActive, t, togglePanel]
  );

  const settingsToggles = useMemo(
    () => [
      {
        id: "merge-messages",
        label: tOrDefault(t, "settings.toggles.mergeMessages", "合并消息"),
      },
      {
        id: "focus-mode",
        label: tOrDefault(t, "settings.toggles.allowInterrupt", "允许打断"),
      },
      {
        id: "proactive-chat",
        label: tOrDefault(t, "settings.toggles.proactiveChat", "主动搭话"),
      },
      {
        id: "proactive-vision",
        label: tOrDefault(t, "settings.toggles.proactiveVision", "自主视觉"),
      },
    ],
    [t]
  );

  const agentToggles = useMemo(
    () => [
      {
        id: "agent-master",
        label: tOrDefault(t, "settings.toggles.agentMaster", "Agent总开关"),
      },
      {
        id: "agent-keyboard",
        label: tOrDefault(t, "settings.toggles.keyboardControl", "键鼠控制"),
      },
      {
        id: "agent-mcp",
        label: tOrDefault(t, "settings.toggles.mcpTools", "MCP工具"),
      },
      {
        id: "agent-user-plugin",
        label: tOrDefault(t, "settings.toggles.userPlugin", "用户插件"),
      },
    ],
    [t]
  );

  const handleSettingsToggleChange = useCallback((id: string, checked: boolean) => {
    if (typeof window === "undefined") return;

    // 对齐旧逻辑：这些开关直接写 window 全局并保存
    if (id === "merge-messages") {
      (window as any).mergeMessagesEnabled = checked;
      if (typeof (window as any).saveNEKOSettings === "function") (window as any).saveNEKOSettings();
      return;
    }
    if (id === "focus-mode") {
      // 旧逻辑 inverted: 允许打断 = !focusModeEnabled
      (window as any).focusModeEnabled = !checked;
      if (typeof (window as any).saveNEKOSettings === "function") (window as any).saveNEKOSettings();
      return;
    }
    if (id === "proactive-chat") {
      (window as any).proactiveChatEnabled = checked;
      if (typeof (window as any).saveNEKOSettings === "function") (window as any).saveNEKOSettings();
      if (checked && typeof (window as any).resetProactiveChatBackoff === "function") {
        (window as any).resetProactiveChatBackoff();
      }
      if (!checked && typeof (window as any).stopProactiveChatSchedule === "function") {
        (window as any).stopProactiveChatSchedule();
      }
      return;
    }
    if (id === "proactive-vision") {
      (window as any).proactiveVisionEnabled = checked;
      if (typeof (window as any).saveNEKOSettings === "function") (window as any).saveNEKOSettings();
      if (checked) {
        if (typeof (window as any).resetProactiveChatBackoff === "function") {
          (window as any).resetProactiveChatBackoff();
        }
        if (typeof (window as any).isRecording !== "undefined" && (window as any).isRecording) {
          if (typeof (window as any).startProactiveVisionDuringSpeech === "function") {
            (window as any).startProactiveVisionDuringSpeech();
          }
        }
      } else {
        if (typeof (window as any).stopProactiveChatSchedule === "function") {
          if (!(window as any).proactiveChatEnabled) {
            (window as any).stopProactiveChatSchedule();
          }
        }
        if (typeof (window as any).stopProactiveVisionDuringSpeech === "function") {
          (window as any).stopProactiveVisionDuringSpeech();
        }
      }
    }
  }, []);

  const openSettingsUrl = useCallback(
    (url: string, opts?: { withLanlanName?: boolean; asLocation?: boolean }) => {
      if (typeof window === "undefined") return;
      let finalUrl = url;
      if (opts?.withLanlanName) {
        const lanlanName = ((window as any).lanlan_config && (window as any).lanlan_config.lanlan_name) || "";
        const sep = url.includes("?") ? "&" : "?";
        finalUrl = `${url}${sep}lanlan_name=${encodeURIComponent(lanlanName)}`;
      }

      // 兼容旧互斥：打开前关闭旧窗口引用
      if (typeof (window as any).closeAllSettingsWindows === "function") {
        (window as any).closeAllSettingsWindows();
      }

      if (opts?.asLocation) {
        window.location.href = finalUrl;
        return;
      }
      window.open(
        finalUrl,
        "_blank",
        "width=1000,height=800,menubar=no,toolbar=no,location=no,status=no"
      );
    },
    []
  );

  if (!visible) return null;

  return (
    <div ref={rootRef} className="live2d-right-toolbar" style={containerStyle}>
      {goodbyeMode ? (
        <button
          type="button"
          className="live2d-right-toolbar__button live2d-right-toolbar__return"
          title={tOrDefault(t, "buttons.return", "请她回来")}
          onClick={handleReturn}
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
              data-active={
                b.active || (openPanel === "agent" && b.id === "agent") || (openPanel === "settings" && b.id === "settings")
                  ? "true"
                  : "false"
              }
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
                {settingsToggles.map((x) => (
                  <label key={x.id} className="live2d-right-toolbar__row">
                    <input
                      id={`live2d-${x.id}`}
                      type="checkbox"
                      className="live2d-right-toolbar__checkbox"
                      defaultChecked={(() => {
                        try {
                          const w: any = window as any;
                          if (x.id === "merge-messages") return Boolean(w.mergeMessagesEnabled);
                          if (x.id === "focus-mode") return !Boolean(w.focusModeEnabled);
                          if (x.id === "proactive-chat") return Boolean(w.proactiveChatEnabled);
                          if (x.id === "proactive-vision") return Boolean(w.proactiveVisionEnabled);
                          return false;
                        } catch (_e) {
                          return false;
                        }
                      })()}
                      onChange={(e) => handleSettingsToggleChange(x.id, e.target.checked)}
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
                      onClick={() => openSettingsUrl("/l2d", { withLanlanName: true, asLocation: true })}
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
                      onClick={() => openSettingsUrl("/api_key")}
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
                      onClick={() => openSettingsUrl("/chara_manager")}
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
                      onClick={() => openSettingsUrl("/voice_clone", { withLanlanName: true })}
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
                      onClick={() => openSettingsUrl("/memory_browser")}
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
                      onClick={() => openSettingsUrl("/steam_workshop_manager")}
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
                  {tOrDefault(t, "settings.toggles.checking", "查询中...")}
                </div>
                {agentToggles.map((x) => (
                  <label key={x.id} className="live2d-right-toolbar__row" title={tOrDefault(t, "settings.toggles.checking", "查询中...")}
                  >
                    <input
                      id={`live2d-${x.id}`}
                      type="checkbox"
                      className="live2d-right-toolbar__checkbox"
                      disabled
                      title={tOrDefault(t, "settings.toggles.checking", "查询中...")}
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
