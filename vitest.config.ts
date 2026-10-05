import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  esbuild: { jsx: 'automatic' },
  resolve: {
    alias: {
      // Tests run against a small hand-written mock of the host SDK; the real one only exists inside Hermes Desktop.
      '@hermes/plugin-sdk': fileURLToPath(new URL('./src/desktop/__tests__/sdk-mock.tsx', import.meta.url))
    }
  },
  test: {
    environment: 'jsdom',
    globals: true,
    include: ['src/**/*.test.{ts,tsx}']
  }
})
