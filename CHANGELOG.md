# Changelog

## 1.7.1 — 2026-10-15

- **Report a problem:** a new `fish-audio:fish-audio-report-problem` skill. Ask your agent to "report a problem with
  Fish Audio": it checks setup with `hermes fish doctor --no-synth` and `/fish status` (no billed synthesis unless you
  say yes), searches existing issues, drafts a report with the bug form's headings, and files it with `gh` only after
  you approve that draft. Security problems go to the private advisory form instead.
- **Contributor docs:** `AGENTS.md`, `CONTRIBUTING.md`, a bug report form, a PR template and a troubleshooting section
  in the README (#40).

## 1.7.0 — 2026-10-14

- **Free-model wording follows Fish's end date:** Fish announced `s2.1-pro-free` is free through 30 November 2026.
  Before 1 November nothing changes. During November the notice says how to keep speaking after the end date. From
  1 December the setup badge and the Desktop onboarding (Voices page and Settings) no longer advertise a free model,
  model pickers show "S2.1 Pro Free (free period ended)", `/fish status` says "no API credit" instead of "free tier",
  an explicit `s2.1-pro-free` choice gets a warning in `/fish status`, `/fish model`, `hermes fish` and
  `hermes fish login`, and a billing error says the free period ended (Desktop too). Which model the plugin sends does
  not change on any date.
- **Install-time text carries the date:** the plugin description, the "Allow s2.1-pro-free" setting, the README,
  SECURITY.md and the setup skill say the free period was announced through 30 November 2026.
- **Desktop translations** write the end date the local way in all 8 non-English languages.

## 1.6.1 — 2026-10-14

- **Voice design accepts Fish's limits:** 2000-character voice-design descriptions and 150-character sample text,
  through the tools and Desktop Voices page. Model descriptions and titles keep their existing limits.
- **Live dictation keeps the last phrase:** when it is still pending, Hermes re-transcribes the recording instead of
  returning a shorter transcript.
- **README:** explains how Fish's MCP server and this plugin fit together, their separate credits and which to use.
- **Expressive speech:** a Drama 3 worked example with a direction, a sound and paired word tags.

## 1.6.0 — 2026-10-14

- **Drama 3.** Fish's released `drama-3` model is selectable everywhere (`/fish model`, `fish_speak`, the Desktop
  settings page); `drama-3-preview` keeps working. The expressive-speech skill covers Drama 3's directions, sounds and
  word tags, and sends angle-bracket tags through `fish_speak`, because Hermes's `text_to_speech` cleanup breaks
  closing tags such as `</whisper>`. Parity pins follow Fish's API after the Drama 3 release.

## 1.5.2 — 2026-10-13

- **No false "set by the operator" lock on managed agents.** The plugin now asks Hermes which settings the operator's
  managed layer pins, instead of keeping its own copy of that rule. On Hermes builds where an empty section in the
  managed config pins nothing, voice, model and saved voice names are no longer refused or shown as locked because of
  one. Upstream Hermes, which still treats an empty section as pinned, behaves as before.
- **Tested against Hermes 0.21.7.** CI now runs against Hermes 0.21.5, 0.21.6, 0.21.7, current `main` and the Electric
  Sheep fleet runtime.
- **README:** Desktop 0.21.7 and newer switches on the desktop half when you install with **Enable after install**. A
  troubleshooting row covers Fish tools that go missing after turning the plugin off and on in Hermes 0.21.6 and older.

## 1.5.1 — 2026-10-12

- **Fish Audio errors in your language on the Desktop Voices page.** Errors from Fish (key, credit, rate limit, voice not
  found, and so on) and the page's own refusals now appear in all nine languages. The request id, Fish trace and error
  code are kept. An older gateway still shows its English text, and older Desktop builds see no change.
- **Numbers and money follow the display language:** compact counts with the right plural forms (`1,5 тыс.
  использований`, `使用 1942.8万 回`), localized currency and grouping. English keeps `1.5k uses` and `$12.35`.
- **Right-to-left and wording polish.** Voice descriptions wrap to two lines instead of losing their start in Arabic;
  voice titles and data inside Arabic sentences are isolated, so names and ids keep their order; button names put the
  voice title where each language wants it; Chinese and Japanese lines use full-width punctuation.
- **Inputs stop at the gateway's limits** (counted in characters, so emoji count once), and an empty audio file is
  refused before upload.
- **Voice log line.** The gateway log says which voice speaks and where it came from, once per change, for example
  `Fish Audio voice: 0327fdb5… (from nested)`. `nested` is `tts.fish-audio.voice`, `call` is a voice id passed with the
  request, and `default` is Fish's default voice.

## 1.5.0 — 2026-10-11

- **Live dictation.** On Hermes 0.21.6+, `stt.provider: fish-audio` and `stt.streaming: true` show captions phrase by
  phrase in CLI voice mode, the TUI and Desktop's composer mic, with final text about half a second after you stop.
  Each committed phrase is billed at Fish's ASR price, rounded up to a second; a phrase needs at least 300 ms of sound
  (100 ms for a last word after an earlier phrase), so a pause is never committed on its own, and after a phrase is committed
  nothing is streamed until sound returns, except the half second just before it.
- Uses Fish's ElevenLabs-compatible realtime endpoint on `api.fish.audio`. A failed live session falls back quietly to
  file transcription; if Fish has not answered the last phrase 15 s after you stop, the text holds only the phrases it
  already returned. Hermes 0.21.5, remote self-hosted endpoints and plugin-host isolation keep the file path; a
  loopback `base_url` (a local proxy) receives the live audio.

## 1.4.0 — 2026-10-11

- **Voice names.** `/fish alias add <name> <voice-id>` saves a name. The name then works wherever a voice id goes:
  `/fish use`, `/fish preview`, `hermes fish use`, `fish_speak` (including multi-speaker) and the Desktop Voices page.
  `/fish use <name>` saves the voice itself, so changing the name later never changes your voice.
- **Settings page in Hermes Desktop 0.21.6 and newer.** Settings ▸ Plugins ▸ Fish Audio shows and changes the selected
  agent's voice, speech model and saved names, in all nine languages. Desktop 0.21.5 loads the plugin as before.
- **Clearer operator settings.** On a managed agent, `/fish model`, `/fish use` and the new settings say when the
  operator sets that value, instead of replying "Saved.".
- The speech provider now lists your saved names and current voice through `list_voices()`, for Hermes surfaces that
  show voices.

## 1.3.3 — 2026-10-11

- **S1 is deprecated.** Fish retires S1 on 2026-12-31 and then serves it as S2.1 Pro. `/fish model s1`,
  `hermes fish status` and `hermes fish doctor` now warn about it. From 2026-12-31 the plugin sends S1 requests as
  S2.1 Pro, with bracket emotion cues such as `[happy]`.
- **No development packages on install.** Hermes 0.21.6 offered to install the plugin's build and test tooling
  (156 Node packages) into the plugin folder. That tooling now lives in `devtools/`; the Desktop bundle is unchanged.
- **README:** the catalog install comes first. On the same computer, Desktop picks up the Voices page by itself
  (switch on **Desktop** under Capabilities → Plugins). New Performance section with measured latency and accuracy.
- **Fixes:** a WebSocket "Reference not found" now says the voice wasn't found instead of "Fish Audio is
  unavailable". `fish_transcribe` waits up to 900 seconds for long files.
- **Housekeeping:** parity data refreshed against live probes, a weekly drift check, and CI on Python 3.14 and
  current Hermes `main`.

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
