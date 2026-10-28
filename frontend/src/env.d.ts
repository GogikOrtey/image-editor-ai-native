/**
 * TypeScript declaration bridge for Vue single-file components.
 * Vite's Vue plugin transforms these modules during the frontend build.
 * It is consumed by tsconfig's src include and has no runtime side effects.
 */
declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<Record<string, never>, Record<string, never>, unknown>
  export default component
}
