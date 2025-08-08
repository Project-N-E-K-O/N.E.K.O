import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // 添加一些配置来处理 hydration 问题
  compiler: {
    // 禁用一些可能导致问题的优化
    removeConsole: false,
  },
  // 配置 webpack 来处理一些浏览器扩展的问题
  webpack: (config, { isServer }) => {
    if (!isServer) {
      config.resolve.fallback = {
        ...config.resolve.fallback,
        fs: false,
      };
    }
    return config;
  },
};

export default nextConfig;
