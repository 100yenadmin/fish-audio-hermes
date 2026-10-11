import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { createTestContext, resetHost, resetQueryCache, setLocale } from './sdk-mock'
import { $account, $available, $availableError, $tab, ApiError, bindContext, call } from '../api'
import { cloneVoice } from '../upload'
import { $playing, releasePlayback } from '../audio'
import { VoicesPage, Onboarding } from '../page'
import { SettingsPage } from '../settings-page'
import { compact, number, usd } from '../format'
import { clamp } from '../limits'
import { en } from '../locales/en'
import { de } from '../locales/de'
import { ja } from '../locales/ja'
import { ar } from '../locales/ar'
import { ru } from '../locales/ru'
import { zh } from '../locales/zh'
import { zhHant } from '../locales/zh-hant'

const bundles = { en, de, ja, ar }
const voice = { id: 'a'.repeat(32), title: 'Narrator', author: 'fish', description: 'A long voice description' }
const empty = { ok: true, items: [voice], total: 1, page: 1 }
const account = { ok: true, credit: '12.35', cumulative_top_up: '12.35', package: null, links: {}, low: false }
const settings = { ok: true, voice: null, model: null, models: [], managed: [], aliases: [{ name: 'narrator', voice: voice.id }] }
const flush = async () => { for (let i = 0; i < 5; i++) await act(() => new Promise(resolve => setTimeout(resolve, 0))) }
const contexts: ReturnType<typeof createTestContext>[] = []
function mount(locale: keyof typeof bundles, component = <VoicesPage />) {
  const rest = vi.fn(async (path: string) => path === '/settings' ? settings : path === '/account' ? account :
    path === '/design' ? { ok: true, candidates: [{ design_token: 'test', index: 0, mime: 'audio/mpeg' }] } : empty)
  const context = createTestContext({ rest })
  contexts.push(context)
  context.ctx.i18n.register(bundles)
  bindContext(context.ctx as any)
  $available.set({ key: true, account: true, version: '1.5.0' })
  setLocale(locale)
  render(component)
  return rest
}
afterEach(() => {
  cleanup()
  contexts.splice(0).forEach(c => c.dispose())
  releasePlayback()
  resetHost()
  resetQueryCache()
  $available.set(null)
  $availableError.set(null)
  $account.set(null)
  $tab.set('library')
})

it.each(['en', 'ja', 'ar'] as const)('%s renders joined voice names from the bundle', async locale => {
  mount(locale)
  await flush()
  const t = bundles[locale]
  expect(screen.getByRole('button', { name: t.previewVoice('Narrator') })).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: t.favouriteVoice('Narrator') }))
  expect(screen.getByRole('button', { name: t.unfavouriteVoice('Narrator') })).toBeTruthy()
  act(() => $playing.set(`preview:conn-1::default:${voice.id}`))
  expect(screen.getByRole('button', { name: t.stopVoice('Narrator') })).toBeTruthy()
  fireEvent.click(screen.getByRole('tab', { name: t.tabs.mine }))
  await flush()
  expect(screen.getByRole('button', { name: t.deleteVoice('Narrator') })).toBeTruthy()
})

it.each(['en', 'ja', 'ar'] as const)('%s renders the save name in locale order', async locale => {
  mount(locale)
  fireEvent.click(screen.getByRole('tab', { name: bundles[locale].tabs.create }))
  fireEvent.change(screen.getByRole('textbox', { name: bundles[locale].designTitle }), { target: { value: 'warm voice' } })
  fireEvent.click(screen.getByRole('button', { name: bundles[locale].design }))
  await flush()
  const field = screen.getByRole('textbox', { name: bundles[locale].saveAsN(1) })
  expect(field.getAttribute('maxlength')).toBeNull()
})

it.each(['en', 'ja', 'ar'] as const)('%s renders the remove alias name and its input limits', async locale => {
  mount(locale, <SettingsPage />)
  await flush()
  const t = bundles[locale]
  expect(screen.getByRole('button', { name: t.settings.removeAlias('narrator') })).toBeTruthy()
  expect(screen.getByRole('textbox', { name: t.settings.name }).getAttribute('maxlength')).toBe('32')
  expect(screen.getByRole('textbox', { name: t.settings.voice }).getAttribute('maxlength')).toBe('128')
})

it('pins the English names and Japanese and Arabic word order independently', () => {
  expect([en.previewVoice('A'), en.stopVoice('A'), en.deleteVoice('A'), en.saveAsN(1), en.settings.removeAlias('A')])
    .toEqual(['Preview A', 'Stop A', 'Delete A', 'Name 1', 'Remove A'])
  expect([en.favouriteVoice('A'), en.unfavouriteVoice('A')]).toEqual(['Favourite A', 'Unfavourite A'])
  expect([ja.previewVoice('A'), ja.deleteVoice('A'), ja.saveAsN(1), ja.settings.removeAlias('A')])
    .toEqual(['「A」を試聴', '「A」を削除', '候補 1 の名前', 'A を削除'])
  expect([ar.previewVoice('A'), ar.favouriteVoice('A'), ar.saveAsN(1), ar.settings.removeAlias('A')])
    .toEqual(['معاينة ⁨A⁩', 'إضافة ⁨A⁩ إلى المفضلة', 'اسم المرشح ⁨1⁩', 'إزالة ⁨A⁩'])
})

it.each(['en', 'ja', 'de'] as const)('%s renders the top-up line without a page-owned colon', async locale => {
  mount(locale)
  fireEvent.click(screen.getByRole('tab', { name: bundles[locale].tabs.account }))
  await flush()
  const expected = { en: 'Lifetime top-ups: $12.35', ja: '累計チャージ額：$12.35', de: 'Aufladungen insgesamt: 12,35 $' }
  expect(screen.getByText(expected[locale], { normalizer: text => text })).toBeTruthy()
})

it('preserves CJK top-up punctuation and ungrouped English currency bytes', () => {
  expect(zh.topUpsLine('12.35')).toBe('累计充值：US$12.35')
  expect(zhHant.topUpsLine('12.35')).toBe('累計儲值：US$12.35')
  // Arabic keeps "US$" whole in a right-to-left line.
  expect(ar.usd('12.35')).toBe('\u200f12.35\u00a0\u2068US$\u2069')
  expect(ar.topUpsLine('12.35')).toBe('إجمالي الشحنات منذ إنشاء الحساب: \u2068\u200f12.35\u00a0\u2068US$\u2069\u2069')
  expect(en.usd('1234.5')).toBe('$1234.50')
  expect(en.topUpsLine('1234.5')).toBe('Lifetime top-ups: $1234.50')
})

it.each(['en', 'de', 'ja', 'ar', 'ru'])('%s formats compact, currency and grouped numbers through Intl', tag => {
  expect(compact(tag, 1500)).toBe(new Intl.NumberFormat(tag, { notation: 'compact', maximumFractionDigits: 1 }).format(1500))
  expect(usd(tag, '12.35')).toBe(new Intl.NumberFormat(tag, { style: 'currency', currency: 'USD' }).format(12.35))
  expect(number(tag, 1234567)).toBe(new Intl.NumberFormat(tag).format(1234567))
})
it('selects count forms for the number shown, not the raw count', () => {
  expect([en.uses(1), en.uses(1500)]).toEqual(['1 use', '1.5k uses'])
  expect([ru.uses(1), ru.uses(2), ru.uses(5), ru.uses(21), ru.uses(1500)])
    .toEqual(['1 использование', '2 использования', '5 использований', '21 использование', '1,5\u00a0тыс. использований'])
  // 1001 and 21000 show as "1 тыс." and "21 тыс.": the noun follows тысяча, not "1" or "21".
  expect([ru.uses(1001), ru.uses(21000), ru.uses(2_000_001)])
    .toEqual(['1\u00a0тыс. использований', '21\u00a0тыс. использований', '2\u00a0млн использований'])
  // Arabic: 1011 shows as one thousand, so it takes the noun form after ألف, not the "many" form of 1011.
  expect(ar.uses(1011)).toBe(ar.uses(1000))
  expect(ar.uses(1000)).toMatch(/ استخدام$/)
  expect(ar.uses(11)).toMatch(/ استخدامًا$/)
})

it('clamps by code points, as the gateway counts', () => {
  expect(clamp('a🐟b', 2)).toBe('a🐟')
  expect(clamp('abc', 5)).toBe('abc')
  expect(clamp('🐟'.repeat(3), 3)).toBe('🐟'.repeat(3))
})
it('limits search, clone metadata, design instruction and save names to the gateway limits', async () => {
  // Emoji are two UTF-16 units each: a maxLength attribute would stop at half the limit, and the host SearchField drops it.
  const fill = (name: string, limit: number) => {
    const box = screen.getByRole('textbox', { name }) as HTMLInputElement
    expect(box.getAttribute('maxlength')).toBeNull()
    fireEvent.change(box, { target: { value: '🐟'.repeat(limit + 5) } })
    expect(box.value).toBe('🐟'.repeat(limit))
  }
  mount('en')
  fill(en.search, 100)
  fireEvent.click(screen.getByRole('tab', { name: en.tabs.create }))
  fill(en.voiceTitle, 100)
  fill(en.descriptionOptional, 500)
  fill(en.designTitle, 500)
  fireEvent.click(screen.getByRole('button', { name: en.design }))
  await flush()
  fill(en.saveAsN(1), 100)
})

it('refuses a zero-byte pick before dispatch and retranslates its notice', async () => {
  const rest = mount('en')
  fireEvent.click(screen.getByRole('tab', { name: en.tabs.create }))
  const input = document.querySelector('input[type=file]')!
  const before = rest.mock.calls.length
  fireEvent.change(input, { target: { files: [new File([], 'empty.wav')] } })
  expect(screen.getByRole('status').textContent).toBe('empty.wav is empty.')
  expect(screen.getByRole('button', { name: en.clone }).getAttribute('disabled')).not.toBeNull()
  await act(() => setLocale('ja'))
  expect(screen.getByRole('status').textContent).toBe('empty.wav は空のファイルです。')
  expect(rest.mock.calls.length).toBe(before)
})

it('clamps details to two lines, isolates the title and uses logical onboarding padding', async () => {
  mount('ar')
  await flush()
  const title = screen.getByText('Narrator')
  expect(title.tagName).toBe('BDI')
  expect(title.parentElement!.getAttribute('dir')).toBeNull()
  const details = title.parentElement!.nextElementSibling as HTMLElement
  expect(details.style.display).toBe('-webkit-box')
  expect(details.style.webkitLineClamp).toBe('2')
  expect(details.style.webkitBoxOrient).toBe('vertical')
  expect(details.querySelectorAll('bdi')).toHaveLength(2)
  render(<Onboarding profile="test" operator={false} />)
  expect(screen.getByRole('list').style.paddingInlineStart).toBe('20px')
  expect(ar.onboardStep2).toContain('◂')
})

it('carries structured Fish detail through ordinary calls and uploads without parsing the message', async () => {
  const detail = { request_id: 'r', trace_id: 't', code: 'quota', hint: 'paid_model' }
  const context = createTestContext({ rest: async () => ({ ok: false, kind: 'quota', message: 'Gateway text', detail }) })
  contexts.push(context)
  bindContext(context.ctx as any)
  for (const operation of [() => call('/preview'), () => cloneVoice([new Blob(['audio'])],
    { title: 'Test', consent: true }, { connectionId: 'test', profile: 'test' }, {
      current: () => ({ connectionId: 'test', profile: 'test' }), rest: context.ctx.rest })]) {
    try {
      await operation()
      throw new Error('Expected gateway failure')
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError)
      expect(error).toMatchObject({ kind: 'quota', message: 'Gateway text', detail })
    }
  }
})
