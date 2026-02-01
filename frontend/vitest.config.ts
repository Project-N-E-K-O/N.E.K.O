import { defineConfig } from "vitest/config";
import path from "path";

export default defineConfig({
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    coverage: {
      provider: "v8",
      reporter: ["text", "json", "html"],
      exclude: [
        "node_modules/",
        "dist/",
        "**/*.d.ts",
        "**/*.config.*",
        "**/vendor/**",
        "scripts/**"
      ]
    }
  },
  resolve: {
    alias: {
      "@project_neko/components": path.resolve(__dirname, "packages/components/index.ts"),
      "@project_neko/common": path.resolve(__dirname, "packages/common/index.ts"),
      "@project_neko/request": path.resolve(__dirname, "packages/request/index.ts"),
      "@project_neko/web-bridge": path.resolve(__dirname, "packages/web-bridge/src/index.ts"),
      // Stub react-native for tests
      "react-native": path.resolve(__dirname, "packages/request/__mocks__/react-native.ts")
    }
  }
});
