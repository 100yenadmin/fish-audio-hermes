// Small shared pieces of the Voices page.
import { Button, Codicon, ErrorState, Skeleton, usePluginI18n } from '@hermes/plugin-sdk'
import type { CSSProperties } from 'react'

import { errorText } from './api'
import { PLUGIN_ID } from './strings'

export const muted: CSSProperties = { color: 'var(--ui-text-tertiary)' }
export const card: CSSProperties = {
  background: 'var(--ui-bg-card)',
  border: '1px solid var(--ui-stroke-tertiary)',
  borderRadius: 6,
  padding: 16
}

export function Rows({ n = 4 }: { n?: number }) {
  const t = usePluginI18n(PLUGIN_ID)
  return (
    <div aria-busy="true" style={{ display: 'grid', gap: 10, padding: '0 24px' }}>
      {Array.from({ length: n }, (_, i) => (
        <Skeleton key={i} style={{ height: 44 }} />
      ))}
    </div>
  )
}

export function BilledNote({ text }: { text: string }) {
  const t = usePluginI18n(PLUGIN_ID)
  return (
    <p style={{ ...muted, alignItems: 'center', display: 'flex', fontSize: 12, gap: 6, margin: 0 }}>
      <Codicon name="info" />
      {text}
    </p>
  )
}

export function LoadError({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const t = usePluginI18n(PLUGIN_ID)
  return (
    <ErrorState description={errorText(error)} title={t('loadFailed')}>
      <Button onClick={onRetry} size="xs" variant="secondary">
        {t('retry')}
      </Button>
    </ErrorState>
  )
}
