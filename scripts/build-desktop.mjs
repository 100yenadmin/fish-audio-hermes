// Bundle src/desktop/plugin.tsx into the single uncompiled-ESM file Hermes Desktop loads (desktop/plugin.js).
// The desktop loader only resolves '@hermes/plugin-sdk' and 'react' (+ jsx runtimes), so those stay external.
// Unminified on purpose: easier for catalog reviewers and safer for the loader's import scanner.
// `--check` rebuilds in memory and fails if the committed bundle is stale.
import { readFileSync, writeFileSync } from 'node:fs'
import { build } from 'esbuild'

const OUT = 'desktop/plugin.js'
const check = process.argv.includes('--check')

const result = await build({
  entryPoints: ['src/desktop/plugin.tsx'],
  bundle: true,
  format: 'esm',
  platform: 'browser',
  target: 'es2022',
  jsx: 'automatic',
  minify: false,
  legalComments: 'none',
  external: ['@hermes/plugin-sdk', 'react', 'react/jsx-runtime', 'react/jsx-dev-runtime'],
  write: false,
  banner: { js: '// Generated from src/desktop by scripts/build-desktop.mjs. Do not edit by hand.' }
})

const text = result.outputFiles[0].text

if (check) {
  let current = ''
  try {
    current = readFileSync(OUT, 'utf8')
  } catch {
    // missing bundle counts as stale
  }
  if (current !== text) {
    console.error(`${OUT} is out of date. Run: npm run build`)
    process.exit(1)
  }
  console.log(`${OUT} is in sync`)
} else {
  writeFileSync(OUT, text)
  console.log(`wrote ${OUT} (${text.length} bytes)`)
}
