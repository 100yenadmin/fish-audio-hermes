// Locale tags are owned by each message bundle, never by the machine's locale.
export const compact = (tag: string, n: number) => new Intl.NumberFormat(tag, { notation: 'compact', maximumFractionDigits: 1 }).format(n)
export const usd = (tag: string, value: number | string) => new Intl.NumberFormat(tag, { style: 'currency', currency: 'USD' }).format(Number(value))
export const number = (tag: string, n: number) => new Intl.NumberFormat(tag).format(n)

export const plural = (tag: string, n: number, forms: Partial<Record<Intl.LDMLPluralRule, string>> & { other: string }) =>
  forms[new Intl.PluralRules(tag).select(n)] ?? forms.other
