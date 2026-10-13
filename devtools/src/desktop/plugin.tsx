import {
  type HermesPlugin,
  host,
  PALETTE_AREA,
  type PaletteContribution,
  type PluginContext,
  type PluginLocaleBundles,
  type RouteContribution,
  ROUTES_AREA,
  SIDEBAR_NAV_AREA,
  type SidebarNavContribution,
  STATUSBAR_AREAS,
  usePluginI18n,
  useValue
} from '@hermes/plugin-sdk'

import { releasePlayback } from './audio'
import { $account, $available, $availableError, $tab, type Account, bindContext, currentAgentEpoch, endAgentOperations, isNotFoundError, setRefresher } from './api'
import { VoicesPage } from './page'
import { SettingsPage } from './settings-page'
import { en } from './locales/en'
import { zh } from './locales/zh'
import { zhHant } from './locales/zh-hant'
import { ja } from './locales/ja'
import { ar } from './locales/ar'
import { ru } from './locales/ru'
import { fr } from './locales/fr'
import { de } from './locales/de'
import { es } from './locales/es'
import { PAGE_PATH, PLUGIN_ID } from './strings'
export { PAGE_PATH, PLUGIN_ID } from './strings'

const PROBE_INTERVAL_MS = 60_000
const ACCOUNT_REFRESH_MS = 5 * 60_000

export { isNotFoundError }

export function openTab(tab: ReturnType<typeof $tab.get>) {
  $tab.set(tab)
  host.navigate(PAGE_PATH)
}

/** The status-bar credit chip: the selected agent's Fish API credit, in warning colour when low. */
function CreditChip() {
  const t = usePluginI18n(PLUGIN_ID)
  const account = useValue($account)
  if (!account) return null
  return (
    <button
      aria-label={t('chipTip')}
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
      title={account.low ? t('lowCredit') : t('chipTip')}
      type="button"
    >
      {t('chip', t('usd', account.credit))}
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
    if (available && !removers) {
      removers = [
        ctx.register({
          id: 'nav',
          area: SIDEBAR_NAV_AREA,
          order: 46,
          data: { codicon: 'unmute', label: ctx.i18n.t('navLabel'), path: PAGE_PATH } satisfies SidebarNavContribution
        }),
        ctx.register({
          id: 'palette-voices',
          area: PALETTE_AREA,
          data: { id: 'fish-audio.voices', keywords: ['fish', 'voice', 'tts'], label: ctx.i18n.t('paletteVoices'), run: () => openTab('library') } satisfies PaletteContribution
        })
      ]
    } else if (!available && removers) {
      removers.forEach(remove => remove())
      removers = null
    }
    // After the nav block, so the palette lists Voices before Account as in 1.0.4.
    if (available && account && !accountRemovers) {
      accountRemovers = [
        ctx.register({ id: 'credit', area: STATUSBAR_AREAS.right, order: 70, render: () => <CreditChip /> }),
        ctx.register({ id: 'palette-account', area: PALETTE_AREA,
          data: { id: 'fish-audio.account', keywords: ['fish', 'credit', 'balance'], label: ctx.i18n.t('paletteAccount'), run: () => openTab('account') } satisfies PaletteContribution })
      ]
    } else if ((!available || !account) && accountRemovers) {
      accountRemovers.forEach(remove => remove())
      accountRemovers = null
    }
  }

  const probe = (force = false) => {
    if (force) {
      forcePending = true
      $availableError.set(null)  // a forced probe (Check again, a new agent) shows loading, not the last failure
    }
    const mine = ++generation
    return ctx.rest<{ key?: boolean; version?: string; account?: boolean; free_model?: string }>('/available').then(
      res => {
        if (mine !== generation || disposed) return
        cancelRetry?.()
        $availableError.set(null)
        const free_model = res?.free_model === 'active' || res?.free_model === 'ending' || res?.free_model === 'ended' ? res.free_model : undefined
        $available.set({ key: res?.key === true, version: String(res?.version ?? ''), account: res?.account !== false, ...(free_model && { free_model }) })
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
  const stopInterval = ctx.setInterval(() => void probe(), PROBE_INTERVAL_MS)
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
  const stops = [host.state.profile.listen(onAgentChange), host.state.connectionId.listen(onAgentChange), ctx.i18n.onLocaleChange(() => {
    if (disposed || !removers) return
    const accountShown = accountRemovers !== null
    show(false)
    show(true, accountShown)
  }), stopInterval]
  /** Stop probing and listening. `hide` also removes this gate's entries (a re-activation replacing it); a dispose
   *  leaves them to the host, which removes everything the plugin registered. */
  const end = (hide = false) => {
    if (disposed) return
    if (hide) show(false)
    disposed = true
    cancelRetry?.()
    stops.forEach(stop => stop())
  }
  ctx.onDispose(() => end())
  return { probe, end }
}

/** Settings ▸ Plugins ▸ Fish Audio (Hermes Desktop 0.21.6+; older hosts and the fork lack the member, so nothing is
 *  registered there). Registered once, unconditionally: gating it on /available would un-fold and re-fold the host's
 *  "Agent settings" form (which holds the API key) as availability flaps. Only the page body follows availability.
 *  The title is the brand in every locale, so a locale change never re-registers it: a re-registered entry loses the
 *  host's fold of "Agent settings" (seen on 0.21.6, which applies the saved language after plugins activate). The body
 *  re-renders in the new locale on its own. */
export function registerSettingsPage(ctx: PluginContext) {
  const remove = ctx.registerSettingsPage?.({ id: 'fish-audio', title: 'Fish Audio', icon: 'unmute', render: () => <SettingsPage /> })
  if (!remove) return () => undefined
  let disposed = false
  const end = () => {
    if (disposed) return
    disposed = true
    remove()
  }
  ctx.onDispose(end)
  return end
}

/** Ends the live activation. A second register without a dispose replaces the first, so listeners, probes and the
 *  settings page never stack. */
let deactivate: (() => void) | undefined

const plugin: HermesPlugin = {
  id: PLUGIN_ID,
  name: 'Fish Audio',
  description: 'A Voices page for Fish Audio: search and preview the voice library, use a voice, clone or design voices, and see your account.',
  register(ctx) {
    deactivate?.()
    ctx.i18n.register({ en, zh, 'zh-hant': zhHant, ja, ar, ru, fr, de, es } satisfies PluginLocaleBundles)
    bindContext(ctx)
    ctx.register({
      id: 'page',
      area: ROUTES_AREA,
      data: { path: PAGE_PATH } satisfies RouteContribution,
      render: () => <VoicesPage />
    })
    const endPage = registerSettingsPage(ctx)
    const gate = registerAvailabilityGate(ctx)
    setRefresher(gate.probe)
    ctx.onDispose(releasePlayback)
    ctx.onDispose(endAgentOperations)
    const mine = () => {
      endPage()
      gate.end(true)
      endAgentOperations()
    }
    deactivate = mine
    // Once disposed, its disposers must never run again: they remove by id, so they would remove a later page.
    ctx.onDispose(() => {
      if (deactivate === mine) deactivate = undefined
    })
  }
}

export default plugin
