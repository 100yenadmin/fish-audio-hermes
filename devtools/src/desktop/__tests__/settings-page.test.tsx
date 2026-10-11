import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'

import { createTestContext, host, resetHost, resetQueryCache, setLocale } from './sdk-mock'
import { $account, $available, $availableError, bindContext, type SettingsResponse, versionAtLeast } from '../api'
import plugin from '../plugin'
import { badAliasName, SettingsPage } from '../settings-page'
import { en } from '../locales/en'
import { ja } from '../locales/ja'

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
const gateway = (state = SETTINGS, writes: Rest = async () => ({ ok: true })): Rest => async (path, opts) => {
  if (path === '/available') return { ok: true, plugin: 'fish-audio', version: '1.4.0', key: true, account: true }
  if (path === '/account') return { ok: true, credit: '5', cumulative_top_up: '5', has_free_credit: false, low: false, package: null, links: {} }
  if (path === '/settings' && !opts?.method) return state
  if (path.startsWith('/voices/')) return { ok: true, voice: { id: A, title: 'Warm narrator' } }
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

  it('registers once at activation, before availability is known, with id, icon and the localized title', () => {
    const t = createTestContext({ rest: () => new Promise(() => undefined) })  // /available never answers
    const register = vi.fn((_page: any) => () => undefined)
    const translate = vi.spyOn(t.ctx.i18n, 't')
    Object.assign(t.ctx, { registerSettingsPage: register })
    plugin.register(t.ctx as any)
    expect(register).toHaveBeenCalledTimes(1)
    const page = register.mock.calls[0][0]
    expect(page).toMatchObject({ id: 'fish-audio', icon: 'unmute', title: en.settings.title })
    expect(translate).toHaveBeenCalledWith('settings.title')
    expect(typeof page.render).toBe('function')
    t.dispose()
  })

  it('a locale change calls the old disposer BEFORE registering again; a disable removes it and stops listening', () => {
    const t = createTestContext({ rest: gateway() })
    const events: string[] = []
    let n = 0
    Object.assign(t.ctx, {
      registerSettingsPage: (page: any) => {
        const id = ++n
        events.push(`register ${id} ${page.title}`)
        return () => events.push(`dispose ${id}`)
      }
    })
    plugin.register(t.ctx as any)
    setLocale('ja')
    expect(events).toEqual([`register 1 ${en.settings.title}`, 'dispose 1', `register 2 ${ja.settings.title}`])
    t.dispose()
    expect(events.at(-1)).toBe('dispose 2')
    setLocale('fr')
    expect(events).toHaveLength(4)
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

  it.each(['1.3.3', '1.3.10', ''])('a gateway at %j asks for a restart and keeps the Voices-page link', version => {
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
    const { writes } = mount(gateway(SETTINGS, async () => ({ ok: true, aliases, updated: false })))
    await flush()
    fireEvent.change(screen.getByLabelText(en.settings.name), { target: { value: ' Host ' } })
    fireEvent.change(screen.getByLabelText(en.settings.voice), { target: { value: B } })
    fireEvent.click(screen.getByRole('button', { name: en.settings.add }))
    await flush()
    expect(writes()).toEqual([{ path: '/aliases', method: 'POST', body: { name: 'Host', voice: B } }])
    expect(screen.getByText('host')).toBeTruthy()
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
    const { writes } = mount(gateway(SETTINGS, async () => ({ ok: true, aliases: [] })))
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

describe('gateway version compare', () => {
  it.each([['1.4.0', true], ['1.4.1', true], ['1.10.0', true], ['2.0', true], ['1.4', true], ['1.4.0rc1', true],
    ['1.3.9', false], ['1.3.10', false], ['0.9.9', false], ['', false], ['dev', false]])('%j ≥ 1.4.0 is %s', (version, ok) => {
    expect(versionAtLeast(version, '1.4.0')).toBe(ok)
  })
})
