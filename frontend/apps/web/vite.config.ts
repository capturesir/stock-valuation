import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { VitePWA } from "vite-plugin-pwa";

// API in dev: `uvicorn backend.app.main:app --port 8000` (proxied below).
// In Docker the web container (Caddy) serves this build and proxies /api itself.
export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: "prompt",
      includeAssets: ["icon.svg", "apple-touch-icon.png"],
      manifest: {
        name: "估值工作台",
        short_name: "估值工作台",
        description: "股票估值：多方法理論價、估值走勢與 AI 分析（自帶金鑰）",
        lang: "zh-Hant",
        theme_color: "#1F5FBF",
        background_color: "#F4F5F7",
        display: "standalone",
        start_url: "/",
        icons: [
          { src: "icon-192.png", sizes: "192x192", type: "image/png" },
          { src: "icon-512.png", sizes: "512x512", type: "image/png" },
          { src: "icon-512-maskable.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
          { src: "icon.svg", sizes: "any", type: "image/svg+xml" },
        ],
      },
      workbox: {
        navigateFallback: "/index.html",
        navigateFallbackDenylist: [/^\/api\//],
        runtimeCaching: [
          {
            // last-known valuations and watchlist stay readable offline
            urlPattern: ({ url, request }) =>
              request.method === "GET" && /^\/api\/v1\/(watchlist|stocks\/[^/]+\/(valuations|prices)|settings\/ai|ai\/usage)/.test(url.pathname),
            handler: "NetworkFirst",
            options: { cacheName: "sv-api", networkTimeoutSeconds: 6, expiration: { maxEntries: 200, maxAgeSeconds: 30 * 86400 } },
          },
          {
            urlPattern: ({ url }) => url.origin === "https://fonts.googleapis.com" || url.origin === "https://fonts.gstatic.com",
            handler: "StaleWhileRevalidate",
            options: { cacheName: "sv-fonts" },
          },
        ],
      },
    }),
  ],
  server: {
    port: 5173,
    proxy: { "/api": { target: process.env.VITE_API_PROXY ?? "http://127.0.0.1:8000", changeOrigin: false } },
  },
  build: {
    chunkSizeWarningLimit: 900,
    rollupOptions: { output: { manualChunks: { echarts: ["echarts/core", "echarts/charts", "echarts/components", "echarts/renderers"] } } },
  },
});
