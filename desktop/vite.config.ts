import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// @ts-expect-error process is a nodejs global
const host = process.env.TAURI_DEV_HOST;
const backendOrigin = process.env.AMADEUS_BACKEND_ORIGIN || "http://127.0.0.1:8000";
const frontendPort = Number(process.env.AMADEUS_FRONTEND_PORT || "1420");

// https://vite.dev/config/
export default defineConfig(async () => ({
  plugins: [react()],

  // Vite options tailored for Tauri development and only applied in `tauri dev` or `tauri build`
  //
  // 1. prevent Vite from obscuring rust errors
  clearScreen: false,
  // 2. tauri expects a fixed port, fail if that port is not available
  server: {
    port: frontendPort,
    strictPort: true,
    // Keep local development on an explicit IPv4 loopback. On this Windows
    // host, Vite's `localhost` default resolves to ::1 but the IPv6 loopback
    // socket is unavailable, so Vite can print "ready" without a reachable
    // server. TAURI_DEV_HOST still wins for intentional remote-device use.
    host: host || "127.0.0.1",
    hmr: host
      ? {
          protocol: "ws",
          host,
          port: 1421,
        }
      : undefined,
    watch: {
      // 3. tell Vite to ignore watching `src-tauri`
      ignored: ["**/src-tauri/**"],
    },
    proxy: {
      '/ws': {
        target: backendOrigin,
        ws: true,
        changeOrigin: true
      },
      '/api': {
        target: backendOrigin,
        changeOrigin: true
      }
    }
  },
}));
