import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

/**
 * Vite config for Chart Manager PWA
 *
 * NOTE for Claude Code:
 *   npm install vite @vitejs/plugin-react react react-dom
 *
 *   For the tablet to reach the dev server, add your machine's LAN IP:
 *     vite --host 0.0.0.0
 *
 *   VITE_API_URL in .env.local should point to your Express server:
 *     VITE_API_URL=http://192.168.1.X:3001
 *
 *   The transpose.js module is shared between the server and the PWA.
 *   In the PWA it's imported as: import { transposeChart } from '../../transpose'
 *   Adjust the relative path based on where you place the files.
 *   Alternatively, copy transpose.js into src/lib/transpose.js and update imports.
 */
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,      // expose on LAN for tablet access
    port: 3000,
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
  },
});
