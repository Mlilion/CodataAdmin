import { defineConfig } from "vitest/config";
import vue from "@vitejs/plugin-vue";
import path from "node:path";

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "src"),
      "@utils": path.resolve(__dirname, "src/utils"),
      "@stores": path.resolve(__dirname, "src/store"),
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    include: ["src/**/*.{test,spec}.{ts,js}"],
  },
});
