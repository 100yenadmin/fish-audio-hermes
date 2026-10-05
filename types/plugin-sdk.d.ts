// Minimal hand-written types for the parts of Hermes Desktop's '@hermes/plugin-sdk' this plugin uses (copied
// from hermes-cloud-file-manager and extended).
// The SDK is not published as a package; `npm run check:sdk` verifies every name imported from it is
// really exported by the pinned upstream and fork SDKs (apps/desktop/src/sdk/index.ts).
declare module '@hermes/plugin-sdk' {
  import type { ComponentType, ReactNode } from 'react'

  export interface ReadableAtom<T> {
    get(): T
    /** Calls the listener at once with the current value, then on every change (Nano Stores). */
    subscribe(listener: (value: T) => void): () => void
    /** Change-only: no immediate call. */
    listen(listener: (value: T) => void): () => void
  }
  export interface WritableAtom<T> extends ReadableAtom<T> {
    set(value: T): void
  }
  export function atom<T>(initial: T): WritableAtom<T>
  export function useValue<T>(store: ReadableAtom<T>): T

  export interface Contribution<D = unknown> {
    id: string
    area: string
    title?: string
    order?: number
    render?: (props?: any) => ReactNode
    data?: D
  }

  export interface PluginRestOptions {
    method?: string
    body?: unknown
    timeoutMs?: number
  }

  export interface PluginOs {
    writeClipboard(text: string): Promise<boolean>
    openExternal(url: string): Promise<boolean>
  }

  export interface PluginStorage {
    get<T>(key: string, fallback: T): T
    set(key: string, value: unknown): void
    remove(key: string): void
  }

  export interface PluginContext {
    register(contribution: Contribution<any>): () => void
    onDispose(fn: () => void): void
    /** Both return a cancel function (not a timer id). */
    setTimeout(fn: () => void, ms: number): () => void
    setInterval(fn: () => void, ms: number): () => void
    rest<T = unknown>(path: string, opts?: PluginRestOptions): Promise<T>
    os: PluginOs
    storage: PluginStorage
  }

  export interface HermesPlugin {
    id: string
    name?: string
    description?: string
    defaultEnabled?: boolean
    register(ctx: PluginContext): void | Promise<void>
  }

  export interface FocusedSessionOwner {
    connectionId: null | string
    profile: string
  }

  export const host: {
    state: {
      connectionId: ReadableAtom<null | string>
      profile: ReadableAtom<string>
      focusedSessionOwner: ReadableAtom<FocusedSessionOwner | null>
      activeSessionId: ReadableAtom<null | string>
    }
    notify(note: { kind?: 'info' | 'success' | 'warning' | 'error'; title?: string; message: string }): void
    navigate(path: string): void
  }

  export const ROUTES_AREA: 'routes'
  export const PALETTE_AREA: 'palette'
  export const STATUSBAR_AREAS: { readonly left: 'statusBar.left'; readonly right: 'statusBar.right' }
  export interface PaletteContribution {
    id: string
    label: string
    keywords?: string[]
    run: () => void
    detail?: () => string
  }
  export const SIDEBAR_NAV_AREA: 'sidebar.nav'
  export const COMPOSER_AREAS: {
    readonly underside: 'composer.underside'
    readonly attachments: 'composer.attachments'
    readonly [key: string]: string
  }

  export interface RouteContribution {
    path: string
  }
  export interface SidebarNavContribution {
    codicon: string
    label: string
    path: string
    tier?: 'advanced'
  }
  export interface ComposerAttachmentContext {
    insertText(text: string): void
  }
  export interface ComposerAttachmentProvider {
    label: string
    icon?: string
    run(ctx: ComposerAttachmentContext): void | Promise<void>
  }

  // React Query (the app's own client and provider). Only the slice this plugin uses.
  export interface UseQueryResult<T> {
    data: T | undefined
    error: unknown
    isFetching: boolean
    refetch(): Promise<unknown>
  }
  export function useQuery<T>(options: {
    queryKey: readonly unknown[]
    queryFn: () => Promise<T>
    enabled?: boolean
    retry?: boolean | number
    staleTime?: number
    placeholderData?: (previousData: T | undefined, previousQuery: { queryKey: readonly unknown[] } | undefined) => T | undefined
  }): UseQueryResult<T>
  export function useQueryClient(): {
    invalidateQueries(filters: { queryKey: readonly unknown[] }): Promise<void>
    getQueryData<T = unknown>(queryKey: readonly unknown[]): T | undefined
  }

  // UI kit: loosely typed on purpose; the app owns the real props.
  export const Badge: ComponentType<any>
  export const Button: ComponentType<any>
  export const Checkbox: ComponentType<any>
  export const Codicon: ComponentType<any>
  export const Dialog: ComponentType<any>
  export const DialogContent: ComponentType<any>
  export const DialogFooter: ComponentType<any>
  export const DialogHeader: ComponentType<any>
  export const DialogTitle: ComponentType<any>
  export const EmptyState: ComponentType<any>
  export const ErrorState: ComponentType<any>
  export const Input: ComponentType<any>
  export const SearchField: ComponentType<any>
  export const Select: ComponentType<any>
  export const SelectContent: ComponentType<any>
  export const SelectItem: ComponentType<any>
  export const SelectTrigger: ComponentType<any>
  export const SelectValue: ComponentType<any>
  export const Skeleton: ComponentType<any>
  export const Streamdown: ComponentType<any>
  export const SegmentedControl: ComponentType<any>
  export const Textarea: ComponentType<any>
}
