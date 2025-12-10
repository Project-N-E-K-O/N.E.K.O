// 公共工具与类型的入口，可按需扩展

export type ApiResponse<T = unknown> = {
  code?: number;
  message?: string;
  data?: T;
};

export function noop(): void {
  // intentionally empty
}

