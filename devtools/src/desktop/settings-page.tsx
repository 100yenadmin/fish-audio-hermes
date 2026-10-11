// Settings ▸ Plugins ▸ Fish Audio (Hermes Desktop 0.21.6+): the selected agent's voice, speech model and voice names.
import {
  Button,
  Codicon,
  ErrorState,
  host,
  Input,
  ListRow,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  useQuery,
  usePluginI18n,
  useValue
} from '@hermes/plugin-sdk'
import { type ReactNode, useEffect, useRef, useState } from 'react'

import {
  $available,
  $availableError,
  addAlias,
  type AgentPin,
  agentKey,
  currentAgentEpoch,
  currentPin,
  errorText,
  gatewayAtLeast,
  getSettings,
  pluginCtx,
  postSettings,
  refreshAvailability,
  removeAlias,
  samePin,
  type SettingsKey,
  type SettingsResponse,
  type Voice
} from './api'
import { $favouritesRevision, readFavourites } from './favourites'
import { Onboarding, readFor } from './page'
import { openTab } from './plugin'
import { LINKS, PAGE_PATH, PLUGIN_ID } from './strings'
import { muted, Rows } from './ui'

/** The first gateway half that serves /settings and /aliases. */
export const SETTINGS_VERSION = '1.4.0'
/** Where plugins are installed and enabled (Settings ▸ Plugins redirects here on older hosts too). */
const PLUGINS_PATH = '/capabilities?tab=plugins&plugin=fish-audio'
const DEFAULT_MODEL = '__fish_default__'
const HEX32 = /^[0-9a-f]{32}$/
const NAME = /^[a-z][a-z0-9_-]{1,31}$/
const RESERVED = ['add', 'remove', 'list', 'current', 'default']

/** Decision 2's name rule, checked before sending; the gateway stays authoritative. A non-ASCII name goes to the
 *  gateway unchecked: its casefold can differ from toLowerCase. */
export function badAliasName(raw: string): boolean {
  const name = raw.trim().toLowerCase()
  if (/[^\x00-\x7f]/.test(name)) return false
  return !NAME.test(name) || HEX32.test(name) || RESERVED.includes(name)
}

const shortId = (id: string) => id.slice(0, 8)

export function SettingsPage() {
  const t = usePluginI18n(PLUGIN_ID)
  const available = useValue($available)
  const availableError = useValue($availableError)
  const profile = useValue(host.state.profile)
  const connectionId = useValue(host.state.connectionId)
  const pin: AgentPin = { connectionId, profile }
  let body: ReactNode
  if (available === false) {
    body = (
      <Notice text={t('notSetUp', profile)}>
        <Button onClick={() => host.navigate(PLUGINS_PATH)} size="xs" variant="secondary">
          {t('openPlugins')}
        </Button>
      </Notice>
    )
  } else if (available === null) {
    body = availableError ? (
      <ErrorState description={errorText(availableError)} title={t('unreachable', profile)}>
        <Button onClick={() => void refreshAvailability()} size="xs" variant="secondary">
          {t('checkAgain')}
        </Button>
      </ErrorState>
    ) : (
      <Rows n={3} />
    )
  } else if (!gatewayAtLeast(SETTINGS_VERSION)) {
    body = (
      <Notice text={t('settings.restart')}>
        <Button onClick={() => host.navigate(PAGE_PATH)} size="xs" variant="secondary">
          {t('settings.voicesPage')}
        </Button>
      </Notice>
    )
  } else if (!available.key) {
    body = <Onboarding operator={available.account === false} profile={profile} />
  } else {
    // Keyed by agent: a switch starts a fresh read, and nothing typed for one agent is sent to another.
    body = <Sections key={agentKey(pin)} operator={available.account === false} pin={pin} />
  }
  return (
    <div style={{ display: 'grid', gap: 8 }}>
      <p style={{ ...muted, fontSize: 12, margin: 0 }}>{t('forAgent', profile)}</p>
      {body}
    </div>
  )
}

function Notice({ text, children }: { text: string; children: ReactNode }) {
  return (
    <div style={{ display: 'grid', gap: 10, justifyItems: 'start' }}>
      <p style={{ ...muted, fontSize: 13, lineHeight: 1.5, margin: 0, maxWidth: 560 }}>{text}</p>
      {children}
    </div>
  )
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section style={{ marginTop: 12 }}>
      <h3 style={{ color: 'var(--ui-text-secondary)', fontSize: 12, fontWeight: 600, margin: 0 }}>{title}</h3>
      {children}
    </section>
  )
}

function Sections({ pin, operator }: { pin: AgentPin; operator: boolean }) {
  const t = usePluginI18n(PLUGIN_ID)
  const [state, setState] = useState<null | SettingsResponse>(null)
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState<null | string>(null)
  const [name, setName] = useState('')
  const [voice, setVoice] = useState<null | string>(null)  // null: follow the current voice
  const [badName, setBadName] = useState(false)
  const generation = useRef(0)
  useValue($favouritesRevision)
  const favourites = readFavourites(pin)

  const load = () => {
    const mine = ++generation.current
    setError(null)
    readFor<SettingsResponse>(pin, '/settings').then(
      res => mine === generation.current && setState(res),
      failure => mine === generation.current && setError(failure)
    )
  }
  useEffect(() => {
    load()
    return () => void generation.current++
  }, [])

  const current = state?.voice ?? null
  const currentVoice = useQuery({
    enabled: Boolean(current),
    queryFn: () => readFor<{ voice: Voice }>(pin, `/voices/${encodeURIComponent(current ?? '')}`),
    queryKey: ['fish-audio', agentKey(pin), 'voice', current],
    retry: false,
    staleTime: 300_000
  })
  const titleOf = (id: string) =>
    (currentVoice.data?.voice.id === id && currentVoice.data.voice.title) || favourites.find(f => f.id === id)?.title || shortId(id)

  /** One write at a time. Each re-checks the agent before sending and after the answer: an answer that arrives after
   *  the agent changed (even A→B→A) changes nothing on this page. */
  const write = async <T,>(id: string, send: () => Promise<T>, apply: (res: T) => void) => {
    const i18n = pluginCtx().i18n
    if (!samePin(currentPin(), pin)) return host.notify({ kind: 'error', message: i18n.t('agentChangedNothingSent') })
    const epoch = currentAgentEpoch()
    const mine = ++generation.current
    setBusy(id)
    try {
      const res = await send()
      if (!samePin(currentPin(), pin) || epoch !== currentAgentEpoch()) {
        return host.notify({ kind: 'error', message: i18n.t('settings.agentChangedAfterSend', pin.profile) })
      }
      if (mine === generation.current) apply(res)
    } catch (failure) {
      if (samePin(currentPin(), pin)) host.notify({ kind: 'error', message: errorText(failure) })
    } finally {
      if (samePin(currentPin(), pin)) setBusy(null)
    }
  }
  const saved = (message: string) => host.notify({ kind: 'success', message })

  if (error) {
    return (
      <ErrorState description={errorText(error)} title={t('settings.loadFailed')}>
        <Button onClick={load} size="xs" variant="secondary">
          {t('retry')}
        </Button>
      </ErrorState>
    )
  }
  if (!state) return <Rows n={3} />

  const managed = (key: SettingsKey) => state.managed.includes(key)
  const managedNote = (key: SettingsKey) =>
    managed(key) ? <div style={{ ...muted, fontSize: 12, marginTop: 4 }}>{t('settings.managed')}</div> : undefined
  const locked = busy !== null
  const models = state.model && !state.models.some(m => m.id === state.model)
    ? [...state.models, { id: state.model, display: state.model, deprecated: false }]
    : state.models
  const setModel = (value: string) => {
    const model = value === DEFAULT_MODEL ? null : value
    if (model === state.model) return
    void write('model', () => postSettings({ model }), res => {
      setState(res)
      saved(pluginCtx().i18n.t('settings.modelSaved'))
    })
  }
  const aliasVoice = voice ?? current ?? ''
  const add = () => {
    if (badAliasName(name)) return setBadName(true)
    const typed = name.trim()
    void write('add', () => addAlias(typed, aliasVoice.trim()), res => {
      setState(s => s && { ...s, aliases: res.aliases })
      setName('')
      setVoice(null)
      saved(pluginCtx().i18n.t(res.updated ? 'settings.updated' : 'settings.added', typed.toLowerCase()))
    })
  }
  const remove = (alias: string) =>
    void write(`remove:${alias}`, () => removeAlias(alias), res => {
      setState(s => s && { ...s, aliases: res.aliases })
      saved(pluginCtx().i18n.t('settings.removed', alias))
    })

  return (
    <div>
      <Section title={t('settings.voiceSection')}>
        <ListRow
          action={
            <>
              <Button disabled={managed('voice')} onClick={() => openTab('library')} size="xs" variant="secondary">
                {t('settings.change')}
              </Button>
              {current && (
                <Button
                  disabled={managed('voice') || locked}
                  loading={busy === 'reset'}
                  onClick={() => void write('reset', () => postSettings({ voice: null }), res => {
                    setState(res)
                    saved(pluginCtx().i18n.t('settings.resetDone'))
                  })}
                  size="xs"
                  variant="ghost"
                >
                  {t('settings.reset')}
                </Button>
              )}
            </>
          }
          below={managedNote('voice')}
          description={current ? <bdi>{titleOf(current)}</bdi> : t('settings.defaultVoice')}
          title={t('settings.currentVoice')}
        />
      </Section>
      <Section title={t('settings.modelSection')}>
        <ListRow
          action={
            <div style={{ width: 220 }}>
              <Select disabled={managed('model') || locked} onValueChange={setModel} value={state.model ?? DEFAULT_MODEL}>
                <SelectTrigger aria-label={t('settings.model')}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={DEFAULT_MODEL}>{t('settings.defaultModel')}</SelectItem>
                  {models.map(model => (
                    <SelectItem key={model.id} value={model.id}>
                      {model.display}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          }
          below={managedNote('model')}
          description={t('settings.modelHint')}
          title={t('settings.model')}
        />
      </Section>
      <Section title={t('settings.namesSection')}>
        <p style={{ ...muted, fontSize: 12, lineHeight: 1.5, margin: '4px 0 0' }}>{t('settings.namesHint')}</p>
        {managedNote('aliases')}
        {state.aliases.length === 0 && <p style={{ ...muted, fontSize: 13, margin: '8px 0 0' }}>{t('settings.noNames')}</p>}
        {state.aliases.map(alias => (
          <ListRow
            action={
              <Button
                aria-label={`${t('settings.remove')} ${alias.name}`}
                disabled={managed('aliases') || locked}
                loading={busy === `remove:${alias.name}`}
                onClick={() => remove(alias.name)}
                size="xs"
                variant="ghost"
              >
                {t('settings.remove')}
              </Button>
            }
            description={<bdi>{titleOf(alias.voice)}</bdi>}
            key={alias.name}
            title={<bdi>{alias.name}</bdi>}
          />
        ))}
        <ListRow
          action={
            <>
              <div style={{ width: 140 }}>
                <Input
                  aria-label={t('settings.name')}
                  disabled={managed('aliases')}
                  onChange={(e: { target: { value: string } }) => {
                    setName(e.target.value)
                    setBadName(false)
                  }}
                  placeholder={t('settings.name')}
                  value={name}
                />
              </div>
              <div style={{ width: 240 }}>
                <Input
                  aria-label={t('settings.voice')}
                  disabled={managed('aliases')}
                  onChange={(e: { target: { value: string } }) => setVoice(e.target.value)}
                  placeholder={t('settings.voicePlaceholder')}
                  value={aliasVoice}
                />
              </div>
              <Button
                disabled={managed('aliases') || locked || !name.trim() || !aliasVoice.trim()}
                loading={busy === 'add'}
                onClick={add}
                size="xs"
              >
                {t('settings.add')}
              </Button>
            </>
          }
          below={
            <>
              {badName && <div role="alert" style={{ color: 'var(--ui-orange)', fontSize: 12, marginTop: 4 }}>{t('settings.badName')}</div>}
              {favourites.length > 0 && !managed('aliases') && (
                <div style={{ alignItems: 'center', display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 6 }}>
                  <span style={{ ...muted, fontSize: 12 }}>{t('favouritesOnly')}</span>
                  {favourites.slice(0, 8).map(f => (
                    <Button key={f.id} onClick={() => setVoice(f.id)} size="xs" variant="ghost">
                      <bdi>{f.title}</bdi>
                    </Button>
                  ))}
                </div>
              )}
            </>
          }
          title={t('settings.addName')}
        />
      </Section>
      <Section title={t('settings.linksSection')}>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 8 }}>
          <Button onClick={() => host.navigate(PAGE_PATH)} size="xs" variant="secondary">
            {t('settings.voicesPage')}
          </Button>
          {!operator && (
            <Button onClick={() => void pluginCtx().os.openExternal(LINKS.account)} size="xs" variant="ghost">
              <Codicon name="link-external" />
              {t('settings.fishAccount')}
            </Button>
          )}
        </div>
        <p style={{ ...muted, fontSize: 12, lineHeight: 1.5, margin: '8px 0 0' }}>{t('settings.agentSettingsNote')}</p>
      </Section>
    </div>
  )
}
