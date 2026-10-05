import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'

import { createTestContext, resetHost, resetQueryCache } from './sdk-mock'
import { $available, $tab, bindContext } from '../api'
import { VoicesPage } from '../page'

const flush = async () => {
  for (let i = 0; i < 5; i++) await act(() => new Promise(resolve => setTimeout(resolve, 0)))
}
const VOICES = { ok: true, page: 1, total: 1, items: [{ id: 'a'.repeat(32), title: 'Narrator', author: 'fish', languages: ['en'], task_count: 1200 }] }

function mount(rest: (path: string, opts?: any) => Promise<any>) {
  const calls: Array<{ path: string; body?: any }> = []
  const t = createTestContext({ rest: async (path, opts) => (calls.push({ path, body: opts?.body }), rest(path, opts)) })
  bindContext(t.ctx as any)
  render(<VoicesPage />)
  return { calls, t }
}

afterEach(() => {
  cleanup()
  resetHost()
  resetQueryCache()
  $available.set(null)
  $tab.set('library')
})

describe('Voices page', () => {
  it('shows the onboarding card, not the tabs, when the agent has no key', async () => {
    $available.set({ key: false, version: '0.3.0' })
    const { calls } = mount(async () => ({ ok: true }))
    expect(screen.getByText('Connect your Fish Audio account')).toBeTruthy()
    expect(screen.getByText('Get an API key')).toBeTruthy()
    expect(screen.queryByRole('tab')).toBeNull()
    expect(calls).toEqual([])
  })

  it('searches the library, previews with a billed note, uses and stars a voice', async () => {
    $available.set({ key: true, version: '0.3.0' })
    const { calls, t } = mount(async path =>
      path.startsWith('/voices') ? VOICES : path === '/preview' ? { ok: true, audio: 'SUQz', mime: 'audio/mpeg' } : { ok: true, message: 'Saved.' }
    )
    await flush()
    expect(screen.getByText('Narrator')).toBeTruthy()
    expect(screen.getAllByText(/billed to your Fish Audio account/).length).toBeGreaterThan(0)
    expect(calls[0].path).toBe('/voices?page=1')
    fireEvent.click(screen.getByText('Use'))
    await flush()
    expect(calls.at(-1)).toEqual({ path: '/use', body: { voice: 'a'.repeat(32) } })
    expect(screen.getByText('In use')).toBeTruthy()
    fireEvent.click(screen.getByLabelText('Favourite'))
    expect(t.stored.get('favourites:conn-1::default')).toEqual([{ author: 'fish', id: 'a'.repeat(32), languages: ['en'], title: 'Narrator' }])
  })

  it('shows the not-installed note on a definite 404', () => {
    $available.set(false)
    mount(async () => ({ ok: true }))
    expect(screen.getByText(/isn't set up on this agent's machine/)).toBeTruthy()
  })

  it('switches tabs from the tab list', async () => {
    $available.set({ key: true, version: '0.3.0' })
    mount(async path => (path.startsWith('/voices') ? { ...VOICES, items: [] } : { ok: true }))
    fireEvent.click(screen.getByRole('tab', { name: 'Create' }))
    await flush()
    expect(screen.getByText('Clone a voice')).toBeTruthy()
    expect(screen.getByText('Design a voice')).toBeTruthy()
    expect((screen.getByText('Clone voice') as HTMLButtonElement).disabled).toBe(true)
  })
})
