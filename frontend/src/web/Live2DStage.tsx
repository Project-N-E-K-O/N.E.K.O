import "./Live2DStage.css";
import React from "react";
import { createLive2DService } from "@project_neko/live2d-service";
import { createPixiLive2DAdapter } from "@project_neko/live2d-service/web";
import type { Live2DService } from "@project_neko/live2d-service";

type ScriptSpec = { id: string; src: string };

const scriptPromiseById = new Map<string, Promise<void>>();

function loadScriptOnce({ id, src }: ScriptSpec): Promise<void> {
  const existing = scriptPromiseById.get(id);
  if (existing) return existing;

  const promise = new Promise<void>((resolve, reject) => {
    // 已经存在对应 id 的脚本标签：认为加载过（或正在加载）
    const el = document.getElementById(id) as HTMLScriptElement | null;
    if (el) {
      // 若脚本已加载完成，直接 resolve；否则等事件
      if ((el as any)._nekoLoaded) {
        resolve();
        return;
      }
      el.addEventListener("load", () => resolve(), { once: true });
      el.addEventListener("error", () => reject(new Error(`script load failed: ${src}`)), { once: true });
      return;
    }

    const script = document.createElement("script");
    script.id = id;
    script.async = false; // 保持顺序（UMD 依赖）
    script.src = src;
    script.onload = () => {
      (script as any)._nekoLoaded = true;
      resolve();
    };
    script.onerror = () => reject(new Error(`script load failed: ${src}`));
    document.head.appendChild(script);
  });

  scriptPromiseById.set(id, promise);
  return promise;
}

function toAbsoluteUrl(staticBaseUrl: string, pathOrUrl: string) {
  const s = String(pathOrUrl || "");
  if (!s) return s;
  if (/^https?:\/\//i.test(s)) return s;
  // 支持传入 "/static/..." 或 "static/..."
  const p = s.startsWith("/") ? s : `/${s}`;

  // staticBaseUrl 约定为“站点根”（如 http://localhost:48911），但实际使用中可能误填成包含 /static：
  // - staticBaseUrl: http://localhost:48911/static
  // - p: /static/mao_pro/...
  // 若直接拼接会变成 /static/static/... 导致 404，这里做一次兼容兜底。
  const base = String(staticBaseUrl || "").replace(/\/+$/, "");
  if (base.endsWith("/static") && p.startsWith("/static/")) {
    return `${base}${p.slice("/static".length)}`;
  }
  return `${base}${p}`;
}

export interface Live2DStageProps {
  staticBaseUrl: string;
  /**
   * model3.json 的 URL 或 /static/... 路径
   */
  modelUri: string;
}

export function Live2DStage({ staticBaseUrl, modelUri }: Live2DStageProps) {
  const containerRef = React.useRef<HTMLDivElement | null>(null);
  const canvasRef = React.useRef<HTMLCanvasElement | null>(null);
  const serviceRef = React.useRef<Live2DService | null>(null);
  const offRef = React.useRef<(() => void) | null>(null);
  const [status, setStatus] = React.useState<"idle" | "loading" | "ready" | "error">("idle");

  React.useEffect(() => {
    let cancelled = false;

    async function boot() {
      setStatus("loading");

      // 复用你们 templates/index.html 的脚本依赖顺序：
      // - Cubism Core
      // - Cubism 2.x（可选，兼容旧模型；不影响 3/4）
      // - Pixi
      // - pixi-live2d-display (RaSan147 fork UMD)
      const scripts: ScriptSpec[] = [
        { id: "neko-live2d-cubism-core", src: toAbsoluteUrl(staticBaseUrl, "/static/libs/live2dcubismcore.min.js") },
        { id: "neko-live2d-cubism2-core", src: toAbsoluteUrl(staticBaseUrl, "/static/libs/live2d.min.js") },
        { id: "neko-pixi", src: toAbsoluteUrl(staticBaseUrl, "/static/libs/pixi.min.js") },
        { id: "neko-pixi-live2d-display", src: toAbsoluteUrl(staticBaseUrl, "/static/libs/index.min.js") },
      ];

      for (const s of scripts) {
        await loadScriptOnce(s);
      }

      if (cancelled) return;

      const container = containerRef.current;
      const canvas = canvasRef.current;
      if (!container || !canvas) {
        throw new Error("[webapp] Live2DStage: container/canvas 未就绪");
      }

      // Pixi/Live2DModel 将从 window.PIXI 注入（adapter 内部会检查）
      const adapter = createPixiLive2DAdapter({
        container,
        canvas,
        defaultAnchor: { x: 0.65, y: 0.75 },
      });

      const service = createLive2DService(adapter);
      serviceRef.current = service;

      offRef.current?.();
      offRef.current = service.on("stateChanged", ({ next }) => {
        setStatus(next.status);
      });

      const uri = toAbsoluteUrl(staticBaseUrl, modelUri);
      await service.loadModel({ uri, source: "url" });

      if (!cancelled) setStatus("ready");
    }

    boot().catch((e) => {
      console.error("[webapp] Live2DStage init failed:", e);
      if (!cancelled) setStatus("error");
    });

    return () => {
      cancelled = true;
      const svc = serviceRef.current;
      serviceRef.current = null;
      offRef.current?.();
      offRef.current = null;
      if (svc) {
        // best-effort 清理
        svc.dispose().catch(() => {});
      }
    };
  }, [modelUri, staticBaseUrl]);

  return (
    <div className="live2dStage">
      <div className="live2dStage__badge">Live2D: {status}</div>
      <div ref={containerRef} className="live2dStage__container">
        <canvas ref={canvasRef} className="live2dStage__canvas" />
      </div>
    </div>
  );
}

