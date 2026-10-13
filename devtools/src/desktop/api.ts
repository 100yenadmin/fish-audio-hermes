import { atom, host, type PluginContext, type PluginRestOptions } from '@hermes/plugin-sdk'
import { LINKS } from './strings'

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
export const $available = atom<null | false | { version: string; key: boolean; account: boolean; free_model?: 'active' | 'ending' | 'ended' }>(null)
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
export interface ErrorDetail {
  request_id?: string | null
  trace_id?: string | null
  code?: string | null
  hint?: string | null
  /** The bundle key of the sentence the gateway chose (its operator wording, 401 or 403); absent = keep its text. */
  text?: string | null
}

export class ApiError extends Error {
  constructor(
    readonly kind: string,
    message: string,
    readonly detail?: ErrorDetail
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
  const res = await pluginCtx().rest<T & { ok?: boolean; kind?: string; message?: string; detail?: ErrorDetail }>(path, opts)
  if (res && res.ok === false) throw new ApiError(res.kind ?? 'error', res.message ?? '', res.detail)
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

const FIXED_TEXT = new Set(['no_key', 'not_owner', 'gone', 'bad_upload_id', 'consent', 'managed', 'io_error', 'chunk_too_large', 'offset', 'size_mismatch', 'limit'])
/** Sentences a gateway may name in `detail.text`: Fish API errors, and the refusals with an operator variant. */
const NAMED = {
  errors: new Set(['credential', 'credentialAccess', 'credentialOperator', 'quota', 'quotaOperator', 'rate_limit', 'rateLimitOperator',
    'availability', 'invalid_request', 'not_found', 'too_large', 'unsupported_media', 'voice_not_found']),
  refusals: new Set(['no_key', 'no_keyOperator'])
}

export function errorText(error: unknown): string {
  if (!(error instanceof ApiError)) return error instanceof Error ? error.message : String(error)
  const t = pluginCtx().i18n.t
  if (error.kind === 'bad_chunk_size') return t('errors.badChunkSize')
  const sentence = (key: string, url?: string) => (t(key, url) !== key ? t(key, url) : null)
  if (error.detail) {
    // The gateway chose the sentence for this request (operator wording, 401 or 403); this bundle only supplies the words.
    // A sentence the bundles lack, or no name at all, keeps the gateway's own text, which already carries every id.
    const group = FIXED_TEXT.has(error.kind) ? 'refusals' : 'errors'
    const name = error.detail.text
    const url = error.kind === 'credential' ? LINKS.keys : error.kind === 'quota' ? LINKS.account : LINKS.discovery
    const base = typeof name === 'string' && NAMED[group].has(name) ? sentence(`${group}.${name}`, url) : null
    if (base !== null) {
      let text = base
      const { code, hint, request_id, trace_id } = error.detail
      // CJK suffixes carry no leading space after 。; one still separates them from a sentence ending in a URL.
      const append = (suffix: string) => { text += /[!-~]$/.test(text) && /^\S/.test(suffix) ? ` ${suffix}` : suffix }
      if (code) append(t('errors.code', code))
      if (hint === 'paid_model') {
        const available = $available.get()
        if (available && available.free_model === 'ended') append(t('errors.freeEnded'))
        append(t('errors.paidModel', LINKS.account))
      }
      if (request_id) append(t('errors.requestId', request_id))
      if (trace_id) append(t('errors.fishTrace', trace_id))
      return text
    }
  } else if (FIXED_TEXT.has(error.kind)) {
    // A gateway without detail (1.5.0 and older): fixed refusal texts by kind, operator wording from the last probe.
    const available = $available.get()
    const variant = `refusals.${error.kind}Operator`
    const text = (available && available.account === false ? sentence(variant) : null) ?? sentence(`refusals.${error.kind}`)
    if (text !== null) return text
  }
  return error.message || t('errors.error')
}

/** The agent selected right now: requests from `ctx.rest` go wherever this points at call time. */
export interface AgentPin {
  connectionId: null | string
  profile: string
}

export const currentPin = (): AgentPin => ({ connectionId: host.state.connectionId.get(), profile: host.state.profile.get() })

export const samePin = (a: AgentPin, b: AgentPin) => a.connectionId === b.connectionId && a.profile === b.profile

export const agentKey = (pin: AgentPin) => `${pin.connectionId ?? 'local'}::${pin.profile}`

// ---- Settings ▸ Plugins ▸ Fish Audio (gateway 1.4.0+). Shapes follow the v1.4.0 wire contract exactly.

export type SettingsKey = 'aliases' | 'model' | 'voice'

export interface ModelOption {
  id: string
  display: string
  deprecated: boolean
}

export interface VoiceAlias {
  name: string
  voice: string
}

export interface SettingsResponse {
  ok: true
  /** The configured nested values; null means Fish's default. */
  voice: null | string
  model: null | string
  models: ModelOption[]
  aliases: VoiceAlias[]
  managed: SettingsKey[]
}

export interface AliasesResponse {
  ok: true
  aliases: VoiceAlias[]
}

/** `model: null` removes the configured model; `voice` accepts only null (voices are set through /use). */
export const postSettings = (body: { model?: null | string; voice?: null }) => post<SettingsResponse>('/settings', body)

export const addAlias = (name: string, voice: string) => post<AliasesResponse & { updated: boolean }>('/aliases', { name, voice })

export const removeAlias = (name: string) => call<AliasesResponse>(`/aliases/${encodeURIComponent(name)}`, { method: 'DELETE', timeoutMs: 60_000 })

/** Parts of a strict `major.minor.patch` version (a `-prerelease` or `+build` suffix is dropped), else null. */
function versionParts(version: unknown): null | number[] {
  if (typeof version !== 'string') return null
  const core = version.split(/[-+]/)[0]
  return /^\d+\.\d+\.\d+$/.test(core) ? core.split('.').map(Number) : null
}

/** True when `version` is at least `min`. Anything that is not a strict `x.y.z` counts as too old. */
export function versionAtLeast(version: unknown, min: string): boolean {
  const have = versionParts(version)
  const want = versionParts(min)
  if (!have || !want) return false
  for (let i = 0; i < 3; i++) if (have[i] !== want[i]) return have[i] > want[i]
  return true
}

/** Whether the selected agent's gateway half is at least `min`, from its /available answer. */
export function gatewayAtLeast(min: string): boolean {
  const available = $available.get()
  return available ? versionAtLeast(available.version, min) : false
}
