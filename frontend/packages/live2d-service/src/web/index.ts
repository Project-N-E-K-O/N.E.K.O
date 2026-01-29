/**
 * Web adapters（PixiJS + Live2D Cubism）
 *
 * 设计要点：
 * - service core 不直接依赖 Pixi/DOM；Web adapter 负责把命令映射到 Pixi/Live2D 实例
 * - 为了避免把 Pixi 打进 UMD bundles，adapter 支持通过 options 注入 PIXI/Live2DModel，
 *   或使用全局 window.PIXI（由宿主负责 script 加载顺序）
 */

export type { Live2DAdapter } from "../types";
export { createPixiLive2DAdapter } from "./pixiLive2DAdapter";
export type { PixiLive2DAdapterOptions } from "./pixiLive2DAdapter";

