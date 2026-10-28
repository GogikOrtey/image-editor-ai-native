/**
 * Vite configuration for the Vue Image Editor client.
 * The Vue plugin compiles App.vue and preserves its TypeScript script block.
 * API requests stay same-origin and are forwarded to the FastAPI backend on port 8000.
 *
 * The proxy timeout is raised because image generation can take over a minute; the default
 * caused 504 responses on slower models even though the backend was still working normally.
 */
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        // Generation regularly exceeds 60 s; keep the socket open well past that.
        timeout: 600_000,
        proxyTimeout: 600_000,
      },
    },
  },
})