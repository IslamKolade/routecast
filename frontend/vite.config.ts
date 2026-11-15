/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// In development, /api is proxied to the Django server so no CORS setup is needed.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8000',
    },
  },
  build: {
    // MapLibre is ~800 kB on its own; keep it in a separate, long-cached chunk.
    chunkSizeWarningLimit: 1100,
    rollupOptions: {
      output: { manualChunks: { maplibre: ['maplibre-gl'] } },
    },
  },
  test: {
    environment: 'node',
  },
});
