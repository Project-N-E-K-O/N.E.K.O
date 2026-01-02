import "./Live2DStage.css";
import React from "react";
import { createLive2DManager } from "@project_neko/live2d-service";
import { createPixiLive2DAdapter } from "@project_neko/live2d-service/web";
import type { Live2DManager, Live2DPreferencesRepository, EmotionMappingProvider } from "@project_neko/live2d-service";

type ScriptSpec = { id: string; src: string };

const scriptPromiseById = new Map<string, Promise<void>>();
const SAVE_DEBOUNCE_MS = 350;

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
  preferences?: Live2DPreferencesRepository;
  emotionMappingProvider?: EmotionMappingProvider;
  /**
   * Live2D 引擎就绪回调（用于 App 层接管控制，逐步对齐 legacy Live2DManager）
   */
  onReady?: (manager: Live2DManager) => void;
}

export function Live2DStage({ staticBaseUrl, modelUri, preferences, emotionMappingProvider, onReady }: Live2DStageProps) {
  const containerRef = React.useRef<HTMLDivElement | null>(null);
  const canvasRef = React.useRef<HTMLCanvasElement | null>(null);
  const managerRef = React.useRef<Live2DManager | null>(null);
  const offRef = React.useRef<(() => void) | null>(null);
  const [status, setStatus] = React.useState<"idle" | "loading" | "ready" | "error">("idle");
  const [locked, setLocked] = React.useState(false);
  const lockedRef = React.useRef(false);
  const saveTimerRef = React.useRef<ReturnType<typeof setTimeout> | null>(null);
  const dragRef = React.useRef<{
    active: boolean;
    pointerId: number;
    startClientX: number;
    startClientY: number;
    startPosX: number;
    startPosY: number;
    moved: boolean;
  } | null>(null);

  const scheduleSave = React.useCallback(() => {
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(() => {
      saveTimerRef.current = null;
      const mgr = managerRef.current;
      if (!mgr) return;
      mgr.savePreferences().catch(() => {});
    }, SAVE_DEBOUNCE_MS);
  }, []);

  const toggleLocked = React.useCallback(() => {
    setLocked((prev) => {
      const next = !prev;
      lockedRef.current = next;
      managerRef.current?.setLocked(next);
      return next;
    });
  }, []);

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

      const manager = createLive2DManager(adapter, { preferences, emotionMappingProvider });
      managerRef.current = manager;
      onReady?.(manager);

      offRef.current?.();
      offRef.current = manager.on("stateChanged", ({ next }) => {
        setStatus(next.status);
      });

      const uri = toAbsoluteUrl(staticBaseUrl, modelUri);
      await manager.loadModel(uri);

      if (!cancelled) setStatus("ready");
    }

    boot().catch((e) => {
      console.error("[webapp] Live2DStage init failed:", e);
      if (!cancelled) setStatus("error");
    });

    return () => {
      cancelled = true;
      const mgr = managerRef.current;
      managerRef.current = null;
      offRef.current?.();
      offRef.current = null;
      if (saveTimerRef.current) {
        clearTimeout(saveTimerRef.current);
        saveTimerRef.current = null;
      }
      if (mgr) {
        // best-effort 清理
        mgr.dispose().catch(() => {});
      }
    };
  }, [modelUri, staticBaseUrl, preferences, emotionMappingProvider, onReady]);

  // Web 交互层（拖拽 / 滚轮缩放）：宿主实现，映射到 manager.setTransform + savePreferences
  React.useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const clamp = (v: number, min: number, max: number) => Math.max(min, Math.min(max, v));

    const onPointerDown = (e: PointerEvent) => {
      if (lockedRef.current) return;
      const mgr = managerRef.current;
      if (!mgr) return;

      // 鼠标仅响应左键；触摸 pointerType=touch 时 button 可能为 0
      if (e.pointerType === "mouse" && e.button !== 0) return;

      const snap = mgr.getTransformSnapshot();
      if (!snap) return;

      try {
        canvas.setPointerCapture(e.pointerId);
      } catch {
        // ignore
      }

      dragRef.current = {
        active: true,
        pointerId: e.pointerId,
        startClientX: e.clientX,
        startClientY: e.clientY,
        startPosX: snap.position.x,
        startPosY: snap.position.y,
        moved: false,
      };

      // 阻止浏览器默认的拖拽/选中行为（尤其是触摸）
      e.preventDefault();
    };

    const onPointerMove = (e: PointerEvent) => {
      if (lockedRef.current) return;
      const mgr = managerRef.current;
      if (!mgr) return;
      const d = dragRef.current;
      if (!d || !d.active) return;
      if (e.pointerId !== d.pointerId) return;

      const dx = e.clientX - d.startClientX;
      const dy = e.clientY - d.startClientY;
      if (Math.abs(dx) + Math.abs(dy) > 0.5) d.moved = true;

      mgr
        .setTransform({
          position: { x: d.startPosX + dx, y: d.startPosY + dy },
        })
        .catch(() => {});

      e.preventDefault();
    };

    const endDrag = (e: PointerEvent) => {
      const d = dragRef.current;
      if (!d || !d.active) return;
      if (e.pointerId !== d.pointerId) return;

      dragRef.current = null;
      try {
        canvas.releasePointerCapture(e.pointerId);
      } catch {
        // ignore
      }
      if (d.moved) scheduleSave();
    };

    const onWheel = (e: WheelEvent) => {
      if (lockedRef.current) return;
      const mgr = managerRef.current;
      if (!mgr) return;

      const snap = mgr.getTransformSnapshot();
      if (!snap) return;

      // 约定：deltaY > 0 缩小，deltaY < 0 放大
      const delta = Number(e.deltaY);
      if (!Number.isFinite(delta)) return;

      const k = clamp(1 - delta * 0.001, 0.85, 1.15);
      const nextX = clamp(snap.scale.x * k, 0.1, 5);
      const nextY = clamp(snap.scale.y * k, 0.1, 5);

      // 如果启用了缩放，我们希望避免页面滚动
      e.preventDefault();

      mgr
        .setTransform({
          scale: { x: nextX, y: nextY },
        })
        .catch(() => {});

      scheduleSave();
    };

    canvas.addEventListener("pointerdown", onPointerDown);
    canvas.addEventListener("pointermove", onPointerMove);
    canvas.addEventListener("pointerup", endDrag);
    canvas.addEventListener("pointercancel", endDrag);
    canvas.addEventListener("wheel", onWheel, { passive: false });

    return () => {
      canvas.removeEventListener("pointerdown", onPointerDown);
      canvas.removeEventListener("pointermove", onPointerMove);
      canvas.removeEventListener("pointerup", endDrag);
      canvas.removeEventListener("pointercancel", endDrag);
      canvas.removeEventListener("wheel", onWheel as any);
    };
  }, [scheduleSave]);

  return (
    <div className="live2dStage">
      <div className="live2dStage__badge">Live2D: {status}</div>
      <button
        type="button"
        className="live2dStage__lockButton"
        title={locked ? "已锁定（点击解锁）" : "未锁定（点击锁定）"}
        onClick={toggleLocked}
      >
        {locked ? (
          <svg className="live2dStage__lockIcon" viewBox="0 0 24 24" aria-hidden="true">
            <path
              fill="currentColor"
              d="M12 1a5 5 0 0 0-5 5v3H6a2 2 0 0 0-2 2v9a3 3 0 0 0 3 3h10a3 3 0 0 0 3-3v-9a2 2 0 0 0-2-2h-1V6a5 5 0 0 0-5-5Zm-3 8V6a3 3 0 1 1 6 0v3H9Z"
            />
          </svg>
        ) : (
          <svg className="live2dStage__lockIcon" viewBox="0 0 24 24" aria-hidden="true">
            <path
              fill="currentColor"
              d="M17 8V6a5 5 0 0 0-9.584-2.23a1 1 0 0 0 1.768.93A3 3 0 0 1 15 6v2H6a2 2 0 0 0-2 2v9a3 3 0 0 0 3 3h10a3 3 0 0 0 3-3v-9a2 2 0 0 0-2-2h-1Zm-1 2h2v9a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1v-9h10Z"
            />
          </svg>
        )}
      </button>
      <div ref={containerRef} className="live2dStage__container">
        <canvas ref={canvasRef} className="live2dStage__canvas" />
      </div>
    </div>
  );
}

