![Fish Audio for Hermes](docs/media/banner.png)

# Fish Audio for Hermes

**The official [Fish Audio](https://fish.audio) plugin for [Hermes Agent](https://github.com/NousResearch/hermes-agent),
maintained by Electric Sheep.**

Give your agent a voice: expressive speech in 80+ languages, Fish Audio's library of community voices, your own
cloned or designed voices, and accurate speech-to-text. It works in the Hermes CLI, every messaging app Hermes
supports and the Desktop app.

```bash
hermes plugins install fish-audio --enable
hermes fish login        # paste your key from https://fish.audio/app/api-keys
```

This installs the reviewed release pinned in the Hermes plugin catalog. To follow the repository's latest `main` instead:

```bash
hermes plugins install https://github.com/100yenadmin/fish-audio-hermes --enable
```

On Hermes 0.21.6 and newer, Hermes asks before installing the plugin's Python dependencies; in a script, add
`--yes-deps`. Hermes 0.21.5 installs them without asking and has no `--yes-deps` flag. On either version,
`hermes plugins enable fish-audio` afterwards also installs them.

`hermes fish login` checks the key against your Fish account and saves it to the active profile. It makes Fish
Audio the speech and transcription provider where none is set, and replaces another provider only if you confirm
at the prompt or pass `--yes`. Once Fish Audio is the provider, voice replies,
read-aloud and voice notes go through it.

## What you get

| | |
|---|---|
| **Expressive speech** | Fish Audio S2 models follow emotion and delivery cues such as `[excited]`, `[whispering]` and `[laughing]`. Drama 3 (`drama-3`) takes plain-language direction and word-level tags such as `<whisper>…</whisper>`. Multi-speaker dialogue and custom pronunciations are supported too. |
| **Streaming voice** | CLI voice mode and Desktop voice replies stream each sentence as it is ready. In our tests the first audio bytes arrived 0.24–0.38 s after a sentence was sent (see [Streaming voice](#configuration) for how this was measured). |
| **Voice library** | Search Fish Audio's community voices from chat (`/fish voices narrator`) and switch with `/fish use <id>`. |
| **Voice names** | Save a voice under a short name (`/fish alias add narrator <id>`), then use the name wherever a voice id goes: `/fish use narrator`, `/fish preview narrator` or `fish_speak`. |
| **Your own voices** | Clone a voice from a short sample, or describe one in words and pick from the candidates. Cloning and deleting from chat go through Hermes's approval gate, so by default Hermes asks you first. |
| **Speech-to-text** | Voice notes use `transcribe-1-pro`. Live dictation shows phrases while you speak in CLI voice mode, TUI and Desktop (Hermes 0.21.6+). `fish_transcribe` adds speaker turns, timestamps and SRT subtitles. |
| **Desktop Voices page** | In Hermes Desktop: browse and preview voices, pick one for the agent, clone or design your own, and check your credit. |
| **Account at a glance** | `/fish balance` shows your API credit and plan. When credits run out, tools and `/fish` commands reply with a top-up link; for voice replies, `/fish status` shows the last error. |

## Setup

1. **Get a key** at <https://fish.audio/app/api-keys>. New accounts can start on Fish's free `s2.1-pro-free` model, free through 30 November 2026;
   after that, add API credit.
2. **Add it** with `hermes fish login` on the machine running Hermes, or in Desktop: **Voices ▸ Enter API key** opens
   the key field in Settings ▸ Plugins ▸ Fish Audio ▸ Agent settings (on older Desktop, open it with the gear on the
   fish-audio row in Capabilities ▸ Plugins). Or use
   `hermes tools` ▸ Text-to-Speech ▸ Fish Audio. `login` checks the key against your Fish wallet and saves it to the
   active profile. It makes Fish Audio the speech and transcription provider where none is set; it replaces another
   provider only if you confirm at the prompt or pass `--yes`. `--key-stdin` reads the key from standard input.
3. **Check it** with `hermes fish doctor`, which makes one short billed synthesis (`--no-synth` skips it). Restart a
   running gateway or the Desktop app to pick up a new key.

Never paste API keys into a chat. `/fish` refuses them and tells you to rotate the key.

![hermes fish doctor: key, wallet, providers, Hermes version, a billed TTS round trip and STT all pass](docs/media/screenshot-setup.png)

## Use

**In conversation:** just ask.
- "Read that back to me in an excited voice."
- "Find me a deep British narrator voice and use it."
- "Design a voice for our support agent and let me pick."
- "Transcribe meeting.m4a with speakers and give me subtitles."

**Commands**

| Command | What it does |
|---|---|
| `/fish status` | Key, providers, voice, model and why it was chosen, credit, last error |
| `/fish voices [query]` | Search the voice library |
| `/fish use <voice-id or name>` | Use a voice for this profile |
| `/fish model <id>` | Pick a model (`s2.1-pro`, `s2.1-pro-free`, `s2-pro`, `s1`, `drama-3`, `drama-3-preview`) |
| `/fish preview <voice-id or name> [text]` | Hear a voice (a billed synthesis) |
| `/fish alias` | List your saved voice names |
| `/fish alias add <name> <voice-id>` | Save a name for a voice: 2–32 letters, digits, `-` or `_`, starting with a letter |
| `/fish alias remove <name>` | Delete a saved name |
| `/fish balance` | API credit and plan |
| `hermes fish login [--key-stdin] [--yes]` | Save and check a key, offer to switch providers |
| `hermes fish status` · `hermes fish use <voice-id>` | Status and voice choice from your terminal |
| `hermes fish doctor [--no-synth]` | Check the setup with one billed synthesis, or without it |

**Model tools** (toolset `fish_audio`; calls are billed to your Fish Audio account)

| Tool | What it does |
|---|---|
| `fish_speak` | Expressive or multi-speaker speech; with `timestamps: true`, word timings plus SRT and WebVTT subtitles |
| `fish_voices` | Search, list and inspect voices; clone, design, save, update and delete them |
| `fish_transcribe` | Rich transcription with speakers, timestamps and SRT |

Plain read-aloud uses Hermes's own `text_to_speech` tool, with Fish Audio as the provider. Include the
`media_tag` that `fish_speak` returns in the reply to deliver its audio; the plugin also appends missing audio tags
through its output hook. Plugin skills: `fish-audio:fish-audio-setup`, `fish-audio:fish-audio-expressive-speech`,
`fish-audio:fish-audio-voice-studio` and `fish-audio:fish-audio-report-problem` (drafts a bug report and files it with
`gh` only after you approve the draft).

## Hermes Desktop

![The Voices page in Hermes Desktop: the Fish Audio voice library searched for "narrator", with Preview, Use and favourite on each voice](docs/media/screenshot-desktop-library.png)

The Voices page follows Hermes Desktop's display language: English, Simplified Chinese (`zh`), Traditional Chinese
(`zh-hant`), Japanese (`ja`), Arabic (`ar`), Russian (`ru`), French (`fr`), German (`de`) and Spanish (`es`).
Changing the display language updates the page, sidebar, status bar and command palette without a reload.
Fish Audio errors and the page's own messages are translated too, keeping the request id and Fish trace; numbers
and amounts use the language's own formats.
The translations are machine-authored and checked against Hermes Desktop's own wording; native-speaker corrections
are welcome as pull requests to `devtools/src/desktop/locales/`.

The plugin adds a **Voices** page to Hermes Desktop for the selected agent:

- **Library:** search Fish Audio's voices by name and language, preview them (a short billed sample), star
  favourites, and choose **Use** to make a voice the agent's voice.
- **My voices:** the voices you cloned or designed; delete one by typing its name.
- **Create:** clone a voice from 1–3 recordings (up to 10 MB each, with the speaker's permission), or describe a
  voice and save the candidate you like.
- **Account:** API credit, plan credits and top-up links. The status bar shows your API credit, in orange when it
  runs low.

| Create | Account |
|---|---|
| ![Create: design a voice from a description and save a candidate, or clone one from recordings with the speaker's consent](docs/media/screenshot-desktop-create.png) | ![Account: API credit, plan credits, top-up and plan links](docs/media/screenshot-desktop-account.png) |

The page has two halves in one package: routes that run **on the agent's gateway** and screens that run **in
Hermes Desktop**. On the same computer, where Desktop and the agent share a Hermes home (the usual local install),
Desktop picks up the desktop half by itself after the CLI install. Open **Capabilities → Plugins → Fish Audio**,
switch on **Desktop**, and restart the gateway once.

For Desktop connected to an agent on another machine, install from Desktop:

1. Open **Capabilities → Plugins → Install from Git**, enter `https://github.com/100yenadmin/fish-audio-hermes`,
   choose **Review repository**, then **Install**. Desktop installs the agent half into the connected agent and
   the desktop half on this computer.
2. On Desktop 0.21.7 and newer, **Install** with **Enable after install** also switches on the desktop half. On
   older Desktop, open **Fish Audio** in **Capabilities → Plugins** and switch on **Desktop**.
3. **Restart the agent's gateway** once. Hermes mounts plugin routes only at startup.

The **Voices** row appears only
for agents whose gateway has the plugin; an agent without a Fish Audio key shows a card that links to the key page
and the plugin's settings.

### Settings page (Hermes Desktop 0.21.6 and newer)

**Settings ▸ Plugins ▸ Fish Audio** shows the selected agent's voice, speech model and saved voice names, and lets you
change them. **Change** opens the Voices page to pick a voice. The plugin's own options (API key, server address,
streaming and transport) sit under the same entry as **Agent settings**.

The page acts on the agent selected in Desktop. Its header names that agent, or says "this agent" for the default
profile. Desktop's own **Settings ▸ Voice** doesn't list plugin speech providers or their voices yet
(NousResearch/hermes-agent#136485), so pick Fish voices here or on the Voices page. Desktop 0.21.5 has no plugin
settings pages; use the Voices page and `/fish` there.

## Configuration

All settings are per profile.

```yaml
tts:
  provider: fish-audio
  fish-audio:
    voice: 933563129e564b19a115bedd57b7406a   # any Fish voice id (this one: "Sarah", a conversational narrator)
    model: s2.1-pro                           # optional; see "Which model?"
    latency: balanced                         # normal | balanced | low
    temperature: 0.7
stt:
  provider: fish-audio
  streaming: true              # live dictation on Hermes 0.21.6+
  fish-audio:
    model: transcribe-1-pro
plugins:
  entries:
    fish-audio:
      settings:
        streaming: auto        # auto | "off"
        allow_free_model: true # false = never use s2.1-pro-free
```

The `plugins.entries.fish-audio.settings` keys (`base_url`, `streaming`, `transport`, `allow_free_model`,
`operator_account`) and the API key also appear in Desktop ▸ Settings ▸ Plugins ▸ Fish Audio ▸ Agent settings (on
older Desktop, the gear on the fish-audio row in Capabilities ▸ Plugins). Set the voice and model from the Fish Audio settings page, the Voices page, `/fish use` or
`/fish model`. Saved voice names live in `tts.fish-audio.aliases` (name → voice id). On a managed agent, when the
operator sets the voice, model or names, the plugin says so instead of saving.

**Which model?** A valid `tts.fish-audio.model` is used as set (for `fish_speak`, a model named in the call comes
first); invalid ids are ignored. Otherwise the plugin uses `s2.1-pro`, and picks `s2.1-pro-free` only when Fish
reports a wallet with no API credit and no past top-ups that doesn't carry Fish's free-credit flag. If the wallet can't be read, it uses
`s2.1-pro`. Before 1 November 2026, its free-model notice says the model is free until 30 November 2026 and that
Fish Audio may use free-tier requests to improve its models; during November it adds a reminder to add API credit to keep speaking.
After 30 November it describes the announced end and suggests adding credit if speech fails, without changing the model sent.
`allow_free_model: false` replaces `s2.1-pro-free`
with `s2.1-pro` everywhere, even when you name the free model yourself.

**Streaming voice.** With `tts.provider: fish-audio` on Hermes 0.21.6 or later, which has the plugin streaming hook,
the plugin streams 24 kHz PCM from `POST /v1/tts` to CLI and TUI voice playback, Desktop read-aloud and gateway
streaming. Hermes 0.21.5 uses whole-file synthesis instead: sentence by sentence in CLI voice mode, and the whole reply
in Desktop read-aloud and gateway voice. That includes the Desktop app and Termux builds still on 0.21.5. They stream
once they update; the plugin needs no change.
Each streamed sentence is one billed request and is not retried. Measured first audio bytes: Fish HTTP API p50 243 ms (3 samples, 238–356
ms), CLI voice mode 373–381 ms, Desktop speak-stream handler 310 ms (in-process, not through the Electron UI);
these exclude the model's own reply time. Set `streaming: "off"` for whole-file speech; `transport: ws` switches to Fish's live
WebSocket for diagnosis.

Under host plugin isolation (`plugins.isolation: host`, newer Hermes builds), the `hermes fish` terminal command is
unavailable and voice replies use whole-file speech instead of streaming; `/fish`, the tools, the hooks and
both providers still work. Config changes apply to the active profile; managed installs may refuse writes.

### Live dictation

On Hermes 0.21.6 or newer, set `stt.provider: fish-audio` and `stt.streaming: true`. Live dictation works in CLI
voice mode, the TUI and Desktop dictation (the composer mic). Captions appear phrase by phrase after a short pause,
and the final text arrives about half a second after you stop. Fish transcribes one phrase at a time, so when it is
slow, consecutive phrases can arrive together. If the live session fails, Hermes quietly transcribes the recording as
a file instead. That includes Fish not answering the last phrase within 15 seconds of you stopping, so a late last
phrase is never dropped.

Each committed phrase is billed at Fish's ASR price, with its audio rounded up to a whole second. A phrase is committed
only when it holds at least 300 ms of sound above Hermes's `voice.silence_threshold` (100 ms for a last word after an
earlier phrase), so a pause is never sent as a phrase on its own. After a phrase is committed, nothing is streamed until
sound returns, except the half second just before it. Live dictation uses Fish's ElevenLabs-compatible realtime
endpoint at `wss://api.fish.audio/compat/elevenlabs/v1/speech-to-text/realtime`; microphone audio streams to Fish while
you speak.
It is unavailable with a self-hosted `base_url` on a remote host or under plugin-host isolation
(`plugins.isolation: host`), which keep file transcription; a loopback `base_url` (a local proxy) receives the live
audio. Language and prompt hints are ignored on this endpoint.

### Operator-managed keys

Set `plugins.entries.fish-audio.settings.operator_account: true` (Desktop: **Operator-managed account**) when the
Fish account belongs to the agent operator. This hides balance, plan, billing and API-key links in chat and Desktop, and directs
setup or credit problems to the operator. Because one operator account can serve many agents, the account's own
voices aren't listed, edited or deleted from an agent (no My voices tab; `fish_voices` refuses `mine`, `update` and
`delete`); cloning and designing still work, and a voice created on the Voices page goes into that agent's Favourites. It defaults to false. Tool descriptions update after a gateway restart.
Operators should also pin `tts.fish-audio.model` or set `allow_free_model: false`, so the model never depends on
the wallet that users can't see.

## Privacy and security

- **No telemetry.** The plugin talks only to Fish Audio's API (`api.fish.audio`, or the `base_url` you configure),
  and only for the work you ask for. See [SECURITY.md](SECURITY.md) for exactly what each request carries. Links to
  Fish Audio are plain links, with no tracking parameters.
- **Keys** come from the active profile's secret scope. When one Hermes process serves several profiles, each
  profile uses its own key; a single-profile install may also read `FISH_API_KEY` from the environment. Keys are
  never logged or put in error messages.
- **Voice fields:** search accepts `author_id`, `title_language` and `licensed`, and returns boolean `has_more`.
  Design accepts `num_step` (1–128), `guidance_scale` and `instruct_guidance_scale` (finite, ≥ 0). Clone/update
  accept private/unlist `visibility` and `cover_image_path` (PNG/JPEG/WebP, ≤ 5 MiB); clone also accepts boolean
  `generate_sample`. Transcription returns `language` when Fish supplies a string.
- **Files** the model tools upload (`fish_voices` clone samples, `fish_transcribe` recordings) must be regular audio
  files (covers must be images). They refuse symlinks, Hermes config and secret files, and SSH or cloud credential paths.
- **Approvals:** cloning and deleting voices from chat or the model tools go through Hermes's approval gate and
  follow your Hermes approval settings. By default Hermes asks you, and refuses when no one is there to answer. The
  Desktop Voices page confirms them on the page instead (see [Disclosure](#disclosure)).
- Report vulnerabilities privately; see [SECURITY.md](SECURITY.md).

## Disclosure

- Synthesis, transcription, cloning and voice design are paid Fish Audio requests billed to your Fish account (except
  `s2.1-pro-free` while Fish offers it free, announced through 30 November 2026).
- Fish Audio may use free-model requests to improve its models.
- The Desktop Voices page adds gateway routes under /api/plugins/fish-audio/, behind the Hermes dashboard's
  existing authentication. On that page, previews, clones and voice designs are billed; cloning a voice or saving a
  designed one creates a voice in your Fish account; cloning and deleting are confirmed on the page (a
  speaker-consent box, a typed voice name) instead of through Hermes's approval gate; and Use sets the
  profile's Fish voice, and its speech provider when none is set. Preview and design audio files are deleted
  from the gateway once returned; a design's audio is held in gateway memory so you can save it, until it is
  saved, a later design or save finds it over an hour old, or the gateway stops; clone uploads are deleted
  after the clone, or by the next upload once idle for 15 minutes. With a key set, Desktop reads your Fish
  wallet when an agent is selected and about every five minutes, for the status-bar credit; favourites stay in
  Desktop's plugin storage on that computer, and fetched previews stay in the window's memory until it closes.

## Compatibility

Hermes Agent 0.21.5 or newer, Python 3.11+, macOS and Linux. Streaming voice and live dictation need 0.21.6 or newer. CI tests the
plugin against Hermes 0.21.5 (the floor), 0.21.6 (the catalog floor), the latest Hermes release, Hermes `main` and the
Electric Sheep fork. Windows is untested. CI also runs Python 3.14.

### Performance

Measured on 2026-10-11 from Tokyo with plugin v1.3.2, N=3, p50: HTTP streaming time to first audio was
0.293 s on s2.1-pro, 0.266 s on s2-pro and 0.244 s on s1. Whole-file MP3 synthesis of 310 characters took
3.605 s on s2.1-pro and 2.209 s on s2-pro. On a clean synthetic speech corpus, transcribe-1-pro had English
word error rate (WER) 1.8% and Japanese/Chinese character error rate (CER) 0. These numbers depend on region and load.

## Fish Audio's MCP server and this plugin

This plugin is Hermes's speech provider and uses a developer API key, billed to Fish API credit.
Fish's remote HTTP MCP server uses browser sign-in (OAuth, no API key) and is billed to your Fish plan's package credits.

Which to use:

- Speech replies, voice notes, dictation, the Desktop Voices page and the `fish_speak`, `fish_voices` and
  `fish_transcribe` tools: this plugin. An MCP server cannot be Hermes's speech provider.
- Story Studio audiobooks and podcasts, images and video: Fish's MCP server.

In Hermes 0.21.7 or newer, add the MCP server with:

```sh
hermes mcp add fish-audio --url https://api.fish.audio/mcp --auth oauth --connect-timeout 180
```

Hermes opens Fish's sign-in page: choose the team whose credits to use, then allow access. Fish may email a 6-digit
code first, and Hermes waits only 30 seconds for the sign-in unless you pass `--connect-timeout`.
`hermes mcp login fish-audio` signs in again later.
Ask for each piece of speech through one tool only.
Fish's [official plugin](https://github.com/fishaudio/fish-audio-plugin) connects Claude Code, claude.ai, Codex and ChatGPT to its MCP server.
[Fish Audio voice agents (phone agents)](https://docs.fish.audio/agents/overview) are not in this plugin.

## Troubleshooting

| Message | Fix |
|---|---|
| "Fish Audio isn't set up for this profile" | `hermes fish login` |
| "Top up Fish Audio API credits" | Top up at <https://fish.audio/app/developers/billing>. Plan credits and API credits are separate. |
| "Fish Audio concurrency limit reached" | Fish allows 5 concurrent requests below $100 of lifetime top-ups, 15 from $100, and 50 from $1,000. |
| "The voice id was not found" | The id is wrong or the voice is private to another account. Try `/fish voices`. |
| Voice replies stay silent | Run `/fish status`; it shows the last Fish error, with its Fish trace id when Fish sent one. |
| Fish tools missing after turning the plugin off and on (Hermes 0.21.6 and older) | Add them back with `hermes tools`. Hermes 0.21.7 and newer restore them. |
| Anything else | Run `hermes fish doctor --no-synth`; when speech itself fails, plain `hermes fish doctor` adds one short billed synthesis. Quote the Fish trace id from the error when you contact Fish Audio support. |

## Known limitations

Live dictation captions are committed phrases, rather than word-by-word interim text. It covers the composer mic,
CLI voice mode and TUI; Desktop voice conversations and messaging voice notes keep file transcription. Remote
self-hosted endpoints, Hermes 0.21.5 and plugin-host isolation also keep file transcription.

The auto-append fallback is per session. Mid-turn context compression can drop that fallback;
the model normally includes the returned `media_tag` itself.

Core gives plugins a `.mp3` path for plain `text_to_speech` and re-encodes it to Opus for voice bubbles
([upstream #133133](https://github.com/NousResearch/hermes-agent/issues/133133)). Core's text normaliser
also alters `<|speaker:N|>` markup and Drama 3 closing tags such as `</whisper>` on that path: use `fish_speak` for
multi-speaker speech and Drama 3 word tags
([upstream #133131](https://github.com/NousResearch/hermes-agent/issues/133131)).

## License

Apache-2.0. Maintained by [Electric Sheep](https://github.com/100yenadmin) with the Fish Audio team.
Fish Audio is a trademark of Hanabi AI Inc.

## Development
Weekly regression alarm: see [bench/README.md](bench/README.md).

Run JavaScript tooling from `devtools/`: `cd devtools && npm ci && npm run typecheck && npm test`.
`npm run check:bundle` checks the committed `desktop/plugin.js`; `npm run build` rebuilds it.

## Troubleshooting and reporting a bug

Run `hermes fish doctor --no-synth` to check setup without billed synthesis; it can read Fish account metadata.
Use `/fish status` for key presence, providers, voice, model, credit and the last failure; operator-managed accounts hide credit.
Under plugin-host isolation the terminal command is unavailable, so use `/fish status`.
Plain `hermes fish doctor` makes a billed synthesis; only run it with the user's OK.
Ask your Hermes agent to "report a problem with Fish Audio": it drafts a report and files nothing until you approve the draft.
Use the [bug report form](https://github.com/100yenadmin/fish-audio-hermes/issues/new?template=bug_report.yml), with versions and short, redacted logs.
Report security problems through the [private advisory form](https://github.com/100yenadmin/fish-audio-hermes/security/advisories/new).
