// Mirror dashboard/plugin_api.py (_text, list_voices) and settings.py (ALIAS_RE, resolve_voice).
// The gateway counts code points (Python len); an input's maxLength counts UTF-16 units and would stop emoji text early.
export const clamp = (value: string, limit: number) => (value.length <= limit ? value : Array.from(value).slice(0, limit).join(''))

export const LIMITS = { title: 100, description: 500, instruction: 500, sampleText: 500, aliasName: 32, aliasVoice: 128, search: 100 } as const
