// One shared player for previews and design candidates: starting a sample stops the one before it.
import { atom } from '@hermes/plugin-sdk'

/** Key of the sample playing now (e.g. `preview:<voice>`), or null. */
export const $playing = atom<null | string>(null)

let element: HTMLAudioElement | null = null
let objectUrl: null | string = null

export function base64ToBlob(data: string, mime: string): Blob {
  const binary = atob(data)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i)
  return new Blob([bytes], { type: mime })
}

export function bytesToBase64(bytes: Uint8Array): string {
  let binary = ''
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000))
  return btoa(binary)
}

export function play(key: string, data: string, mime: string): void {
  stop()
  objectUrl = URL.createObjectURL(base64ToBlob(data, mime))
  const audio = new Audio(objectUrl)
  element = audio
  audio.onended = () => stop(key)
  $playing.set(key)
  void audio.play().catch(() => stop(key))
}

/** Stop playback; with a key, only when that sample is the one playing. */
export function stop(key?: string): void {
  if (key !== undefined && $playing.get() !== key) return
  element?.pause()
  element = null
  if (objectUrl) URL.revokeObjectURL(objectUrl)
  objectUrl = null
  if ($playing.get() !== null) $playing.set(null)
}

/** Billed previews already fetched in this window, per agent and voice, so replaying one costs nothing. */
const previews = new Map<string, { audio: string; mime: string }>()

export function cachedPreview(key: string) {
  return previews.get(key)
}

export function rememberPreview(key: string, value: { audio: string; mime: string }) {
  previews.delete(key)
  previews.set(key, value)
  while (previews.size > 24) previews.delete(previews.keys().next().value as string)
}
