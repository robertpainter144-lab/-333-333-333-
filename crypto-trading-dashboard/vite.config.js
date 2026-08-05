import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api/prices': {
        target: 'https://api.coinpaprika.com',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api\/prices/, ''),
      },
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/btc-charts': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
