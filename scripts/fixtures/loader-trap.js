// Positive control for scripts/check-loader-scan.mjs (never bundled). A regex literal containing a quote,
// followed on the same line by a string that looks like an import: a regex-unaware scanner opens a string at
// the quote inside the pattern, so the real string's contents land in "code" and read as `from "x"`.
const quote = /'/g; const label = 'from "x"'.replace(quote, '')
export default label
