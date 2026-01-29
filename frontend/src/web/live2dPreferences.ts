import type { Live2DPreferencesRepository, Live2DPreferencesSnapshot } from "@project_neko/live2d-service";

type RawPref = {
  model_path?: string;
  position?: { x?: number; y?: number };
  scale?: { x?: number; y?: number };
  parameters?: Record<string, number>;
  display?: { screenX?: number; screenY?: number };
};

function normalizePath(p: string): string {
  return String(p || "")
    .split("#")[0]
    .split("?")[0]
    .trim()
    .toLowerCase();
}

function fileName(p: string): string {
  const clean = String(p || "").split("#")[0].split("?")[0];
  const parts = clean.split("/").filter(Boolean);
  return parts[parts.length - 1] || "";
}

function isFiniteNumber(v: unknown): v is number {
  return typeof v === "number" && Number.isFinite(v);
}

export function createLive2DPreferencesRepository(apiBase: string): Live2DPreferencesRepository {
  const base = String(apiBase || "").replace(/\/+$/, "");

  async function fetchAll(): Promise<RawPref[]> {
    const resp = await fetch(`${base}/api/config/preferences`);
    if (!resp.ok) throw new Error(`preferences_http_${resp.status}`);
    const data = (await resp.json()) as unknown;
    return Array.isArray(data) ? (data as RawPref[]) : [];
  }

  function pickBest(all: RawPref[], modelUri: string): RawPref | null {
    const target = normalizePath(modelUri);
    if (!target) return null;

    // 1) 精确匹配（旧版保存用的是 model_path）
    const exact = all.find((p) => normalizePath(String(p?.model_path || "")) === target);
    if (exact) return exact;

    // 2) 文件名匹配（旧版 index.html 有同样的兜底逻辑）
    const tName = fileName(modelUri);
    if (tName) {
      const byName = all.find((p) => fileName(String(p?.model_path || "")) === tName);
      if (byName) return byName;
    }

    // 3) 目录名包含匹配（弱兜底：通过模型名/目录名）
    const parts = target.split("/").filter(Boolean);
    const modelDir = parts.length >= 2 ? parts[parts.length - 2] : "";
    if (modelDir) {
      const byDir = all.find((p) => normalizePath(String(p?.model_path || "")).includes(`/${modelDir}/`));
      if (byDir) return byDir;
    }

    return null;
  }

  return {
    async load(modelUri: string): Promise<Live2DPreferencesSnapshot | null> {
      const all = await fetchAll();
      const pref = pickBest(all, modelUri);
      if (!pref) return null;

      const pos = pref.position || {};
      const scale = pref.scale || {};

      const snapshot: Live2DPreferencesSnapshot = {
        modelUri,
        position: isFiniteNumber(pos.x) && isFiniteNumber(pos.y) ? { x: pos.x, y: pos.y } : undefined,
        scale: isFiniteNumber(scale.x) && isFiniteNumber(scale.y) ? { x: scale.x, y: scale.y } : undefined,
        parameters: pref.parameters && typeof pref.parameters === "object" ? pref.parameters : undefined,
      };
      return snapshot;
    },

    async save(snapshot: Live2DPreferencesSnapshot): Promise<void> {
      const position = snapshot.position;
      const scale = snapshot.scale;
      if (!position || !scale) return;

      // 与后端 validate_model_preferences 对齐：必须包含 model_path/position/scale，且数值有效
      if (!isFiniteNumber(position.x) || !isFiniteNumber(position.y)) return;
      if (!isFiniteNumber(scale.x) || !isFiniteNumber(scale.y)) return;
      if (scale.x <= 0 || scale.y <= 0) return;

      const body: Record<string, unknown> = {
        model_path: snapshot.modelUri,
        position: { x: position.x, y: position.y },
        scale: { x: scale.x, y: scale.y },
      };
      if (snapshot.parameters) body.parameters = snapshot.parameters;

      const resp = await fetch(`${base}/api/config/preferences`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!resp.ok) throw new Error(`preferences_save_http_${resp.status}`);

      const data = (await resp.json()) as { success?: boolean; error?: string };
      if (data && data.success === false) {
        throw new Error(data.error || "preferences_save_failed");
      }
    },
  };
}

