// 由 Vite 构建时注入（见 packages/realtime/vite.config.ts）。
// 在非 Vite 环境（如 React Native/Metro）中这些常量通常不存在，应保持可选。
declare const __NEKO_VITE_MODE__: string | undefined;
declare const __NEKO_VITE_NODE_ENV__: string | undefined;


