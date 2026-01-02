import "./styles.css";
import { useCallback, useEffect, useRef, useState } from "react";
import { StatusToast, Live2DRightToolbar, useT } from "@project_neko/components";
import type {
  StatusToastHandle,
  Live2DSettingsToggleId,
  Live2DSettingsState,
  Live2DRightToolbarPanel,
  Live2DSettingsMenuId,
} from "@project_neko/components";
import { ChatContainer } from "@project_neko/components";
import { useLive2DAgentBackend } from "./useLive2DAgentBackend";

const trimTrailingSlash = (url?: string) => (url ? url.replace(/\/+$/, "") : "");

const API_BASE = trimTrailingSlash(
  (import.meta as any).env?.VITE_API_BASE_URL ||
  (typeof window !== "undefined" ? (window as any).API_BASE_URL : "") ||
  "http://localhost:48911"
);
const STATIC_BASE = trimTrailingSlash(
  (import.meta as any).env?.VITE_STATIC_SERVER_URL ||
  (typeof window !== "undefined" ? (window as any).STATIC_SERVER_URL : "") ||
  API_BASE
);

/**
 * Root React component demonstrating API requests and interactive UI controls.
 *
 * 展示了请求示例、StatusToast 以及 Modal 交互入口。
 */
export interface AppProps {
  language: "zh-CN" | "en";
  onChangeLanguage: (lng: "zh-CN" | "en") => void;
}

function App(_props: AppProps) {
  const t = useT();
  const toastRef = useRef<StatusToastHandle | null>(null);

  const [isMobile, setIsMobile] = useState(false);

  const [toolbarGoodbyeMode, setToolbarGoodbyeMode] = useState(false);
  const [toolbarMicEnabled, setToolbarMicEnabled] = useState(false);
  const [toolbarScreenEnabled, setToolbarScreenEnabled] = useState(false);
  const [toolbarOpenPanel, setToolbarOpenPanel] = useState<Live2DRightToolbarPanel>(null);
  const [toolbarSettings, setToolbarSettings] = useState<Live2DSettingsState>({
    mergeMessages: true,
    allowInterrupt: true,
    proactiveChat: false,
    proactiveVision: false,
  });

  const { agent: toolbarAgent, onAgentChange: handleToolbarAgentChange } = useLive2DAgentBackend({
    apiBase: API_BASE,
    t,
    toastRef,
    openPanel: toolbarOpenPanel,
  });

  const handleToolbarSettingsChange = useCallback((id: Live2DSettingsToggleId, next: boolean) => {
    setToolbarSettings((prev: Live2DSettingsState) => ({ ...prev, [id]: next }));
  }, []);

  const handleSettingsMenuClick = useCallback((id: Live2DSettingsMenuId) => {
    const map: Record<Live2DSettingsMenuId, string> = {
      live2dSettings: "/l2d",
      apiKeys: "/api_key",
      characterManage: "/chara_manager",
      voiceClone: "/voice_clone",
      memoryBrowser: "/memory_browser",
      steamWorkshop: "/steam_workshop_manager",
    };
    const url = map[id];
    const newWindow = window.open(url, "_blank");
    if (!newWindow) {
      window.location.href = url;
    }
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;

    const mq = window.matchMedia?.("(max-width: 768px)");
    if (!mq) return;

    const update = () => setIsMobile(mq.matches);
    update();

    // Safari <= 13 兼容
    if ("addEventListener" in mq) {
      mq.addEventListener("change", update);
      return () => mq.removeEventListener("change", update);
    }

    // @ts-expect-error legacy API
    mq.addListener(update);
    // @ts-expect-error legacy API
    return () => mq.removeListener(update);
  }, []);

  return (
    <>
      <StatusToast ref={toastRef} staticBaseUrl={STATIC_BASE} />
      <Live2DRightToolbar
        visible
        isMobile={isMobile}
        right={isMobile ? 12 : 24}
        top={isMobile ? 12 : 24}
        micEnabled={toolbarMicEnabled}
        screenEnabled={toolbarScreenEnabled}
        goodbyeMode={toolbarGoodbyeMode}
        openPanel={toolbarOpenPanel}
        onOpenPanelChange={setToolbarOpenPanel}
        settings={toolbarSettings}
        onSettingsChange={handleToolbarSettingsChange}
        agent={toolbarAgent}
        onAgentChange={handleToolbarAgentChange}
        onToggleMic={(next) => {
          setToolbarMicEnabled(next);
        }}
        onToggleScreen={(next) => {
          setToolbarScreenEnabled(next);
        }}
        onGoodbye={() => {
          setToolbarGoodbyeMode(true);
          setToolbarOpenPanel(null);
        }}
        onReturn={() => {
          setToolbarGoodbyeMode(false);
        }}
        onSettingsMenuClick={handleSettingsMenuClick}
      />
      <div className="chatDemo">
        <ChatContainer />
      </div>
    </>
  );
}

export default App;

