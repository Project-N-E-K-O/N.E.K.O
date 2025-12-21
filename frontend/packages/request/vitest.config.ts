import path from "path";
import { fileURLToPath } from "url";
import { defineConfig } from "vitest/config";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  resolve: {
    alias: {
      "@react-native-async-storage/async-storage": path.resolve(
        __dirname,
        "__mocks__/async-storage.ts"
      ),
    },
  },
});


