// Layer B: one Playwright connectOverCDP session to the dev Desktop, holding a 1280x800@2 metrics override for
// a whole step sequence. Never browser.close() (that would quit the app): callers end with process.exit.
import { createRequire } from 'node:module'
import fs from 'node:fs'
import path from 'node:path'

export async function connect({ tree, cdpPort, vitePort }) {
  const require = createRequire(path.join(tree, 'package.json'))
  const { chromium } = require('playwright')
  const browser = await chromium.connectOverCDP(`http://127.0.0.1:${cdpPort}`)
  let page
  for (let i = 0; i < 60 && !page; i++) {
    page = browser.contexts().flatMap(c => c.pages()).find(p => p.url().includes(`:${vitePort}`))
    if (!page) await sleep(1000)
  }
  if (!page) throw new Error(`no renderer page on :${vitePort}`)
  const cdp = await page.context().newCDPSession(page)
  const s = { browser, page, cdp }
  await metrics(s)
  return s
}

export const sleep = ms => new Promise(r => setTimeout(r, ms))

export async function metrics({ cdp }, w = 1280, h = 800, dsf = 2) {
  await cdp.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: dsf, mobile: false })
  await cdp.send('Emulation.setFocusEmulationEnabled', { enabled: true })
}

export async function shot({ cdp }, file) {
  fs.mkdirSync(path.dirname(file), { recursive: true })
  const s = await cdp.send('Page.captureScreenshot', { format: 'png' })
  fs.writeFileSync(file, Buffer.from(s.data, 'base64'))
  return file
}

export async function nav({ page }, hash) {
  await page.evaluate(h => { location.hash = h }, hash)
  await sleep(900)
}

// Real pointer click on the first visible element whose trimmed text is exactly `text` (Radix needs pointer events).
export async function clickText({ page }, text, { role, timeout = 8000 } = {}) {
  const loc = role ? page.getByRole(role, { name: text, exact: true }) : page.getByText(text, { exact: true })
  await loc.first().click({ timeout })
}

export async function buttons({ page }, re) {
  return page.evaluate(src => {
    const rx = new RegExp(src, 'i')
    return [...document.querySelectorAll('button,a[role=button],[role=tab]')].map(b => (b.innerText || b.getAttribute('aria-label') || '').trim()).filter(t => t && rx.test(t))
  }, re.source)
}

// Close an open Settings overlay or menu before navigating.
export async function page_escape({ page }) {
  for (let i = 0; i < 2; i++) { await page.keyboard.press('Escape'); await sleep(250) }
}

export async function text({ page }) { return page.evaluate(() => document.body.innerText) }

// The app's own Settings > Appearance > Theme control (a segmented Light/Dark/System control under a "Theme"
// sub-page on current builds). Retries: the first Settings open after launch can still be loading.
export async function setTheme(s, mode) {
  const label = mode === 'dark' ? 'Dark' : 'Light'
  let last
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      await page_escape(s)
      await nav(s, '#/settings'); await sleep(1000 + attempt * 1000)
      await clickText(s, 'Appearance'); await sleep(700)
      const btn = s.page.getByRole('button', { name: label, exact: true })
      if (!(await btn.count())) { await clickText(s, 'Theme'); await sleep(700) }
      await btn.first().click({ timeout: 5000 }); await sleep(700)
      await page_escape(s); await sleep(400)
      const cls = await s.page.evaluate(() => document.documentElement.className)
      if ((mode === 'dark') === /\bdark\b/.test(cls)) return cls || '(no class)'
      last = new Error(`theme ${mode} not applied: ${cls}`)
    } catch (e) { last = e }
  }
  throw last
}
