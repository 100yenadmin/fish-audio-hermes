import { usePluginI18n, useValue } from '@hermes/plugin-sdk'
import { $available } from './api'

export const PLUGIN_ID = 'fish-audio'
export const PAGE_PATH = '/fish-audio'

/** Where the host renders the API key field for `profile`: Settings ▸ Plugins ▸ Fish Audio ▸ Agent settings. `plugin=`
 *  keeps the redirect on hosts without Settings ▸ Plugins pointed at this row; Settings adopts `profile=` as its scope,
 *  so the key is saved for the agent the user is looking at. A string literal on purpose (runbook §3a). */
export function keySettingsPath(profile: string): string {
  const base = '/settings?tab=plugins&agent=fish-audio&plugin=fish-audio'
  return profile ? `${base}&profile=${encodeURIComponent(profile)}` : base
}

export function useAccountText(normal: string, operator: string) {
  const t = usePluginI18n(PLUGIN_ID)
  const available = useValue($available)
  return t(available && available.account === false ? operator : normal)
}

export const LINKS = {
  keys: 'https://fish.audio/app/api-keys',
  discovery: 'https://fish.audio/discovery',
  account: 'https://fish.audio/app/developers/billing'
}

// Voice-language filter codes; each locale bundle names them under `languages`.
export const LANGUAGES = ['en', 'zh', 'ja', 'ko', 'es', 'fr', 'de', 'it', 'pt', 'ru', 'ar', 'nl', 'pl'] as const
