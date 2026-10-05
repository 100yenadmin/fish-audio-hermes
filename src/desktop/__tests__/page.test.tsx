import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'

import { createTestContext, host, resetHost, resetQueryCache } from './sdk-mock'
import { $available, $tab, bindContext } from '../api'
import { previewVoice, VoicesPage } from '../page'
import plugin from '../plugin'
import * as audio from '../audio'
import { S } from '../strings'

const flush = async () => {
  for (let i = 0; i < 5; i++) await act(() => new Promise(resolve => setTimeout(resolve, 0)))
}
const VOICES = { ok: true, page: 1, total: 1, items: [{ id: 'a'.repeat(32), title: 'Narrator', author: 'fish', languages: ['en'], task_count: 1200 }] }

function mount(rest: (path: string, opts?: any) => Promise<any>) {
  const calls: Array<{ path: string; body?: any }> = []
  const t = createTestContext({ rest: async (path, opts) => (calls.push({ path, body: opts?.body }), rest(path, opts)) })
  bindContext(t.ctx as any)
  render(<VoicesPage />)
  return { calls, t }
}

afterEach(() => {
  cleanup()
  resetHost()
  resetQueryCache()
  $available.set(null)
  $tab.set('library')
})

describe('Voices page', () => {
  it('shows the onboarding card, not the tabs, when the agent has no key', async () => {
    $available.set({ key: false, version: '0.3.0' })
    const { calls } = mount(async () => ({ ok: true }))
    expect(screen.getByText('Connect your Fish Audio account')).toBeTruthy()
    expect(screen.getByText('Get an API key')).toBeTruthy()
    expect(screen.queryByRole('tab')).toBeNull()
    expect(calls).toEqual([])
  })

  it('searches the library, previews with a billed note, uses and stars a voice', async () => {
    $available.set({ key: true, version: '0.3.0' })
    const { calls, t } = mount(async path =>
      path.startsWith('/voices') ? VOICES : path === '/preview' ? { ok: true, audio: 'SUQz', mime: 'audio/mpeg' } : { ok: true, message: 'Saved.' }
    )
    await flush()
    expect(screen.getByText('Narrator')).toBeTruthy()
    expect(screen.getAllByText(/billed to your Fish Audio account/).length).toBeGreaterThan(0)
    expect(calls[0].path).toBe('/voices?page=1')
    fireEvent.click(screen.getByText('Use'))
    await flush()
    expect(calls.at(-1)).toEqual({ path: '/use', body: { voice: 'a'.repeat(32) } })
    expect(screen.getByText('In use')).toBeTruthy()
    fireEvent.click(screen.getByLabelText('Favourite'))
    expect(t.stored.get('favourites:conn-1::default')).toEqual([{ author: 'fish', id: 'a'.repeat(32), languages: ['en'], title: 'Narrator' }])
  })

  it('shows the not-installed note on a definite 404', () => {
    $available.set(false)
    mount(async () => ({ ok: true }))
    expect(screen.getByText(/isn't set up on this agent's machine/)).toBeTruthy()
  })

  it('switches tabs from the tab list', async () => {
    $available.set({ key: true, version: '0.3.0' })
    mount(async path => (path.startsWith('/voices') ? { ...VOICES, items: [] } : { ok: true }))
    fireEvent.click(screen.getByRole('tab', { name: 'Create' }))
    await flush()
    expect(screen.getByText('Clone a voice')).toBeTruthy()
    expect(screen.getByText('Design a voice')).toBeTruthy()
    expect((screen.getByText('Clone voice') as HTMLButtonElement).disabled).toBe(true)
  })
})

// Keep A's callbacks mounted while the host's synchronous selector already reports B.
// This deliberately exercises the gap before the host's React remount commits.
describe('selected-agent dispatch and completion guards', () => {
  const pin = { connectionId: 'conn-1', profile: 'default' }
  const candidate = { design_token: 'd'.repeat(32), index: 0, mime: 'audio/wav', audio: 'SUQz' }
  const otherSelected = () => vi.spyOn(host.state.profile, 'get').mockReturnValue('other')
  const deferred = () => {
    let resolve!: (value: any) => void
    let reject!: (error: Error) => void
    const promise = new Promise<any>((yes, no) => { resolve = yes; reject = no })
    return { promise, resolve, reject }
  }

  beforeEach(() => $available.set({ key: true, version: '0.3.0' }))
  afterEach(() => vi.restoreAllMocks())

  it.each(['use', 'preview', 'delete'])('does not dispatch %s after selection changed before the click', async action => {
    if (action === 'delete') $tab.set('mine')
    const { calls } = mount(async () => VOICES)
    await flush()
    let button: HTMLElement
    if (action === 'delete') {
      fireEvent.click(screen.getByLabelText('Delete Narrator'))
      fireEvent.change(screen.getByLabelText(S.deleteConfirmLabel('Narrator')), { target: { value: 'Narrator' } })
      button = screen.getByRole('button', { name: 'Delete' })
    } else button = screen.getByRole('button', { name: action === 'use' ? 'Use' : 'Preview Narrator' })
    const notify = vi.spyOn(host, 'notify')
    calls.length = 0
    otherSelected()
    fireEvent.click(button)
    await flush()
    expect(calls).toEqual([])
    expect(notify).toHaveBeenCalledWith({ kind: 'error', message: S.agentChangedNothingSent })
  })

  it.each(['design', 'save'])('does not dispatch %s after selection changed before the click', async action => {
    $tab.set('create')
    const { calls } = mount(async () => ({ ok: true, candidates: [candidate] }))
    fireEvent.change(screen.getByLabelText(S.designTitle), { target: { value: 'Warm narrator' } })
    if (action === 'save') {
      fireEvent.click(screen.getByRole('button', { name: S.design }))
      await flush()
      fireEvent.change(screen.getByLabelText(`${S.saveAs} 1`), { target: { value: 'Warm' } })
    }
    const notify = vi.spyOn(host, 'notify')
    calls.length = 0
    otherSelected()
    fireEvent.click(screen.getByRole('button', { name: action === 'design' ? S.design : S.save }))
    await flush()
    expect(calls).toEqual([])
    expect(notify).toHaveBeenCalledWith({ kind: 'error', message: S.agentChangedNothingSent })
  })

  it('drops a late preview before caching or playback', async () => {
    const answer = deferred()
    const voice = 'late-preview'
    bindContext(createTestContext({ rest: () => answer.promise }).ctx as any)
    const playback = vi.spyOn(audio, 'play').mockImplementation(() => undefined)
    const pending = previewVoice(pin, voice)
    otherSelected()
    answer.resolve({ ok: true, audio: 'SUQz', mime: 'audio/mpeg' })
    await pending
    expect(audio.cachedPreview(`preview:conn-1::default:${voice}`)).toBeUndefined()
    expect(playback).not.toHaveBeenCalled()
  })

  it('a preview that returns after the page closed is kept for a free replay, never played', async () => {
    const answer = deferred()
    const voice = 'closed-page'
    const playback = vi.spyOn(audio, 'play').mockImplementation(() => undefined)
    mount(async path => (path === '/preview' ? answer.promise : path.startsWith('/voices') ? VOICES : { ok: true, key: true }))
    await flush()
    const pending = previewVoice(pin, voice)
    cleanup()
    answer.resolve({ ok: true, audio: 'SUQz', mime: 'audio/mpeg' })
    await pending
    expect(playback).not.toHaveBeenCalled()
    expect(audio.cachedPreview(`preview:conn-1::default:${voice}`)).toEqual({ ok: true, audio: 'SUQz', mime: 'audio/mpeg' })
  })

  it('a preview that returns after the plugin was turned off never plays', async () => {
    const answer = deferred()
    const t = createTestContext({ rest: path => (path === '/preview' ? answer.promise : Promise.resolve({ ok: true, key: true })) })
    plugin.register(t.ctx as any)
    const playback = vi.spyOn(audio, 'play').mockImplementation(() => undefined)
    const pending = previewVoice(pin, 'disabled-plugin')
    t.dispose()
    answer.resolve({ ok: true, audio: 'SUQz', mime: 'audio/mpeg' })
    await pending
    expect(playback).not.toHaveBeenCalled()
  })

  it('does not play a cached preview after selection changed', async () => {
    const key = 'preview:conn-1::default:cached-preview'
    audio.rememberPreview(key, { audio: 'SUQz', mime: 'audio/mpeg' })
    const playback = vi.spyOn(audio, 'play').mockImplementation(() => undefined)
    const rest = vi.fn(async () => ({ ok: true }))
    bindContext(createTestContext({ rest }).ctx as any)
    otherSelected()
    await previewVoice(pin, 'cached-preview')
    expect(playback).not.toHaveBeenCalled()
    expect(rest).not.toHaveBeenCalled()
  })

  it.each(['use', 'design', 'delete', 'save'])('drops late %s success effects after selection changed', async action => {
    const answer = deferred()
    if (action === 'design' || action === 'save') $tab.set('create')
    if (action === 'delete') $tab.set('mine')
    const { calls } = mount(async (path, opts) => opts?.method === 'DELETE' ? answer.promise : path.startsWith('/voices') ? VOICES : action === 'save' && path === '/design' ? { ok: true, candidates: [candidate] } : answer.promise)
    await flush()
    let button: HTMLElement
    if (action === 'design' || action === 'save') {
      fireEvent.change(screen.getByLabelText(S.designTitle), { target: { value: 'Warm narrator' } })
      if (action === 'save') {
        fireEvent.click(screen.getByRole('button', { name: S.design }))
        await flush()
        fireEvent.change(screen.getByLabelText(`${S.saveAs} 1`), { target: { value: 'Warm' } })
      }
      button = screen.getByRole('button', { name: action === 'design' ? S.design : S.save })
    } else if (action === 'delete') {
      fireEvent.click(screen.getByLabelText('Delete Narrator'))
      fireEvent.change(screen.getByLabelText(S.deleteConfirmLabel('Narrator')), { target: { value: 'Narrator' } })
      button = screen.getByRole('button', { name: 'Delete' })
    } else button = screen.getByRole('button', { name: 'Use' })
    const notify = vi.spyOn(host, 'notify')
    fireEvent.click(button)
    const count = calls.length
    otherSelected()
    answer.resolve({ ok: true, message: 'Saved.', candidates: [candidate], voice: { id: 'v', title: 'Warm' } })
    await flush()
    expect(notify).not.toHaveBeenCalled()
    expect(screen.queryByText('In use')).toBeNull()
    expect(screen.queryByLabelText(`${S.saveAs} 1`)).toBe(action === 'save' ? screen.getByLabelText(`${S.saveAs} 1`) : null)
    expect(screen.queryByText(S.saved('Warm'))).toBeNull()
    expect(calls).toHaveLength(count) // no late delete/save query invalidation
  })

  it.each(['use', 'design', 'preview', 'delete', 'save'])('drops late %s errors after selection changed', async action => {
    const answer = deferred()
    if (action === 'design' || action === 'save') $tab.set('create')
    if (action === 'delete') $tab.set('mine')
    mount(async (path, opts) => opts?.method === 'DELETE' ? answer.promise : path.startsWith('/voices') ? VOICES : action === 'save' && path === '/design' ? { ok: true, candidates: [candidate] } : answer.promise)
    await flush()
    let button: HTMLElement
    if (action === 'design' || action === 'save') {
      fireEvent.change(screen.getByLabelText(S.designTitle), { target: { value: 'Warm narrator' } })
      if (action === 'save') {
        fireEvent.click(screen.getByRole('button', { name: S.design }))
        await flush()
        fireEvent.change(screen.getByLabelText(`${S.saveAs} 1`), { target: { value: 'Warm' } })
      }
      button = screen.getByRole('button', { name: action === 'design' ? S.design : S.save })
    } else if (action === 'delete') {
      fireEvent.click(screen.getByLabelText('Delete Narrator'))
      fireEvent.change(screen.getByLabelText(S.deleteConfirmLabel('Narrator')), { target: { value: 'Narrator' } })
      button = screen.getByRole('button', { name: 'Delete' })
    } else button = screen.getByRole('button', { name: action === 'use' ? 'Use' : 'Preview Narrator' })
    const notify = vi.spyOn(host, 'notify')
    fireEvent.click(button)
    otherSelected()
    answer.reject(new Error('synthetic failure'))
    await flush()
    expect(notify).not.toHaveBeenCalled()
    expect(screen.queryByText('synthetic failure')).toBeNull()
  })

  it('uses truthful delete consequences', () => {
    expect(S.deleteBody).toBe('This removes the voice from your Fish Audio account. Agents using this voice will need another voice selected.')
  })
})

describe('review follow-ups', () => {
  beforeEach(() => $available.set({ key: true, version: '0.3.0' }))
  afterEach(() => vi.restoreAllMocks())
  const pin = { connectionId: 'conn-1', profile: 'default' }
  const page = (n: number) => ({ ok: true, page: n, total: 5000, items: Array.from({ length: 20 }, (_, i) => ({ id: `${n}x${i}`.padEnd(32, 'a'), title: `Voice ${n}-${i}` })) })

  describe.each(['library', 'mine'] as const)('%s exact totals', tab => {
    it.each([20, '20'])('offers no Next for exactly %s voices', async total => {
      $tab.set(tab)
      mount(async () => ({ ...page(1), total }))
      await flush()
      const next = screen.queryByRole('button', { name: S.next }) as HTMLButtonElement | null
      expect(next === null || next.disabled).toBe(true)
    })

    it('disables Next on page 2 of exactly 40 voices', async () => {
      $tab.set(tab)
      mount(async path => ({ ...page(Number(new URLSearchParams(path.split('?')[1]).get('page'))), total: 40 }))
      await flush()
      expect((screen.getByRole('button', { name: S.next }) as HTMLButtonElement).disabled).toBe(false)
      fireEvent.click(screen.getByRole('button', { name: S.next }))
      await flush()
      expect(screen.getByText(S.pageOf(2))).toBeTruthy()
      expect((screen.getByRole('button', { name: S.next }) as HTMLButtonElement).disabled).toBe(true)
    })

    it.each(['1000+', undefined, Number.POSITIVE_INFINITY])('keeps the full-page heuristic for total %s', async total => {
      $tab.set(tab)
      mount(async () => ({ ...page(1), total }))
      await flush()
      expect((screen.getByRole('button', { name: S.next }) as HTMLButtonElement).disabled).toBe(false)
    })
  })

  it('shows unavailable plan details while keeping the wallet and Plans button', async () => {
    $tab.set('account')
    const { t } = mount(async () => ({ ok: true, credit: '2.54', cumulative_top_up: '10', has_free_credit: false, low: false,
      package: null, package_unavailable: true, links: { plans: 'https://example.invalid/plans' } }))
    const open = vi.spyOn(t.ctx.os, 'openExternal')
    await flush()
    expect(screen.getByText('Plan details are unavailable right now.')).toBeTruthy()
    expect(screen.queryByText(S.noPlan)).toBeNull()
    expect(screen.getByText(S.usd('2.54'))).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: S.plans }))
    expect(open).toHaveBeenCalledWith('https://example.invalid/plans')
  })

  it('stops paging at the gateway\'s last page (50)', async () => {
    const { calls } = mount(async path => page(Number(new URLSearchParams(path.split('?')[1] ?? '').get('page') ?? 1)))
    await flush()
    for (let n = 1; n < 50; n++) {
      fireEvent.click(screen.getByRole('button', { name: S.next }))
      await flush()
    }
    expect(screen.getByText(S.pageOf(50))).toBeTruthy()
    expect((screen.getByRole('button', { name: S.next }) as HTMLButtonElement).disabled).toBe(true)
    expect(calls.some(c => c.path.includes('page=51'))).toBe(false)
  })

  it('pages through My voices', async () => {
    $tab.set('mine')
    const { calls } = mount(async path => page(Number(new URLSearchParams(path.split('?')[1] ?? '').get('page') ?? 1)))
    await flush()
    fireEvent.click(screen.getByRole('button', { name: S.next }))
    await flush()
    expect(calls.map(c => c.path)).toContain('/voices?page=2&self=true')
    expect(screen.getByText('Voice 2-0')).toBeTruthy()
  })

  it('sends one billed preview per voice at a time, then refreshes the wallet', async () => {
    let release!: (value: unknown) => void
    const rest = vi.fn((path: string) => (path === '/preview' ? new Promise(resolve => (release = resolve)) : Promise.resolve({ ok: true, key: true })))
    bindContext(createTestContext({ rest }).ctx as any)
    vi.spyOn(audio, 'play').mockImplementation(() => undefined)
    const first = previewVoice(pin, 'single-flight')
    const second = previewVoice(pin, 'single-flight')
    release({ ok: true, audio: 'SUQz', mime: 'audio/mpeg' })
    await Promise.all([first, second])
    expect(rest.mock.calls.filter(([path]) => path === '/preview')).toHaveLength(1)
  })

  it.each([
    [{ subscription_status: 'active', cancel_at_period_end: false }, 'Renews 2026-10-30'],
    [{ subscription_status: 'active', cancel_at_period_end: true }, 'Current period ends 2026-10-30'],
    [{}, 'Current period ends 2026-10-30']
  ])('labels the plan date by renewal state %o', async (state, label) => {
    $tab.set('account')
    mount(async () => ({ ok: true, credit: '2.54', cumulative_top_up: '10', has_free_credit: false, low: false,
      package: { type: 'plus', total: 250000, balance: 250000, finished_at: '2026-10-30T00:00:00Z', ...state }, links: {} }))
    await flush()
    expect(screen.getByText(label)).toBeTruthy()
  })

  it('a double click on Design sends one billed design', async () => {
    $tab.set('create')
    let release!: (value: unknown) => void
    const { calls } = mount(path => (path === '/design' ? new Promise(resolve => (release = resolve)) : Promise.resolve({ ok: true, key: true })))
    fireEvent.change(screen.getByLabelText(S.designTitle), { target: { value: 'Warm narrator' } })
    const button = screen.getByRole('button', { name: S.design })
    fireEvent.click(button)
    fireEvent.click(button)
    release({ ok: true, candidates: [] })
    await flush()
    expect(calls.filter(c => c.path === '/design')).toHaveLength(1)
  })
})

describe('list reads stay with the captured agent', () => {
  beforeEach(() => $available.set({ key: true, version: '0.3.0' }))
  afterEach(() => vi.restoreAllMocks())
  const full = (who: string) => ({ ok: true, page: 1, total: 40, items: Array.from({ length: 20 }, (_, i) => ({ id: `${who}${i}`.padEnd(32, 'b'), title: `${who} voice ${i}` })) })

  it('a stale My voices view does not page the newly selected agent', async () => {
    $tab.set('mine')
    const { calls } = mount(async () => full(host.state.profile.get()))
    await flush()
    vi.spyOn(host.state.profile, 'get').mockReturnValue('other')
    calls.length = 0
    fireEvent.click(screen.getByRole('button', { name: S.next }))
    await flush()
    expect(calls).toEqual([])
    expect(screen.queryByText('other voice 0')).toBeNull()
  })

  it('drops a list response that arrives after the agent changed', async () => {
    $tab.set('mine')
    let release!: (value: unknown) => void
    mount(() => new Promise(resolve => (release = resolve)))
    await flush()
    vi.spyOn(host.state.profile, 'get').mockReturnValue('other')
    release(full('other'))
    await flush()
    expect(screen.queryByText('other voice 0')).toBeNull()
  })
})
