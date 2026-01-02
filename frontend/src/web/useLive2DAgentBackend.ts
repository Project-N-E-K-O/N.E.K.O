import { useCallback, useEffect, useRef, useState } from "react";
import type { RefObject } from "react";
import type { StatusToastHandle, Live2DAgentState, Live2DAgentToggleId, Live2DRightToolbarPanel } from "@project_neko/components";
import type { TFunction } from "@project_neko/components";
import { tOrDefault } from "@project_neko/components";

type AgentFlagsKey = "agent_enabled" | "computer_use_enabled" | "mcp_enabled" | "user_plugin_enabled";

type AgentFlagsResponse = {
  success: boolean;
  agent_flags?: Partial<Record<AgentFlagsKey, boolean>>;
  analyzer_enabled?: boolean;
  notification?: string;
  error?: string;
};

export interface UseLive2DAgentBackendArgs {
  apiBase: string;
  t: TFunction;
  toastRef: RefObject<StatusToastHandle | null>;
  openPanel: Live2DRightToolbarPanel;
}

export interface UseLive2DAgentBackendResult {
  agent: Live2DAgentState;
  onAgentChange: (id: Live2DAgentToggleId, next: boolean) => void;
}

export function useLive2DAgentBackend({ apiBase, t, toastRef, openPanel }: UseLive2DAgentBackendArgs): UseLive2DAgentBackendResult {
  const agentUserOpSeqRef = useRef(0);
  const agentRefreshSeqRef = useRef(0);
  const agentProcessingRef = useRef(false);
  const agentPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const availabilityCacheRef = useRef<{
    updatedAt: number;
    keyboard?: boolean;
    mcp?: boolean;
    userPlugin?: boolean;
  }>({ updatedAt: 0 });

  const [agent, setAgent] = useState<Live2DAgentState>({
    statusText: tOrDefault(t, "settings.toggles.checking", "查询中..."),
    master: false,
    keyboard: false,
    mcp: false,
    userPlugin: false,
    disabled: {},
  });

  const fetchAgentHealth = useCallback(async (): Promise<boolean> => {
    try {
      const resp = await fetch(`${apiBase}/api/agent/health`);
      return resp.ok;
    } catch (_e) {
      return false;
    }
  }, [apiBase]);

  const fetchAgentFlags = useCallback(async (): Promise<AgentFlagsResponse> => {
    try {
      const resp = await fetch(`${apiBase}/api/agent/flags`);
      if (!resp.ok) {
        return { success: false, error: `http_${resp.status}` };
      }
      return (await resp.json()) as AgentFlagsResponse;
    } catch (e) {
      return { success: false, error: String(e) };
    }
  }, [apiBase]);

  const fetchAvailability = useCallback(
    async (kind: "computer_use" | "mcp" | "user_plugin"): Promise<boolean> => {
      const map: Record<"computer_use" | "mcp" | "user_plugin", string> = {
        computer_use: `${apiBase}/api/agent/computer_use/availability`,
        mcp: `${apiBase}/api/agent/mcp/availability`,
        user_plugin: `${apiBase}/api/agent/user_plugin/availability`,
      };
      try {
        const resp = await fetch(map[kind]);
        if (!resp.ok) return false;
        const data = (await resp.json()) as { ready?: boolean };
        return Boolean(data.ready);
      } catch (_e) {
        return false;
      }
    },
    [apiBase]
  );

  const updateAvailabilityCache = useCallback(
    async (opts?: { force?: boolean; refreshSeq?: number }) => {
      const now = Date.now();
      const cache = availabilityCacheRef.current;
      const force = Boolean(opts?.force);
      const shouldRefresh = force || now - cache.updatedAt > 5000;
      if (!shouldRefresh) return;
      if (agentProcessingRef.current) return;

      const [keyboardAvailable, mcpAvailable, userPluginAvailable] = await Promise.all([
        fetchAvailability("computer_use"),
        fetchAvailability("mcp"),
        fetchAvailability("user_plugin"),
      ]);

      if (agentProcessingRef.current) return;
      if (typeof opts?.refreshSeq === "number" && opts.refreshSeq !== agentRefreshSeqRef.current) return;

      availabilityCacheRef.current = {
        updatedAt: Date.now(),
        keyboard: keyboardAvailable,
        mcp: mcpAvailable,
        userPlugin: userPluginAvailable,
      };
    },
    [fetchAvailability]
  );

  const refreshAgentState = useCallback(async () => {
    if (agentProcessingRef.current) return;
    const seq = ++agentRefreshSeqRef.current;

    const [healthOk, flagsData] = await Promise.all([fetchAgentHealth(), fetchAgentFlags()]);

    if (seq !== agentRefreshSeqRef.current) return;

    if (flagsData.notification) {
      toastRef.current?.show(flagsData.notification, 3000);
    }

    if (!healthOk || !flagsData.success) {
      setAgent((prev) => ({
        ...prev,
        statusText: tOrDefault(t, "settings.toggles.serverOffline", "Agent服务器未启动"),
        master: false,
        keyboard: false,
        mcp: false,
        userPlugin: false,
        disabled: {
          master: true,
          keyboard: true,
          mcp: true,
          userPlugin: true,
        },
      }));
      return;
    }

    const analyzerEnabled = Boolean(flagsData.analyzer_enabled);
    const flags = flagsData.agent_flags || {};

    // availability 结果缓存：刷新不阻塞 UI。
    // 1) 先用缓存渲染（更快）
    // 2) 如需要，再异步刷新缓存并二次渲染（更稳）
    const cachedAvail = availabilityCacheRef.current;
    const keyboardAvailable = cachedAvail.keyboard ?? false;
    const mcpAvailable = cachedAvail.mcp ?? false;
    const userPluginAvailable = cachedAvail.userPlugin ?? false;

    if (!analyzerEnabled) {
      setAgent((prev) => ({
        ...prev,
        statusText: tOrDefault(t, "agent.status.ready", "Agent服务器就绪"),
        master: false,
        keyboard: false,
        mcp: false,
        userPlugin: false,
        disabled: {
          master: false,
          keyboard: true,
          mcp: true,
          userPlugin: true,
        },
      }));
      return;
    }

    setAgent((prev) => ({
      ...prev,
      statusText: tOrDefault(t, "agent.status.enabled", "Agent模式已开启"),
      master: true,
      keyboard: Boolean(flags.computer_use_enabled) && keyboardAvailable,
      mcp: Boolean(flags.mcp_enabled) && mcpAvailable,
      userPlugin: Boolean(flags.user_plugin_enabled) && userPluginAvailable,
      disabled: {
        master: false,
        keyboard: !keyboardAvailable,
        mcp: !mcpAvailable,
        userPlugin: !userPluginAvailable,
      },
    }));

    // 异步刷新 availability（不阻塞本次渲染）；刷新完再同步 UI 一次
    void (async () => {
      if (!analyzerEnabled) return;
      await updateAvailabilityCache({ refreshSeq: seq });
      if (agentProcessingRef.current) return;
      if (seq !== agentRefreshSeqRef.current) return;

      const updated = availabilityCacheRef.current;
      const k = updated.keyboard ?? false;
      const m = updated.mcp ?? false;
      const u = updated.userPlugin ?? false;

      setAgent((prev) => ({
        ...prev,
        keyboard: Boolean(flags.computer_use_enabled) && k,
        mcp: Boolean(flags.mcp_enabled) && m,
        userPlugin: Boolean(flags.user_plugin_enabled) && u,
        disabled: {
          master: false,
          keyboard: !k,
          mcp: !m,
          userPlugin: !u,
        },
      }));
    })();
  }, [fetchAgentFlags, fetchAgentHealth, t, toastRef, updateAvailabilityCache]);

  const postAgentFlags = useCallback(
    async (flags: Partial<Record<AgentFlagsKey, boolean>>) => {
      const resp = await fetch(`${apiBase}/api/agent/flags`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ flags }),
      });
      if (!resp.ok) {
        throw new Error(`flags_http_${resp.status}`);
      }
      const data = (await resp.json()) as { success?: boolean; error?: string };
      if (data && data.success === false) {
        throw new Error(data.error || "flags_failed");
      }
    },
    [apiBase]
  );

  const postAdminControl = useCallback(
    async (action: "enable_analyzer" | "disable_analyzer") => {
      const resp = await fetch(`${apiBase}/api/agent/admin/control`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action }),
      });
      if (!resp.ok) {
        throw new Error(`admin_http_${resp.status}`);
      }
      const data = (await resp.json()) as { success?: boolean; error?: string };
      if (data && data.success === false) {
        throw new Error(data.error || "admin_failed");
      }
    },
    [apiBase]
  );

  const onAgentChange = useCallback(
    (id: Live2DAgentToggleId, next: boolean) => {
      void (async () => {
        const seq = ++agentUserOpSeqRef.current;
        agentProcessingRef.current = true;

        const setProcessingState = (patch: Partial<Live2DAgentState>, disabledAll: boolean) => {
          setAgent((prev) => ({
            ...prev,
            ...patch,
            disabled: disabledAll
              ? {
                  master: true,
                  keyboard: true,
                  mcp: true,
                  userPlugin: true,
                }
              : { ...prev.disabled, ...(patch.disabled || {}) },
          }));
        };

        try {
          if (id === "master") {
            if (next) {
              setProcessingState(
                {
                  statusText: tOrDefault(t, "agent.status.connecting", "Agent服务器连接中..."),
                  master: true,
                  keyboard: false,
                  mcp: false,
                  userPlugin: false,
                },
                true
              );

              const healthOk = await fetchAgentHealth();
              if (seq !== agentUserOpSeqRef.current) return;
              if (!healthOk) {
                toastRef.current?.show(tOrDefault(t, "settings.toggles.serverOffline", "Agent服务器未启动"), 3000);
                await refreshAgentState();
                return;
              }

              await postAgentFlags({
                agent_enabled: true,
                computer_use_enabled: false,
                mcp_enabled: false,
                user_plugin_enabled: false,
              });
              if (seq !== agentUserOpSeqRef.current) return;

              await postAdminControl("enable_analyzer");
              if (seq !== agentUserOpSeqRef.current) return;

              // 后端确认成功后，立即更新 UI（更快），再异步刷新状态做最终对齐（更稳）
              setAgent((prev) => ({
                ...prev,
                statusText: tOrDefault(t, "agent.status.enabled", "Agent模式已开启"),
                master: true,
                keyboard: false,
                mcp: false,
                userPlugin: false,
                disabled: {
                  master: false,
                  keyboard: true,
                  mcp: true,
                  userPlugin: true,
                },
              }));
              agentProcessingRef.current = false;
              void refreshAgentState();
              return;
            }

            setProcessingState(
              {
                statusText: tOrDefault(t, "agent.status.disabled", "Agent模式已关闭"),
                master: false,
                keyboard: false,
                mcp: false,
                userPlugin: false,
              },
              true
            );

            await postAdminControl("disable_analyzer");
            if (seq !== agentUserOpSeqRef.current) return;
            await postAgentFlags({
              agent_enabled: false,
              computer_use_enabled: false,
              mcp_enabled: false,
              user_plugin_enabled: false,
            });
            if (seq !== agentUserOpSeqRef.current) return;

            setAgent((prev) => ({
              ...prev,
              statusText: tOrDefault(t, "agent.status.disabled", "Agent模式已关闭"),
              master: false,
              keyboard: false,
              mcp: false,
              userPlugin: false,
              disabled: {
                master: false,
                keyboard: true,
                mcp: true,
                userPlugin: true,
              },
            }));
            agentProcessingRef.current = false;
            void refreshAgentState();
            return;
          }

          if (!agent.master) {
            await refreshAgentState();
            return;
          }

          setAgent((prev) => ({
            ...prev,
            [id]: next,
            disabled: { ...prev.disabled, [id]: true },
          }));

          const availabilityMap: Record<Exclude<Live2DAgentToggleId, "master">, "computer_use" | "mcp" | "user_plugin"> = {
            keyboard: "computer_use",
            mcp: "mcp",
            userPlugin: "user_plugin",
          };
          const flagKeyMap: Record<Exclude<Live2DAgentToggleId, "master">, AgentFlagsKey> = {
            keyboard: "computer_use_enabled",
            mcp: "mcp_enabled",
            userPlugin: "user_plugin_enabled",
          };

          const kind = availabilityMap[id];
          const flagKey = flagKeyMap[id];

          if (next) {
            const available = await fetchAvailability(kind);
            if (seq !== agentUserOpSeqRef.current) return;
            if (!available) {
              toastRef.current?.show(tOrDefault(t, "settings.toggles.unavailable", "功能不可用"), 3000);
              await refreshAgentState();
              return;
            }
          }

          await postAgentFlags({ [flagKey]: next });
          if (seq !== agentUserOpSeqRef.current) return;
          agentProcessingRef.current = false;
          void refreshAgentState();
        } catch (e) {
          if (seq === agentUserOpSeqRef.current) {
            toastRef.current?.show(String(e), 3000);
            agentProcessingRef.current = false;
            void refreshAgentState();
          }
        } finally {
          if (seq === agentUserOpSeqRef.current) {
            agentProcessingRef.current = false;
          }
        }
      })();
    },
    [agent.master, fetchAgentHealth, fetchAvailability, postAdminControl, postAgentFlags, refreshAgentState, t, toastRef]
  );

  useEffect(() => {
    const shouldPoll = openPanel === "agent" || agent.master;
    if (!shouldPoll) {
      if (agentPollRef.current) {
        clearInterval(agentPollRef.current);
        agentPollRef.current = null;
      }
      return;
    }

    refreshAgentState();
    if (agentPollRef.current) {
      clearInterval(agentPollRef.current);
      agentPollRef.current = null;
    }
    agentPollRef.current = setInterval(() => {
      refreshAgentState();
    }, 1500);

    return () => {
      if (agentPollRef.current) {
        clearInterval(agentPollRef.current);
        agentPollRef.current = null;
      }
    };
  }, [agent.master, openPanel, refreshAgentState]);

  return { agent, onAgentChange };
}
