# Changelog

## 0.1.0 — 2026-10-05

First release of the Fish Audio plugin for Hermes Agent (private preview).

- **Text-to-speech provider `fish-audio`** for Hermes's `text_to_speech`, voice replies and read-aloud. The output
  format follows the requested file suffix, and `.ogg` gets Fish's native Ogg/Opus. One model resolver picks the
  model: `s2.1-pro` for funded accounts, `s2.1-pro-free` for new ones (with a one-time notice), and never the free
  model when `allow_free_model: false`.
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
