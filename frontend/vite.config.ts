import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig(({ mode }) => {
  // Extra host names the dev server answers to (e.g. a tunnel), from CONTEXTHUB_ALLOWED_HOSTS
  // in frontend/.env.local. Empty by default: localhost only.
  const env = loadEnv(mode, process.cwd(), '')
  const allowedHosts = (env.CONTEXTHUB_ALLOWED_HOSTS ?? '')
    .split(',')
    .map((h) => h.trim())
    .filter(Boolean)
  return {
    plugins: [react()],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
      },
    },
    server: {
      allowedHosts,
      proxy: {
        '/api': {
          target: 'http://localhost:8000',
          rewrite: (p) => p.replace(/^\/api/, ''),
        },
      },
    },
  }
})
