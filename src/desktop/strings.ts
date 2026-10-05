// Every user-facing English string, in one place for later i18n.
import { useValue } from '@hermes/plugin-sdk'
import { $available } from './api'

export function useAccountText(normal: string, operator: string) {
  const available = useValue($available)
  return available && available.account === false ? operator : normal
}

const agentName = (profile: string) => (profile && profile !== 'default' ? profile : 'this agent')

export const LINKS = {
  keys: 'https://fish.audio/app/api-keys',
  discovery: 'https://fish.audio/discovery'
}

export const S = {
  title: 'Voices',
  navLabel: 'Voices',
  paletteVoices: 'Fish Audio: Voices',
  paletteAccount: 'Fish Audio: Account',
  poweredBy: 'Fish Audio',
  forAgent: (profile: string) => `For ${agentName(profile)}`,
  notSetUp: (profile: string) =>
    `Fish Audio isn't set up on ${agentName(profile)}'s machine yet. Install the plugin there, enable it, and restart the gateway.`,
  tabs: { library: 'Library', mine: 'My voices', create: 'Create', account: 'Account' },
  // Onboarding (no key)
  operatorOnboardTitle: "Voice isn't set up for this agent yet",
  operatorOnboardBody: 'Ask the operator of this agent to finish the Fish Audio setup.',
  operatorBilledNote: 'Preview plays a short sample.',
  operatorCloneBilled: 'Cloning creates a new voice for this agent.',
  operatorDesignBilled: 'Each design creates new candidate voices.',
  onboardTitle: 'Connect your Fish Audio account',
  onboardBody: (profile: string) =>
    `Voices need a Fish Audio API key on ${agentName(profile)}. New accounts can start on the free s2.1-pro-free model.`,
  onboardStep1: 'Create a free API key on fish.audio',
  onboardStep2: 'Paste it in Plugins ▸ Fish Audio, then restart the gateway',
  getKey: 'Get an API key',
  openPlugins: 'Open Plugins',
  checkAgain: 'Check again',
  unreachable: (profile: string) => `Couldn't reach Fish Audio on ${agentName(profile)}`,
  // Library
  search: 'Search voices',
  language: 'Language',
  anyLanguage: 'Any language',
  favouritesOnly: 'Favourites',
  billedNote: 'Preview plays a short sample and is billed to your Fish Audio account.',
  preview: 'Preview',
  stop: 'Stop',
  use: 'Use',
  inUse: 'In use',
  favourite: 'Favourite',
  unfavourite: 'Remove favourite',
  uses: (n: number) => `${compact(n)} uses`,
  noVoices: 'No voices match',
  noFavourites: 'No favourites yet',
  noFavouritesHint: 'Star a voice in the library to keep it here.',
  prev: 'Previous',
  next: 'Next',
  pageOf: (page: number) => `Page ${page}`,
  usedVoice: (title: string, profile: string) => `${title} is now ${agentName(profile)}'s voice`,
  loadFailed: "Couldn't load voices",
  retry: 'Retry',
  // My voices
  mineEmpty: "You haven't created any voices yet",
  mineEmptyHint: 'Clone your voice or design a new one in Create.',
  delete: 'Delete',
  deleteTitle: (title: string) => `Delete "${title}"?`,
  deleteBody: 'This removes the voice from your Fish Audio account. Agents using this voice will need another voice selected.',
  deleteConfirmLabel: (title: string) => `Type "${title}" to confirm`,
  cancel: 'Cancel',
  deleted: (title: string) => `Deleted ${title}`,
  // Create
  cloneTitle: 'Clone a voice',
  cloneBody: 'Upload 1–3 clear recordings of one speaker (MP3, WAV, OGG, WebM, FLAC or MP4, up to 10 MB each).',
  chooseFiles: 'Choose audio files',
  voiceTitle: 'Voice name',
  descriptionOptional: 'Description (optional)',
  consent: "I have the speaker's permission to clone this voice.",
  clone: 'Clone voice',
  cloneBilled: 'Cloning is billed to your Fish Audio account.',
  uploading: (n: number, total: number, percent: number) => `Uploading ${n} of ${total} · ${percent}%`,
  cloning: 'Creating the voice…',
  cloned: (title: string) => `Created ${title}. Find it in My voices.`,
  tooMany: 'Choose up to 3 files.',
  tooLarge: (name: string) => `${name} is larger than 10 MB.`,
  agentChangedNothingSent: 'The selected agent changed, so nothing was sent.',
  agentChanged: 'The selected agent changed, so the upload stopped. Nothing was sent to the other agent.',
  designTitle: 'Design a voice',
  designBody: 'Describe the voice you want. Fish Audio makes a few candidates to choose from.',
  designPlaceholder: 'A warm, unhurried British narrator in her forties, slightly husky',
  design: 'Design voices',
  designing: 'Designing…',
  designBilled: 'Each design is billed to your Fish Audio account.',
  candidate: (n: number) => `Candidate ${n}`,
  saveAs: 'Name',
  save: 'Save voice',
  saved: (title: string) => `Saved ${title}. Find it in My voices.`,
  // Account
  apiCredit: 'API credit',
  lowCredit: 'Low balance — top up to keep voice replies working.',
  topUps: 'Lifetime top-ups',
  freeCredit: 'Free credit available',
  plan: 'Plan',
  planBalance: (balance: number, total: number) => `${balance.toLocaleString()} of ${total.toLocaleString()} credits left`,
  renews: (date: string) => `Renews ${date}`,
  periodEnds: (date: string) => `Current period ends ${date}`,
  noPlan: 'No app plan',
  planUnavailable: 'Plan details are unavailable right now.',
  creditsSeparate: 'App plan credits and API credits are separate. Voice replies use API credit.',
  topUp: 'Top up API credit',
  plans: 'Plans',
  apiKeys: 'API keys',
  chip: (credit: string) => `Fish ${credit}`,
  chipTip: 'Fish Audio API credit',
  usd: (value: string) => `$${Number(value).toFixed(2)}`
}

function compact(n: number) {
  return n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1)}M` : n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n)
}

export const LANGUAGES: Array<[string, string]> = [
  ['en', 'English'],
  ['zh', 'Chinese'],
  ['ja', 'Japanese'],
  ['ko', 'Korean'],
  ['es', 'Spanish'],
  ['fr', 'French'],
  ['de', 'German'],
  ['it', 'Italian'],
  ['pt', 'Portuguese'],
  ['ru', 'Russian'],
  ['ar', 'Arabic'],
  ['nl', 'Dutch'],
  ['pl', 'Polish']
]
