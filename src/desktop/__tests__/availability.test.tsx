import { createTestContext, host, resetHost } from './sdk-mock'
import { $account, $available, $availableError, currentAgentEpoch, httpStatus, refreshAvailability } from '../api'
import plugin, { isNotFoundError } from '../plugin'

const flush = async (rounds = 5) => {
  for (let i = 0; i < rounds; i++) await new Promise(resolve => setTimeout(resolve, 0))
}
// Exactly what Electron hands the renderer: the status only survives inside the message text.
const ipc = (text: string) => new Error(`Error invoking remote method 'hermes:api': Error: ${text}`)
const ACCOUNT = { ok: true, credit: '0.40', cumulative_top_up: '5', has_free_credit: false, low: true, package: null, links: {} }
const gated = ['nav', 'credit', 'palette-voices', 'palette-account']

function backend(answers: Record<string, () => Promise<unknown>> = {}) {
  const calls: string[] = []
  const t = createTestContext({
    rest: path => {
      calls.push(path)
      return (answers[path] ?? (async () => (path === '/account' ? ACCOUNT : { ok: true, version: '0.3.0', key: true })))()
    }
  })
  return { calls, t }
}

afterEach(() => {
  resetHost()
  $available.set(null)
  $account.set(null)
  $availableError.set(null)
})

describe('availability gate', () => {
  it('agent changes and a disable end operations in flight, even when the same agent comes back', async () => {
    const { t } = backend()
    plugin.register(t.ctx as any)
    await flush()
    const start = currentAgentEpoch()
    host.state.profile.set('other')
    host.state.profile.set('default')
    expect(currentAgentEpoch()).toBeGreaterThanOrEqual(start + 2)
    const beforeDisable = currentAgentEpoch()
    t.dispose()
    expect(currentAgentEpoch()).toBeGreaterThan(beforeDisable)
  })

  it('a failing probe while the agent is unknown records the failure; Check again retries and clears it', async () => {
    let answer: () => Promise<unknown> = async () => {
      throw ipc('500: {"detail":"boom"}')
    }
    const { t } = backend({ '/available': () => answer() })
    plugin.register(t.ctx as any)
    await flush()
    expect($available.get()).toBeNull()
    expect($availableError.get()).toBeInstanceOf(Error)
    answer = async () => ({ ok: true, version: '1.0.4', key: true })
    const retry = refreshAvailability()
    expect($availableError.get()).toBeNull() // loading again, not the last failure
    await retry
    await flush()
    expect($available.get()).toEqual({ key: true, version: '1.0.4' })
    expect($availableError.get()).toBeNull()
    t.dispose()
  })

  it('a transient error for an agent that already answered records no failure', async () => {
    let answer: () => Promise<unknown> = async () => ({ ok: true, version: '1.0.4', key: true })
    const { t } = backend({ '/available': () => answer() })
    plugin.register(t.ctx as any)
    await flush()
    answer = async () => {
      throw ipc('503: {"detail":"busy"}')
    }
    t.tickIntervals()
    await flush()
    expect($available.get()).toEqual({ key: true, version: '1.0.4' })
    expect($availableError.get()).toBeNull()
    t.dispose()
  })

  it('always registers the route; nav, chip and palette only after /available answers 200', async () => {
    let release: (value: unknown) => void = () => undefined
    const { t } = backend({ '/available': () => new Promise(resolve => (release = resolve)) })
    plugin.register(t.ctx as any)
    expect(t.live.get('page')?.data).toEqual({ path: '/fish-audio' })
    expect(gated.some(id => t.live.has(id))).toBe(false)
    release({ ok: true, version: '0.3.0', key: true })
    await flush()
    expect(gated.every(id => t.live.has(id))).toBe(true)
    expect(t.live.get('nav')?.data).toMatchObject({ label: 'Voices', path: '/fish-audio' })
    expect(t.live.get('credit')?.area).toBe('statusBar.right')
    expect(t.live.get('palette-voices')?.data.label).toBe('Fish Audio: Voices')
    expect(t.live.get('palette-account')?.data.label).toBe('Fish Audio: Account')
    expect($available.get()).toEqual({ key: true, version: '0.3.0' })
    expect($account.get()?.credit).toBe('0.40')
    t.dispose()
  })

  it('does not read the wallet when the agent has no key', async () => {
    const { calls, t } = backend({ '/available': async () => ({ ok: true, version: '0.3.0', key: false }) })
    plugin.register(t.ctx as any)
    await flush()
    expect(t.live.has('nav')).toBe(true)
    expect(calls).toEqual(['/available'])
    expect($account.get()).toBeNull()
    t.dispose()
  })

  it('removes the gated contributions only on a definite 404, never on transport errors', async () => {
    let answer: () => Promise<unknown> = async () => ({ ok: true, key: true })
    const { t } = backend({ '/available': () => answer() })
    plugin.register(t.ctx as any)
    await flush()
    answer = async () => {
      throw new Error('connect ECONNREFUSED 127.0.0.1:404')
    }
    t.tickIntervals()
    await flush()
    expect(t.live.has('nav')).toBe(true)
    answer = async () => {
      throw ipc('500: {"detail":"cannot read docs/404/notes"}')
    }
    t.tickIntervals()
    await flush()
    expect(t.live.has('nav')).toBe(true)
    answer = async () => {
      throw ipc('404: {"detail":"Plugin not found"}')
    }
    t.tickIntervals()
    await flush()
    expect(gated.some(id => t.live.has(id))).toBe(false)
    expect(t.live.has('page')).toBe(true)
    expect($available.get()).toBe(false)
    t.dispose()
  })

  it('re-probes when the profile or connection changes, and forgets the previous agent wallet at once', async () => {
    const { calls, t } = backend()
    plugin.register(t.ctx as any)
    await flush()
    expect($account.get()).not.toBeNull()
    const before = calls.filter(path => path === '/available').length
    host.state.profile.set('other')
    expect($account.get()).toBeNull()
    expect($available.get()).toBeNull()
    host.state.connectionId.set('conn-2')
    await flush()
    expect(calls.filter(path => path === '/available').length).toBe(before + 2)
    t.dispose()
  })

  it('hides the previous agent entries at once on an agent change, until the new agent answers', async () => {
    let answer: ((value: unknown) => void) | null = null
    let fail: ((error: unknown) => void) | null = null
    let firstProbe = true
    const { t } = backend({
      '/available': () =>
        firstProbe
          ? ((firstProbe = false), Promise.resolve({ ok: true, version: '1.0.1', key: true }))
          : new Promise((resolve, reject) => ((answer = resolve), (fail = reject)))
    })
    plugin.register(t.ctx as any)
    await flush()
    expect(gated.every(id => t.live.has(id))).toBe(true)
    host.state.profile.set('agent-without-fish')
    expect(gated.some(id => t.live.has(id))).toBe(false) // gone before the new agent answers
    fail!(new Error('timeout')) // a transport error for the new agent keeps them hidden
    await flush()
    expect(gated.some(id => t.live.has(id))).toBe(false)
    host.state.profile.set('agent-with-fish')
    answer!({ ok: true, version: '1.0.1', key: true })
    await flush()
    expect(gated.every(id => t.live.has(id))).toBe(true)
    t.dispose()
  })

  it('a re-enable starts unknown, not with the state left by the previous lifecycle', async () => {
    const { t } = backend({ '/available': () => new Promise(() => undefined) })
    $available.set({ key: true, version: '1.0.1' }) // left by an earlier enable, possibly for another agent
    $account.set(ACCOUNT as any)
    plugin.register(t.ctx as any)
    expect($available.get()).toBeNull()
    expect($account.get()).toBeNull()
    t.dispose()
  })

  it('generation counter: a late answer from the previous agent changes nothing', async () => {
    const pending: Array<{ resolve: (v: unknown) => void; reject: (e: unknown) => void }> = []
    const { t } = backend({ '/available': () => new Promise((resolve, reject) => pending.push({ resolve, reject })) })
    plugin.register(t.ctx as any)
    host.state.profile.set('agent-b') // the second probe starts while the first is still pending
    pending[1].resolve({ ok: true, version: '0.3.0', key: false })
    await flush()
    expect(t.live.has('nav')).toBe(true)
    expect($available.get()).toEqual({ key: false, version: '0.3.0' })
    pending[0].reject(ipc('404: {"detail":"Plugin not found"}')) // stale: about the previous agent
    await flush()
    expect(t.live.has('nav')).toBe(true)
    expect($available.get()).toEqual({ key: false, version: '0.3.0' })
    t.dispose()
  })

  it('a stale wallet answer for the previous agent never lands on the new one', async () => {
    let releaseAccount: (value: unknown) => void = () => undefined
    let first = true
    const { t } = backend({
      '/account': () => (first ? ((first = false), new Promise(resolve => (releaseAccount = resolve))) : Promise.resolve({ ...ACCOUNT, credit: '9.00' }))
    })
    plugin.register(t.ctx as any)
    await flush()
    host.state.profile.set('agent-b')
    await flush()
    expect($account.get()?.credit).toBe('9.00')
    releaseAccount(ACCOUNT) // agent A's slow wallet read
    await flush()
    expect($account.get()?.credit).toBe('9.00')
    t.dispose()
  })
})

describe('wallet refresh', () => {
  it('inherits a pending forced refresh when an interval overtakes a known wallet', async () => {
    const pending: Array<(value: unknown) => void> = []
    let known = false
    const { t, calls } = backend({ '/account': () => known ? new Promise(resolve => pending.push(resolve)) : Promise.resolve(ACCOUNT) })
    plugin.register(t.ctx as any)
    await flush()
    expect($account.get()?.credit).toBe('0.40')
    known = true
    const forced = refreshAvailability()
    await flush()
    expect(pending).toHaveLength(1)
    t.tickIntervals()
    await flush()
    try {
      expect(calls.filter(path => path === '/account')).toHaveLength(3) // initial + forced + interval
      pending[0]({ ...ACCOUNT, credit: '8.00' }) // superseded forced answer
      await forced
      expect($account.get()?.credit).toBe('0.40')
      pending[1]({ ...ACCOUNT, credit: '7.00' })
      await flush()
      expect($account.get()?.credit).toBe('7.00')
      t.tickIntervals()
      await flush()
      expect(pending).toHaveLength(2) // a successfully applied result clears the pending force
    } finally {
      t.dispose()
    }
  })

  it('an interval probe that overtakes the first wallet read still reads the unknown wallet', async () => {
    const pending: Array<(value: unknown) => void> = []
    const { t, calls } = backend({ '/account': () => new Promise(resolve => pending.push(resolve)) })
    plugin.register(t.ctx as any)
    await flush() // forced probe: /available answered, its /account read is still pending
    t.tickIntervals() // the 60 s probe starts before that read answers, so the read becomes stale
    await flush()
    pending[0](ACCOUNT) // dropped as stale
    await flush()
    expect(calls.filter(path => path === '/account')).toHaveLength(2)
    pending[1]({ ...ACCOUNT, credit: '7.00' })
    await flush()
    expect($account.get()?.credit).toBe('7.00')
    t.dispose()
  })
})

describe('HTTP status parse', () => {
  it('reads both shapes: the IPC wrapper and a bare leading status', () => {
    expect(httpStatus(ipc('404: {"detail":"Plugin not found"}'))).toBe(404)
    expect(httpStatus(new Error('404: Not Found'))).toBe(404)
    expect(httpStatus(new Error('  502: bad gateway'))).toBe(502)
    expect(httpStatus(new Error('Request timed out after 30000ms'))).toBeNull()
  })

  it('never reads 404 from the body of another status', () => {
    expect(isNotFoundError(ipc('500: {"detail":"cannot list docs/404/report"}'))).toBe(false)
    expect(isNotFoundError(ipc('500: {"detail":"Error: 404: nested"}'))).toBe(false)
    expect(httpStatus(new Error('500: {"detail":"Error: 404: nested"}'))).toBe(500)
    expect(isNotFoundError(new Error('connect ECONNREFUSED 10.0.0.1:404'))).toBe(false)
    expect(isNotFoundError(new Error('timeout after 4040ms'))).toBe(false)
  })
})
