/**
 * Vite configuration for the Vue Image Editor client.
 * The Vue plugin compiles App.vue and preserves its TypeScript script block.
 * API requests remain same-origin so the app can call the existing backend proxy.
 * No backend or server behavior is changed by this frontend-only config.
 */
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8000',
    },
  },
})
