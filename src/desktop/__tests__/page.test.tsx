import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'

import { createTestContext, host, resetHost, resetQueryCache } from './sdk-mock'
import { $available, $availableError, $tab, bindContext, setRefresher } from '../api'
import { previewVoice, VoicesPage } from '../page'
import plugin from '../plugin'
import * as audio from '../audio'
import { en } from '../locales/en'

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
  $availableError.set(null)
  $tab.set('library')
})

describe('Voices page', () => {
  it('shows the onboarding card, not the tabs, when the agent has no key', async () => {
    $available.set({ key: false, version: '0.3.0', account: true })
    const { calls } = mount(async () => ({ ok: true }))
    expect(screen.getByText('Connect your Fish Audio account')).toBeTruthy()
    expect(screen.getByText('Get an API key')).toBeTruthy()
    expect(screen.queryByRole('tab')).toBeNull()
    expect(calls).toEqual([])
  })

  it('searches the library, previews with a billed note, uses and stars a voice', async () => {
    $available.set({ key: true, version: '0.3.0', account: true })
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
    $available.set({ key: true, version: '0.3.0', account: true })
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

  beforeEach(() => $available.set({ key: true, version: '0.3.0', account: true }))
  afterEach(() => vi.restoreAllMocks())

  it.each(['use', 'preview', 'delete'])('does not dispatch %s after selection changed before the click', async action => {
    if (action === 'delete') $tab.set('mine')
    const { calls } = mount(async () => VOICES)
    await flush()
    let button: HTMLElement
    if (action === 'delete') {
      fireEvent.click(screen.getByLabelText('Delete Narrator'))
      fireEvent.change(screen.getByLabelText(en.deleteConfirmLabel('Narrator')), { target: { value: 'Narrator' } })
      button = screen.getByRole('button', { name: 'Delete' })
    } else button = screen.getByRole('button', { name: action === 'use' ? 'Use' : 'Preview Narrator' })
    const notify = vi.spyOn(host, 'notify')
    calls.length = 0
    otherSelected()
    fireEvent.click(button)
    await flush()
    expect(calls).toEqual([])
    expect(notify).toHaveBeenCalledWith({ kind: 'error', message: en.agentChangedNothingSent })
  })

  it.each(['design', 'save'])('does not dispatch %s after selection changed before the click', async action => {
    $tab.set('create')
    const { calls } = mount(async () => ({ ok: true, candidates: [candidate] }))
    fireEvent.change(screen.getByLabelText(en.designTitle), { target: { value: 'Warm narrator' } })
    if (action === 'save') {
      fireEvent.click(screen.getByRole('button', { name: en.design }))
      await flush()
      fireEvent.change(screen.getByLabelText(`${en.saveAs} 1`), { target: { value: 'Warm' } })
    }
    const notify = vi.spyOn(host, 'notify')
    calls.length = 0
    otherSelected()
    fireEvent.click(screen.getByRole('button', { name: action === 'design' ? en.design : en.save }))
    await flush()
    expect(calls).toEqual([])
    expect(notify).toHaveBeenCalledWith({ kind: 'error', message: en.agentChangedNothingSent })
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
      fireEvent.change(screen.getByLabelText(en.designTitle), { target: { value: 'Warm narrator' } })
      if (action === 'save') {
        fireEvent.click(screen.getByRole('button', { name: en.design }))
        await flush()
        fireEvent.change(screen.getByLabelText(`${en.saveAs} 1`), { target: { value: 'Warm' } })
      }
      button = screen.getByRole('button', { name: action === 'design' ? en.design : en.save })
    } else if (action === 'delete') {
      fireEvent.click(screen.getByLabelText('Delete Narrator'))
      fireEvent.change(screen.getByLabelText(en.deleteConfirmLabel('Narrator')), { target: { value: 'Narrator' } })
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
    expect(screen.queryByLabelText(`${en.saveAs} 1`)).toBe(action === 'save' ? screen.getByLabelText(`${en.saveAs} 1`) : null)
    expect(screen.queryByText(en.saved('Warm'))).toBeNull()
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
      fireEvent.change(screen.getByLabelText(en.designTitle), { target: { value: 'Warm narrator' } })
      if (action === 'save') {
        fireEvent.click(screen.getByRole('button', { name: en.design }))
        await flush()
        fireEvent.change(screen.getByLabelText(`${en.saveAs} 1`), { target: { value: 'Warm' } })
      }
      button = screen.getByRole('button', { name: action === 'design' ? en.design : en.save })
    } else if (action === 'delete') {
      fireEvent.click(screen.getByLabelText('Delete Narrator'))
      fireEvent.change(screen.getByLabelText(en.deleteConfirmLabel('Narrator')), { target: { value: 'Narrator' } })
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
    expect(en.deleteBody).toBe('This removes the voice from your Fish Audio account. Agents using this voice will need another voice selected.')
  })
})

describe('review follow-ups', () => {
  beforeEach(() => $available.set({ key: true, version: '0.3.0', account: true }))
  afterEach(() => vi.restoreAllMocks())
  const pin = { connectionId: 'conn-1', profile: 'default' }
  const page = (n: number) => ({ ok: true, page: n, total: 5000, items: Array.from({ length: 20 }, (_, i) => ({ id: `${n}x${i}`.padEnd(32, 'a'), title: `Voice ${n}-${i}` })) })

  describe.each(['library', 'mine'] as const)('%s exact totals', tab => {
    it.each([20, '20'])('offers no Next for exactly %s voices', async total => {
      $tab.set(tab)
      mount(async () => ({ ...page(1), total }))
      await flush()
      const next = screen.queryByRole('button', { name: en.next }) as HTMLButtonElement | null
      expect(next === null || next.disabled).toBe(true)
    })

    it('disables Next on page 2 of exactly 40 voices', async () => {
      $tab.set(tab)
      mount(async path => ({ ...page(Number(new URLSearchParams(path.split('?')[1]).get('page'))), total: 40 }))
      await flush()
      expect((screen.getByRole('button', { name: en.next }) as HTMLButtonElement).disabled).toBe(false)
      fireEvent.click(screen.getByRole('button', { name: en.next }))
      await flush()
      expect(screen.getByText(en.pageOf(2))).toBeTruthy()
      expect((screen.getByRole('button', { name: en.next }) as HTMLButtonElement).disabled).toBe(true)
    })

    it.each(['1000+', undefined, Number.POSITIVE_INFINITY])('keeps the full-page heuristic for total %s', async total => {
      $tab.set(tab)
      mount(async () => ({ ...page(1), total }))
      await flush()
      expect((screen.getByRole('button', { name: en.next }) as HTMLButtonElement).disabled).toBe(false)
    })
  })

  it('shows unavailable plan details while keeping the wallet and Plans button', async () => {
    $tab.set('account')
    const { t } = mount(async () => ({ ok: true, credit: '2.54', cumulative_top_up: '10', has_free_credit: false, low: false,
      package: null, package_unavailable: true, links: { plans: 'https://example.invalid/plans' } }))
    const open = vi.spyOn(t.ctx.os, 'openExternal')
    await flush()
    expect(screen.getByText('Plan details are unavailable right now.')).toBeTruthy()
    expect(screen.queryByText(en.noPlan)).toBeNull()
    expect(screen.getByText(en.usd('2.54'))).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: en.plans }))
    expect(open).toHaveBeenCalledWith('https://example.invalid/plans')
  })

  it('stops paging at the gateway\'s last page (50)', async () => {
    const { calls } = mount(async path => page(Number(new URLSearchParams(path.split('?')[1] ?? '').get('page') ?? 1)))
    await flush()
    for (let n = 1; n < 50; n++) {
      fireEvent.click(screen.getByRole('button', { name: en.next }))
      await flush()
    }
    expect(screen.getByText(en.pageOf(50))).toBeTruthy()
    expect((screen.getByRole('button', { name: en.next }) as HTMLButtonElement).disabled).toBe(true)
    expect(calls.some(c => c.path.includes('page=51'))).toBe(false)
  })

  it('pages through My voices', async () => {
    $tab.set('mine')
    const { calls } = mount(async path => page(Number(new URLSearchParams(path.split('?')[1] ?? '').get('page') ?? 1)))
    await flush()
    fireEvent.click(screen.getByRole('button', { name: en.next }))
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
    fireEvent.change(screen.getByLabelText(en.designTitle), { target: { value: 'Warm narrator' } })
    const button = screen.getByRole('button', { name: en.design })
    fireEvent.click(button)
    fireEvent.click(button)
    release({ ok: true, candidates: [] })
    await flush()
    expect(calls.filter(c => c.path === '/design')).toHaveLength(1)
  })
})

describe('list reads stay with the captured agent', () => {
  beforeEach(() => $available.set({ key: true, version: '0.3.0', account: true }))
  afterEach(() => vi.restoreAllMocks())
  const full = (who: string) => ({ ok: true, page: 1, total: 40, items: Array.from({ length: 20 }, (_, i) => ({ id: `${who}${i}`.padEnd(32, 'b'), title: `${who} voice ${i}` })) })

  it('a stale My voices view does not page the newly selected agent', async () => {
    $tab.set('mine')
    const { calls } = mount(async () => full(host.state.profile.get()))
    await flush()
    vi.spyOn(host.state.profile, 'get').mockReturnValue('other')
    calls.length = 0
    fireEvent.click(screen.getByRole('button', { name: en.next }))
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

describe('#13 follow-ups', () => {
  const NARRATOR = 'a'.repeat(32)
  const TWO = {
    ok: true,
    page: 1,
    total: 2,
    items: [
      { id: NARRATOR, title: 'Narrator', author: 'fish', languages: ['en'] },
      { id: 'b'.repeat(32), title: 'Storyteller', author: 'fish', languages: ['en'] }
    ]
  }
  const deferred = () => {
    let resolve!: (value: any) => void
    const promise = new Promise<any>(yes => (resolve = yes))
    return { promise, resolve }
  }

  beforeEach(() => $available.set({ key: true, version: '1.0.4', account: true }))
  afterEach(() => vi.restoreAllMocks())

  it('one Use per agent at a time: a second Use waits, so only one write is sent', async () => {
    const answer = deferred()
    const { calls } = mount(async path => (path.startsWith('/voices') ? TWO : path === '/use' ? answer.promise : { ok: true }))
    await flush()
    const [first, second] = screen.getAllByRole('button', { name: 'Use' })
    fireEvent.click(first)
    await flush()
    fireEvent.click(second)
    await flush()
    expect(calls.filter(c => c.path === '/use').map(c => c.body)).toEqual([{ voice: NARRATOR }])
    answer.resolve({ ok: true, message: 'Saved.' })
    await flush()
    expect(screen.getByRole('button', { name: 'In use' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Use' }).hasAttribute('disabled')).toBe(false)
  })

  it("deleting a voice also removes it from that agent's favourites", async () => {
    $tab.set('mine')
    const { t } = mount(async (path, opts) => (opts?.method === 'DELETE' ? { ok: true } : path.startsWith('/voices') ? VOICES : { ok: true }))
    t.stored.set('favourites:conn-1::default', [{ id: NARRATOR, title: 'Narrator' }, { id: 'keep', title: 'Other' }])
    await flush()
    fireEvent.click(screen.getByLabelText('Delete Narrator'))
    fireEvent.change(screen.getByLabelText(en.deleteConfirmLabel('Narrator')), { target: { value: 'Narrator' } })
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }))
    await flush()
    expect(t.stored.get('favourites:conn-1::default')).toEqual([{ id: 'keep', title: 'Other' }])
  })

  it('a delete that settles after you open Library is not undone by the next star', async () => {
    const answer = deferred()
    $tab.set('mine')
    const { t } = mount(async (path, opts) => (opts?.method === 'DELETE' ? answer.promise : path.startsWith('/voices') ? TWO : { ok: true }))
    t.stored.set('favourites:conn-1::default', [{ id: NARRATOR, title: 'Narrator' }])
    await flush()
    fireEvent.click(screen.getByLabelText('Delete Narrator'))
    fireEvent.change(screen.getByLabelText(en.deleteConfirmLabel('Narrator')), { target: { value: 'Narrator' } })
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }))
    act(() => $tab.set('library')) // the DELETE is still pending; Library mounts with Narrator starred
    await flush()
    answer.resolve({ ok: true })
    await flush()
    const stars = screen.getAllByRole('button', { name: new RegExp(`^(${en.favourite}|${en.unfavourite})$`) })
    expect(stars.map(b => b.getAttribute('aria-label'))).toEqual([en.favourite, en.favourite])
    fireEvent.click(stars[1]) // star Storyteller
    expect(t.stored.get('favourites:conn-1::default')).toEqual([{ author: 'fish', id: 'b'.repeat(32), languages: ['en'], title: 'Storyteller' }])
  })

  it('a failed probe for an unknown agent shows the failure and Check again, not a skeleton', () => {
    const retry = vi.fn(async () => undefined)
    setRefresher(retry)
    $available.set(null)
    $availableError.set(new Error('The request timed out.'))
    mount(async () => ({ ok: true }))
    expect(screen.getByRole('alert').textContent).toContain(en.unreachable('default'))
    expect(screen.getByText('The request timed out.')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: en.checkAgain }))
    expect(retry).toHaveBeenCalledWith(true)
  })
})


describe('operator account page', () => {
  const candidate = { design_token: 'd'.repeat(32), index: 0, mime: 'audio/wav', audio: 'SUQz' }

  it('restores an account tab into Library, hides Account, and uses operator preview notes', async () => {
    $available.set({ key: true, version: '1.1.0', account: false })
    $tab.set('account')
    const { calls } = mount(async () => VOICES)
    await flush()
    expect($tab.get()).toBe('library')
    expect(screen.queryByRole('tab', { name: 'Account' })).toBeNull()
    expect(screen.getByRole('tab', { name: 'Library' }).getAttribute('aria-selected')).toBe('true')
    expect(screen.getByText(en.operatorBilledNote)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Preview Narrator' }).title).toBe(en.operatorBilledNote)
    expect(calls.some(c => c.path === '/account')).toBe(false)
    expect(document.body.textContent).not.toContain('your Fish Audio account')
    expect(screen.queryByRole('tab', { name: 'My voices' })).toBeNull()
  })

  it("restores a My voices tab into Library and never lists the operator account's voices", async () => {
    $available.set({ key: true, version: '1.1.0', account: false })
    $tab.set('mine')
    const { calls } = mount(async () => VOICES)
    await flush()
    expect($tab.get()).toBe('library')
    expect(screen.queryByRole('tab', { name: 'My voices' })).toBeNull()
    expect(screen.getByRole('tab', { name: 'Library' }).getAttribute('aria-selected')).toBe('true')
    expect(calls.some(c => c.path.includes('self=true'))).toBe(false)
    expect(screen.queryByRole('button', { name: /^Delete / })).toBeNull()
  })

  it('uses operator clone and design notes and updates on a same-agent flip', async () => {
    $available.set({ key: true, version: '1.1.0', account: false })
    $tab.set('create')
    mount(async () => VOICES)
    expect(screen.getByText(en.operatorCloneBilled)).toBeTruthy()
    expect(screen.getByText(en.operatorDesignBilled)).toBeTruthy()
    expect(document.body.textContent).not.toContain('your Fish Audio account')
    expect(screen.queryByRole('tab', { name: 'My voices' })).toBeNull()
    act(() => $available.set({ key: true, version: '1.1.0', account: true }))
    expect(screen.getByRole('tab', { name: 'Account' })).toBeTruthy()
    expect(screen.getByRole('tab', { name: 'My voices' })).toBeTruthy()
    expect(screen.getByText(en.cloneBilled)).toBeTruthy()
    expect(screen.getByText(en.designBilled)).toBeTruthy()
  })

  it('a voice designed here lands in Favourites, where Use reaches it without My voices', async () => {
    $available.set({ key: true, version: '1.1.0', account: false })
    $tab.set('create')
    const { calls, t } = mount(async path => path === '/design' ? { ok: true, candidates: [candidate] }
      : path === '/design/save' ? { ok: true, voice: { id: 'w'.repeat(32), title: 'Warm' } } : path === '/use' ? { ok: true, message: 'Saved.' } : VOICES)
    const notify = vi.spyOn(host, 'notify')
    fireEvent.change(screen.getByLabelText(en.designTitle), { target: { value: 'Warm narrator' } })
    fireEvent.click(screen.getByRole('button', { name: en.design }))
    await flush()
    fireEvent.change(screen.getByLabelText(`${en.saveAs} 1`), { target: { value: 'Warm' } })
    fireEvent.click(screen.getByRole('button', { name: en.save }))
    await flush()
    expect(t.stored.get('favourites:conn-1::default')).toEqual([{ id: 'w'.repeat(32), title: 'Warm' }])
    expect(notify).toHaveBeenCalledWith({ kind: 'success', message: en.operatorSaved('Warm') })
    expect(screen.getByText(en.operatorSaved('Warm'))).toBeTruthy()
    expect(document.body.textContent).not.toContain('My voices')
    act(() => $tab.set('library'))
    fireEvent.click(screen.getByRole('button', { name: en.favouritesOnly }))
    await flush()
    fireEvent.click(screen.getByRole('button', { name: 'Use' }))
    await flush()
    expect(calls.find(c => c.path === '/use')?.body).toEqual({ voice: 'w'.repeat(32) })
  })

  it('default mode keeps created voices out of Favourites and points to My voices', async () => {
    $available.set({ key: true, version: '1.1.0', account: true })
    $tab.set('create')
    const { t } = mount(async path => path === '/design' ? { ok: true, candidates: [candidate] }
      : path === '/design/save' ? { ok: true, voice: { id: 'w'.repeat(32), title: 'Warm' } } : VOICES)
    const notify = vi.spyOn(host, 'notify')
    fireEvent.change(screen.getByLabelText(en.designTitle), { target: { value: 'Warm narrator' } })
    fireEvent.click(screen.getByRole('button', { name: en.design }))
    await flush()
    fireEvent.change(screen.getByLabelText(`${en.saveAs} 1`), { target: { value: 'Warm' } })
    fireEvent.click(screen.getByRole('button', { name: en.save }))
    await flush()
    expect(t.stored.get('favourites:conn-1::default')).toBeUndefined()
    expect(notify).toHaveBeenCalledWith({ kind: 'success', message: en.saved('Warm') })
    expect(screen.getByText(en.saved('Warm'))).toBeTruthy()
  })

  it('offers only Check again when an operator-managed agent has no key', () => {
    $available.set({ key: false, version: '1.1.0', account: false })
    const refresh = vi.fn(async () => undefined)
    setRefresher(refresh)
    const { calls } = mount(async () => VOICES)
    expect(screen.getByText(en.operatorOnboardTitle)).toBeTruthy()
    expect(screen.getByText(en.operatorOnboardBody)).toBeTruthy()
    expect(screen.getAllByRole('button')).toHaveLength(1)
    expect(screen.queryByText(en.getKey)).toBeNull()
    expect(screen.queryByText(en.openPlugins)).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: en.checkAgain }))
    expect(refresh).toHaveBeenCalled()
    expect(calls).toEqual([])
  })
})
