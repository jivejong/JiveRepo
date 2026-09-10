import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

/**
 * The proxy is the whole reason this config exists.
 *
 * The browser talks only to the Vite dev server, which forwards /api to Spring Boot on 8080. That
 * makes every request same-origin from the browser's point of view, so there is no CORS
 * preflight to satisfy and no need to add @CrossOrigin or a CorsConfigurationSource to the
 * backend just to support local development.
 *
 * `changeOrigin` rewrites the Host header to the target, which Spring does not require here but
 * costs nothing and avoids surprises if anything downstream ever inspects it.
 */
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8080',
        changeOrigin: true,
      },
    },
  },
});
