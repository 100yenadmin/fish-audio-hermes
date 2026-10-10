# Changelog

## 1.3.2 — 2026-10-10

- **Streaming voice needs Hermes 0.21.6.** Hermes 0.21.6, released 2026-10-08, is the first release with the plugin
  streaming hook (#133723). The README and the `streaming` setting now say so.
- **The floor stays at Hermes 0.21.5.** On 0.21.5, including the Desktop app and Termux builds that stay on it until
  their next bundled release, the plugin keeps working with whole-file speech. A 0.21.6 floor would stop it loading
  there.
- **`hermes fish doctor` reports the version Hermes checks `requires_hermes` against.** That version comes from
  `version_info` on Hermes 0.21.6 and later. Before this, a source checkout without an install stamp showed
  "Hermes version unknown".
- **CI tests four Hermes builds:** the 0.21.5 floor, the latest release (0.21.6), `main` and the Electric Sheep fork.

## 1.3.1 — 2026-10-07

- **Open Plugins lands on the Fish Audio row** (#23). The no-key card's button now opens
  `/settings?tab=plugins&agent=fish-audio&plugin=fish-audio`. Desktop builds with Settings ▸ Plugins open the Fish
  Audio form from `agent=`, as in 1.2.1. Builds that redirect that link to Capabilities ▸ Plugins (Hermes 0.21.5
  and the evaOS fork) keep only `plugin=`, and now scroll to and highlight the Fish Audio row instead of showing the
  plain list.

## 1.3.0 — 2026-10-06

- **Streaming voice uses Hermes's plugin streaming hook only** (catalog review). The Fish Audio TTS provider declares
  `streams_pcm` and `stream_sample_rate`, and Hermes builds with the hook (#133723, on `main` and in the next release)
  stream its 24 kHz PCM to CLI and TUI voice playback, Desktop read-aloud and gateway streaming.
- **Removed:** the streaming bridge into Hermes's internal `tools.tts_streaming` registry, and the
  `FISH_AUDIO_HERMES_NO_BRIDGE` switch. On Hermes 0.21.5 and other builds without the hook, voice replies use
  whole-file synthesis instead of streaming: sentence by sentence in CLI voice mode, the whole reply in Desktop
  read-aloud and gateway voice.
- `streaming: "off"`, `transport: ws` and the plugin host process behave as before.

## 1.2.1 — 2026-10-06

- **Settings ▸ Plugins.** Newer Hermes Desktop builds put plugin settings in Settings ▸ Plugins. The no-key card's
  **Open Plugins** button now opens `/settings?tab=plugins&agent=fish-audio`, the Fish Audio form, and older
  builds redirect it to Capabilities ▸ Plugins as before. Setup hints in Desktop (all nine languages), chat, the
  terminal, the setup skill and the README name both places.
- **Two more settings in the form.** `allow_free_model` (Allow the free model) and `operator_account`
  (Operator-managed account) are declared in `config_schema`, so they appear in the plugin's settings form. Their
  defaults and behaviour are unchanged, and admin-managed values stay locked.

## 1.2.0 — 2026-10-06

- **Desktop UI translations.** The Voices page (including its language filter), sidebar row, credit chip and command palette follow Hermes
  Desktop’s display language in Simplified Chinese, Traditional Chinese, Japanese, Arabic, Russian, French, German
  and Spanish. Language changes take effect without a reload; English text stays unchanged. The translations are
  machine-authored and checked by a second model against Hermes Desktop's own wording; corrections from native
  speakers are welcome. (#15)
- **Operator-managed keys, finished** (#18). With `operator_account: true`:
  - every `hermes fish` terminal command (`status`, `login`, `doctor`, `use`) keeps the operator's full view,
    including key and billing links in its errors;
  - results from `fish_speak` and the Voices page preview no longer name a model that the operator's wallet chose,
    though a configured model, or the `s2.1-pro` that `allow_free_model: false` fixes, is still shown;
  - the skills describe billing as going to the Fish Audio account behind the agent's key; the setup skill skips its
    key, billing and plan steps and links, and the voice-studio skill skips listing, editing and deleting the
    account's voices;
  - the clone approval prompt names "this agent's voice account";
  - deleting a voice from chat is refused at once, with no approval prompt.
- **Use under a managed provider.** When a managed layer pins a speech provider other than Fish Audio on an
  operator-managed agent, Use saves the voice and says that the agent's operator sets its provider, instead of
  pointing to `hermes tools`. The gateway's `/use` route now also returns `provider` and `operator_pinned`, so the
  Voices page words this note in the display language. (#18)

## 1.1.0 — 2026-10-06

- **Operator-managed keys.** When the Fish account behind an agent's key belongs to whoever runs the agent (for example
  a managed evaOS agent), set `plugins.entries.fish-audio.settings.operator_account: true`. People using the agent then
  never see that account: no balance or plan, no top-up, plan or API-key links, and no "billed to your Fish Audio
  account" notes. This covers `/fish balance` and `/fish status`, tool descriptions, error messages, the gateway's
  `/account` route and the Desktop Voices page (no Account tab or credit chip). Because one operator account can serve
  many agents, its own voices aren't listed either: there is no My voices tab, and listing, editing or deleting the
  account's voices is refused on the Voices page, the gateway routes and `fish_voices`. Cloning and designing a voice
  still work, and a voice created on the Voices page goes into that agent's Favourites, where Preview and Use reach it. Errors keep the same `error_kind` values, so nothing that branches on them changes. Without the
  setting, everything works as before.
- **Use never competes with a managed provider.** Where a managed layer already sets the speech provider (such as
  evaOS's `evaos-fishaudio`), `/fish use` and the Voices page's **Use** save only the voice, and never write a provider
  to the profile. Elsewhere they work as before: they select Fish Audio when the profile has no provider of its own.
- **Voices appears sooner after a slow start.** When an agent's gateway doesn't answer the first availability check
  (a timeout or a server error), Hermes Desktop now retries after 5, 15 and 30 seconds, instead of waiting a minute.
  An agent without the plugin still shows nothing. (#16)

## 1.0.4 — 2026-10-06

Hermes Desktop Voices page fixes, from a review of the evaOS Agent app's copy of the plugin (#13):
- **Switching agents and back stops a voice-clone upload.** Before, if you switched from agent A to B and back to A
  during an upload, it carried on to A. Now any agent switch, or turning the plugin off, stops it.
- **One Use at a time.** Before, clicking Use on two voices quickly sent both, and either could end up as the agent's
  voice. Now the other Use buttons wait until the first one finishes.
- **Deleting a voice removes it from Favourites.** A deleted voice used to stay starred, but could no longer be
  previewed or used.
- **A Voices page that can't reach the agent says so.** When the agent's gateway timed out or returned an error
  before it had ever answered, the page showed a loading skeleton forever. Now it shows the error and a Check again
  button.

## 1.0.3 — 2026-10-06

- **A voice preview no longer plays after you leave the Voices page.** If you started a preview and then left the page
  or turned the plugin off before the preview returned, it played anyway, on whatever screen you were on. Now it stays
  silent. It is still kept, so playing that preview again on the page costs nothing. (#11)

## 1.0.2 — 2026-10-06

- **Switching agents in Hermes Desktop no longer shows the previous agent's Voices entries.** When you switched from an
  agent with the Fish Audio gateway plugin to one without it, the Voices sidebar row, credit chip and palette
  commands stayed until the new agent answered "not found", and stayed for good if it timed out. A new agent now starts
  with them hidden until its own gateway answers. For the same agent, a transient error still never hides them.
- **Turning the plugin off and on again starts fresh.** Before, after you disabled the plugin, switched agents and
  enabled it again, the Voices page briefly showed the previous agent's availability and balance. Now it starts
  unknown and loads the current agent's.

## 1.0.1 — 2026-10-05

- **Transcription failures say what kind of failure they are.** A failed transcription now carries `error_kind`
  (`credential`, `quota`, `rate_limit`, `availability`, `invalid_request` and the rest), alongside the existing
  message. A caller can then decide to retry elsewhere without parsing text.
- **A clearer limit for pronunciations.** When call `pronunciations` and a configured inline dictionary together
  come to more than Fish's 15,000 entries, the message gives the count and the limit instead of "Invalid
  pronunciation entry." (#8)
- Each release now attaches `fish-audio-<version>-managed.tgz` with a `.fish-audio-release.json` sidecar, for
  managed installs that pin an artifact by digest.

## 1.0.0 — 2026-10-05

- **Every field of Fish Audio's voice API the plugin claims is now sent and tested.** The parity map has no planned
  fields left: each one is verified by a test, sent through an equivalent encoding, or excluded with a stated reason.
- **`fish_speak` uses your configured voice settings.** Temperature, top_p, latency, chunk length, bitrates,
  volume and the rest of `tts.fish-audio.*` now apply to `fish_speak` (including timestamped speech), as they
  already did for voice replies. Call arguments still win. Call `pronunciations` merge with a configured inline
  dictionary.
- **Voice design:** `num_step`, `guidance_scale` and `instruct_guidance_scale`.
- **Voice search:** `author_id`, `title_language` and `licensed` filters. Results report `has_more`.
- **Your voices:**
  - clone and update accept `visibility` (`private` or `unlist`; publish publicly from the Fish Audio website);
  - clone accepts `generate_sample`;
  - clone and update accept a `cover_image_path` (PNG, JPEG or WebP, checked like audio samples).
- **Transcription** returns the language Fish detected.
- **Account tab:** an unreadable plan response shows "Plan details are unavailable right now". An empty response
  still means no plan.
- **Tested live** on Hermes 0.21.5, through the gateway and Desktop audio routes:
  - read-aloud;
  - WebM dictation;
  - streaming voice mode (first audio in 0.26 s);
  - design → save → delete;
  - clone → delete;
  - per-profile keys on one dashboard.

## 0.3.1 — 2026-10-05

- Voices page polish:
  - the credit chip always reflects a billed preview or design;
  - Next stops at the last page when the total is known;
  - deleting finds your voice even past the first 100 with the same name;
  - a temporary plan-service failure shows "Plan details are unavailable" instead of "No app plan".

## 0.3.0 — 2026-10-05

- **Voices page in Hermes Desktop.** A sidebar page for the selected agent with four tabs: **Library** (search by
  name and language, billed previews, favourites, **Use** to set the agent's voice), **My voices** (with delete
  after typing the voice's name), **Create** (clone from 1–3 recordings with the speaker's consent, or design a
  voice and save a candidate) and **Account** (API credit, plan and top-up links). The status bar shows API
  credit, in orange when it runs low, and the command palette opens the page. Agents without the plugin keep
  their sidebar unchanged; agents without a key get an onboarding card.
- **Gateway routes** under `/api/plugins/fish-audio/`, behind the Hermes dashboard's existing authentication. They
  resolve the key and settings for the profile Desktop has selected on every request, and return errors in the
  response body without the key. Clone uploads arrive in JSON chunks into owner-only temp files that are removed
  after the clone or by the next upload after 15 minutes idle. Symlinks along the upload folder's path inside
  the profile are refused. See SECURITY.md for what the page bypasses (Hermes's approval gate) and
  what it bills.
- Install both halves from **Capabilities → Plugins → Install from Git**, switch on **Desktop**, and restart the
  gateway once.

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
- **Fixes.** Subtitles keep apostrophes, accents and sentence punctuation; a subtitle failure no longer hides
  the audio; a streamed sentence that returns no audio is reported (and shown by `/fish status`) instead of
  playing silence.
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
