import { usePluginI18n, useValue } from '@hermes/plugin-sdk'
import { $available } from './api'

export const PLUGIN_ID = 'fish-audio'
export const PAGE_PATH = '/fish-audio'

export function useAccountText(normal: string, operator: string) {
  const t = usePluginI18n(PLUGIN_ID)
  const available = useValue($available)
  return t(available && available.account === false ? operator : normal)
}

export const LINKS = {
  keys: 'https://fish.audio/app/api-keys',
  discovery: 'https://fish.audio/discovery'
}

// Voice-language filter codes; each locale bundle names them under `languages`.
export const LANGUAGES = ['en', 'zh', 'ja', 'ko', 'es', 'fr', 'de', 'it', 'pt', 'ru', 'ar', 'nl', 'pl'] as const
