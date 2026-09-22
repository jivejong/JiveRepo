import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";
import fs from "node:fs";
import { fileURLToPath } from "node:url";

const sharedParserPath = fileURLToPath(new URL("./server/chartParser.js", import.meta.url));
const sharedParserId = "\0shared-chart-parser";

// Serve and build the same parser source Express requires. For the browser
// entry, remove only the CommonJS branch so Vite does not wrap the entire app
// in the parser's lazy module factory; the parser still initializes the UMD
// browser global consumed by src/lib/chartParser.js.
function sharedChartParser() {
  return {
    name: "shared-chart-parser",
    resolveId(id) {
      if (id === "/chartParser.js") return sharedParserId;
      return null;
    },
    load(id) {
      if (id === sharedParserId) {
        return fs.readFileSync(sharedParserPath, "utf8").replace(
          '  if (typeof module === "object" && module.exports) module.exports = factory();\n  else root.ChartParser = factory();',
          "  root.ChartParser = factory();",
        );
      }
      return null;
    },
  };
}

// The server (Express) runs on :3001; in dev the Vite dev server proxies /api
// to it so the client can use relative URLs (which also work same-origin in
// production behind Caddy/Tailscale — required for the service worker).
export default defineConfig({
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://localhost:3001", changeOrigin: true },
    },
  },
  build: { outDir: "dist" },
  plugins: [
    sharedChartParser(),
    react(),
    VitePWA({
      registerType: "autoUpdate",
      injectRegister: "auto",          // auto-registers the SW; no manual code
      includeAssets: ["icon-192.png", "icon-512.png"],
      manifest: {
        name: "Chart Manager",
        short_name: "Charts",
        description: "Chord chart manager for live performance",
        start_url: "/",
        display: "standalone",
        background_color: "#1A1A1F",
        theme_color: "#1A1A1F",
        orientation: "any",
        icons: [
          { src: "/icon-192.png", sizes: "192x192", type: "image/png" },
          { src: "/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any maskable" },
        ],
      },
      workbox: {
        // Precache the built app shell (hash-named JS/CSS) so it opens offline.
        globPatterns: ["**/*.{js,css,html,png,svg,woff2}"],
        navigateFallback: "/index.html",
        navigateFallbackDenylist: [/^\/api\//],
        runtimeCaching: [
          {
            // Data still comes primarily from IndexedDB; this is a bonus layer.
            urlPattern: ({ url }) => url.pathname.startsWith("/api/"),
            handler: "NetworkFirst",
            options: { cacheName: "api", networkTimeoutSeconds: 4 },
          },
        ],
      },
    }),
  ],
});
