// Layer B entry point: node acceptance/desktop/run.mjs --host-json <out>/host.json --out <out>
//   [--rows B-NOKEY-1,...] [--themes light,dark] [--seed <prebuilt desktop tree>] [--vite-port 5194] [--cdp-port 9281]
//   [--no-launch] [--keep-up] [--no-billed] [--merge]
// Builds or reuses the host's Desktop dev build, drives every Layer B row (UI rows in light and dark through the app's own
// Appearance control), and writes <out>/rows-b.json plus screenshots under <out>/shots/.
import { execFileSync, spawnSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import * as c from './cdp.mjs'
import { keyFieldProbe } from './probes.mjs'
import { convRows } from './conv.mjs'

const buildEnv = () => Object.fromEntries(Object.entries(process.env).filter(([k]) => !/(?:API_KEY|TOKEN|SECRET)$/.test(k)))
const HERE = path.dirname(fileURLToPath(import.meta.url))
const REPO = path.resolve(HERE, '..', '..')
const FAKE_KEY = 'sk-fish-acceptance-fake-000000'
const args = parseArgs(process.argv.slice(2))
const host = JSON.parse(fs.readFileSync(args['host-json'], 'utf8'))
const OUT = path.resolve(args.out || path.dirname(args['host-json']))
const SHOTS = path.join(OUT, 'shots')
const VITE = Number(args['vite-port'] || process.env.ACCEPTANCE_VITE_PORT || 5194)
const CDP = Number(args['cdp-port'] || process.env.ACCEPTANCE_CDP_PORT || 9281)
const THEMES = (args.themes || 'light,dark').split(',')
const ONLY = args.rows ? new Set(args.rows.split(',')) : null
const H = host.host
const rows = []
const want = id => !ONLY || ONLY.has(id) || [...ONLY].some(o => id.startsWith(o + '@'))

function parseArgs(a) {
  const o = {}
  for (let i = 0; i < a.length; i++) {
    if (!a[i].startsWith('--')) continue
    const k = a[i].slice(2)
    if (i + 1 < a.length && !a[i + 1].startsWith('--')) o[k] = a[++i]; else o[k] = true
  }
  return o
}
export function row(id, status, reason, extra = {}) {
  const r = { id, status, reason, evidence: [], metrics: {}, ...extra }
  rows.push(r)
  console.log(`[${H}] ${id}${r.theme ? ' (' + r.theme + ')' : ''}: ${status} — ${reason}`)
  fs.writeFileSync(path.join(OUT, 'rows-b.partial.json'), JSON.stringify(rows, null, 1))
  return r
}
const shotPath = (id, theme, tag = '') => path.join(SHOTS, `${id}${tag ? '-' + tag : ''}${theme ? '-' + theme : ''}.png`)

// --- host control: Lane A's hostctl.sh. Its key commands only edit the profile .env, so each is followed by a
// gateway restart (the Desktop save path also sets the key in the gateway's environment). ---
function hostctl(cmd) {
  const r = spawnSync(path.join(REPO, 'acceptance', 'hostctl.sh'), ['--run-dir', OUT, cmd], { encoding: 'utf8', timeout: 240000 })
  if (r.status !== 0) throw new Error(`hostctl ${cmd} failed: ${(r.stderr || '').split('\n').slice(-3).join(' ')}`)
  if (cmd.endsWith('-key')) hostctl('restart-gateway')
  return (r.stdout || '').trim()
}
const token = () => fs.readFileSync(host.session_token_file, 'utf8').trim()
export async function gw(p, init = {}) {
  const res = await fetch(host.gateway_url + p, { ...init, headers: { 'X-Hermes-Session-Token': token(), Authorization: 'Bearer ' + token(), 'Content-Type': 'application/json', ...(init.headers || {}) } })
  let body = null; try { body = await res.json() } catch {}
  return { status: res.status, body }
}
async function waitGateway(ms = 90000) {
  const t0 = Date.now()
  while (Date.now() - t0 < ms) { try { const r = await gw('/api/plugins/fish-audio/available'); if (r.status === 200) return r.body } catch {} await c.sleep(1000) }
  throw new Error('gateway did not come back')
}

// --- Desktop build + launch ---
function buildTree() {
  if (args.tree) return args.tree
  const a = ['--host', H, '--repo', host.desktop_src_repo, '--ref', host.desktop_ref, '--cache', process.env.ACCEPTANCE_CACHE || `${process.env.HOME}/.cache/fish-audio-acceptance`]
  const seed = args.seed || process.env.ACCEPTANCE_DESKTOP_SEED
  if (seed) a.push('--seed', seed)
  for (let attempt = 1; attempt <= 2; attempt++) {
    const r = spawnSync(path.join(HERE, 'build.sh'), a, { encoding: 'utf8', timeout: 1800000, env: buildEnv() })
    if (r.status === 0) return r.stdout.trim().split('\n').pop()
    console.error(`build attempt ${attempt} failed: ${(r.stderr || '').split('\n').slice(-4).join(' ')}`)
  }
  throw new Error('Desktop dev build failed twice')
}
function launch(tree) {
  const half = path.join(host.hermes_home, 'plugins', 'fish-audio', 'desktop', 'plugin.js')
  const a = ['up', '--tree', tree, '--out', OUT, '--gateway', host.gateway_url, '--token-file', host.session_token_file,
    '--vite-port', String(VITE), '--cdp-port', String(CDP)]
  // upstream has no bundled Fish Audio: install the plugin's desktop half the way Install from Git does.
  // es.20 bundles its own desktop half (src/plugins/fish-audio), which is what an evaOS user runs.
  if (H === 'upstream' && fs.existsSync(half)) a.push('--desktop-half', half)
  execFileSync(path.join(HERE, 'launch.sh'), a, { stdio: ['ignore', 'inherit', 'inherit'], timeout: 300000, env: buildEnv() })
}

// --- shared UI steps ---
const SHIM = fs.readFileSync(path.join(HERE, 'mic-shim.js'), 'utf8')
async function setup(s) {
  await s.cdp.send('Page.enable')
  await s.cdp.send('Page.addScriptToEvaluateOnNewDocument', { source: SHIM })
  await s.page.evaluate(SHIM)
  await c.clickText(s, 'No thanks', { timeout: 1500 }).catch(() => {})
  return enableDesktopHalf(s)
}
async function enableDesktopHalf(s) {
  // Install from Git delivers the desktop half switched off; a user turns it on once (setup, not a row).
  // On a cold launch the plugin list takes ~15-20 s to arrive.
  // Hermes 0.22.5+ opens this tab on the catalog and keeps an installed plugin's switches in a detail pane that the
  // plugin= deep link opens; older hosts show the switch in the row, and the deep link only highlights it.
  await c.nav(s, '#/')
  await c.sleep(500)
  await c.nav(s, '#/capabilities?tab=plugins&plugin=fish-audio')
  const sw = s.page.locator('[role=switch][aria-label="Desktop: Fish Audio"]')
  for (let i = 0; i < 90 && !(await sw.count()); i++) await c.sleep(500)
  if (!(await sw.count())) return 'no Desktop switch for Fish Audio (bundled half or not installed)'
  let result = 'Desktop half already on'
  if ((await sw.first().getAttribute('aria-checked')) === 'false') { await sw.first().click(); await c.sleep(1500); result = 'Desktop half switched on by the harness' }
  await c.page_escape(s)
  return result
}
// The key lands by file + gateway restart, which the Desktop half only notices on its next 60 s probe; a forced
// probe (plugin init) fetches /account and the credit chip at once. Reload the renderer, as a user restarting would.
async function reloadRenderer(s) {
  const t0 = Date.now()
  await s.page.evaluate(() => location.reload())
  await s.page.waitForLoadState('domcontentloaded', { timeout: 60000 }).catch(() => {})
  const v = s.page.getByText('Voices', { exact: true })
  for (let i = 0; i < 90 && !(await v.count().catch(() => 0)); i++) await c.sleep(500)
  await c.clickText(s, 'No thanks', { timeout: 1500 }).catch(() => {})
  return `renderer reloaded; app shell with Voices after ${Date.now() - t0} ms`
}
async function openVoices(s) {
  await c.page_escape(s)
  await c.nav(s, '#/'); await c.sleep(500)
  const v = s.page.getByText('Voices', { exact: true })
  for (let i = 0; i < 60 && !(await v.count()); i++) await c.sleep(500)
  await c.clickText(s, 'Voices', { timeout: 10000 })
  for (let i = 0; i < 40; i++) {
    const t = await c.text(s)
    if (/Connect your Fish Audio account|Library|Check again/.test(t)) break
    await c.sleep(250)
  }
  await c.sleep(800)
}
async function cardButton(s) {
  const b = await c.buttons(s, /^(Enter API key|Open Plugins)$/)
  return b[0] || null
}
async function settleKeyField(s, ms = 6000) {
  const t0 = Date.now(); let p
  while (Date.now() - t0 < ms) { p = await s.page.evaluate(keyFieldProbe); if (p.found) break; await c.sleep(100) }
  await c.sleep(1000) // the first plugins visit re-renders ~2.2 s after launch (cold-start race); measure the settled view
  p = await s.page.evaluate(keyFieldProbe); p.foundAfterMs = Date.now() - t0
  return p
}
const why = p => !p.found ? `no "Fish Audio API key" input after settling (hash ${p.hash})`
  : `inView=${p.inView} placeholder=${JSON.stringify(p.placeholder)} edgeContrast=${p.contrast}:1 (border ${p.borderContrast}, inset ${p.shadowContrast}) card ${p.colors.card}`

// --- rows ---
async function noKeyRows(s, theme) {
  await openVoices(s)
  const t = await c.text(s)
  const btn = await cardButton(s)
  const card = /Connect your Fish Audio account/.test(t)
  if (want('B-NOKEY-1')) {
    const r = row('B-NOKEY-1', card && btn ? 'pass' : 'fail', card ? `onboarding card shown; primary key button "${btn}"` : 'no onboarding card on Voices', { theme })
    r.metrics.primary_button = btn; r.evidence.push(await c.shot(s, shotPath('B-NOKEY-1', theme)))
  }
  if (!want('B-NOKEY-2') || !btn) { if (want('B-NOKEY-2')) row('B-NOKEY-2', 'fail', 'no card key button to click', { theme }); return }
  await c.clickText(s, btn)
  const p = await settleKeyField(s)
  const r = row('B-NOKEY-2', p.pass ? 'pass' : 'fail', `after "${btn}" (no gear click): ${why(p)}`, { theme })
  r.metrics = { button: btn, ...p }; r.evidence.push(await c.shot(s, shotPath('B-NOKEY-2', theme)))
  // B-NOKEY-2b (advisory): the state after the user also clicks the plugin row's gear, scrolled to the field.
  const gear = s.page.locator('[aria-label="Settings: fish-audio"]')
  if (want('B-NOKEY-2b')) {
    if (!p.found && await gear.count()) {
      await gear.first().click(); await c.sleep(1200)
      await s.page.evaluate(() => document.getElementById('plugin-settings-fish-audio-api_key')?.scrollIntoView({ block: 'center' }))
      await c.sleep(600)
      const g = await s.page.evaluate(keyFieldProbe)
      const r2 = row('B-NOKEY-2b', g.pass ? 'pass' : 'fail', `advisory, after the gear click + scroll: ${why(g)}`, { theme, advisory: true })
      r2.metrics = g; r2.evidence.push(await c.shot(s, shotPath('B-NOKEY-2b', theme)))
    } else row('B-NOKEY-2b', 'na', p.found ? 'the card button already opened the form; no gear step' : 'no gear on the landing page', { theme, advisory: true })
  }
}

async function pluginPageRow(s, theme) {
  if (!want('B-NOKEY-3')) return
  await c.page_escape(s)
  await c.nav(s, '#/settings?tab=plugins&plugin=fish-audio'); await c.sleep(1500)
  const t = await c.text(s)
  if (!/Connect your Fish Audio account/.test(t)) {
    const r = row('B-NOKEY-3', H === 'evaos' ? 'na' : 'fail', H === 'evaos' ? 'es.20 has no Settings ▸ Plugins page for the plugin (pre-0.21.6 SDK)' : 'Settings ▸ Plugins ▸ Fish Audio shows no no-key card', { theme })
    r.evidence.push(await c.shot(s, shotPath('B-NOKEY-3', theme, 'page'))); return
  }
  const btn = await cardButton(s)
  const shot1 = await c.shot(s, shotPath('B-NOKEY-3', theme, 'page'))
  if (!btn) { row('B-NOKEY-3', 'fail', 'plugin page card has no key button', { theme, evidence: [shot1] }); return }
  await c.clickText(s, btn)
  const p = await settleKeyField(s)
  const r = row('B-NOKEY-3', p.pass ? 'pass' : 'fail', `plugin page card "${btn}" → ${why(p)}`, { theme, evidence: [shot1] })
  r.metrics = { button: btn, ...p }; r.evidence.push(await c.shot(s, shotPath('B-NOKEY-3', theme)))
}

async function paletteRow(s, theme) {
  if (!want('B-PAL-1')) return
  await c.page_escape(s)
  await c.nav(s, '#/'); await c.sleep(600)
  await s.page.keyboard.press('Meta+k'); await c.sleep(700)
  await s.page.keyboard.type('Fish Audio'); await c.sleep(700)
  const items = await s.page.evaluate(() => [...document.querySelectorAll('[cmdk-item],[role=option]')].map(e => e.textContent.replace(/\s+/g, ' ').trim()).filter(t => /fish.audio/i.test(t)))
  const cmds = items.filter(t => /^Fish Audio: /i.test(t))
  const r = row('B-PAL-1', cmds.length ? 'pass' : 'fail', cmds.length ? `palette commands: ${cmds.join(', ')} (all matches: ${items.join(' | ')})` : `no "Fish Audio:" palette commands (matches: ${items.join(' | ') || 'none'})`, { theme })
  r.metrics.items = items; r.evidence.push(await c.shot(s, shotPath('B-PAL-1', theme)))
  await s.page.keyboard.press('Escape')
}

async function keyEntryRow(s, theme) {
  if (!want('B-KEY-1')) return
  await openVoices(s)
  const btn = await cardButton(s)
  if (!btn) { row('B-KEY-1', 'fail', 'no no-key card (is a key already set?)', { theme }); return }
  await c.clickText(s, btn)
  let p = await settleKeyField(s), via = btn
  if (!p.found && await s.page.locator('[aria-label="Settings: fish-audio"]').count()) {
    await s.page.locator('[aria-label="Settings: fish-audio"]').first().click(); await c.sleep(1200); via += ' + gear'
    p = await s.page.evaluate(keyFieldProbe)
  }
  if (!p.found) { row('B-KEY-1', 'fail', `key field not reachable (${via})`, { theme }); return }
  const input = s.page.locator(`#${p.id}`)
  await input.scrollIntoViewIfNeeded(); await input.fill(FAKE_KEY)
  await s.page.evaluate(() => { window.__toasts = []; new MutationObserver(ms => ms.forEach(m => m.addedNodes.forEach(n => { const t = (n.innerText || n.textContent || '').trim(); if (t) window.__toasts.push(t.slice(0, 160)) }))).observe(document.body, { childList: true, subtree: true }) })
  await c.clickText(s, 'Save settings', { role: 'button' })
  // Up to 15 s: the first save on a cold Desktop can keep its spinner for several seconds. toast_ms records how long.
  let toast = null; const tSave = Date.now()
  for (let i = 0; i < 100 && !toast; i++) { await c.sleep(150); toast = (await s.page.evaluate(() => window.__toasts)).find(t => /saved|updated|error|fail|could not/i.test(t)) || null }
  const toastMs = toast ? Date.now() - tSave : null
  // Positive control for the conversation rows' toast probe (role=region > role=alert|status).
  const regionToast = await s.page.evaluate(() => [...document.querySelectorAll('[role=region] [role=alert], [role=region] [role=status]')].map(e => e.innerText.trim()).filter(Boolean)[0] || null)
  const shot = await c.shot(s, shotPath('B-KEY-1', theme))
  const avail = (await gw('/api/plugins/fish-audio/available')).body || {}
  const ok = /saved|updated/i.test(toast || '') && !/error|fail|could not/i.test(toast || '') && avail.key === true
  const r = row('B-KEY-1', ok ? 'pass' : 'fail', `fake key typed via ${via}, Save → toast ${JSON.stringify(toast)}; /available key:${avail.key} (no restart)`, { theme, evidence: [shot] })
  r.metrics = { toast, toast_ms: toastMs, key_after_save: avail.key, region_toast_probe: regionToast }
  // Back to no key for the next theme / phase: remove the line and restart the gateway.
  hostctl('clear-key'); await waitGateway(); await c.sleep(3000)
}

async function libraryRows(s, theme, account) {
  await openVoices(s)
  if (/Connect your Fish Audio account/.test(await c.text(s))) { await c.clickText(s, 'Check again').catch(() => {}); await c.sleep(4000) }
  await c.clickText(s, 'Library', { timeout: 5000 }).catch(() => {}) // the page remembers the last tab
  let uses = 0
  for (let i = 0; i < 60 && !uses; i++) { uses = await s.page.getByRole('button', { name: 'Use', exact: true }).count(); if (!uses) await c.sleep(250) }
  // The status-bar credit chip loads asynchronously after the key lands. If the first /account read fails (for example a
  // busy shared key), the plugin reads it again on its next /available probe, every 60 s: so allow one probe cycle.
  let chip = null; const tChip = Date.now()
  for (let i = 0; i < 300 && !chip; i++) { chip = (await c.text(s)).match(/Fish \$\s?([\d.,]+)/); if (!chip) await c.sleep(250) }
  const chipMs = chip ? Date.now() - tChip : null
  if (want('B-LIB-1')) {
    const r = row('B-LIB-1', uses && chip ? 'pass' : 'fail', `Library lists ${uses} voices; credit chip ${chip ? chip[0] : 'missing'}`, { theme })
    r.metrics = { voices: uses, chip: chip && chip[0], chip_ms: chipMs }; r.evidence.push(await c.shot(s, shotPath('B-LIB-1', theme)))
  }
  if (!uses) { for (const id of ['B-LIB-2', 'B-LIB-3']) if (want(id)) row(id, 'fail', 'Library listed no voices', { theme }) }
  if (want('B-LIB-2') && !args['no-billed'] && uses) {
    await s.page.evaluate(() => { window.__mediaLog && (window.__mediaLog.length = 0) })
    const before = await s.page.evaluate(() => (window.__audioLog || []).length)
    await s.page.locator('button').filter({ hasText: /^\s*Preview\s*$/ }).first().click({ timeout: 10000 })
    let played = null
    for (let i = 0; i < 60 && !played; i++) {
      await c.sleep(250)
      played = await s.page.evaluate(n => {
        const a = [...document.querySelectorAll('audio')].find(x => x.duration > 0 && (x.currentTime > 0 || !x.paused))
        if (a) return { via: 'audio', duration: a.duration }
        const l = (window.__audioLog || []).slice(n).filter(e => e.d > 0)
        if (l.length) return { via: 'webaudio', duration: l.reduce((x, e) => x + e.d, 0) }
        const m = (window.__mediaLog || []).find(e => e.d > 0)
        return m ? { via: 'media.play', duration: m.d } : null
      }, before)
    }
    const r = row('B-LIB-2', played && (played.duration > 0) ? 'pass' : 'fail', played ? `preview played via ${played.via}, ${played.duration?.toFixed?.(2)} s` : 'no audio within 15 s of Preview', { theme })
    r.metrics = played || {}; r.evidence.push(await c.shot(s, shotPath('B-LIB-2', theme)))
  } else if (want('B-LIB-2') && uses) row('B-LIB-2', 'skip', 'billed:cents rows disabled (--no-billed)', { theme })
  if (want('B-LIB-3') && uses) {
    const cfg = path.join(host.hermes_home, 'config.yaml')
    const idx = theme === 'dark' ? 1 : 0 // a different voice per theme, so the write is observable
    const name = await s.page.evaluate(i => { const b = [...document.querySelectorAll('button')].filter(x => x.innerText.trim() === 'Use')[i]; let n = b; for (let k = 0; k < 6 && n; k++) { n = n.parentElement; const fav = n && n.querySelector('button[aria-label^="Favourite "]'); if (fav) return fav.getAttribute('aria-label').slice(10); if (n && n.innerText.split('\n').length > 3) return n.innerText.split('\n').find(l => l.trim().length > 1) } return null }, idx)
    // The write must change the saved voice: an "In use" marker left by the other theme's pick proves nothing.
    const prior = ((await gw('/api/plugins/fish-audio/settings')).body || {}).voice || null
    await s.page.getByRole('button', { name: 'Use', exact: true }).nth(idx).click(); await c.sleep(1000)
    let set = {}
    for (let i = 0; i < 12; i++) { set = (await gw('/api/plugins/fish-audio/settings')).body || {}; if (set.voice && set.voice !== prior) break; await c.sleep(500) }
    const changed = !!set.voice && set.voice !== prior
    const yaml = fs.existsSync(cfg) ? fs.readFileSync(cfg, 'utf8') : ''
    const inYaml = set.voice && yaml.includes(set.voice)
    let ui = null; const tUi = Date.now()
    for (let i = 0; i < 60 && !ui; i++) { ui = await s.page.evaluate(() => document.body.innerText.match(/(In use|Using|Current voice|Active)[^\n]{0,60}/)?.[0] || null); if (!ui) await c.sleep(250) }
    const uiMs = ui ? Date.now() - tUi : null
    const r = row('B-LIB-3', changed && inYaml && ui ? 'pass' : 'fail', `Use on "${name}" → tts.fish-audio.voice=${set.voice || 'unset'}${changed ? '' : ` (unchanged from ${prior || 'unset'})`} (config.yaml ${inYaml ? 'has it' : 'lacks it'}); UI marker ${JSON.stringify(ui)}`, { theme })
    r.metrics = { voice_name: name, voice_id: set.voice, prior_voice_id: prior, ui, ui_ms: uiMs }; r.evidence.push(await c.shot(s, shotPath('B-LIB-3', theme)))
  }
  if (want('B-LIB-4')) {
    const errs = []; const ev = []
    for (const tab of ['My voices', 'Account']) {
      await c.clickText(s, tab, { timeout: 5000 }).catch(e => errs.push(`${tab}: ${e.message.split('\n')[0]}`)); await c.sleep(2500)
      const t = await c.text(s)
      if (/something went wrong|failed to load|error/i.test(t.split('Voices').slice(1).join(' ').slice(0, 2000))) errs.push(`${tab}: error text on page`)
      ev.push(await c.shot(s, shotPath('B-LIB-4', theme, tab.replace(' ', '-').toLowerCase())))
    }
    // The Account card owns this label; the separate status-bar chip cannot satisfy this row.
    const t = await s.page.evaluate(() => [...document.querySelectorAll('div')]
      .find(e => e.textContent.trim() === 'API credit')?.parentElement?.innerText || '')
    const credit = account?.credit == null || account.credit === '' ? NaN : Number(account.credit)
    const shown = Number.isFinite(credit) && t.includes(credit.toFixed(2))
    const r = row('B-LIB-4', !errs.length && shown ? 'pass' : 'fail', `${errs.length ? errs.join('; ') : 'both tabs render, no error banner'}; Account shows credit ${shown ? credit.toFixed(2) : (Number.isFinite(credit) ? 'NOT matching ' + credit.toFixed(2) : 'missing or non-finite wallet value')}`, { theme, evidence: ev })
    r.metrics = { account_credit: account?.credit }
  }
}

// --- main ---
async function main() {
  fs.mkdirSync(SHOTS, { recursive: true })
  const meta = { host: H, desktop_ref: host.desktop_ref, hermes_ref: host.hermes_ref, plugin_ref: host.plugin_ref, plugin_version: host.plugin_version, started: new Date().toISOString() }
  let tree
  try { tree = buildTree() } catch (e) { row('B-BUILD', 'fail', e.message); return finish(meta) }
  meta.tree = tree
  meta.patches = fs.existsSync(path.join(tree, '.acceptance-patches')) ? fs.readFileSync(path.join(tree, '.acceptance-patches'), 'utf8').trim().split('\n') : []
  meta.desktop_half = H === 'upstream' ? `plugin ${host.plugin_ref} desktop/plugin.js (Install-from-Git layout)` : `bundled in ${host.desktop_ref} (src/plugins/fish-audio)`
  try { hostctl('clear-key') } catch (e) { console.error(e.message) }
  await waitGateway()
  if (!args['no-launch']) { launched = true; launch(tree) }
  const s = await c.connect({ tree, cdpPort: CDP, vitePort: VITE })
  meta.desktop_half_setup = await setup(s)
  meta.mic_gate = await s.page.evaluate(() => Promise.race([window.hermesDesktop?.requestMicrophoneAccess?.(), new Promise(r => setTimeout(() => r('TIMEOUT'), 3000))]).then(String, e => 'ERR ' + e.message))
  for (const theme of THEMES) {
    if (!(await guard(`theme ${theme}`, () => c.setTheme(s, theme)))) continue
    await guard(`no-key ${theme}`, () => noKeyRows(s, theme))
    await guard(`plugin page ${theme}`, () => pluginPageRow(s, theme))
    await guard(`palette ${theme}`, () => paletteRow(s, theme))
    await guard(`key entry ${theme}`, () => keyEntryRow(s, theme))
  }
  const needReal = ['B-LIB-1', 'B-LIB-2', 'B-LIB-3', 'B-LIB-4', 'B-CONV-1', 'B-CONV-2', 'B-CONV-3', 'B-CONV-3L', 'B-CONV-4', 'B-CONV-5', 'B-DICT-1', 'B-EVAOS-CONV-1'].some(want)
  if (needReal) {
    let walletBefore = null
    try { hostctl('set-real-key'); await waitGateway(); await c.sleep(3000); meta.reload_after_key = await reloadRenderer(s) } catch (e) { row('B-LIB-1', 'fail', `could not place the real key: ${e.message}`); return finish(meta, s) }
    // The wallet read is the reference for B-LIB-1/4; retry it over a transient Fish limit.
    let account = null
    for (let i = 0; i < 4 && account?.credit == null; i++) { if (i) await c.sleep(5000); account = (await gw('/api/plugins/fish-audio/account')).body }
    walletBefore = account?.credit ?? null
    for (const theme of THEMES) { if (await guard(`theme ${theme}`, () => c.setTheme(s, theme))) await guard(`library ${theme}`, () => libraryRows(s, theme, account)) }
    for (const theme of THEMES) {
      if (!(await guard(`theme ${theme}`, () => c.setTheme(s, theme)))) continue
      const convWant = id => want(id) && (!!process.env.GLM_API_KEY || id === 'B-DICT-1')
      if (!process.env.GLM_API_KEY) for (const id of ['B-CONV-1', 'B-CONV-2', 'B-CONV-3', 'B-CONV-3L', 'B-CONV-4', 'B-CONV-5', 'B-EVAOS-CONV-1']) {
        if (want(id) && (H === 'evaos') === (id === 'B-EVAOS-CONV-1')) row(id, 'skip', 'no LLM key', { theme })
      }
      await guard(`conversation ${theme}`, () => convRows({ s, host, OUT, SHOTS, row, want: convWant, gw, hostctl, theme, shotPath }))
    }
    const after = (await gw('/api/plugins/fish-audio/account')).body
    meta.wallet = { before: walletBefore, after: after?.credit ?? null }
    try { hostctl('clear-key') } catch {}
  }
  return finish(meta, s)
}
async function guard(label, fn) {
  try { await fn(); return true } catch (e) { console.error(`[${H}] ${label}: ${e.stack}`); row('B-ERROR', 'fail', `${label}: ${e.message.split('\n')[0]}`); return false }
}
async function finish(meta, s) {
  meta.finished = new Date().toISOString()
  // --merge: a partial rerun (--rows) replaces only its own (id, theme) results in an existing rows-b.json.
  let all = rows
  if (args.merge && fs.existsSync(path.join(OUT, 'rows-b.json'))) {
    const fresh = new Set(rows.map(r => `${r.id}|${r.theme}`))
    all = JSON.parse(fs.readFileSync(path.join(OUT, 'rows-b.json'), 'utf8')).filter(r => !fresh.has(`${r.id}|${r.theme}`)).concat(rows)
  }
  fs.writeFileSync(path.join(OUT, 'rows-b.json'), JSON.stringify(all, null, 1))
  fs.writeFileSync(path.join(OUT, 'rows-b.meta.json'), JSON.stringify(meta, null, 1))
  fs.rmSync(path.join(OUT, 'rows-b.partial.json'), { force: true })
  teardown()
  process.exit(0)
}
// Desktop teardown runs on every exit path, including an exception before finish().
let launched = false, tornDown = false
function teardown() {
  if (!launched || tornDown || args['keep-up']) return
  tornDown = true
  spawnSync(path.join(HERE, 'launch.sh'), ['down', '--out', OUT], { stdio: ['ignore', 'inherit', 'inherit'] })
}
try { await main() } catch (e) {
  console.error(`[${H}] run aborted: ${e.stack}`)
  process.exitCode = 1
} finally { teardown() }
