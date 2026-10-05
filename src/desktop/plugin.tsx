import {
  type HermesPlugin,
  host,
  PALETTE_AREA,
  type PaletteContribution,
  type PluginContext,
  type RouteContribution,
  ROUTES_AREA,
  SIDEBAR_NAV_AREA,
  type SidebarNavContribution,
  STATUSBAR_AREAS,
  useValue
} from '@hermes/plugin-sdk'

import { $account, $available, $tab, type Account, bindContext, isNotFoundError, setRefresher } from './api'
import { VoicesPage } from './page'
import { S } from './strings'

export const PLUGIN_ID = 'fish-audio'
export const PAGE_PATH = '/fish-audio'
const PROBE_INTERVAL_MS = 60_000
const ACCOUNT_REFRESH_MS = 5 * 60_000

export { isNotFoundError }

export function openTab(tab: ReturnType<typeof $tab.get>) {
  $tab.set(tab)
  host.navigate(PAGE_PATH)
}

/** The status-bar credit chip: the selected agent's Fish API credit, in warning colour when low. */
function CreditChip() {
  const account = useValue($account)
  if (!account) return null
  return (
    <button
      aria-label={S.chipTip}
      onClick={() => openTab('account')}
      style={{
        alignItems: 'center',
        background: 'transparent',
        border: 0,
        color: account.low ? 'var(--ui-orange)' : 'var(--ui-text-tertiary)',
        cursor: 'pointer',
        display: 'inline-flex',
        fontSize: '0.6875rem',
        fontVariantNumeric: 'tabular-nums',
        gap: 4,
        height: '100%',
        padding: '0 6px'
      }}
      title={account.low ? S.lowCredit : S.chipTip}
      type="button"
    >
      {S.chip(S.usd(account.credit))}
    </button>
  )
}

/** The Voices route is always registered (a restored /fish-audio never falls through to the session route).
 *  The sidebar row, status-bar chip and palette commands appear only while the selected agent's gateway answers
 *  /available. Re-probe when the agent changes and every 60 s; remove them only on a definite 404. A generation
 *  counter makes only the newest probe count, so a late answer about the previous agent changes nothing. */
export function registerAvailabilityGate(ctx: PluginContext) {
  let removers: Array<() => void> | null = null
  let disposed = false
  let generation = 0
  let accountAt = 0

  const show = (available: boolean) => {
    if (disposed) return
    if (available && !removers) {
      removers = [
        ctx.register({
          id: 'nav',
          area: SIDEBAR_NAV_AREA,
          order: 46,
          data: { codicon: 'unmute', label: S.navLabel, path: PAGE_PATH } satisfies SidebarNavContribution
        }),
        ctx.register({ id: 'credit', area: STATUSBAR_AREAS.right, order: 70, render: () => <CreditChip /> }),
        ctx.register({
          id: 'palette-voices',
          area: PALETTE_AREA,
          data: { id: 'fish-audio.voices', keywords: ['fish', 'voice', 'tts'], label: S.paletteVoices, run: () => openTab('library') } satisfies PaletteContribution
        }),
        ctx.register({
          id: 'palette-account',
          area: PALETTE_AREA,
          data: { id: 'fish-audio.account', keywords: ['fish', 'credit', 'balance'], label: S.paletteAccount, run: () => openTab('account') } satisfies PaletteContribution
        })
      ]
    } else if (!available && removers) {
      removers.forEach(remove => remove())
      removers = null
    }
  }

  const probe = (force = false) => {
    const mine = ++generation
    return ctx.rest<{ key?: boolean; version?: string }>('/available').then(
      res => {
        if (mine !== generation || disposed) return
        $available.set({ key: res?.key === true, version: String(res?.version ?? '') })
        show(true)
        if (res?.key !== true) {
          $account.set(null)
          return
        }
        // Throttle only once this agent's wallet is known: a forced read dropped as stale must not hide it for 5 min.
        if (!force && $account.get() !== null && Date.now() - accountAt < ACCOUNT_REFRESH_MS) return
        accountAt = Date.now()
        return ctx.rest<Account | { ok: false }>('/account').then(
          account => {
            if (mine === generation && !disposed) $account.set(account?.ok ? (account as Account) : null)
          },
          () => undefined
        )
      },
      error => {
        if (mine !== generation || disposed || !isNotFoundError(error)) return
        $available.set(false)
        $account.set(null)
        show(false)
      }
    )
  }

  void probe(true)
  ctx.setInterval(() => void probe(), PROBE_INTERVAL_MS)
  // listen, not subscribe: Nano Stores' subscribe also fires at once, which would triple the first probe.
  // A new agent starts unknown: one agent's wallet or key state never carries over to another.
  const onAgentChange = () => {
    if (disposed) return
    $account.set(null)
    $available.set(null)
    void probe(true)
  }
  const stops = [host.state.profile.listen(onAgentChange), host.state.connectionId.listen(onAgentChange)]
  ctx.onDispose(() => {
    disposed = true
    stops.forEach(stop => stop())
  })
  return { probe }
}

const plugin: HermesPlugin = {
  id: PLUGIN_ID,
  name: 'Fish Audio',
  description: 'A Voices page for Fish Audio: search and preview the voice library, use a voice, clone or design voices, and see your account.',
  register(ctx) {
    bindContext(ctx)
    ctx.register({
      id: 'page',
      area: ROUTES_AREA,
      data: { path: PAGE_PATH } satisfies RouteContribution,
      render: () => <VoicesPage />
    })
    setRefresher(registerAvailabilityGate(ctx).probe)
  }
}

export default plugin
