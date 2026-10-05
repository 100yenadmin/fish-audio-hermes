// Clone uploads: chunked JSON + base64 (multipart `upload` throws on cookie/OAuth remotes), pinned to the agent
// the files were picked on. ctx.rest always goes to the agent selected at call time, so every request of the
// operation re-checks the pin synchronously, with no await between the check and the dispatch.
import { type AgentPin, ApiError, samePin } from './api'
import { bytesToBase64 } from './audio'

export const MAX_FILES = 3
export const MAX_FILE_BYTES = 10 * 1024 * 1024

export interface CloneDeps {
  rest: <T>(path: string, opts: { method: string; body: unknown; timeoutMs?: number }) => Promise<T>
  /** The agent selected right now. */
  current: () => AgentPin
  /** Changes on every agent change and plugin disable, so A→B→A still stops the upload. */
  epoch?: () => number
  readChunk?: (slice: Blob) => Promise<string>
}

export interface CloneMeta {
  title: string
  description?: string
  consent: boolean
}

/** The selected agent changed during the operation; nothing more was sent. */
export class AgentChanged extends Error {
  constructor() {
    super('agent changed')
  }
}

const readChunk = async (slice: Blob) => bytesToBase64(new Uint8Array(await slice.arrayBuffer()))

export async function cloneVoice(
  files: Blob[],
  meta: CloneMeta,
  pin: AgentPin,
  deps: CloneDeps,
  onProgress?: (file: number, sent: number, total: number) => void
): Promise<{ id: string; title: string }> {
  const read = deps.readChunk ?? readChunk
  const epoch = deps.epoch?.()
  const still = () => samePin(deps.current(), pin) && deps.epoch?.() === epoch

  const send = <T>(path: string, body: unknown, timeoutMs: number): Promise<T> => {
    if (!still()) throw new AgentChanged()
    return deps.rest<T & { kind?: string; message?: string; ok?: boolean }>(path, { method: 'POST', body, timeoutMs }).then(res => {
      if (!still()) throw new AgentChanged()
      if (res && res.ok === false) throw new ApiError(res.kind ?? 'error', res.message ?? 'Something went wrong')
      return res
    })
  }

  const uploaded: Array<{ size: number; upload_id: string }> = []
  const total = files.reduce((sum, file) => sum + file.size, 0)
  let sent = 0
  let finishing = false
  try {
    for (const [index, file] of files.entries()) {
      const start = await send<{ chunk_bytes: number; upload_id: string }>('/clone/start', { size: file.size }, 30_000)
      uploaded.push({ upload_id: start.upload_id, size: file.size })
      if (!Number.isInteger(start.chunk_bytes) || start.chunk_bytes <= 0) throw new ApiError('error', 'The gateway sent an invalid upload chunk size.')
      for (let offset = 0; offset < file.size; offset += start.chunk_bytes) {
        const data = await read(file.slice(offset, offset + start.chunk_bytes))
        await send('/clone/chunk', { upload_id: start.upload_id, offset, data }, 120_000)
        if (!still()) throw new AgentChanged()
        sent += Math.min(start.chunk_bytes, file.size - offset)
        onProgress?.(index + 1, sent, total)
      }
    }
    finishing = true
    const done = await send<{ voice: { id: string; title: string } }>(
      '/clone/finish',
      { files: uploaded, title: meta.title, description: meta.description || undefined, consent: meta.consent },
      600_000
    )
    return done.voice
  } catch (error) {
    // Finish removes the temps itself. Otherwise abort them, but only while the pinned agent is selected:
    // the gateway's expiry cleans up after an agent switch.
    if (!finishing && still()) {
      for (const item of uploaded) {
        void deps.rest('/clone/abort', { method: 'POST', body: { upload_id: item.upload_id } }).catch(() => undefined)
      }
    }
    throw error
  }
}
