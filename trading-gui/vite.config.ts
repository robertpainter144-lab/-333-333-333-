import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Served under the /terminal path prefix behind the existing gateway.
export default defineConfig({
  base: "/terminal/",
  plugins: [react()],
  server: {
    // Local dev: the API has no CORS headers, so proxy /api to the shared
    // server — same-origin from the browser's point of view. Your access key
    // still travels as the x-api-key header set in the app's Settings.
    proxy: {
      "/api": {
        target: "https://129-153-96-201.sslip.io",
        changeOrigin: true,
      },
    },
  },
  build: {
    sourcemap: false,
    chunkSizeWarningLimit: 1500,
  },
});
