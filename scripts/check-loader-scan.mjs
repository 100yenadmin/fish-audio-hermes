// Run each pinned Hermes Desktop runtime loader's own import scanner over the built bundle and fail if it
// reports an unsupported import. The scanner is not a parser: an older one (the fork's) does not know regex
// literals, so a pattern containing a quote or backtick can make it read later code as `from "x"` and refuse
// to load the plugin. Rather than re-implementing it, this extracts the scanning helpers from each pinned
// runtime-loader.ts by source text, strips types with esbuild, and evaluates them.
//
// Usage: node scripts/check-loader-scan.mjs [--self-test] [--bundle desktop/plugin.js] <runtime-loader.ts> [...]
//   --self-test  also assert the scanners work: a foreign import is flagged by every loader, and the
//                positive-control fixture (scripts/fixtures/loader-trap.js) is flagged by at least one loader
//                (the regex-unaware fork); it must be, or this check could not catch the trap at all.
import { existsSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { transform } from 'esbuild'

const args = process.argv.slice(2)
const selfTest = args.includes('--self-test')
const bundleAt = args.indexOf('--bundle')
const bundle = bundleAt >= 0 ? args[bundleAt + 1] : 'desktop/plugin.js'
// Drop the --bundle value only when --bundle was given (bundleAt === -1 would otherwise drop argv[0]).
const loaders = args.filter((arg, i) => !arg.startsWith('--') && !(bundleAt >= 0 && i === bundleAt + 1))
if (!loaders.length) {
  console.error('usage: check-loader-scan.mjs [--self-test] [--bundle file] <runtime-loader.ts> [...]')
  process.exit(2)
}

const ENTRY = 'unsupportedImports'

/** Top-level declarations of a prettier-formatted TS file: name → source text up to the next column-0 construct. */
function topLevelDeclarations(src) {
  const bounds = [...src.matchAll(/^(?:export|import|function|async|const|let|var|class|type|interface|\/\*|\/\/)/gm)].map(m => m.index)
  bounds.push(src.length)
  const decls = new Map()
  for (const m of src.matchAll(/^(?:export\s+)?(?:async\s+)?(?:function\*?|const|let|var)\s+([A-Za-z_$][\w$]*)/gm)) {
    const end = bounds.find(b => b > m.index)
    decls.set(m[1], src.slice(m.index, end).replace(/^export\s+/, ''))
  }
  return decls
}

/** Specifier keys of sdkImportMap() in the sibling sdk/runtime.ts (the loader imports it from there). */
function importMapKeys(loaderPath) {
  const runtime = join(dirname(loaderPath), '..', 'sdk', 'runtime.ts')
  if (!existsSync(runtime)) throw new Error(`cannot find ${runtime} (needed for sdkImportMap)`)
  const body = /export function sdkImportMap\(\)[^{]*\{([\s\S]*?)\n\}/.exec(readFileSync(runtime, 'utf8'))?.[1]
  const keys = body ? [...body.matchAll(/^\s*(?:'([^']+)'|"([^"]+)"|([A-Za-z_$][\w$]*))\s*:/gm)].map(m => m[1] ?? m[2] ?? m[3]) : []
  if (!keys.includes('@hermes/plugin-sdk')) throw new Error(`could not read sdkImportMap keys from ${runtime}`)
  return keys
}

/** Build `unsupportedImports(source)` from a pinned loader: the entry plus every top-level helper it reaches. */
async function loadScanner(loaderPath) {
  const decls = topLevelDeclarations(readFileSync(loaderPath, 'utf8'))
  if (!decls.has(ENTRY)) throw new Error(`${loaderPath}: no top-level ${ENTRY}()`)
  const included = new Map()
  const queue = [ENTRY]
  while (queue.length) {
    const name = queue.shift()
    if (included.has(name) || !decls.has(name)) continue
    // Strip types and comments first so only real identifiers pull in further helpers.
    const js = (await transform(decls.get(name), { loader: 'ts' })).code
    included.set(name, js)
    for (const id of new Set(js.match(/[A-Za-z_$][\w$]*/g))) if (decls.has(id) && !included.has(id)) queue.push(id)
  }
  const keys = importMapKeys(loaderPath)
  const map = Object.fromEntries(keys.map(key => [key, `shim:${key}`]))
  // Declarations are hoisted inside one function scope, so their order does not matter.
  const factory = new Function('sdkImportMap', `${[...included.values()].join('\n')}\nreturn ${ENTRY}`)
  return { scan: factory(() => map), helpers: [...included.keys()], keys }
}

let failed = false
const here = dirname(fileURLToPath(import.meta.url))
const trap = readFileSync(join(here, 'fixtures', 'loader-trap.js'), 'utf8')
let trapCaught = 0
let scanned = 0

for (const loader of loaders) {
  let scanner
  try {
    scanner = await loadScanner(loader)
  } catch (error) {
    console.error(`${loader}: extraction failed: ${error.message}`)
    failed = true
    continue
  }
  scanned += 1
  const label = `${loader} [${scanner.helpers.join(', ')}]`
  const bad = scanner.scan(readFileSync(bundle, 'utf8'))
  if (bad.length) {
    failed = true
    console.error(`${label}: ${bundle} would be refused, unsupported import(s): ${bad.join(', ')}`)
  } else {
    console.log(`${label}: ${bundle} passes the import scan`)
  }
  if (selfTest) {
    const foreign = scanner.scan('import pad from "left-pad"\nimport { x } from \'@hermes/plugin-sdk\'\n')
    if (foreign.join() !== 'left-pad') {
      failed = true
      console.error(`${loader}: self-test: expected [left-pad] from a foreign import, got [${foreign.join(', ')}]`)
    }
    const caught = scanner.scan(trap)
    if (caught.length) trapCaught += 1
    console.log(`${loader}: self-test: loader-trap.js ${caught.length ? `flagged (${caught.join(', ')})` : 'not flagged'}`)
  }
}

// Every loader named on the command line must have been scanned (upstream and fork in CI).
if (selfTest && (scanned !== loaders.length || scanned < 2)) {
  failed = true
  console.error(`self-test: scanned ${scanned} of ${loaders.length} loader(s); expected every given loader, at least 2`)
}
if (selfTest && !trapCaught) {
  failed = true
  console.error('self-test: loader-trap.js was not flagged by any loader; the check cannot catch the regex trap')
}
process.exit(failed ? 1 : 0)
