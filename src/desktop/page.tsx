// The Voices page: header, tabs (Library, My voices, Create, Account) and the no-key onboarding card.
import {
  atom,
  Badge,
  Button,
  Codicon,
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  EmptyState,
  ErrorState,
  host,
  Input,
  SearchField,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  SegmentedControl,
  useQuery,
  useQueryClient,
  usePluginI18n,
  useValue
} from '@hermes/plugin-sdk'
import { type ReactNode, useEffect, useState } from 'react'

import {
  $account,
  $available,
  $availableError,
  $tab,
  type Account,
  type AgentPin,
  agentKey,
  ApiError,
  call,
  currentPin,
  errorText,
  pluginCtx,
  post,
  query,
  refreshAvailability,
  samePin,
  type Voice,
  type VoicesResponse
} from './api'
import { $playing, cachedPreview, play, playbackEpoch, releasePlayback, rememberPreview, stop } from './audio'
import { CreateTab } from './create'
import { $favouritesRevision, forgetFavourite, readFavourites, writeFavourites } from './favourites'
import { LANGUAGES, LINKS, PLUGIN_ID, useAccountText } from './strings'
import { BilledNote, card, LoadError, muted, Rows } from './ui'

const pad = '0 24px'

/** A read for the captured agent: refused when another agent is selected now, dropped when it changed meanwhile,
 *  so one agent's results never land in another agent's query cache. */
function readFor<T>(pin: AgentPin, path: string): Promise<T> {
  const changed = () => new ApiError('agent_changed', pluginCtx().i18n.t('agentChangedNothingSent'))
  if (!samePin(currentPin(), pin)) return Promise.reject(changed())
  return call<T>(path).then(res => {
    if (!samePin(currentPin(), pin)) throw changed()
    return res
  })
}

/** A plan renews only when Fish reports an active (or trial) subscription that is not set to cancel. */
const renews = (plan: NonNullable<Account['package']>) =>
  plan.cancel_at_period_end === false && ['active', 'trialing'].includes(plan.subscription_status ?? '')

export function VoicesPage() {
  const t = usePluginI18n(PLUGIN_ID)
  const available = useValue($available)
  const availableError = useValue($availableError)
  const profile = useValue(host.state.profile)
  const connectionId = useValue(host.state.connectionId)
  const pin: AgentPin = { connectionId, profile }
  if (available === false) {
    return (
      <Frame profile={profile}>
        <p style={{ ...muted, fontSize: 13, lineHeight: 1.5, maxWidth: 560, padding: pad }}>{t('notSetUp', profile)}</p>
      </Frame>
    )
  }
  if (available === null) {
    return (
      <Frame profile={profile}>
        {availableError ? (
          <div style={{ padding: pad }}>
            <ErrorState description={errorText(availableError)} title={t('unreachable', profile)}>
              <Button onClick={() => void refreshAvailability()} size="xs" variant="secondary">
                {t('checkAgain')}
              </Button>
            </ErrorState>
          </div>
        ) : (
          <Rows />
        )}
      </Frame>
    )
  }
  if (!available.key) {
    return (
      <Frame profile={profile}>
        <Onboarding profile={profile} operator={available.account === false} />
      </Frame>
    )
  }
  // Keyed by agent: switching agents resets searches, uploads and design candidates.
  return <Body key={agentKey(pin)} pin={pin} />
}

function Frame({ children, profile, tabs }: { children: ReactNode; profile: string; tabs?: ReactNode }) {
  const t = usePluginI18n(PLUGIN_ID)
  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      <header style={{ alignItems: 'center', display: 'flex', gap: 10, padding: '20px 24px 12px' }}>
        <Codicon name="unmute" size={18} />
        <h1 style={{ fontSize: 18, fontWeight: 600, margin: 0 }}>{t('title')}</h1>
        <Badge variant="muted">{t('poweredBy')}</Badge>
        <span style={{ ...muted, fontSize: 12 }}>{t('forAgent', profile)}</span>
        <div style={{ flex: 1 }} />
        {tabs}
      </header>
      <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', paddingBottom: 24 }}>{children}</div>
    </div>
  )
}

function Onboarding({ profile, operator }: { profile: string; operator: boolean }) {
  const t = usePluginI18n(PLUGIN_ID)
  const open = (url: string) => void pluginCtx().os.openExternal(url)
  return (
    <div style={{ padding: pad }}>
      <div style={{ ...card, maxWidth: 560 }}>
        <h2 style={{ fontSize: 15, fontWeight: 600, margin: '0 0 6px' }}>{operator ? t('operatorOnboardTitle') : t('onboardTitle')}</h2>
        <p style={{ ...muted, fontSize: 13, lineHeight: 1.5, margin: '0 0 12px' }}>{operator ? t('operatorOnboardBody') : t('onboardBody', profile)}</p>
        {!operator && <ol style={{ fontSize: 13, lineHeight: 1.8, listStyle: 'decimal', margin: '0 0 14px', paddingLeft: 20 }}>
          <li>{t('onboardStep1')}</li>
          <li>{t('onboardStep2')}</li>
        </ol>}
        <div style={{ display: 'flex', gap: 8 }}>
          {!operator && <><Button onClick={() => open(LINKS.keys)}>{t('getKey')}</Button>
          <Button onClick={() => host.navigate('/capabilities?tab=plugins')} variant="secondary">
            {t('openPlugins')}
          </Button></>}
          <Button onClick={() => void refreshAvailability()} variant="ghost">
            {t('checkAgain')}
          </Button>
        </div>
      </div>
    </div>
  )
}

function Body({ pin }: { pin: AgentPin }) {
  const t = usePluginI18n(PLUGIN_ID)
  const selected = useValue($tab)
  const available = useValue($available)
  const operator = available && available.account === false
  // An operator's account spans its agents: its balance and its own voices are not this agent's to show.
  const hidden = operator && (selected === 'account' || selected === 'mine')
  const tab = hidden ? 'library' : selected
  useEffect(() => { if (hidden) $tab.set('library') }, [hidden])
  const tabs = (
    <SegmentedControl
      onChange={(id: typeof tab) => $tab.set(id)}
      options={(['library', 'mine', 'create', 'account'] as const).filter(id => !operator || (id !== 'account' && id !== 'mine')).map(id => ({ id, label: t(`tabs.${id}`) }))}
      value={tab}
    />
  )
  useEffect(() => () => releasePlayback(), [])
  return (
    <Frame profile={pin.profile} tabs={tabs}>
      {tab === 'library' && <Library pin={pin} />}
      {tab === 'mine' && <MyVoices pin={pin} />}
      {tab === 'create' && <CreateTab pin={pin} />}
      {tab === 'account' && <AccountTab pin={pin} />}
    </Frame>
  )
}

function useDebounced<T>(value: T, ms: number): T {
  const [settled, setSettled] = useState(value)
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), ms)
    return () => clearTimeout(timer)
  }, [value, ms])
  return settled
}

function useFavourites(pin: AgentPin) {
  useValue($favouritesRevision)
  const list = readFavourites(pin)
  const toggle = (voice: Voice) => {
    // Read storage again: a delete may have changed it since this render.
    const current = readFavourites(pin)
    writeFavourites(
      pin,
      current.some(f => f.id === voice.id)
        ? current.filter(f => f.id !== voice.id)
        : [...current, { author: voice.author, id: voice.id, languages: voice.languages, title: voice.title }]
    )
  }
  return { has: (id: string) => list.some(f => f.id === id), list, toggle }
}

function Library({ pin }: { pin: AgentPin }) {
  const t = usePluginI18n(PLUGIN_ID)
  const billedNote = useAccountText('billedNote', 'operatorBilledNote')
  const [text, setText] = useState('')
  const [language, setLanguage] = useState('any')
  const [page, setPage] = useState(1)
  const [favouritesOnly, setFavouritesOnly] = useState(false)
  const q = useDebounced(text.trim(), 350)
  const favourites = useFavourites(pin)
  useEffect(() => setPage(1), [q, language])
  const voices = useQuery({
    enabled: !favouritesOnly,
    queryFn: () => readFor<VoicesResponse>(pin, query('/voices', { language: language === 'any' ? undefined : language, page, q })),
    queryKey: ['fish-audio', agentKey(pin), 'voices', q, language, page],
    retry: false,
    staleTime: 60_000
  })
  const list = favouritesOnly ? favourites.list : voices.data?.items
  const more = !favouritesOnly && hasMore(voices.data?.items.length, page, voices.data?.total)
  return (
    <div style={{ display: 'grid', gap: 12, padding: pad }}>
      <div style={{ alignItems: 'center', display: 'flex', flexWrap: 'wrap', gap: 10 }}>
        <div style={{ width: 280 }}>
          <SearchField aria-label={t('search')} onChange={setText} placeholder={t('search')} value={text} />
        </div>
        <div style={{ width: 160 }}>
          <Select onValueChange={setLanguage} value={language}>
            <SelectTrigger aria-label={t('language')}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="any">{t('anyLanguage')}</SelectItem>
              {LANGUAGES.map(code => (
                <SelectItem key={code} value={code}>
                  {t(`languages.${code}`)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <Button aria-pressed={favouritesOnly} onClick={() => setFavouritesOnly(!favouritesOnly)} size="xs" variant={favouritesOnly ? 'secondary' : 'ghost'}>
          <Codicon name={favouritesOnly ? 'star-full' : 'star-empty'} />
          {t('favouritesOnly')}
        </Button>
      </div>
      <BilledNote text={billedNote} />
      {!favouritesOnly && voices.error ? (
        <LoadError error={voices.error} onRetry={() => void voices.refetch()} />
      ) : !list ? (
        <Rows />
      ) : list.length === 0 ? (
        favouritesOnly ? <EmptyState description={t('noFavouritesHint')} title={t('noFavourites')} /> : <EmptyState title={t('noVoices')} />
      ) : (
        <VoiceList favourites={favourites} pin={pin} voices={list} />
      )}
      {!favouritesOnly && <Pager more={more} page={page} setPage={setPage} />}
    </div>
  )
}

/** The gateway serves pages 1–50 of 20 voices. */
const PAGE_SIZE = 20
const LAST_PAGE = 50
const hasMore = (items: number | undefined, page: number, total: number | string | undefined) => {
  if ((typeof total === 'number' && Number.isFinite(total)) || (typeof total === 'string' && /^\d+$/.test(total))) {
    return page * PAGE_SIZE < Number(total) && page < LAST_PAGE
  }
  return (items ?? 0) >= PAGE_SIZE && page < LAST_PAGE
}

function Pager({ page, more, setPage }: { page: number; more: boolean; setPage: (page: number) => void }) {
  const t = usePluginI18n(PLUGIN_ID)
  if (page <= 1 && !more) return null
  return (
    <div style={{ alignItems: 'center', display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
      <Button disabled={page <= 1} onClick={() => setPage(page - 1)} size="xs" variant="ghost">
        {t('prev')}
      </Button>
      <span style={{ ...muted, fontSize: 12 }}>{t('pageOf', page)}</span>
      <Button disabled={!more} onClick={() => setPage(page + 1)} size="xs" variant="ghost">
        {t('next')}
      </Button>
    </div>
  )
}

const inFlightPreviews = new Set<string>()

/** The voice whose Use is in flight, per agent. One at a time: two in flight could land in either order, on the
 *  gateway and on the page. */
const $usePending = atom<Record<string, string>>({})

/** Play a billed preview of a voice, or replay one already fetched in this window. */
export async function previewVoice(pin: AgentPin, voiceId: string) {
  if (!samePin(currentPin(), pin)) return host.notify({ kind: 'error', message: pluginCtx().i18n.t('agentChangedNothingSent') })
  const key = `preview:${agentKey(pin)}:${voiceId}`
  if ($playing.get() === key) return stop()
  const cached = cachedPreview(key)
  if (cached) return play(key, cached.audio, cached.mime)
  if (inFlightPreviews.has(key)) return  // one billed preview per voice at a time
  inFlightPreviews.add(key)
  const epoch = playbackEpoch()
  try {
    const res = await post<{ audio: string; mime: string }>('/preview', { voice: voiceId })
    if (!samePin(currentPin(), pin)) return
    rememberPreview(key, res)
    // Returned after the page closed or the plugin was turned off: kept for a free replay, never played.
    if (playbackEpoch() === epoch) play(key, res.audio, res.mime)
    void refreshAvailability()
  } finally {
    inFlightPreviews.delete(key)
  }
}

function VoiceList({ voices, pin, favourites, onDelete }: {
  voices: Voice[]
  pin: AgentPin
  favourites?: ReturnType<typeof useFavourites>
  onDelete?: (voice: Voice) => void
}) {
  const t = usePluginI18n(PLUGIN_ID)
  const billedNote = useAccountText('billedNote', 'operatorBilledNote')
  const playing = useValue($playing)
  const [busy, setBusy] = useState<null | string>(null)
  const [used, setUsed] = useState<null | string>(null)
  const pendingUse = useValue($usePending)[agentKey(pin)]
  const run = async (id: string, action: () => Promise<unknown>) => {
    if (!samePin(currentPin(), pin)) return host.notify({ kind: 'error', message: pluginCtx().i18n.t('agentChangedNothingSent') })
    setBusy(id)
    try {
      await action()
    } catch (error) {
      if (samePin(currentPin(), pin)) host.notify({ kind: 'error', message: errorText(error) })
    } finally {
      if (samePin(currentPin(), pin)) setBusy(null)
    }
  }
  const use = async (voice: Voice) => {
    const agent = agentKey(pin)
    if ($usePending.get()[agent]) return
    $usePending.set({ ...$usePending.get(), [agent]: voice.id })
    try {
      await run(`use:${voice.id}`, async () => {
        const res = await post<{ message: string; provider?: string | null; operator_pinned?: boolean }>('/use', { voice: voice.id })
        if (!samePin(currentPin(), pin)) return
        setUsed(voice.id)
        const i18n = pluginCtx().i18n
        // Gateways before 1.2.0 send only an English message.
        const note = res.provider
          ? ` ${i18n.t(res.operator_pinned ? 'useProviderByOperator' : 'useOtherProvider', res.provider)}`
          : res.provider === undefined && res.message && res.message !== 'Saved.' ? ` ${res.message.replace(/^Saved\.\s*/, '')}` : ''
        host.notify({ kind: 'success', message: i18n.t('usedVoice', voice.title, pin.profile) + note })
      })
    } finally {
      const { [agent]: mine, ...rest } = $usePending.get()
      if (mine === voice.id) $usePending.set(rest)
    }
  }
  return (
    <div style={{ border: '1px solid var(--ui-stroke-tertiary)', borderRadius: 6 }}>
      {voices.map((voice, i) => {
        const key = `preview:${agentKey(pin)}:${voice.id}`
        const meta = [voice.author, (voice.languages ?? []).join(', '), voice.task_count ? t('uses', voice.task_count) : '']
          .filter(Boolean)
          .join(' · ')
        return (
          <div
            key={voice.id}
            style={{
              alignItems: 'center',
              borderTop: i ? '1px solid var(--ui-stroke-tertiary)' : 0,
              display: 'grid',
              gap: 12,
              gridTemplateColumns: '36px minmax(0, 1fr) auto',
              padding: '10px 12px'
            }}
          >
            <Avatar title={voice.title} />
            <div style={{ minWidth: 0 }}>
              <div style={{ fontSize: 13, fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {voice.title}
              </div>
              <div style={{ ...muted, fontSize: 12, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {[meta, voice.description].filter(Boolean).join(' — ')}
              </div>
            </div>
            <div style={{ alignItems: 'center', display: 'flex', gap: 6 }}>
              <Button
                aria-label={`${playing === key ? t('stop') : t('preview')} ${voice.title}`}
                loading={busy === `play:${voice.id}`}
                onClick={() => void run(`play:${voice.id}`, () => previewVoice(pin, voice.id))}
                size="xs"
                title={billedNote}
                variant="secondary"
              >
                <Codicon name={playing === key ? 'debug-stop' : 'play'} />
                {playing === key ? t('stop') : t('preview')}
              </Button>
              <Button
                disabled={used === voice.id || (pendingUse !== undefined && pendingUse !== voice.id)}
                loading={pendingUse === voice.id}
                onClick={() => void use(voice)}
                size="xs"
                variant={used === voice.id ? 'ghost' : 'default'}
              >
                {used === voice.id ? t('inUse') : t('use')}
              </Button>
              {favourites && (
                <Button
                  aria-label={favourites.has(voice.id) ? t('unfavourite') : t('favourite')}
                  onClick={() => favourites.toggle(voice)}
                  size="icon-xs"
                  style={{ color: favourites.has(voice.id) ? 'var(--ui-orange)' : undefined }}
                  variant="ghost"
                >
                  <Codicon name={favourites.has(voice.id) ? 'star-full' : 'star-empty'} />
                </Button>
              )}
              {onDelete && (
                <Button aria-label={`${t('delete')} ${voice.title}`} onClick={() => onDelete(voice)} size="icon-xs" variant="ghost">
                  <Codicon name="trash" />
                </Button>
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
}

function Avatar({ title }: { title: string }) {
  const t = usePluginI18n(PLUGIN_ID)
  const hue = [...title].reduce((sum, c) => sum + c.charCodeAt(0), 0) % 360
  return (
    <div
      aria-hidden
      style={{
        alignItems: 'center',
        background: `hsl(${hue} 55% 45% / 0.18)`,
        borderRadius: '50%',
        color: `hsl(${hue} 60% 55%)`,
        display: 'flex',
        fontSize: 14,
        fontWeight: 600,
        height: 36,
        justifyContent: 'center',
        width: 36
      }}
    >
      {(title.trim()[0] ?? '?').toUpperCase()}
    </div>
  )
}

function MyVoices({ pin }: { pin: AgentPin }) {
  const t = usePluginI18n(PLUGIN_ID)
  const billedNote = useAccountText('billedNote', 'operatorBilledNote')
  const client = useQueryClient()
  const [page, setPage] = useState(1)
  const queryKey = ['fish-audio', agentKey(pin), 'mine']
  const voices = useQuery({
    queryFn: () => readFor<VoicesResponse>(pin, query('/voices', { page, self: 'true' })),
    queryKey: [...queryKey, page],
    retry: false,
    staleTime: 30_000
  })
  const [target, setTarget] = useState<null | Voice>(null)
  return (
    <div style={{ display: 'grid', gap: 12, padding: pad }}>
      <BilledNote text={billedNote} />
      {voices.error ? (
        <LoadError error={voices.error} onRetry={() => void voices.refetch()} />
      ) : !voices.data ? (
        <Rows n={3} />
      ) : voices.data.items.length === 0 ? (
        <EmptyState description={t('mineEmptyHint')} title={t('mineEmpty')} />
      ) : (
        <VoiceList onDelete={setTarget} pin={pin} voices={voices.data.items} />
      )}
      <Pager more={hasMore(voices.data?.items.length, page, voices.data?.total)} page={page} setPage={setPage} />
      <DeleteDialog pin={pin} onClose={() => setTarget(null)} onDeleted={() => void client.invalidateQueries({ queryKey })} voice={target} />
    </div>
  )
}

function DeleteDialog({ pin, voice, onClose, onDeleted }: { pin: AgentPin; voice: null | Voice; onClose: () => void; onDeleted: () => void }) {
  const t = usePluginI18n(PLUGIN_ID)
  const [typed, setTyped] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => setTyped(''), [voice])
  const confirm = async () => {
    if (!voice) return
    if (!samePin(currentPin(), pin)) return host.notify({ kind: 'error', message: pluginCtx().i18n.t('agentChangedNothingSent') })
    setBusy(true)
    try {
      await call(`/voices/${encodeURIComponent(voice.id)}`, { method: 'DELETE', timeoutMs: 60_000 })
      forgetFavourite(pin, voice.id)  // gone from that agent's account, whichever agent is selected now
      if (!samePin(currentPin(), pin)) return
      host.notify({ kind: 'success', message: pluginCtx().i18n.t('deleted', voice.title) })
      onDeleted()
      onClose()
    } catch (error) {
      if (samePin(currentPin(), pin)) host.notify({ kind: 'error', message: errorText(error) })
    } finally {
      if (samePin(currentPin(), pin)) setBusy(false)
    }
  }
  return (
    <Dialog onOpenChange={(open: boolean) => !open && onClose()} open={voice !== null}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{voice ? t('deleteTitle', voice.title) : ''}</DialogTitle>
        </DialogHeader>
        <p style={{ ...muted, fontSize: 13, lineHeight: 1.5, margin: 0 }}>{t('deleteBody')}</p>
        <label style={{ display: 'grid', fontSize: 12, gap: 6 }}>
          {voice ? t('deleteConfirmLabel', voice.title) : ''}
          <Input aria-label={voice ? t('deleteConfirmLabel', voice.title) : ''} onChange={(e: { target: { value: string } }) => setTyped(e.target.value)} value={typed} />
        </label>
        <DialogFooter>
          <Button onClick={onClose} variant="ghost">
            {t('cancel')}
          </Button>
          <Button disabled={!voice || typed !== voice.title} loading={busy} onClick={() => void confirm()} variant="destructive">
            {t('delete')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function AccountTab({ pin }: { pin: AgentPin }) {
  const t = usePluginI18n(PLUGIN_ID)
  const account = useQuery({
    queryFn: () => readFor<Account>(pin, '/account'),
    queryKey: ['fish-audio', agentKey(pin), 'account'],
    retry: false,
    staleTime: 30_000
  })
  useEffect(() => {
    if (account.data && samePin(currentPin(), pin)) $account.set(account.data)
  }, [account.data])
  const open = (url: string) => void pluginCtx().os.openExternal(url)
  if (account.error) return <div style={{ padding: pad }}><LoadError error={account.error} onRetry={() => void account.refetch()} /></div>
  const data = account.data
  if (!data) return <Rows n={2} />
  const plan = data.package
  return (
    <div style={{ display: 'grid', gap: 14, gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', maxWidth: 820, padding: pad }}>
      <div style={card}>
        <div style={{ ...muted, fontSize: 12 }}>{t('apiCredit')}</div>
        <div style={{ color: data.low ? 'var(--ui-orange)' : undefined, fontSize: 28, fontVariantNumeric: 'tabular-nums', fontWeight: 600, margin: '4px 0 8px' }}>
          {t('usd', data.credit)}
        </div>
        {data.low && <p style={{ color: 'var(--ui-orange)', fontSize: 12, margin: '0 0 8px' }}>{t('lowCredit')}</p>}
        <div style={{ ...muted, fontSize: 12, lineHeight: 1.7 }}>
          <div>
            {t('topUps')}: {t('usd', data.cumulative_top_up)}
          </div>
          {data.has_free_credit && <div>{t('freeCredit')}</div>}
        </div>
        <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
          <Button onClick={() => open(data.links.top_up)}>{t('topUp')}</Button>
          <Button onClick={() => open(data.links.keys)} variant="ghost">
            {t('apiKeys')}
          </Button>
        </div>
      </div>
      <div style={card}>
        <div style={{ ...muted, fontSize: 12 }}>{t('plan')}</div>
        <div style={{ fontSize: 20, fontWeight: 600, margin: '4px 0 8px', textTransform: data.package_unavailable ? 'none' : 'capitalize' }}>{data.package_unavailable ? t('planUnavailable') : plan?.type ?? t('noPlan')}</div>
        {plan && (
          <div style={{ ...muted, fontSize: 12, lineHeight: 1.7 }}>
            {typeof plan.total === 'number' && <div>{t('planBalance', Number(plan.balance ?? 0), plan.total)}</div>}
            {plan.finished_at && (
              <div>
                {t(renews(plan) ? 'renews' : 'periodEnds', String(plan.finished_at).slice(0, 10))}
              </div>
            )}
          </div>
        )}
        <p style={{ ...muted, fontSize: 12, lineHeight: 1.5 }}>{t('creditsSeparate')}</p>
        <Button onClick={() => open(data.links.plans)} variant="secondary">
          {t('plans')}
        </Button>
      </div>
    </div>
  )
}
