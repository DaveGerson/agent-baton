import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  base: '/pmo/',
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: 'http://localhost:8741',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
  },
});
