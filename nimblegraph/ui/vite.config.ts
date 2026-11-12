import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

// `nimblegraph ui --dev` sets NIMBLEGRAPH_API to the Python server that answers /api/*
const api = process.env.NIMBLEGRAPH_API ?? 'http://127.0.0.1:4777';

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: { '/api': { target: api, changeOrigin: true } },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    chunkSizeWarningLimit: 1500,
  },
});
