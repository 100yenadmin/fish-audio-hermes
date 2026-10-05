// Per-agent favourites, kept in Desktop's plugin storage on this computer.
import { atom } from '@hermes/plugin-sdk'

import { $available, type AgentPin, agentKey, pluginCtx, type Voice } from './api'

export type Favourite = Pick<Voice, 'author' | 'id' | 'languages' | 'title'>

export const favouritesKey = (pin: AgentPin) => `favourites:${agentKey(pin)}`

/** Storage holds the favourites; this changes on every write, so a mounted Library re-reads them. */
export const $favouritesRevision = atom(0)

export function readFavourites(pin: AgentPin) {
  return pluginCtx().storage.get<Favourite[]>(favouritesKey(pin), [])
}

export function writeFavourites(pin: AgentPin, list: Favourite[]) {
  pluginCtx().storage.set(favouritesKey(pin), list)
  $favouritesRevision.set($favouritesRevision.get() + 1)
}

/** A voice deleted from the account can no longer be previewed or used, so it leaves that agent's favourites too. */
export function forgetFavourite(pin: AgentPin, id: string) {
  const list = readFavourites(pin)
  if (list.some(f => f.id === id)) writeFavourites(pin, list.filter(f => f.id !== id))
}

/**
 * Operator mode has no My voices tab (the operator's account spans its agents), so a voice created here goes into this
 * agent's favourites, where Preview and Use reach it. Returns whether it did.
 */
export function keepCreated(pin: AgentPin, voice: { id: string; title: string }) {
  const available = $available.get()
  if (!available || available.account !== false) return false
  const list = readFavourites(pin)
  if (!list.some(f => f.id === voice.id)) writeFavourites(pin, [...list, { id: voice.id, title: voice.title }])
  return true
}
