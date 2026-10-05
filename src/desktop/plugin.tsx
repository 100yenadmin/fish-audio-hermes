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

import { releasePlayback } from './audio'
import { $account, $available, $availableError, $tab, type Account, bindContext, currentAgentEpoch, endAgentOperations, isNotFoundError, setRefresher } from './api'
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
 *  /available. Re-probe when the agent changes and every 60 s. For the same agent they go away only on a definite 404,
 *  so a transient error never flaps them; a new agent starts hidden until its own gateway answers. A generation
 *  counter makes only the newest probe count, so a late answer about the previous agent changes nothing. */
export function registerAvailabilityGate(ctx: PluginContext) {
  let removers: Array<() => void> | null = null
  let accountRemovers: Array<() => void> | null = null
  let retries = 0
  let cancelRetry: (() => void) | undefined
  let disposed = false
  let generation = 0
  let accountAt = 0
  let forcePending = false
  // The stores outlive a disable: a re-enable may face another agent, so start unknown, never with the last answer.
  $available.set(null)
  $account.set(null)
  $availableError.set(null)

  const show = (available: boolean, account = false) => {
    if (disposed) return
    if (available && account && !accountRemovers) {
      accountRemovers = [
        ctx.register({ id: 'credit', area: STATUSBAR_AREAS.right, order: 70, render: () => <CreditChip /> }),
        ctx.register({ id: 'palette-account', area: PALETTE_AREA,
          data: { id: 'fish-audio.account', keywords: ['fish', 'credit', 'balance'], label: S.paletteAccount, run: () => openTab('account') } satisfies PaletteContribution })
      ]
    } else if ((!available || !account) && accountRemovers) {
      accountRemovers.forEach(remove => remove())
      accountRemovers = null
    }
    if (available && !removers) {
      removers = [
        ctx.register({
          id: 'nav',
          area: SIDEBAR_NAV_AREA,
          order: 46,
          data: { codicon: 'unmute', label: S.navLabel, path: PAGE_PATH } satisfies SidebarNavContribution
        }),
        ctx.register({
          id: 'palette-voices',
          area: PALETTE_AREA,
          data: { id: 'fish-audio.voices', keywords: ['fish', 'voice', 'tts'], label: S.paletteVoices, run: () => openTab('library') } satisfies PaletteContribution
        })
      ]
    } else if (!available && removers) {
      removers.forEach(remove => remove())
      removers = null
    }
  }

  const probe = (force = false) => {
    if (force) {
      forcePending = true
      $availableError.set(null)  // a forced probe (Check again, a new agent) shows loading, not the last failure
    }
    const mine = ++generation
    return ctx.rest<{ key?: boolean; version?: string; account?: boolean }>('/available').then(
      res => {
        if (mine !== generation || disposed) return
        cancelRetry?.()
        $availableError.set(null)
        $available.set({ key: res?.key === true, version: String(res?.version ?? ''), account: res?.account !== false })
        show(true, res?.account !== false)
        if (res?.key !== true || res?.account === false) {
          $account.set(null)
          return
        }
        // Throttle only once this agent's wallet is known: a forced read dropped as stale must not hide it for 5 min.
        if (!forcePending && $account.get() !== null && Date.now() - accountAt < ACCOUNT_REFRESH_MS) return
        accountAt = Date.now()
        return ctx.rest<Account | { ok: false }>('/account').then(
          account => {
            if (mine === generation && !disposed) {
              forcePending = false
              $account.set(account?.ok ? (account as Account) : null)
            }
          },
          () => undefined
        )
      },
      error => {
        if (mine !== generation || disposed) return
        if (!isNotFoundError(error)) {
          // Still unknown with no answer: the page shows the failure and Check again, not an endless skeleton.
          // A known agent keeps its entries: a transient error never hides them.
          if ($available.get() === null) {
            $availableError.set(error)
            if (!cancelRetry && retries < 3) {
              const epoch = currentAgentEpoch()
              cancelRetry = ctx.setTimeout(() => {
                if (disposed || epoch !== currentAgentEpoch()) return
                cancelRetry = undefined
                if ($available.get() === null) void probe()
              }, [5_000, 15_000, 30_000][retries++])
            }
          }
          return
        }
        cancelRetry?.()
        $availableError.set(null)
        $available.set(false)
        $account.set(null)
        show(false)
      }
    )
  }

  void probe(true)
  ctx.setInterval(() => void probe(), PROBE_INTERVAL_MS)
  // listen, not subscribe: Nano Stores' subscribe also fires at once, which would triple the first probe.
  // A new agent starts unknown and hidden: one agent's entries, wallet or key state never carry over to another.
  const onAgentChange = () => {
    if (disposed) return
    endAgentOperations()
    cancelRetry?.()
    cancelRetry = undefined
    retries = 0
    $account.set(null)
    $available.set(null)
    show(false)
    void probe(true)
  }
  const stops = [host.state.profile.listen(onAgentChange), host.state.connectionId.listen(onAgentChange)]
  ctx.onDispose(() => {
    disposed = true
    cancelRetry?.()
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
    ctx.onDispose(releasePlayback)
    ctx.onDispose(endAgentOperations)
  }
}

export default plugin
