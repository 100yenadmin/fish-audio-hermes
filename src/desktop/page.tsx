// The Voices page: header, tabs (Library, My voices, Create, Account) and the no-key onboarding card.
import {
  Badge,
  Button,
  Codicon,
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  EmptyState,
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
  useValue
} from '@hermes/plugin-sdk'
import { type ReactNode, useEffect, useState } from 'react'

import {
  $account,
  $available,
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
import { $playing, cachedPreview, play, rememberPreview, stop } from './audio'
import { CreateTab } from './create'
import { LANGUAGES, LINKS, S } from './strings'
import { BilledNote, card, LoadError, muted, Rows } from './ui'

const pad = '0 24px'

/** A read for the captured agent: refused when another agent is selected now, dropped when it changed meanwhile,
 *  so one agent's results never land in another agent's query cache. */
function readFor<T>(pin: AgentPin, path: string): Promise<T> {
  const changed = () => new ApiError('agent_changed', S.agentChangedNothingSent)
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
  const available = useValue($available)
  const profile = useValue(host.state.profile)
  const connectionId = useValue(host.state.connectionId)
  const pin: AgentPin = { connectionId, profile }
  if (available === false) {
    return (
      <Frame profile={profile}>
        <p style={{ ...muted, fontSize: 13, lineHeight: 1.5, maxWidth: 560, padding: pad }}>{S.notSetUp(profile)}</p>
      </Frame>
    )
  }
  if (available === null) {
    return (
      <Frame profile={profile}>
        <Rows />
      </Frame>
    )
  }
  if (!available.key) {
    return (
      <Frame profile={profile}>
        <Onboarding profile={profile} />
      </Frame>
    )
  }
  // Keyed by agent: switching agents resets searches, uploads and design candidates.
  return <Body key={agentKey(pin)} pin={pin} />
}

function Frame({ children, profile, tabs }: { children: ReactNode; profile: string; tabs?: ReactNode }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      <header style={{ alignItems: 'center', display: 'flex', gap: 10, padding: '20px 24px 12px' }}>
        <Codicon name="unmute" size={18} />
        <h1 style={{ fontSize: 18, fontWeight: 600, margin: 0 }}>{S.title}</h1>
        <Badge variant="muted">{S.poweredBy}</Badge>
        <span style={{ ...muted, fontSize: 12 }}>{S.forAgent(profile)}</span>
        <div style={{ flex: 1 }} />
        {tabs}
      </header>
      <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', paddingBottom: 24 }}>{children}</div>
    </div>
  )
}

function Onboarding({ profile }: { profile: string }) {
  const open = (url: string) => void pluginCtx().os.openExternal(url)
  return (
    <div style={{ padding: pad }}>
      <div style={{ ...card, maxWidth: 560 }}>
        <h2 style={{ fontSize: 15, fontWeight: 600, margin: '0 0 6px' }}>{S.onboardTitle}</h2>
        <p style={{ ...muted, fontSize: 13, lineHeight: 1.5, margin: '0 0 12px' }}>{S.onboardBody(profile)}</p>
        <ol style={{ fontSize: 13, lineHeight: 1.8, listStyle: 'decimal', margin: '0 0 14px', paddingLeft: 20 }}>
          <li>{S.onboardStep1}</li>
          <li>{S.onboardStep2}</li>
        </ol>
        <div style={{ display: 'flex', gap: 8 }}>
          <Button onClick={() => open(LINKS.keys)}>{S.getKey}</Button>
          <Button onClick={() => host.navigate('/capabilities?tab=plugins')} variant="secondary">
            {S.openPlugins}
          </Button>
          <Button onClick={() => void refreshAvailability()} variant="ghost">
            {S.checkAgain}
          </Button>
        </div>
      </div>
    </div>
  )
}

function Body({ pin }: { pin: AgentPin }) {
  const tab = useValue($tab)
  const tabs = (
    <SegmentedControl
      onChange={(id: typeof tab) => $tab.set(id)}
      options={(['library', 'mine', 'create', 'account'] as const).map(id => ({ id, label: S.tabs[id] }))}
      value={tab}
    />
  )
  useEffect(() => () => stop(), [])
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

type Favourite = Pick<Voice, 'author' | 'id' | 'languages' | 'title'>

function useFavourites(pin: AgentPin) {
  const storageKey = `favourites:${agentKey(pin)}`
  const [list, setList] = useState<Favourite[]>(() => pluginCtx().storage.get<Favourite[]>(storageKey, []))
  const toggle = (voice: Voice) => {
    const next = list.some(f => f.id === voice.id)
      ? list.filter(f => f.id !== voice.id)
      : [...list, { author: voice.author, id: voice.id, languages: voice.languages, title: voice.title }]
    pluginCtx().storage.set(storageKey, next)
    setList(next)
  }
  return { has: (id: string) => list.some(f => f.id === id), list, toggle }
}

function Library({ pin }: { pin: AgentPin }) {
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
          <SearchField aria-label={S.search} onChange={setText} placeholder={S.search} value={text} />
        </div>
        <div style={{ width: 160 }}>
          <Select onValueChange={setLanguage} value={language}>
            <SelectTrigger aria-label={S.language}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="any">{S.anyLanguage}</SelectItem>
              {LANGUAGES.map(([code, label]) => (
                <SelectItem key={code} value={code}>
                  {label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <Button aria-pressed={favouritesOnly} onClick={() => setFavouritesOnly(!favouritesOnly)} size="xs" variant={favouritesOnly ? 'secondary' : 'ghost'}>
          <Codicon name={favouritesOnly ? 'star-full' : 'star-empty'} />
          {S.favouritesOnly}
        </Button>
      </div>
      <BilledNote text={S.billedNote} />
      {!favouritesOnly && voices.error ? (
        <LoadError error={voices.error} onRetry={() => void voices.refetch()} />
      ) : !list ? (
        <Rows />
      ) : list.length === 0 ? (
        favouritesOnly ? <EmptyState description={S.noFavouritesHint} title={S.noFavourites} /> : <EmptyState title={S.noVoices} />
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
  if (page <= 1 && !more) return null
  return (
    <div style={{ alignItems: 'center', display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
      <Button disabled={page <= 1} onClick={() => setPage(page - 1)} size="xs" variant="ghost">
        {S.prev}
      </Button>
      <span style={{ ...muted, fontSize: 12 }}>{S.pageOf(page)}</span>
      <Button disabled={!more} onClick={() => setPage(page + 1)} size="xs" variant="ghost">
        {S.next}
      </Button>
    </div>
  )
}

const inFlightPreviews = new Set<string>()

/** Play a billed preview of a voice, or replay one already fetched in this window. */
export async function previewVoice(pin: AgentPin, voiceId: string) {
  if (!samePin(currentPin(), pin)) return host.notify({ kind: 'error', message: S.agentChangedNothingSent })
  const key = `preview:${agentKey(pin)}:${voiceId}`
  if ($playing.get() === key) return stop()
  const cached = cachedPreview(key)
  if (cached) return play(key, cached.audio, cached.mime)
  if (inFlightPreviews.has(key)) return  // one billed preview per voice at a time
  inFlightPreviews.add(key)
  try {
    const res = await post<{ audio: string; mime: string }>('/preview', { voice: voiceId })
    if (!samePin(currentPin(), pin)) return
    rememberPreview(key, res)
    play(key, res.audio, res.mime)
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
  const playing = useValue($playing)
  const [busy, setBusy] = useState<null | string>(null)
  const [used, setUsed] = useState<null | string>(null)
  const run = async (id: string, action: () => Promise<unknown>) => {
    if (!samePin(currentPin(), pin)) return host.notify({ kind: 'error', message: S.agentChangedNothingSent })
    setBusy(id)
    try {
      await action()
    } catch (error) {
      if (samePin(currentPin(), pin)) host.notify({ kind: 'error', message: errorText(error) })
    } finally {
      if (samePin(currentPin(), pin)) setBusy(null)
    }
  }
  const use = (voice: Voice) =>
    run(`use:${voice.id}`, async () => {
      const res = await post<{ message: string }>('/use', { voice: voice.id })
      if (!samePin(currentPin(), pin)) return
      setUsed(voice.id)
      const note = res.message && res.message !== 'Saved.' ? ` ${res.message.replace(/^Saved\.\s*/, '')}` : ''
      host.notify({ kind: 'success', message: S.usedVoice(voice.title, pin.profile) + note })
    })
  return (
    <div style={{ border: '1px solid var(--ui-stroke-tertiary)', borderRadius: 6 }}>
      {voices.map((voice, i) => {
        const key = `preview:${agentKey(pin)}:${voice.id}`
        const meta = [voice.author, (voice.languages ?? []).join(', '), voice.task_count ? S.uses(voice.task_count) : '']
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
                aria-label={`${playing === key ? S.stop : S.preview} ${voice.title}`}
                loading={busy === `play:${voice.id}`}
                onClick={() => void run(`play:${voice.id}`, () => previewVoice(pin, voice.id))}
                size="xs"
                title={S.billedNote}
                variant="secondary"
              >
                <Codicon name={playing === key ? 'debug-stop' : 'play'} />
                {playing === key ? S.stop : S.preview}
              </Button>
              <Button
                disabled={used === voice.id}
                loading={busy === `use:${voice.id}`}
                onClick={() => void use(voice)}
                size="xs"
                variant={used === voice.id ? 'ghost' : 'default'}
              >
                {used === voice.id ? S.inUse : S.use}
              </Button>
              {favourites && (
                <Button
                  aria-label={favourites.has(voice.id) ? S.unfavourite : S.favourite}
                  onClick={() => favourites.toggle(voice)}
                  size="icon-xs"
                  style={{ color: favourites.has(voice.id) ? 'var(--ui-orange)' : undefined }}
                  variant="ghost"
                >
                  <Codicon name={favourites.has(voice.id) ? 'star-full' : 'star-empty'} />
                </Button>
              )}
              {onDelete && (
                <Button aria-label={`${S.delete} ${voice.title}`} onClick={() => onDelete(voice)} size="icon-xs" variant="ghost">
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
      <BilledNote text={S.billedNote} />
      {voices.error ? (
        <LoadError error={voices.error} onRetry={() => void voices.refetch()} />
      ) : !voices.data ? (
        <Rows n={3} />
      ) : voices.data.items.length === 0 ? (
        <EmptyState description={S.mineEmptyHint} title={S.mineEmpty} />
      ) : (
        <VoiceList onDelete={setTarget} pin={pin} voices={voices.data.items} />
      )}
      <Pager more={hasMore(voices.data?.items.length, page, voices.data?.total)} page={page} setPage={setPage} />
      <DeleteDialog pin={pin} onClose={() => setTarget(null)} onDeleted={() => void client.invalidateQueries({ queryKey })} voice={target} />
    </div>
  )
}

function DeleteDialog({ pin, voice, onClose, onDeleted }: { pin: AgentPin; voice: null | Voice; onClose: () => void; onDeleted: () => void }) {
  const [typed, setTyped] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => setTyped(''), [voice])
  const confirm = async () => {
    if (!voice) return
    if (!samePin(currentPin(), pin)) return host.notify({ kind: 'error', message: S.agentChangedNothingSent })
    setBusy(true)
    try {
      await call(`/voices/${encodeURIComponent(voice.id)}`, { method: 'DELETE', timeoutMs: 60_000 })
      if (!samePin(currentPin(), pin)) return
      host.notify({ kind: 'success', message: S.deleted(voice.title) })
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
          <DialogTitle>{voice ? S.deleteTitle(voice.title) : ''}</DialogTitle>
        </DialogHeader>
        <p style={{ ...muted, fontSize: 13, lineHeight: 1.5, margin: 0 }}>{S.deleteBody}</p>
        <label style={{ display: 'grid', fontSize: 12, gap: 6 }}>
          {voice ? S.deleteConfirmLabel(voice.title) : ''}
          <Input aria-label={voice ? S.deleteConfirmLabel(voice.title) : ''} onChange={(e: { target: { value: string } }) => setTyped(e.target.value)} value={typed} />
        </label>
        <DialogFooter>
          <Button onClick={onClose} variant="ghost">
            {S.cancel}
          </Button>
          <Button disabled={!voice || typed !== voice.title} loading={busy} onClick={() => void confirm()} variant="destructive">
            {S.delete}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function AccountTab({ pin }: { pin: AgentPin }) {
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
        <div style={{ ...muted, fontSize: 12 }}>{S.apiCredit}</div>
        <div style={{ color: data.low ? 'var(--ui-orange)' : undefined, fontSize: 28, fontVariantNumeric: 'tabular-nums', fontWeight: 600, margin: '4px 0 8px' }}>
          {S.usd(data.credit)}
        </div>
        {data.low && <p style={{ color: 'var(--ui-orange)', fontSize: 12, margin: '0 0 8px' }}>{S.lowCredit}</p>}
        <div style={{ ...muted, fontSize: 12, lineHeight: 1.7 }}>
          <div>
            {S.topUps}: {S.usd(data.cumulative_top_up)}
          </div>
          {data.has_free_credit && <div>{S.freeCredit}</div>}
        </div>
        <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
          <Button onClick={() => open(data.links.top_up)}>{S.topUp}</Button>
          <Button onClick={() => open(data.links.keys)} variant="ghost">
            {S.apiKeys}
          </Button>
        </div>
      </div>
      <div style={card}>
        <div style={{ ...muted, fontSize: 12 }}>{S.plan}</div>
        <div style={{ fontSize: 20, fontWeight: 600, margin: '4px 0 8px', textTransform: data.package_unavailable ? 'none' : 'capitalize' }}>{data.package_unavailable ? S.planUnavailable : plan?.type ?? S.noPlan}</div>
        {plan && (
          <div style={{ ...muted, fontSize: 12, lineHeight: 1.7 }}>
            {typeof plan.total === 'number' && <div>{S.planBalance(Number(plan.balance ?? 0), plan.total)}</div>}
            {plan.finished_at && (
              <div>
                {(renews(plan) ? S.renews : S.periodEnds)(String(plan.finished_at).slice(0, 10))}
              </div>
            )}
          </div>
        )}
        <p style={{ ...muted, fontSize: 12, lineHeight: 1.5 }}>{S.creditsSeparate}</p>
        <Button onClick={() => open(data.links.plans)} variant="secondary">
          {S.plans}
        </Button>
      </div>
    </div>
  )
}
