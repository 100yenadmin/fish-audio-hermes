# Changelog

## 0.2.0 — 2026-10-05

- **Streaming voice.** CLI voice mode and Desktop voice replies stream sentence by sentence. The plugin streams
  24 kHz PCM from Fish Audio through Hermes's streaming TTS registry (first audio bytes 0.24–0.38 s after a
  sentence was sent in our tests, excluding the model's reply time), and adopts Hermes's plugin streaming seam automatically once it ships. Set
  `streaming: "off"` for whole-file speech, or `FISH_AUDIO_HERMES_NO_BRIDGE=1` to turn the bridge off.
- **Speech timestamps.** `fish_speak` with `timestamps: true` returns word timings plus SRT and WebVTT subtitles
  next to the audio.
- **Safer requests.** Whole-file speech and transcription make at most three attempts. They retry on 429, 500 and
  503 responses, and on transport errors raised while connecting or before the request body finished sending; they
  stop retrying once audio or a transcript has started arriving. 502 and 504 are not retried, and neither are
  streaming sentences or voice-library changes. Whole-file audio, transcription, voice-library and voice-design
  responses are capped at 64 MiB.
- README product page, SECURITY.md and launch art.

## 0.1.0 — 2026-10-05

First release of the Fish Audio plugin for Hermes Agent (private preview).

- **Text-to-speech provider `fish-audio`** for Hermes's `text_to_speech`, voice replies and read-aloud. The output
  format follows the requested file suffix, and `.ogg` gets Fish's native Ogg/Opus. One model resolver picks the
  model: a configured model when valid, otherwise `s2.1-pro`, and `s2.1-pro-free` only for a wallet with no API
  credit or top-ups and no free-credit flag (with a one-time notice); never the free model when `allow_free_model: false`.
- **Speech-to-text provider `fish-audio`** on `transcribe-1-pro`, with clean transcripts for voice notes and
  dictation (including WebM).
- **Model tools** in toolset `fish_audio`:
  - `fish_speak`: expressive and multi-speaker speech, inline pronunciations.
  - `fish_voices`: search, get, mine, clone, design, save, update, delete. Clone and delete go through Hermes's
    human-approval gate.
  - `fish_transcribe`: speaker turns, timestamps and SRT subtitles.
- **Commands:** `/fish status | voices | use | model | preview | balance | help` in every chat surface, and
  `hermes fish login | status | doctor | use` in the terminal.
- **Skills:** `fish-audio-setup`, `fish-audio-expressive-speech`, `fish-audio-voice-studio`.
- **Parity map** of Fish's OpenAPI (`parity.yaml`), checked in CI.
