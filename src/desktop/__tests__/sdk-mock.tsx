// Test double for Hermes Desktop's '@hermes/plugin-sdk' (aliased in vitest.config.ts).
// Only what the plugin uses; behaviour mirrors the real SDK where tests depend on it. Copied from
// hermes-cloud-file-manager and extended (palette/status-bar areas, SegmentedControl, Badge, storage with a fallback).
import { createContext, createElement, type ReactNode, useContext, useEffect, useReducer, useRef, useSyncExternalStore } from 'react'

type Listener<T> = (value: T) => void

export function atom<T>(initial: T) {
  let value = initial
  const listeners = new Set<Listener<T>>()
  return {
    get: () => value,
    set(next: T) {
      value = next
      listeners.forEach(fn => fn(value))
    },
    /** Like Nano Stores: calls the listener at once with the current value, then on every change. */
    subscribe(fn: Listener<T>) {
      listeners.add(fn)
      fn(value)
      return () => void listeners.delete(fn)
    },
    /** Like Nano Stores: change-only, no immediate call. */
    listen(fn: Listener<T>) {
      listeners.add(fn)
      return () => void listeners.delete(fn)
    }
  }
}

export function useValue<T>(store: ReturnType<typeof atom<T>>): T {
  return useSyncExternalStore(store.subscribe, store.get, store.get)
}

export const host = {
  state: {
    connectionId: atom<null | string>('conn-1'),
    profile: atom<string>('default'),
    focusedSessionOwner: atom<null | { connectionId: null | string; profile: string }>({ connectionId: 'conn-1', profile: 'default' }),
    activeSessionId: atom<null | string>('session-1')
  },
  notify: (_note: unknown) => undefined,
  navigate: (_path: string) => undefined
}

/** Put the host atoms back to the default agent (tests that switch agents call this in afterEach). */
export function resetHost() {
  host.state.connectionId.set('conn-1')
  host.state.profile.set('default')
  host.state.focusedSessionOwner.set({ connectionId: 'conn-1', profile: 'default' })
  host.state.activeSessionId.set('session-1')
}

export const ROUTES_AREA = 'routes'
export const PALETTE_AREA = 'palette'
export const STATUSBAR_AREAS = { left: 'statusBar.left', right: 'statusBar.right' } as const
export const SIDEBAR_NAV_AREA = 'sidebar.nav'
export const COMPOSER_AREAS = { underside: 'composer.underside', attachments: 'composer.attachments' } as const

// ---- React Query: a tiny keyed cache with the slice of the API the plugin uses (data/error/refetch,
// `enabled`, prefix invalidation). Real React Query is the app's; this only mirrors its observable shape.
interface CacheEntry {
  data?: unknown
  error?: unknown
  pending?: Promise<void>
  fn?: () => Promise<unknown>
  listeners: Set<() => void>
}
const cache = new Map<string, CacheEntry>()
const entryFor = (key: string) => {
  if (!cache.has(key)) cache.set(key, { listeners: new Set() })
  return cache.get(key)!
}
function fetchEntry(entry: CacheEntry): Promise<void> {
  if (!entry.fn) return Promise.resolve()
  entry.pending = entry.fn().then(
    data => {
      entry.data = data
      entry.error = undefined
    },
    error => {
      entry.error = error
    }
  )
  return entry.pending.finally(() => {
    entry.pending = undefined
    entry.listeners.forEach(fn => fn())
  })
}
export function resetQueryCache() {
  cache.clear()
}
export function useQuery<T>({ queryKey, queryFn, enabled = true, placeholderData }: {
  queryKey: readonly unknown[]; queryFn: () => Promise<T>; enabled?: boolean
  placeholderData?: (previousData: T | undefined, previousQuery: { queryKey: readonly unknown[] } | undefined) => T | undefined
}) {
  const last = useRef<{ data: T; queryKey: readonly unknown[] } | undefined>(undefined)
  const key = JSON.stringify(queryKey)
  const [, force] = useReducer((n: number) => n + 1, 0)
  const fn = useRef(queryFn)
  fn.current = queryFn
  useEffect(() => {
    const entry = entryFor(key)
    entry.listeners.add(force)
    entry.fn = () => fn.current()
    if (enabled && !('data' in entry) && !entry.error && !entry.pending) void fetchEntry(entry)
    return () => void entry.listeners.delete(force)
  }, [key, enabled])
  const entry = cache.get(key)
  if (enabled && entry?.data !== undefined) last.current = { data: entry.data as T, queryKey }
  const data = entry?.data as T | undefined
  return {
    data: enabled ? data ?? placeholderData?.(last.current?.data, last.current && { queryKey: last.current.queryKey }) : undefined,
    error: enabled ? entry?.error : undefined,
    isFetching: Boolean(entry?.pending),
    refetch: () => fetchEntry(entryFor(key))
  }
}
export function useQueryClient() {
  return {
    getQueryData: <T,>(queryKey: readonly unknown[]) => cache.get(JSON.stringify(queryKey))?.data as T | undefined,
    invalidateQueries: async ({ queryKey }: { queryKey: readonly unknown[] }) => {
      const prefix = JSON.stringify(queryKey).slice(0, -1)
      await Promise.all([...cache].filter(([key]) => key.startsWith(prefix)).map(([, entry]) => fetchEntry(entry)))
    }
  }
}

// ---- UI kit: plain elements with the props tests interact with.
const omit = (props: any, ...keys: string[]) => {
  const rest = { ...props }
  for (const key of ['children', 'variant', 'size', 'loading', ...keys]) delete rest[key]
  return rest
}
const passthrough = (tag: string) => (props: any) => createElement(tag, omit(props), props?.children)

export const Badge = passthrough('span')
export const Button = (props: any) => createElement('button', { type: 'button', ...omit(props), disabled: props.disabled || props.loading }, props.children)
export const Codicon = (props: any) => createElement('i', { 'data-codicon': props?.name })
export const EmptyState = ({ title, description }: { title: string; description?: string }) =>
  createElement('div', { 'data-testid': 'empty' }, title, description ? createElement('p', null, description) : null)
export const ErrorState = ({ title, description, children }: { title: ReactNode; description?: ReactNode; children?: ReactNode }) =>
  createElement('div', { role: 'alert' }, createElement('h2', null, title), description, children)
export const SearchField = ({ value, onChange, placeholder, 'aria-label': label }: any) =>
  createElement('input', { 'aria-label': label ?? placeholder, placeholder, value, onChange: (e: any) => onChange(e.target.value) })
export const Checkbox = ({ checked, onCheckedChange, 'aria-label': label }: any) =>
  createElement('input', { type: 'checkbox', 'aria-label': label, checked: Boolean(checked), onChange: () => onCheckedChange?.(!checked) })
export const Input = passthrough('input')
export const Skeleton = passthrough('div')
export const Streamdown = ({ children }: { children?: ReactNode }) => createElement('div', { 'data-testid': 'md' }, children)
export const Textarea = passthrough('textarea')
// SegmentedControl: one button per option, reporting its id to onChange.
export const SegmentedControl = ({ options, value, onChange }: any) =>
  createElement('div', { role: 'tablist' }, options.map((o: any) =>
    createElement('button', { key: o.id, role: 'tab', type: 'button', 'aria-selected': o.id === value, onClick: () => onChange(o.id) }, o.label)))
export const Dialog = ({ open, children }: { open?: boolean; children?: ReactNode }) => (open ? createElement('div', null, children) : null)
export const DialogContent = (props: any) => createElement('div', { role: 'dialog', ...omit(props, 'onOpenAutoFocus') }, props.children)
export const DialogHeader = passthrough('div')
export const DialogFooter = passthrough('div')
export const DialogTitle = passthrough('h2')
// Select: every item is a clickable option that reports its value to the Select's onValueChange.
const SelectCtx = createContext<{ value?: string; onValueChange?: (value: string) => void }>({})
export const Select = ({ value, onValueChange, children }: any) =>
  createElement(SelectCtx.Provider, { value: { value, onValueChange } }, createElement('div', { 'data-select': value }, children))
export const SelectContent = passthrough('div')
export const SelectItem = ({ value, children }: any) => {
  const ctx = useContext(SelectCtx)
  return createElement('div', { role: 'option', 'aria-selected': ctx.value === value, onClick: () => ctx.onValueChange?.(value) }, children)
}
export const SelectTrigger = passthrough('div')
export const SelectValue = passthrough('span')

export interface TestContextOptions {
  rest?: (path: string, opts?: any) => Promise<any>
}

/** A PluginContext double that records live contributions and lets tests drive timers manually. */
export function createTestContext(options: TestContextOptions = {}) {
  const live = new Map<string, any>()
  const intervals: Array<() => void> = []
  const timeouts: Array<{ fn: () => void; ms: number; cancelled: boolean }> = []
  const disposers: Array<() => void> = []
  const stored = new Map<string, unknown>()
  const ctx = {
    register(contribution: any) {
      live.set(contribution.id, contribution)
      return () => live.delete(contribution.id)
    },
    onDispose: (fn: () => void) => disposers.push(fn),
    setTimeout: (fn: () => void, ms: number) => {
      const timer = { fn, ms, cancelled: false }
      timeouts.push(timer)
      return () => { timer.cancelled = true }
    },
    setInterval: (fn: () => void) => (intervals.push(fn), () => undefined),
    rest: options.rest ?? (async () => ({ ok: true })),
    os: { writeClipboard: async (_text: string) => true, openExternal: async () => true },
    storage: {
      get: <T,>(key: string, fallback: T): T => (stored.has(key) ? (stored.get(key) as T) : fallback),
      set: (key: string, value: unknown) => void stored.set(key, value),
      remove: (key: string) => void stored.delete(key)
    }
  }
  return {
    ctx,
    live,
    tickIntervals: () => intervals.forEach(fn => fn()),
    stored,
    timeouts,
    fireTimeout: (index: number) => { const timer = timeouts[index]; if (!timer.cancelled) timer.fn() },
    dispose: () => disposers.forEach(fn => fn())
  }
}
