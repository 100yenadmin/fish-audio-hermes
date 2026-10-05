// Fail if src/desktop imports a name from '@hermes/plugin-sdk' that a pinned Hermes Desktop SDK does not export.
// A missing named export is an ESM link error that would stop the whole plugin from loading on that build.
// Usage: node scripts/check-sdk-exports.mjs <path/to/apps/desktop/src/sdk/index.ts> [...more]
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

const sdkFiles = process.argv.slice(2)
if (!sdkFiles.length) {
  console.error('usage: check-sdk-exports.mjs <sdk/index.ts> [...]')
  process.exit(2)
}

function walk(dir) {
  return readdirSync(dir).flatMap(name => {
    const p = join(dir, name)
    if (name === '__tests__') return []
    return statSync(p).isDirectory() ? walk(p) : /\.(ts|tsx)$/.test(name) ? [p] : []
  })
}

const used = new Set()
for (const file of walk('src/desktop')) {
  const src = readFileSync(file, 'utf8')
  for (const m of src.matchAll(/import\s*\{([^}]*)\}\s*from\s*['"]@hermes\/plugin-sdk['"]/g)) {
    for (const part of m[1].split(',')) {
      const name = part.trim().replace(/^type\s+/, '').split(/\s+as\s+/)[0].trim()
      if (name && !part.trim().startsWith('type ')) used.add(name)
    }
  }
}

function exportedNames(src) {
  const names = new Set()
  for (const m of src.matchAll(/export\s+(?:declare\s+)?(?:async\s+)?(?:const|let|var|function\*?|class)\s+([A-Za-z_$][\w$]*)/g)) names.add(m[1])
  for (const m of src.matchAll(/export\s*\{([^}]*)\}/g)) {
    for (const part of m[1].split(',')) {
      const piece = part.trim().replace(/^type\s+/, '')
      if (!piece || part.trim().startsWith('type ')) continue
      const alias = piece.split(/\s+as\s+/)
      names.add((alias[1] ?? alias[0]).trim())
    }
  }
  return names
}

let failed = false
for (const file of sdkFiles) {
  const exported = exportedNames(readFileSync(file, 'utf8'))
  const missing = [...used].filter(name => !exported.has(name))
  if (missing.length) {
    failed = true
    console.error(`${file}: missing ${missing.join(', ')}`)
  } else {
    console.log(`${file}: all ${used.size} imported names exported`)
  }
}
process.exit(failed ? 1 : 0)
