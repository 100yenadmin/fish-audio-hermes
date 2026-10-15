// Page-side probes, passed to page.evaluate. Kept as plain functions (no closures) so Playwright can serialize them.

// B-NOKEY-2 visibility check for the Fish key <input>, found by its label "Fish Audio API key".
// visible: its rect intersects the nearest scrollable ancestor's visible rect (and the viewport).
// findable: a non-empty placeholder, or edge contrast >= 1.5:1 against the card behind it (border, fill or inset shadow).
export function keyFieldProbe() {
  const LABEL = 'Fish Audio API key'
  let input = document.getElementById('plugin-settings-fish-audio-api_key')
  if (!input) {
    const lab = [...document.querySelectorAll('label')].find(l => l.innerText.trim() === LABEL)
    input = lab && (lab.control || (lab.htmlFor && document.getElementById(lab.htmlFor)))
  }
  if (!input) input = document.querySelector(`input[aria-label="${LABEL}"]`)
  if (!input) {
    // Rows that render the label as text next to the input (no <label for>).
    const t = [...document.querySelectorAll('div,span,p,h3,h4')].find(e => e.childElementCount === 0 && e.textContent.trim() === LABEL)
    let n = t; for (let i = 0; n && i < 6 && !input; i++) { n = n.parentElement; input = n && n.querySelector('input') }
  }
  if (!input) return { found: false, hash: location.hash }
  const cv = document.createElement('canvas'); cv.width = cv.height = 1; const g = cv.getContext('2d', { willReadFrequently: true })
  const rgba = css => { g.clearRect(0, 0, 1, 1); g.fillStyle = '#000'; g.fillStyle = css; g.fillRect(0, 0, 1, 1); const d = g.getImageData(0, 0, 1, 1).data; return [d[0], d[1], d[2], d[3] / 255] }
  const over = (top, base) => [0, 1, 2].map(i => top[i] * top[3] + base[i] * (1 - top[3])).concat(1)
  const lum = c => { const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4 }; return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]) }
  const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return +((x + 0.05) / (y + 0.05)).toFixed(2) }
  const dark = /\bdark\b/.test(document.documentElement.className)
  // Card colour: composite the ancestors' backgrounds from the first opaque one down to the input's parent.
  const stack = []; let base = null
  for (let n = input.parentElement; n; n = n.parentElement) {
    const c = rgba(getComputedStyle(n).backgroundColor)
    if (c[3] > 0) stack.push(c)
    if (c[3] >= 0.99) { base = stack.pop(); break }
  }
  const baseAssumed = !base
  if (!base) base = dark ? [30, 30, 30, 1] : [255, 255, 255, 1]
  const card = stack.reverse().reduce((acc, c) => over(c, acc), base)
  const cs = getComputedStyle(input)
  const fill = over(rgba(cs.backgroundColor), card)
  const border = over(rgba(cs.borderTopColor), fill)
  const borderContrast = parseFloat(cs.borderTopWidth) > 0 ? ratio(border, card) : 1
  const fillContrast = ratio(fill, card)
  // An inset shadow draws the visible edge on some themes (upstream inputs: 1px black/38% inset); count its colour too.
  const sh = /^(color\([^)]*\)|rgba?\([^)]*\)|oklch\([^)]*\)|oklab\([^)]*\)|#[0-9a-f]+)/i.exec(cs.boxShadow || '')
  const shadowContrast = sh && /inset/.test(cs.boxShadow) ? ratio(over(rgba(sh[1]), fill), card) : 1
  const r = input.getBoundingClientRect()
  let sc = input.parentElement
  while (sc && sc !== document.documentElement) { const o = getComputedStyle(sc).overflowY; if (/(auto|scroll|overlay)/.test(o) && sc.scrollHeight > sc.clientHeight + 1) break; sc = sc.parentElement }
  const vr = sc && sc !== document.documentElement ? sc.getBoundingClientRect() : { top: 0, left: 0, bottom: innerHeight, right: innerWidth }
  const box = { top: Math.max(vr.top, 0), left: Math.max(vr.left, 0), bottom: Math.min(vr.bottom, innerHeight), right: Math.min(vr.right, innerWidth) }
  const inView = r.width > 0 && r.height > 0 && r.bottom > box.top && r.top < box.bottom && r.right > box.left && r.left < box.right
  const shown = cs.visibility !== 'hidden' && cs.display !== 'none' && parseFloat(cs.opacity) > 0.1
  const placeholder = (input.placeholder || '').trim()
  const contrast = Math.max(borderContrast, fillContrast, shadowContrast)
  return {
    found: true, id: input.id, hash: location.hash, dark, inView: inView && shown, placeholder, contrast, borderContrast, fillContrast, shadowContrast,
    baseAssumed, rect: [r.x, r.y, r.width, r.height].map(Math.round), scroller: [vr.top, vr.bottom].map(Math.round),
    colors: { border: cs.borderTopColor, fill: cs.backgroundColor, shadow: cs.boxShadow, card: `rgb(${card.slice(0, 3).map(Math.round)})` },
    pass: inView && shown && (placeholder.length > 0 || contrast >= 1.5)
  }
}

// Status text of the voice conversation controls (sr-only role=status) plus playback / dictation pills.
export function voiceStatusProbe() {
  const sr = [...document.querySelectorAll('.sr-only[role=status]')].map(e => e.textContent.trim()).filter(Boolean)
  const pills = [...document.querySelectorAll('[role=status][aria-live]')].map(e => e.innerText.trim()).filter(Boolean)
  return { sr, pills, audio: (window.__audioLog || []).length }
}
