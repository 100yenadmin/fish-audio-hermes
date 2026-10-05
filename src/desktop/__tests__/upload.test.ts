import { type AgentPin, ApiError } from '../api'
import { AgentChanged, cloneVoice } from '../upload'

const MiB = 1024 * 1024
const blob = (size: number) => new Blob([new Uint8Array(size)])
const A: AgentPin = { connectionId: 'conn-1', profile: 'default' }

/** A fake gateway that records which agent was selected when each request was dispatched. The injected chunk
 *  reader encodes a slice as its byte length, so `data` = length. */
function harness(handlers: Record<string, (body: any, n: number) => any> = {}) {
  let selected: AgentPin = { ...A }
  const calls: Array<{ path: string; body: any; agent: string }> = []
  const counts: Record<string, number> = {}
  const defaults: Record<string, (body: any) => any> = {
    '/clone/start': () => ({ ok: true, upload_id: `u${counts['/clone/start']}`, chunk_bytes: MiB }),
    '/clone/chunk': body => ({ ok: true, size: body.offset + Number(body.data) }),
    '/clone/finish': () => ({ ok: true, voice: { id: 'v1', title: 'Me' } }),
    '/clone/abort': () => ({ ok: true })
  }
  const deps = {
    current: () => selected,
    readChunk: async (slice: Blob) => String(slice.size),
    rest: async (path: string, opts: any) => {
      calls.push({ path, body: opts.body, agent: selected.profile })
      counts[path] = (counts[path] ?? 0) + 1
      const answer = handlers[path] ? await handlers[path](opts.body, counts[path]) : undefined
      return answer === undefined ? defaults[path](opts.body) : answer
    }
  }
  return { calls, deps: deps as any, select: (pin: AgentPin) => (selected = pin), of: (path: string) => calls.filter(c => c.path === path) }
}

describe('clone upload', () => {
  it('chunks each file, then finishes with the ids, sizes, title and consent', async () => {
    const h = harness()
    const progress: number[] = []
    const voice = await cloneVoice([blob(2.5 * MiB), blob(10)], { title: 'Me', consent: true }, A, h.deps, (_n, sent) => progress.push(sent))
    expect(voice).toEqual({ id: 'v1', title: 'Me' })
    expect(h.of('/clone/start').map(c => c.body)).toEqual([{ size: 2.5 * MiB }, { size: 10 }])
    expect(h.of('/clone/chunk').map(c => [c.body.upload_id, c.body.offset, Number(c.body.data)])).toEqual([
      ['u1', 0, MiB],
      ['u1', MiB, MiB],
      ['u1', 2 * MiB, 0.5 * MiB],
      ['u2', 0, 10]
    ])
    expect(h.of('/clone/finish')[0].body).toEqual({
      files: [
        { upload_id: 'u1', size: 2.5 * MiB },
        { upload_id: 'u2', size: 10 }
      ],
      title: 'Me',
      description: undefined,
      consent: true
    })
    expect(progress.at(-1)).toBe(2.5 * MiB + 10)
  })

  it('stops mid-upload when the selected agent changes: nothing reaches the other agent', async () => {
    const h = harness()
    let reads = 0
    h.deps.readChunk = async (slice: Blob) => {
      // The user switches agents while the second chunk is being read (an await before the next request).
      if (++reads === 2) h.select({ connectionId: 'conn-1', profile: 'other' })
      return String(slice.size)
    }
    await expect(cloneVoice([blob(3 * MiB)], { title: 'Me', consent: true }, A, h.deps)).rejects.toBeInstanceOf(AgentChanged)
    expect(h.calls.map(c => c.path)).toEqual(['/clone/start', '/clone/chunk'])
    expect(h.calls.every(c => c.agent === 'default')).toBe(true)
    expect(h.of('/clone/abort')).toEqual([]) // the old agent's expiry cleans up; no abort goes to the new one
  })

  it('a connection switch with the same profile name also stops it', async () => {
    const h = harness({
      '/clone/chunk': () => {
        h.select({ connectionId: 'conn-2', profile: 'default' })
      }
    })
    await expect(cloneVoice([blob(3 * MiB)], { title: 'Me', consent: true }, A, h.deps)).rejects.toBeInstanceOf(AgentChanged)
    expect(h.calls.map(c => c.path)).toEqual(['/clone/start', '/clone/chunk'])
    expect(h.of('/clone/finish')).toEqual([])
  })

  it('never starts when the agent already changed before the first request', async () => {
    const h = harness()
    h.select({ connectionId: 'conn-9', profile: 'default' })
    await expect(cloneVoice([blob(10)], { title: 'Me', consent: true }, A, h.deps)).rejects.toBeInstanceOf(AgentChanged)
    expect(h.calls).toEqual([])
  })

  it('an in-band refusal aborts the uploaded temps on the same agent and surfaces the message', async () => {
    const h = harness({ '/clone/chunk': (_body, n) => (n === 2 ? { ok: false, kind: 'too_large', message: 'Each sample must be under 10 MB.' } : undefined) })
    const error = await cloneVoice([blob(10), blob(20)], { title: 'Me', consent: true }, A, h.deps).catch(e => e)
    expect(error).toBeInstanceOf(ApiError)
    expect(error.message).toBe('Each sample must be under 10 MB.')
    expect(h.of('/clone/abort').map(c => c.body.upload_id)).toEqual(['u1', 'u2'])
  })

  it('a finish refusal does not send aborts (finish already removed the temps)', async () => {
    const h = harness({ '/clone/finish': () => ({ ok: false, kind: 'consent', message: 'Confirm consent.' }) })
    await expect(cloneVoice([blob(10)], { title: 'Me', consent: false }, A, h.deps)).rejects.toThrow('Confirm consent.')
    expect(h.of('/clone/abort')).toEqual([])
  })
})
