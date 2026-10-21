// Vite configuration for the dashboard (doc 07 Phase 7; doc 05 "Serving (Phase 7)": "Dashboard backend").
// The built app must satisfy the backend's Content-Security-Policy (script-src 'self', style-src 'self'): scripts and styles are files with
// hashed names under dist/assets, nothing is inlined, and no modulepreload polyfill (an inline script) is added.
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
    assetsDir: "assets",
    assetsInlineLimit: 0,
    cssCodeSplit: false,
    modulePreload: { polyfill: false },
  },
  // `npm run dev` only: the API is the serving backend (python -m serving.server). The built app is served by that backend itself.
  server: { proxy: { "/api": "http://127.0.0.1:8081" } },
});
