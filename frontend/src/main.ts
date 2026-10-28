/**
 * Vue entrypoint for the Image Editor MVP.
 * It mounts the single editor surface and imports the global light UI system.
 * App.vue owns canvas state, API calls, and user interaction lifecycle.
 * No backend initialization is required in the browser bundle.
 */
import { createApp } from 'vue'
import App from './App.vue'
import './style.css'

createApp(App).mount('#app')
