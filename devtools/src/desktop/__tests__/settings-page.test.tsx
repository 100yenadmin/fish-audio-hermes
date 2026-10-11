import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'

import { createTestContext, host, resetHost, resetQueryCache, setLocale } from './sdk-mock'
import { $account, $available, $availableError, bindContext, type SettingsResponse, versionAtLeast } from '../api'
import plugin from '../plugin'
import { badAliasName, SettingsPage } from '../settings-page'
import { en } from '../locales/en'
import { ja } from '../locales/ja'
import { de } from '../locales/de'

const flush = async () => {
  for (let i = 0; i < 5; i++) await act(() => new Promise(resolve => setTimeout(resolve, 0)))
}
const A = 'a'.repeat(32)
const B = 'b'.repeat(32)
const SETTINGS: SettingsResponse = {
  ok: true,
  voice: A,
  model: 's2.1-pro',
  models: [{ id: 's2.1-pro', display: 'S2.1 Pro', deprecated: false }, { id: 's1', display: 'S1 (deprecated)', deprecated: true }],
  aliases: [{ name: 'narrator', voice: A }],
  managed: []
}
type Call = { path: string; method?: string; body?: any }
type Rest = (path: string, opts?: any) => Promise<any>

/** The 1.4.0 gateway: GET /settings, the current voice's title, and the writes, each answering per the wire contract. */
const TITLES: Record<string, string> = { [A]: 'Warm narrator', [B]: 'Bright host' }
/** `state` may be a function, so a write can change what the next GET /settings returns. */
const gateway = (state: SettingsResponse | (() => SettingsResponse) = SETTINGS, writes: Rest = async () => ({ ok: true })): Rest => async (path, opts) => {
  if (path === '/available') return { ok: true, plugin: 'fish-audio', version: '1.4.0', key: true, account: true }
  if (path === '/account') return { ok: true, credit: '5', cumulative_top_up: '5', has_free_credit: false, low: false, package: null, links: {} }
  if (path === '/settings' && !opts?.method) return typeof state === 'function' ? state() : state
  if (path.startsWith('/voices/')) {
    const id = path.slice('/voices/'.length)
    if (!TITLES[id]) throw new Error(`Error invoking remote method 'hermes:api': Error: 404: {"detail":"Not Found"}`)
    return { ok: true, voice: { id, title: TITLES[id] } }
  }
  return writes(path, opts)
}

function mount(rest: Rest, available: any = { key: true, version: '1.4.0', account: true }) {
  const calls: Call[] = []
  const t = createTestContext({ rest: async (path, opts) => (calls.push({ path, method: opts?.method, body: opts?.body }), rest(path, opts)) })
  bindContext(t.ctx as any)
  $available.set(available)
  render(<SettingsPage />)
  return { calls, t, writes: () => calls.filter(c => c.method) }
}

afterEach(() => {
  cleanup()
  resetHost()
  resetQueryCache()
  $available.set(null)
  $availableError.set(null)
  $account.set(null)
  vi.restoreAllMocks()
})

describe('settings page registration', () => {
  it('a host without registerSettingsPage (0.21.5, the fork) still activates and registers no page', async () => {
    const t = createTestContext({ rest: gateway() })
    expect('registerSettingsPage' in t.ctx).toBe(false)
    expect(() => plugin.register(t.ctx as any)).not.toThrow()
    await flush()
    expect(t.live.has('page')).toBe(true)
    expect(t.live.has('nav')).toBe(true)
    setLocale('ja')  // nothing to re-register
    t.dispose()
  })

  const LOCALES = ['en', 'zh', 'zh-hant', 'ja', 'ar', 'ru', 'fr', 'de', 'es']

  it.each(LOCALES)('registers once at activation under %s, before availability is known, titled Fish Audio', locale => {
    setLocale(locale)  // the saved language may already apply when the plugin activates
    const t = createTestContext({ rest: () => new Promise(() => undefined) })  // /available never answers
    const register = vi.fn((_page: any) => () => undefined)
    Object.assign(t.ctx, { registerSettingsPage: register })
    plugin.register(t.ctx as any)
    expect(register).toHaveBeenCalledTimes(1)
    const page = register.mock.calls[0][0]
    expect(page).toMatchObject({ id: 'fish-audio', icon: 'unmute', title: 'Fish Audio' })
    expect(typeof page.render).toBe('function')
    t.dispose()
  })

  it('a locale change does NOT dispose or re-register the settings page; its body follows the locale; a disable removes it', async () => {
    const t = createTestContext({ rest: gateway() })
    const dispose = vi.fn()
    const register = vi.fn((_page: any) => dispose)
    Object.assign(t.ctx, { registerSettingsPage: register })
    plugin.register(t.ctx as any)
    await flush()
    render(register.mock.calls[0][0].render())
    await flush()
    expect(screen.getByRole('heading', { name: en.settings.voiceSection })).toBeTruthy()
    // 0.21.6 starts in English and applies the saved language after plugins activate: re-registering would drop
    // the host's folded "Agent settings" (the API key form) from under the entry.
    for (const locale of [...LOCALES.slice(1), 'en']) await act(() => setLocale(locale))
    await act(() => setLocale('de'))
    expect(register).toHaveBeenCalledTimes(1)
    expect(dispose).not.toHaveBeenCalled()
    expect(screen.getByRole('heading', { name: de.settings.voiceSection })).toBeTruthy()
    cleanup()
    t.dispose()
    expect(dispose).toHaveBeenCalledTimes(1)
    setLocale('fr')
    expect(register).toHaveBeenCalledTimes(1)
  })
})

describe('settings page body', () => {
  it('a gateway without the plugin (404) shows the not-set-up wording and a link to Plugins', () => {
    const navigate = vi.spyOn(host, 'navigate')
    const { calls } = mount(gateway(), false)
    expect(screen.getByText(en.forAgent('default'))).toBeTruthy()
    expect(screen.getByText(en.notSetUp('default'))).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: en.openPlugins }))
    expect(navigate).toHaveBeenCalledWith('/capabilities?tab=plugins&plugin=fish-audio')
    expect(calls).toEqual([])
  })

  it.each(['1.3.3', '1.3.10', '', '1.4junk.0', 'dev'])('a gateway at %j asks for a restart and keeps the Voices-page link', version => {
    const navigate = vi.spyOn(host, 'navigate')
    const { calls } = mount(gateway(), { key: true, version, account: true })
    expect(screen.getByText(en.settings.restart)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: en.settings.voicesPage }))
    expect(navigate).toHaveBeenCalledWith('/fish-audio')
    expect(calls).toEqual([])
  })

  it('no key shows the existing no-key card', () => {
    const { calls } = mount(gateway(), { key: false, version: '1.4.0', account: true })
    expect(screen.getByText(en.onboardTitle)).toBeTruthy()
    expect(screen.queryByText(en.settings.restart)).toBeNull()
    expect(calls).toEqual([])
  })

  it('a full gateway shows every section, the current voice title, names and links', async () => {
    const open = vi.fn(async () => true)
    const { calls, t } = mount(gateway())
    t.ctx.os.openExternal = open
    await flush()
    for (const title of [en.settings.voiceSection, en.settings.modelSection, en.settings.namesSection, en.settings.linksSection]) {
      expect(screen.getByRole('heading', { name: title })).toBeTruthy()
    }
    expect(calls.map(c => c.path)).toEqual(['/settings', `/voices/${A}`])
    expect(screen.getAllByText('Warm narrator')).toHaveLength(2)  // the current voice and the narrator name's voice
    expect(screen.getByText('narrator')).toBeTruthy()
    expect(document.querySelector('[data-select]')!.getAttribute('data-select')).toBe('s2.1-pro')
    expect(screen.getByRole('option', { name: 'S1 (deprecated)' })).toBeTruthy()
    expect((screen.getByLabelText(en.settings.voice) as HTMLInputElement).value).toBe(A)  // prefilled with the current voice
    expect(screen.getByText(en.settings.agentSettingsNote)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: en.settings.fishAccount }))
    expect(open).toHaveBeenCalledWith('https://fish.audio/app/developers/billing')
  })

  it('an unset voice reads as the Fish default and offers no Reset', async () => {
    mount(gateway({ ...SETTINGS, voice: null, model: null, aliases: [] }))
    await flush()
    expect(screen.getByText(en.settings.defaultVoice)).toBeTruthy()
    expect(screen.queryByRole('button', { name: en.settings.reset })).toBeNull()
    expect(screen.getByText(en.settings.noNames)).toBeTruthy()
    expect(document.querySelector('[data-select]')!.getAttribute('data-select')).toBe('__fish_default__')
  })

  it('operator mode hides the Fish account link', async () => {
    mount(gateway(), { key: true, version: '1.4.0', account: false })
    await flush()
    expect(screen.getByText(en.settings.voicesPage)).toBeTruthy()
    expect(screen.queryByRole('button', { name: en.settings.fishAccount })).toBeNull()
  })
})

describe('settings page writes', () => {
  it.each([['S1 (deprecated)', 's1'], [en.settings.defaultModel, null]])('choosing %s posts exactly {model: %j}', async (label, model) => {
    const notify = vi.spyOn(host, 'notify')
    const { writes } = mount(gateway(SETTINGS, async () => ({ ...SETTINGS, model })))
    await flush()
    fireEvent.click(screen.getByRole('option', { name: label }))
    await flush()
    expect(writes()).toEqual([{ path: '/settings', method: 'POST', body: { model } }])
    expect(document.querySelector('[data-select]')!.getAttribute('data-select')).toBe(model ?? '__fish_default__')
    expect(notify).toHaveBeenCalledWith({ kind: 'success', message: en.settings.modelSaved })
  })

  it('Reset posts exactly {voice: null} and shows the Fish default from the answer', async () => {
    const { writes } = mount(gateway(SETTINGS, async () => ({ ...SETTINGS, voice: null })))
    await flush()
    fireEvent.click(screen.getByRole('button', { name: en.settings.reset }))
    await flush()
    expect(writes()).toEqual([{ path: '/settings', method: 'POST', body: { voice: null } }])
    expect(screen.getByText(en.settings.defaultVoice)).toBeTruthy()
    expect(screen.queryByRole('button', { name: en.settings.reset })).toBeNull()
  })

  it('Change opens the Voices page on the Library tab', async () => {
    const navigate = vi.spyOn(host, 'navigate')
    mount(gateway())
    await flush()
    fireEvent.click(screen.getByRole('button', { name: en.settings.change }))
    expect(navigate).toHaveBeenCalledWith('/fish-audio')
  })

  it('Add posts exactly {name, voice} and lists the answer', async () => {
    const notify = vi.spyOn(host, 'notify')
    const aliases = [{ name: 'host', voice: B }, { name: 'narrator', voice: A }]
    let server = SETTINGS
    const { writes } = mount(gateway(() => server, async () => ((server = { ...SETTINGS, aliases }), { ok: true, aliases, updated: false })))
    await flush()
    fireEvent.change(screen.getByLabelText(en.settings.name), { target: { value: ' Host ' } })
    fireEvent.change(screen.getByLabelText(en.settings.voice), { target: { value: B } })
    fireEvent.click(screen.getByRole('button', { name: en.settings.add }))
    await flush()
    expect(writes()).toEqual([{ path: '/aliases', method: 'POST', body: { name: 'Host', voice: B } }])
    expect(screen.getByText('host')).toBeTruthy()
    expect(screen.getByText('Bright host')).toBeTruthy()  // the new name's voice title, fetched once
    expect(notify).toHaveBeenCalledWith({ kind: 'success', message: en.settings.added('host') })
    expect((screen.getByLabelText(en.settings.name) as HTMLInputElement).value).toBe('')
  })

  it('an existing name pointed at a new voice says it was updated', async () => {
    const notify = vi.spyOn(host, 'notify')
    mount(gateway(SETTINGS, async () => ({ ok: true, aliases: [{ name: 'narrator', voice: B }], updated: true })))
    await flush()
    fireEvent.change(screen.getByLabelText(en.settings.name), { target: { value: 'narrator' } })
    fireEvent.change(screen.getByLabelText(en.settings.voice), { target: { value: B } })
    fireEvent.click(screen.getByRole('button', { name: en.settings.add }))
    await flush()
    expect(notify).toHaveBeenCalledWith({ kind: 'success', message: en.settings.updated('narrator') })
  })

  it.each(['x', 'add', A, '9lives', 'has space'])('checks the name rule (%s) before sending', async name => {
    const { writes } = mount(gateway())
    await flush()
    fireEvent.change(screen.getByLabelText(en.settings.name), { target: { value: name } })
    fireEvent.click(screen.getByRole('button', { name: en.settings.add }))
    await flush()
    expect(writes()).toEqual([])
    expect(screen.getByRole('alert').textContent).toBe(en.settings.badName)
  })

  it('the name rule matches decision 2', () => {
    for (const ok of ['narrator', 'Host', 'a1', 'my-voice_2', 'x'.repeat(32), 'straße']) expect(badAliasName(ok), ok).toBe(false)
    for (const bad of ['a', 'x'.repeat(33), '1abc', 'remove', 'DEFAULT', B, 'a.b', '']) expect(badAliasName(bad), bad).toBe(true)
  })

  it("shows the gateway's refusal message (flat kind/message envelope)", async () => {
    const notify = vi.spyOn(host, 'notify')
    mount(gateway(SETTINGS, async () => ({ ok: false, kind: 'voice_not_found', message: 'That voice was not found.' })))
    await flush()
    fireEvent.change(screen.getByLabelText(en.settings.name), { target: { value: 'host' } })
    fireEvent.click(screen.getByRole('button', { name: en.settings.add }))
    await flush()
    expect(notify).toHaveBeenCalledWith({ kind: 'error', message: 'That voice was not found.' })
    expect(screen.queryByText('host')).toBeNull()
  })

  it('Remove sends exactly DELETE /aliases/{name} and lists the answer', async () => {
    let server = SETTINGS
    const { writes } = mount(gateway(() => server, async () => ((server = { ...SETTINGS, aliases: [] }), { ok: true, aliases: [] })))
    await flush()
    fireEvent.click(screen.getByRole('button', { name: `${en.settings.remove} narrator` }))
    await flush()
    expect(writes()).toEqual([{ path: '/aliases/narrator', method: 'DELETE', body: undefined }])
    expect(screen.queryByText('narrator')).toBeNull()
    expect(screen.getByText(en.settings.noNames)).toBeTruthy()
  })

  it('a managed key disables its control and says so', async () => {
    const { writes } = mount(gateway({ ...SETTINGS, managed: ['voice', 'model', 'aliases'] }))
    await flush()
    expect(screen.getAllByText(en.settings.managed)).toHaveLength(3)
    expect(document.querySelector('[data-select]')!.hasAttribute('data-disabled')).toBe(true)
    fireEvent.click(screen.getByRole('option', { name: 'S1 (deprecated)' }))
    for (const name of [en.settings.change, en.settings.reset, en.settings.add, `${en.settings.remove} narrator`]) {
      expect((screen.getByRole('button', { name }) as HTMLButtonElement).disabled, name).toBe(true)
    }
    expect((screen.getByLabelText(en.settings.name) as HTMLInputElement).disabled).toBe(true)
    await flush()
    expect(writes()).toEqual([])
  })

  it('only the managed key is locked', async () => {
    mount(gateway({ ...SETTINGS, managed: ['model'] }))
    await flush()
    expect(screen.getAllByText(en.settings.managed)).toHaveLength(1)
    expect((screen.getByRole('button', { name: en.settings.reset }) as HTMLButtonElement).disabled).toBe(false)
  })

  it('does not send a write when the agent changed before the click', async () => {
    const notify = vi.spyOn(host, 'notify')
    const { writes } = mount(gateway())
    await flush()
    const reset = screen.getByRole('button', { name: en.settings.reset })
    vi.spyOn(host.state.profile, 'get').mockReturnValue('other')  // selected now, before the page re-renders
    fireEvent.click(reset)
    await flush()
    expect(writes()).toEqual([])
    expect(notify).toHaveBeenCalledWith({ kind: 'error', message: en.agentChangedNothingSent })
  })

  it('an agent switch while a write is in flight (A→B→A) discards the reply and says so', async () => {
    let finish!: (value: unknown) => void
    const calls: Call[] = []
    const rest = gateway(SETTINGS, async () => new Promise(resolve => { finish = resolve }))
    const t = createTestContext({ rest: async (path, opts) => (calls.push({ path, method: opts?.method, body: opts?.body }), rest(path, opts)) })
    plugin.register(t.ctx as any)  // the availability gate bumps the agent epoch on every switch
    await flush()
    const notify = vi.spyOn(host, 'notify')
    render(<SettingsPage />)
    await flush()
    fireEvent.click(screen.getByRole('option', { name: 'S1 (deprecated)' }))
    await act(() => host.state.profile.set('other'))
    await flush()
    await act(() => host.state.profile.set('default'))
    await flush()
    await act(async () => finish({ ...SETTINGS, model: 's1' }))
    await flush()
    expect(calls.filter(c => c.method)).toEqual([{ path: '/settings', method: 'POST', body: { model: 's1' } }])
    expect(document.querySelector('[data-select]')!.getAttribute('data-select')).toBe('s2.1-pro')
    expect(notify).toHaveBeenCalledWith({ kind: 'error', message: en.settings.agentChangedAfterSend('default') })
    expect(notify).not.toHaveBeenCalledWith({ kind: 'success', message: en.settings.modelSaved })
    t.dispose()
  })

  it('reloads /settings when the agent changes', async () => {
    const calls: string[] = []
    const rest = gateway()
    const t = createTestContext({ rest: async (path, opts) => (calls.push(path), rest(path, opts)) })
    plugin.register(t.ctx as any)
    await flush()
    render(<SettingsPage />)
    await flush()
    expect(screen.getByText(en.forAgent('default'))).toBeTruthy()
    await act(() => host.state.profile.set('studio'))
    await flush()
    expect(screen.getByText(en.forAgent('studio'))).toBeTruthy()
    expect(calls.filter(path => path === '/settings')).toHaveLength(2)
    t.dispose()
  })
})

describe('fix pass 1', () => {
  it('an alias write reloads /settings, so a model change made meanwhile shows after the write', async () => {
    let server = SETTINGS
    const aliases = [{ name: 'host', voice: B }, { name: 'narrator', voice: A }]
    const { calls } = mount(gateway(() => server, async () => ({ ok: true, aliases, updated: false })))
    await flush()
    expect(document.querySelector('[data-select]')!.getAttribute('data-select')).toBe('s2.1-pro')
    server = { ...SETTINGS, model: 's1', aliases }  // another surface (the CLI, /fish model) changed the model
    fireEvent.change(screen.getByLabelText(en.settings.name), { target: { value: 'host' } })
    fireEvent.click(screen.getByRole('button', { name: en.settings.add }))
    await flush()
    expect(calls.filter(c => c.path === '/settings' && !c.method)).toHaveLength(2)
    expect(document.querySelector('[data-select]')!.getAttribute('data-select')).toBe('s1')
    fireEvent.click(screen.getByRole('button', { name: `${en.settings.remove} host` }))
    await flush()
    expect(calls.filter(c => c.path === '/settings' && !c.method)).toHaveLength(3)
  })

  it('a refresh after an alias write that lands after an agent switch changes nothing', async () => {
    let finish!: (value: unknown) => void
    const { calls } = mount(gateway(SETTINGS, async () => new Promise(resolve => { finish = resolve })))
    await flush()
    fireEvent.change(screen.getByLabelText(en.settings.name), { target: { value: 'host' } })
    fireEvent.click(screen.getByRole('button', { name: en.settings.add }))
    vi.spyOn(host.state.profile, 'get').mockReturnValue('other')
    await act(async () => finish({ ok: true, aliases: [], updated: false }))
    await flush()
    expect(calls.filter(c => c.path === '/settings' && !c.method)).toHaveLength(1)  // no re-read for the other agent
    expect(screen.getByText('narrator')).toBeTruthy()
  })

  it("alias rows show their voice's title, fetched once per agent and voice, with the short id on any error", async () => {
    const C = 'c'.repeat(32)
    const { calls } = mount(gateway({ ...SETTINGS, voice: null,
      aliases: [{ name: 'host', voice: B }, { name: 'lost', voice: C }, { name: 'narrator', voice: A }, { name: 'twin', voice: A }] }))
    await flush()
    expect(screen.getByText('Bright host')).toBeTruthy()
    expect(screen.getAllByText('Warm narrator')).toHaveLength(2)
    expect(screen.getByText(C.slice(0, 8))).toBeTruthy()  // 404 → short id
    expect(calls.filter(c => c.path.startsWith('/voices/')).map(c => c.path).sort()).toEqual([`/voices/${A}`, `/voices/${B}`, `/voices/${C}`])
  })

  it('a favourite title needs no fetch, and at most 50 alias titles are fetched', async () => {
    const ids = Array.from({ length: 52 }, (_, i) => i.toString(16).padStart(32, '0'))
    const rest = gateway({ ...SETTINGS, voice: null, aliases: ids.map((voice, i) => ({ name: `n${String(i).padStart(2, '0')}`, voice })) })
    const calls: string[] = []
    const t = createTestContext({ rest: async (path, opts) => (calls.push(path), rest(path, opts)) })
    t.stored.set('favourites:conn-1::default', [{ id: ids[0], title: 'Starred' }])
    bindContext(t.ctx as any)
    $available.set({ key: true, version: '1.4.0', account: true })
    render(<SettingsPage />)
    await flush()
    expect(screen.getAllByText('Starred')).toHaveLength(2)  // the n00 row and its quick-fill chip
    const fetched = new Set(calls.filter(path => path.startsWith('/voices/')))
    expect(fetched.has(`/voices/${ids[0]}`)).toBe(false)
    expect(fetched.size).toBe(49)  // aliases 1–49: the first 50 rows less the favourite
    expect(fetched.has(`/voices/${ids[50]}`)).toBe(false)
  })

  it('a second activation without a dispose replaces the first: no stacked listeners, probes or settings pages', async () => {
    const calls: string[] = []
    const rest = gateway()
    const t = createTestContext({ rest: async (path, opts) => (calls.push(path), rest(path, opts)) })
    let pages = 0
    let localeListeners = 0
    const listen = t.ctx.i18n.onLocaleChange
    Object.assign(t.ctx.i18n, {
      onLocaleChange: (fn: () => void) => {
        localeListeners++
        const stop = listen(fn)
        return () => (localeListeners--, stop())
      }
    })
    Object.assign(t.ctx, { registerSettingsPage: () => (pages++, () => void pages--) })
    plugin.register(t.ctx as any)
    await flush()
    const single = localeListeners
    plugin.register(t.ctx as any)
    await flush()
    expect(localeListeners).toBe(single)
    expect(pages).toBe(1)
    setLocale('ja')
    expect(pages).toBe(1)
    expect(t.live.get('nav').data.label).toBe(ja.navLabel)
    calls.length = 0
    t.tickIntervals()
    await flush()
    expect(calls.filter(path => path === '/available')).toHaveLength(1)
    t.dispose()
    expect(pages).toBe(0)
    expect(localeListeners).toBe(0)
  })
})

describe('gateway version compare', () => {
  it.each([['1.4.0', true], ['1.4.1', true], ['1.10.0', true], ['2.0.0', true], ['1.4.0-rc.1', true], ['1.4.0+build.7', true],
    ['1.3.9', false], ['1.3.10', false], ['0.9.9', false], ['1.4junk.0', false], ['1.4', false], ['2.0', false], ['1.4.0rc1', false],
    ['1.4.0.1', false], [' 1.4.0', false], ['', false], [undefined, false], [null, false], ['dev', false]])('%j ≥ 1.4.0 is %s', (version, ok) => {
    expect(versionAtLeast(version, '1.4.0')).toBe(ok)
  })
})
