import "./styles.css";
import { useCallback, useEffect, useRef } from "react";
import type { ChangeEvent } from "react";
import { Button, StatusToast, Modal, useT, tOrDefault } from "@project_neko/components";
import type { StatusToastHandle, ModalHandle } from "@project_neko/components";
import { createRequestClient, WebTokenStorage } from "@project_neko/request";

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

// 创建一个简单的请求客户端；若无需鉴权，可忽略 token，默认存储在 localStorage
const request = createRequestClient({
  baseURL: API_BASE,
  storage: new WebTokenStorage(),
  refreshApi: async () => {
    // 示例中不做刷新，实际可按需实现
    throw new Error("refreshApi not implemented");
  },
  returnDataOnly: true
});

/**
 * Root React component demonstrating API requests and interactive UI controls.
 *
 * 展示了请求示例、StatusToast 以及 Modal 交互入口。
 */
export interface AppProps {
  language: "zh-CN" | "en";
  onChangeLanguage: (lng: "zh-CN" | "en") => void;
}

function App({ language, onChangeLanguage }: AppProps) {
  const t = useT();
  const toastRef = useRef<StatusToastHandle | null>(null);
  const modalRef = useRef<ModalHandle | null>(null);

  useEffect(() => {
    const getLang = () => {
      try {
        const w: any = typeof window !== "undefined" ? (window as any) : undefined;
        return (
          w?.i18n?.language ||
          (typeof localStorage !== "undefined" ? localStorage.getItem("i18nextLng") : null) ||
          (typeof navigator !== "undefined" ? navigator.language : null) ||
          "unknown"
        );
      } catch (_e) {
        return "unknown";
      }
    };

    console.log("[webapp] 当前 i18n 语言:", getLang());

    const onLocaleChange = () => {
      console.log("[webapp] i18n 语言已更新:", getLang());
    };
    window.addEventListener("localechange", onLocaleChange);
    return () => window.removeEventListener("localechange", onLocaleChange);
  }, []);

  const handleClick = useCallback(async () => {
    try {
      const data = await request.get("/api/config/page_config", {
        params: { lanlan_name: "test" }
      });
      // 将返回结果展示在控制台或弹窗
      console.log("page_config:", data);
    } catch (err: any) {
      console.error(tOrDefault(t, "webapp.errors.requestFailed", "请求失败"), err);
    }
  }, [t]);

  const handleToast = useCallback(() => {
    toastRef.current?.show(
      tOrDefault(t, "webapp.toast.apiSuccess", "接口调用成功（示例 toast）"),
      2500
    );
  }, [t]);

  const handleAlert = useCallback(async () => {
    await modalRef.current?.alert(
      tOrDefault(t, "webapp.modal.alertMessage", "这是一条 Alert 弹窗"),
      tOrDefault(t, "webapp.modal.alertTitle", "提示")
    );
  }, [t]);

  const handleConfirm = useCallback(async () => {
    const ok =
      (await modalRef.current?.confirm(tOrDefault(t, "webapp.modal.confirmMessage", "确认要执行该操作吗？"), tOrDefault(t, "webapp.modal.confirmTitle", "确认"), {
        okText: tOrDefault(t, "webapp.modal.okText", "好的"),
        cancelText: tOrDefault(t, "webapp.modal.cancelText", "再想想"),
        danger: false,
      })) ?? false;
    if (ok) {
      toastRef.current?.show(tOrDefault(t, "webapp.toast.confirmed", "确认已执行"), 2000);
    }
  }, [t]);

  const handlePrompt = useCallback(async () => {
    const name = await modalRef.current?.prompt(tOrDefault(t, "webapp.modal.promptMessage", "请输入昵称："), "Neko");
    if (name) {
      toastRef.current?.show(
        tOrDefault(t, "webapp.toast.hello", `你好，${name}!`, { name }),
        2500
      );
    }
  }, [t]);

  return (
    <>
      <StatusToast ref={toastRef} staticBaseUrl={STATIC_BASE} />
      <Modal ref={modalRef} />
      <main className="app">
        <header className="app__header">
          <div className="app__headerRow">
            <div className="app__headerText">
              <h1>{tOrDefault(t, "webapp.header.title", "N.E.K.O 前端主页")}</h1>
              <p>{tOrDefault(t, "webapp.header.subtitle", "单页应用，无路由 / 无 SSR")}</p>
            </div>
            <div className="langSwitch">
              <label className="langSwitch__label" htmlFor="lang-select">
                {tOrDefault(t, "webapp.language.label", "语言")}
              </label>
              <select
                id="lang-select"
                className="langSwitch__select"
                value={language}
                onChange={(e: ChangeEvent<HTMLSelectElement>) =>
                  onChangeLanguage(e.target.value as "zh-CN" | "en")
                }
              >
                <option value="zh-CN">{tOrDefault(t, "webapp.language.zhCN", "中文")}</option>
                <option value="en">{tOrDefault(t, "webapp.language.en", "English")}</option>
              </select>
            </div>
          </div>
        </header>
        <section className="app__content">
          <div className="card">
            <h2>{tOrDefault(t, "webapp.card.title", "开始使用")}</h2>
            <ol>
              <li>{tOrDefault(t, "webapp.card.step1", "在此处挂载你的组件或业务入口。")}</li>
              <li>
                {tOrDefault(t, "webapp.card.step2Prefix", "如需调用接口，可在 ")}
                <code>@project_neko/request</code>
                {tOrDefault(t, "webapp.card.step2Suffix", " 基础上封装请求。")}
              </li>
              <li>
                {tOrDefault(t, "webapp.card.step3Prefix", "构建产物输出到 ")}
                <code>frontend/dist/webapp</code>
                {tOrDefault(t, "webapp.card.step3Suffix", "（用于开发/调试），模板按需引用即可。")}
              </li>
            </ol>
            <div className="card__actions">
              <Button onClick={handleClick}>{tOrDefault(t, "webapp.actions.requestPageConfig", "请求 page_config")}</Button>
              <Button variant="secondary" onClick={handleToast}>
                {tOrDefault(t, "webapp.actions.showToast", "显示 StatusToast")}
              </Button>
              <Button variant="primary" onClick={handleAlert}>
                {tOrDefault(t, "webapp.actions.modalAlert", "Modal Alert")}
              </Button>
              <Button variant="success" onClick={handleConfirm}>
                {tOrDefault(t, "webapp.actions.modalConfirm", "Modal Confirm")}
              </Button>
              <Button variant="danger" onClick={handlePrompt}>
                {tOrDefault(t, "webapp.actions.modalPrompt", "Modal Prompt")}
              </Button>
            </div>
          </div>
        </section>
      </main>
    </>
  );
}

export default App;

