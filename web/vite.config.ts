import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The Python backend serves the built UI from lumina/ui. In dev, proxy the API to it.
export default defineConfig({
  plugins: [react()],
  build: { outDir: "../lumina/ui", emptyOutDir: true, sourcemap: false, chunkSizeWarningLimit: 900 },
  server: {
    port: 5178,
    proxy: {
      "/api": { target: "http://127.0.0.1:8799", ws: true, changeOrigin: true },
    },
  },
});
