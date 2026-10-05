import { atom, host, type PluginContext, type PluginRestOptions } from '@hermes/plugin-sdk'

export interface Voice {
  id: string
  title: string
  description?: string
  languages?: string[]
  tags?: string[]
  like_count?: number
  task_count?: number
  author?: string
}

export interface VoicesResponse {
  ok: true
  items: Voice[]
  total: number | string
  page: number
}

export interface Account {
  ok: true
  credit: string
  cumulative_top_up: string
  has_free_credit: boolean | null
  low: boolean
  package_unavailable?: boolean
  package: null | {
    type?: string
    total?: number
    balance?: number
    finished_at?: string
    subscription_status?: string
    cancel_at_period_end?: boolean
  }
  links: { top_up: string; plans: string; keys: string }
}

export interface Candidate {
  design_token: string
  index: number
  duration_ms?: number
  audio?: string
  mime: string
}

/** The selected agent's gateway half: null until the first probe answers, false on a definite 404. */
export const $available = atom<null | false | { version: string; key: boolean; account: boolean }>(null)
/** The selected agent's wallet (status-bar chip, Account tab); null when unknown or without a key. */
export const $account = atom<Account | null>(null)
/** Which tab the page shows; palette commands set it before navigating. */
/** Why the availability probe failed while the agent's state is still unknown (a timeout, an auth error or a 5xx). */
export const $availableError = atom<unknown>(null)

/** Bumped on every agent change and when the plugin is turned off. An operation spanning awaits (a clone upload)
 *  stops when it changes, even if the same agent is selected again (A→B→A). */
let agentEpoch = 0
export const currentAgentEpoch = () => agentEpoch
export const endAgentOperations = () => {
  agentEpoch++
}

export const $tab = atom<'account' | 'create' | 'library' | 'mine'>('library')

/** An in-band `{ok:false, kind, message}` answer, raised so callers can show it. */
export class ApiError extends Error {
  constructor(
    readonly kind: string,
    message: string
  ) {
    super(message)
  }
}

let bound: null | PluginContext = null
let refresher: ((force?: boolean) => Promise<void>) | null = null

export function setRefresher(probe: (force?: boolean) => Promise<void>) {
  refresher = probe
}

/** Re-read availability and the wallet now (after a key is added, or a billed action). */
export const refreshAvailability = () => refresher?.(true)

export function bindContext(ctx: PluginContext) {
  bound = ctx
}

export function pluginCtx(): PluginContext {
  if (!bound) throw new Error('Fish Audio is not registered')
  return bound
}

/** GET/POST/DELETE to this plugin's gateway routes; an in-band failure becomes a thrown ApiError. */
export async function call<T>(path: string, opts?: PluginRestOptions): Promise<T> {
  const res = await pluginCtx().rest<T & { ok?: boolean; kind?: string; message?: string }>(path, opts)
  if (res && res.ok === false) throw new ApiError(res.kind ?? 'error', res.message ?? 'Something went wrong')
  return res
}

export const post = <T>(path: string, body: unknown, timeoutMs = 60_000) => call<T>(path, { method: 'POST', body, timeoutMs })

export const query = (path: string, params: Record<string, number | string | undefined>) => {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) if (value !== undefined && value !== '') search.set(key, String(value))
  const text = search.toString()
  return text ? `${path}?${text}` : path
}

/** True when `error` is the gateway saying "this plugin route is not mounted here" (a definite 404). */
export function isNotFoundError(error: unknown): boolean {
  return httpStatus(error) === 404
}

/** The HTTP status: an anchored leading `<status>:` first, else the FIRST `Error: <status>:` (the IPC wrapper
 *  always precedes the body). Never a number found elsewhere in the body. Null when neither is present. */
export function httpStatus(error: unknown): null | number {
  const text = error instanceof Error ? error.message : String(error)
  const match = /^\s*(\d{3}):/.exec(text) ?? /Error: (\d{3}):/.exec(text)
  return match ? Number(match[1]) : null
}

export const errorText = (error: unknown) => (error instanceof Error ? error.message : String(error))

/** The agent selected right now: requests from `ctx.rest` go wherever this points at call time. */
export interface AgentPin {
  connectionId: null | string
  profile: string
}

export const currentPin = (): AgentPin => ({ connectionId: host.state.connectionId.get(), profile: host.state.profile.get() })

export const samePin = (a: AgentPin, b: AgentPin) => a.connectionId === b.connectionId && a.profile === b.profile

export const agentKey = (pin: AgentPin) => `${pin.connectionId ?? 'local'}::${pin.profile}`
