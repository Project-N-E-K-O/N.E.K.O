import "./styles.css";
import { useCallback, useEffect, useRef, useState } from "react";
import { StatusToast, Live2DRightToolbar, useT, tOrDefault, Modal } from "@project_neko/components";
import type {
  StatusToastHandle,
  ModalHandle,
  Live2DSettingsToggleId,
  Live2DSettingsState,
  Live2DRightToolbarPanel,
  Live2DSettingsMenuId,
  ChatMessage,
} from "@project_neko/components";
import { ChatContainer } from "@project_neko/components";
import { useLive2DAgentBackend } from "./useLive2DAgentBackend";
import { Live2DStage } from "./Live2DStage";
import type { Live2DManager } from "@project_neko/live2d-service";
import { createLive2DPreferencesRepository } from "./live2dPreferences";
import { buildWebSocketUrlFromBase, createRealtimeClient } from "@project_neko/realtime";
import type { RealtimeClient, RealtimeConnectionState } from "@project_neko/realtime";
import { createWebAudioService } from "@project_neko/audio-service/web";
import type { AudioServiceState } from "@project_neko/audio-service/web";

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
const WEBSOCKET_BASE = trimTrailingSlash(
  (import.meta as any).env?.VITE_WEBSOCKET_URL ||
  (typeof window !== "undefined" ? (window as any).WEBSOCKET_URL : "") ||
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
  const modalRef = useRef<ModalHandle | null>(null);
  const live2dManagerRef = useRef<Live2DManager | null>(null);
  const live2dPrefsRepoRef = useRef(createLive2DPreferencesRepository(API_BASE));

  const handleLive2DReady = useCallback((mgr: Live2DManager) => {
    live2dManagerRef.current = mgr;
  }, []);

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

  // Realtime WebSocket client
  const realtimeRef = useRef<RealtimeClient | null>(null);
  const realtimeOffRef = useRef<(() => void)[]>([]);
  const [realtimeState, setRealtimeState] = useState<RealtimeConnectionState>("idle");

  // Audio service for voice session
  const audioRef = useRef<ReturnType<typeof createWebAudioService> | null>(null);
  const audioOffRef = useRef<(() => void)[]>([]);
  const [audioState, setAudioState] = useState<AudioServiceState>("idle");
  const [outputAmp, setOutputAmp] = useState(0);

  // Chat messages from realtime voice session
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const chatMessageIdCounter = useRef(0);
  // Buffer for accumulating streaming gemini_response text
  const assistantTextBuffer = useRef<string>("");

  // Generate unique message ID
  const generateMessageId = useCallback(() => {
    chatMessageIdCounter.current += 1;
    return `rt-msg-${Date.now()}-${chatMessageIdCounter.current}`;
  }, []);

  // Add a chat message from realtime events
  const addChatMessage = useCallback((role: ChatMessage["role"], content: string) => {
    const msg: ChatMessage = {
      id: generateMessageId(),
      role,
      content,
      createdAt: Date.now(),
    };
    setChatMessages((prev) => [...prev, msg]);
  }, [generateMessageId]);

  // Flush accumulated assistant text as a message
  const flushAssistantBuffer = useCallback(() => {
    const text = assistantTextBuffer.current.trim();
    if (text) {
      addChatMessage("assistant", text);
      assistantTextBuffer.current = "";
    }
  }, [addChatMessage]);

  // Handle incoming realtime JSON messages for chat
  const handleRealtimeJson = useCallback((json: unknown) => {
    const msg = json as Record<string, unknown>;
    const type = msg?.type as string | undefined;

    // Handle different message types from server
    if (type === "transcript" || type === "user_transcript") {
      // User's speech transcript
      const content = (msg.content || msg.text) as string;
      if (content) {
        addChatMessage("user", content);
      }
    } else if (type === "gemini_response") {
      // Streaming AI response text
      const text = msg.text as string | undefined;
      const isNewMessage = msg.isNewMessage as boolean | undefined;

      // If it's a new message and we have buffered text, flush it first
      if (isNewMessage && assistantTextBuffer.current) {
        flushAssistantBuffer();
      }

      // Accumulate the text
      if (text) {
        assistantTextBuffer.current += text;
      }
    } else if (type === "system") {
      // System message - check for turn end
      const data = msg.data as string | undefined;
      if (data === "turn end") {
        // Flush any accumulated assistant text
        flushAssistantBuffer();
      }
    } else if (type === "assistant_text" || type === "assistant_response") {
      // Non-streaming assistant response
      const content = (msg.content || msg.text) as string;
      if (content) {
        addChatMessage("assistant", content);
      }
    } else if (type === "response.done") {
      // AI response completed with transcript - try multiple field names
      const transcript = (
        msg.transcript ||
        msg.text ||
        msg.content ||
        (msg.response as Record<string, unknown>)?.transcript ||
        (msg.response as Record<string, unknown>)?.text
      ) as string | undefined;
      if (transcript) {
        addChatMessage("assistant", transcript);
      }
    } else if (type === "text_response") {
      // Generic text response (check speaker field)
      const content = (msg.content || msg.text) as string;
      const speaker = msg.speaker as string;
      if (content) {
        addChatMessage(speaker === "user" ? "user" : "assistant", content);
      }
    }
  }, [addChatMessage, flushAssistantBuffer]);

  const { agent: toolbarAgent, onAgentChange: handleToolbarAgentChange } = useLive2DAgentBackend({
    apiBase: API_BASE,
    t,
    toastRef,
    openPanel: toolbarOpenPanel,
  });

  const handleToolbarSettingsChange = useCallback((id: Live2DSettingsToggleId, next: boolean) => {
    setToolbarSettings((prev: Live2DSettingsState) => ({ ...prev, [id]: next }));
  }, []);

  // Cleanup realtime client
  const cleanupRealtime = useCallback((args?: { disconnect?: boolean }) => {
    for (const off of realtimeOffRef.current) {
      try { off(); } catch { /* ignore */ }
    }
    realtimeOffRef.current = [];

    const client = realtimeRef.current;
    if (args?.disconnect && client) {
      try { client.disconnect({ code: 1000, reason: "user_stop" }); } catch { /* ignore */ }
    }
    if (args?.disconnect) {
      realtimeRef.current = null;
    }
  }, []);

  // Cleanup audio service
  const cleanupAudio = useCallback(() => {
    for (const off of audioOffRef.current) {
      try { off(); } catch { /* ignore */ }
    }
    audioOffRef.current = [];

    const svc = audioRef.current;
    if (svc) {
      try { svc.detach(); } catch { /* ignore */ }
    }
    audioRef.current = null;
    setAudioState("idle");
    setOutputAmp(0);
  }, []);

  // Helper: get lanlan_name from window config
  const getLanlanName = useCallback(() => {
    try {
      const w = typeof window !== "undefined" ? (window as any) : undefined;
      const name = w?.lanlan_config?.lanlan_name;
      return typeof name === "string" && name.trim() ? name.trim() : "test";
    } catch { return "test"; }
  }, []);

  // Helper: build WebSocket URL
  const buildWsUrl = useCallback((path: string) => {
    const w = typeof window !== "undefined" ? (window as any) : undefined;
    if (w && typeof w.buildWebSocketUrl === "function") {
      return w.buildWebSocketUrl(path);
    }
    return buildWebSocketUrlFromBase(WEBSOCKET_BASE, path);
  }, []);

  // Helper: detect mobile
  const getIsMobile = useCallback(() => {
    try {
      if (typeof navigator === "undefined") return false;
      return /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent);
    } catch { return false; }
  }, []);

  // Ensure audio service is created and attached
  const ensureAudioService = useCallback(() => {
    const client = realtimeRef.current;
    if (!client) throw new Error("Realtime client not initialized");

    if (audioRef.current) return audioRef.current;

    const svc = createWebAudioService({
      client: client as any,
      isMobile: getIsMobile(),
      focusModeEnabled: false,
      decoder: "global",
    });

    audioRef.current = svc;
    setAudioState(svc.getState());
    audioOffRef.current = [
      svc.on("state", ({ state }) => setAudioState(state)),
      svc.on("outputAmplitude", ({ amplitude }) => setOutputAmp(amplitude)),
    ];

    svc.attach();
    return svc;
  }, [getIsMobile]);

  // Start chat: establish WebSocket connection
  const handleStartChat = useCallback(() => {
    const lanlanName = getLanlanName();
    const path = `/ws/${encodeURIComponent(lanlanName)}`;

    if (realtimeRef.current) {
      realtimeRef.current.connect();
      return;
    }

    const client = createRealtimeClient({
      path,
      buildUrl: buildWsUrl,
      heartbeat: { intervalMs: 30_000, payload: { action: "ping" } },
      reconnect: { enabled: true },
    });
    realtimeRef.current = client;
    setRealtimeState(client.getState());

    realtimeOffRef.current = [
      client.on("state", ({ state }) => setRealtimeState(state)),
      client.on("open", () => {
        toastRef.current?.show(tOrDefault(t, "webapp.toast.chatConnected", "语音连接已建立"), 2000);
        try { ensureAudioService(); } catch { /* ignore */ }
      }),
      client.on("close", () => {
        toastRef.current?.show(tOrDefault(t, "webapp.toast.chatDisconnected", "语音连接已断开"), 2000);
      }),
      client.on("error", ({ event }) => {
        console.warn("[App] realtime error:", event);
      }),
      client.on("json", ({ json }) => {
        handleRealtimeJson(json);
      }),
    ];

    client.connect();
  }, [buildWsUrl, ensureAudioService, getLanlanName, handleRealtimeJson, t]);

  // Stop chat: disconnect WebSocket
  const handleStopChat = useCallback(() => {
    cleanupAudio();
    cleanupRealtime({ disconnect: true });
    setRealtimeState("closed");
  }, [cleanupAudio, cleanupRealtime]);

  // Handle mic toggle: start/stop voice session
  const handleToggleMic = useCallback(async (next: boolean) => {
    setToolbarMicEnabled(next);

    if (next) {
      // Start voice session
      try {
        // Ensure WebSocket is connected first
        if (realtimeState !== "open") {
          handleStartChat();
          // Wait for connection to establish
          await new Promise<void>((resolve, reject) => {
            const checkInterval = setInterval(() => {
              const state = realtimeRef.current?.getState();
              if (state === "open") {
                clearInterval(checkInterval);
                resolve();
              }
            }, 100);
            // Timeout after 5 seconds
            setTimeout(() => {
              clearInterval(checkInterval);
              reject(new Error("Connection timeout"));
            }, 5000);
          });
        }

        const svc = ensureAudioService();
        await svc.startVoiceSession({
          timeoutMs: 10_000,
          targetSampleRate: getIsMobile() ? 16000 : 48000,
        });
        toastRef.current?.show(tOrDefault(t, "webapp.toast.micStarted", "麦克风已开启"), 2000);
      } catch (e: any) {
        console.error("[App] startVoiceSession failed:", e);
        setToolbarMicEnabled(false);
        if (e?.name === "NotAllowedError") {
          toastRef.current?.show(tOrDefault(t, "webapp.toast.micDenied", "麦克风权限被拒绝"), 3000);
        } else if (e?.name === "NotFoundError") {
          toastRef.current?.show(tOrDefault(t, "webapp.toast.micNotFound", "未找到麦克风设备"), 3000);
        } else {
          toastRef.current?.show(tOrDefault(t, "webapp.toast.micError", `麦克风启动失败: ${e?.message || e}`), 3000);
        }
      }
    } else {
      // Stop voice session
      try {
        const svc = audioRef.current;
        if (svc) {
          await svc.stopVoiceSession();
        }
        toastRef.current?.show(tOrDefault(t, "webapp.toast.micStopped", "麦克风已关闭"), 2000);
        live2dManagerRef.current?.setMouth(0);
      } catch (e: any) {
        console.error("[App] stopVoiceSession failed:", e);
      }
    }
  }, [realtimeState, handleStartChat, ensureAudioService, getIsMobile, t]);

  // Drive mouth animation based on output amplitude
  useEffect(() => {
    if (toolbarMicEnabled && outputAmp > 0) {
      live2dManagerRef.current?.setMouth(Math.min(outputAmp * 1.5, 1));
    } else if (!toolbarMicEnabled) {
      live2dManagerRef.current?.setMouth(0);
    }
  }, [outputAmp, toolbarMicEnabled]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      cleanupAudio();
      cleanupRealtime({ disconnect: true });
    };
  }, [cleanupAudio, cleanupRealtime]);

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
      <Modal ref={modalRef} />
      <Live2DStage
        staticBaseUrl={STATIC_BASE}
        modelUri="/static/mao_pro/mao_pro.model3.json"
        preferences={live2dPrefsRepoRef.current}
        onReady={handleLive2DReady}
      />
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
        onToggleMic={handleToggleMic}
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
        <ChatContainer
          externalMessages={chatMessages}
          connectionStatus={realtimeState}
          onSendMessage={(text, images) => {
            // Send text message via WebSocket if connected
            if (realtimeRef.current && realtimeState === "open") {
              realtimeRef.current.sendJson({
                action: "send_text",
                text,
                images, // Include any attached images
              });
            } else {
              // If not connected, try to connect first then send
              toastRef.current?.show(
                tOrDefault(t, "webapp.toast.notConnected", "未连接到服务器，正在尝试连接..."),
                2000
              );
              handleStartChat();
            }
          }}
        />
      </div>
    </>
  );
}

export default App;

