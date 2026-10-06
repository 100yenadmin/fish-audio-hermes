import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import type { PluginMessages } from '@hermes/plugin-sdk'
import { createTestContext, host, resetHost, resetQueryCache, setLocale, translate, tEn } from './sdk-mock'
import { $account, $available, $availableError, $tab, bindContext } from '../api'
import { VoicesPage } from '../page'
import plugin from '../plugin'
import { en } from '../locales/en'
import { zh } from '../locales/zh'
import { zhHant } from '../locales/zh-hant'
import { ja } from '../locales/ja'
import { ar } from '../locales/ar'
import { ru } from '../locales/ru'
import { fr } from '../locales/fr'
import { de } from '../locales/de'
import { es } from '../locales/es'

const locales = { en, zh, 'zh-hant': zhHant, ja, ar, ru, fr, de, es }
const leaves = (messages: PluginMessages, prefix = ''): Record<string, string | ((...args: never[]) => string)> =>
  Object.fromEntries(Object.entries(messages).flatMap(([key, value]) =>
    typeof value === 'object' ? Object.entries(leaves(value, `${prefix}${key}.`)) : [[`${prefix}${key}`, value]]))
const flush = async () => { for (let i = 0; i < 5; i++) await act(() => new Promise(resolve => setTimeout(resolve, 0))) }
const empty = { ok: true, items: [], total: 0 }
const account = { ok: true, credit: '0.40', cumulative_top_up: '5', has_free_credit: false, low: true, package: null, links: {} }
const contexts: ReturnType<typeof createTestContext>[] = []
function context(rest: (path: string, opts?: any) => Promise<any> = async () => empty) {
  const t = createTestContext({ rest })
  contexts.push(t)
  return t
}
function mount(locale: keyof typeof locales, operator = false) {
  const t = context()
  t.ctx.i18n.register(locales)
  bindContext(t.ctx as any)
  $available.set({ key: true, version: '1.2.0', account: !operator })
  setLocale(locale)
  render(<VoicesPage />)
  return t
}
afterEach(() => {
  cleanup()
  contexts.splice(0).forEach(t => t.dispose())
  resetHost()
  resetQueryCache()
  $available.set(null)
  $availableError.set(null)
  $account.set(null)
  $tab.set('library')
  vi.restoreAllMocks()
})

describe('locale completeness', () => {
  it.each(Object.entries(locales))('%s has exactly 113 English keys, matching leaf types and arities, and no empty messages', (_locale, messages) => {
    const english = leaves(en)
    const translated = leaves(messages)
    expect(Object.keys(english)).toHaveLength(113)
    expect(Object.keys(translated).sort()).toEqual(Object.keys(english).sort())
    for (const [key, value] of Object.entries(translated)) {
      expect(typeof value, key).toBe(typeof english[key])
      if (typeof value === 'function') {
        expect(value.length, key).toBe((english[key] as Function).length)
        const args = /Agent|SetUp|Body|unreachable|Voice|Title|Label|deleted|cloned|Cloned|saved|Saved|Large|renews|Ends|chip|usd|Provider/.test(key)
          ? ['Test voice', 'Test agent'] : [2, 5, 40]
        expect(translate(messages, key, ...args).trim(), key).not.toBe('')
      } else expect(value.trim(), key).not.toBe('')
    }
  })
  it('resolves active locale, English fallback and missing key in that order', () => {
    const t = context()
    t.ctx.i18n.register({ en, ja: { title: ja.title } })
    setLocale('ja')
    expect(t.ctx.i18n.t('title')).toBe(ja.title)
    expect(t.ctx.i18n.t('tabs.library')).toBe(tEn('tabs.library'))
    expect(t.ctx.i18n.t('unknown.key')).toBe('unknown.key')
  })
})

describe('localized rendering', () => {
  it.each(['ja', 'ar'] as const)('%s renders the page header, all tabs and billing notice', async locale => {
    mount(locale)
    await flush()
    const messages = locales[locale]
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe(messages.title)
    for (const label of Object.values(messages.tabs)) expect(screen.getByRole('tab', { name: label })).toBeTruthy()
    expect(screen.getByText(messages.billedNote)).toBeTruthy()
    expect(screen.queryByText(en.billedNote)).toBeNull()
  })
  it('Japanese operator billing text reacts to account mode and locale without remounting', async () => {
    mount('ja', true)
    await flush()
    expect(screen.getByText(ja.operatorBilledNote)).toBeTruthy()
    expect(screen.queryByRole('tab', { name: ja.tabs.account })).toBeNull()
    await act(() => $available.set({ key: true, version: '1.2.0', account: true }))
    expect(screen.getByText(ja.billedNote)).toBeTruthy()
    await act(() => setLocale('ar'))
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe(ar.title)
    expect(screen.getByText(ar.billedNote)).toBeTruthy()
    expect(screen.getByRole('tab', { name: ar.tabs.account })).toBeTruthy()
  })
  it('renders translated creation consent text', async () => {
    mount('ja')
    fireEvent.click(screen.getByRole('tab', { name: ja.tabs.create }))
    await flush()
    expect(screen.getByRole('checkbox', { name: ja.consent })).toBeTruthy()
    expect(screen.getByText(ja.cloneBilled)).toBeTruthy()
  })
  it('retranslates an existing clone warning without remounting the form or sending an operation', async () => {
    const t = mount('en')
    const rest = vi.spyOn(t.ctx, 'rest')
    fireEvent.click(screen.getByRole('tab', { name: en.tabs.create }))
    const input = document.querySelector('input[type=file]') as HTMLInputElement
    fireEvent.change(input, { target: { files: [1, 2, 3, 4].map(n => new File(['audio'], `${n}.wav`)) } })
    expect(screen.getByRole('status').textContent).toBe(en.tooMany)
    const count = rest.mock.calls.length
    await act(() => setLocale('ja'))
    expect(screen.getByRole('status').textContent).toBe(ja.tooMany)
    expect(document.querySelector('input[type=file]')).toBe(input)
    expect(rest.mock.calls).toHaveLength(count)
  })
  it('names the voice-language filter options in the active locale', async () => {
    mount('ja')
    await flush()
    for (const name of Object.values(ja.languages)) expect(screen.getByRole('option', { name })).toBeTruthy()
    expect(screen.queryByRole('option', { name: en.languages.ja })).toBeNull()
    await act(() => setLocale('en'))
    expect(Object.values(en.languages)).toEqual(['English', 'Chinese', 'Japanese', 'Korean', 'Spanish', 'French', 'German',
      'Italian', 'Portuguese', 'Russian', 'Arabic', 'Dutch', 'Polish'])
    for (const name of Object.values(en.languages)) expect(screen.getByRole('option', { name })).toBeTruthy()
  })
  it.each([
    [{ ok: true, message: 'Saved.', provider: null, operator_pinned: false }, ''],
    [{ ok: true, message: 'Saved. Your current TTS provider is edge.', provider: 'edge', operator_pinned: false }, ` ${ja.useOtherProvider('edge')}`],
    [{ ok: true, message: "Saved. This agent's speech provider (edge) is set by its operator.", provider: 'edge', operator_pinned: true },
      ` ${ja.useProviderByOperator('edge')}`],
    // A gateway before 1.2.0 sends only its English message.
    [{ ok: true, message: 'Saved. Your current TTS provider is edge.' }, ' Your current TTS provider is edge.'],
  ])('words the Use note in the active locale from the gateway answer (%#)', async (answer, note) => {
    const voice = { id: 'a'.repeat(32), title: 'Narrator' }
    const t = context(async path => path === '/use' ? answer : { ...empty, items: [voice] })
    t.ctx.i18n.register(locales)
    bindContext(t.ctx as any)
    $available.set({ key: true, version: '1.2.0', account: true })
    const notify = vi.spyOn(host, 'notify')
    setLocale('ja')
    render(<VoicesPage />)
    await flush()
    fireEvent.click(screen.getByRole('button', { name: ja.use }))
    await flush()
    expect(notify).toHaveBeenCalledWith({ kind: 'success', message: ja.usedVoice('Narrator', 'default') + note })
  })
  it('uses the current locale when an asynchronous handler finishes after a switch', async () => {
    let finish!: (value: any) => void
    const voice = { id: 'a'.repeat(32), title: 'Narrator' }
    const t = context(async path => path === '/use' ? new Promise(resolve => { finish = resolve }) : { ...empty, items: [voice] })
    t.ctx.i18n.register(locales)
    bindContext(t.ctx as any)
    $available.set({ key: true, version: '1.2.0', account: true })
    const notify = vi.spyOn(host, 'notify')
    setLocale('ja')
    render(<VoicesPage />)
    await flush()
    fireEvent.click(screen.getByRole('button', { name: ja.use }))
    await act(() => setLocale('ar'))
    await act(() => finish({ ok: true, message: 'Saved.' }))
    await flush()
    expect(notify).toHaveBeenCalledWith({ kind: 'success', message: ar.usedVoice('Narrator', 'default') })
  })
})

describe('locale change and contribution gate', () => {
  it('re-registers visible nav/palette labels in order, updates the mounted chip, and makes no REST calls', async () => {
    const rest = vi.fn(async (path: string) => path === '/account' ? account : { key: true, account: true })
    const t = context(rest)
    plugin.register(t.ctx as any)
    await flush()
    const initialNav = t.live.get('nav')
    const count = rest.mock.calls.length
    render(t.live.get('credit').render())
    expect(screen.getByRole('button', { name: en.chipTip }).title).toBe(en.lowCredit)
    await act(() => setLocale('ja'))
    expect(t.live.get('nav')).not.toBe(initialNav)
    expect(t.live.get('nav').data.label).toBe(ja.navLabel)
    expect(t.live.get('palette-voices').data.label).toBe(ja.paletteVoices)
    expect(t.live.get('palette-account').data.label).toBe(ja.paletteAccount)
    expect([...t.live.keys()].filter(id => id.startsWith('palette-'))).toEqual(['palette-voices', 'palette-account'])
    expect(screen.getByRole('button', { name: ja.chipTip }).title).toBe(ja.lowCredit)
    await act(() => setLocale('ar'))
    expect(screen.getByRole('button', { name: ar.chipTip }).title).toBe(ar.lowCredit)
    expect(t.live.get('nav').data.label).toBe(ar.navLabel)
    expect(rest.mock.calls).toHaveLength(count)
    cleanup()
    const nav = t.live.get('nav')
    t.dispose()
    setLocale('fr')
    expect(t.live.get('nav')).toBe(nav)
  })
  it('keeps operator account entries hidden across locale changes', async () => {
    const t = context(async () => ({ key: true, account: false }))
    plugin.register(t.ctx as any)
    await flush()
    setLocale('ja')
    expect(t.live.get('nav').data.label).toBe(ja.navLabel)
    expect(t.live.get('palette-voices').data.label).toBe(ja.paletteVoices)
    expect(t.live.has('credit')).toBe(false)
    expect(t.live.has('palette-account')).toBe(false)
  })
  it('does not reveal contributions while availability is unknown, then registers the active locale', async () => {
    let answer!: (value: any) => void
    const t = context(async () => new Promise(resolve => { answer = resolve }))
    plugin.register(t.ctx as any)
    setLocale('ja')
    expect(t.live.has('nav')).toBe(false)
    answer({ key: false, account: false })
    await flush()
    expect(t.live.get('nav').data.label).toBe(ja.navLabel)
    expect(t.live.has('credit')).toBe(false)
  })
})
