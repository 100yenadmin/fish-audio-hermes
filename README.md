![Fish Audio for Hermes](docs/media/banner.png)

# Fish Audio for Hermes

**The official [Fish Audio](https://fish.audio) plugin for [Hermes Agent](https://github.com/NousResearch/hermes-agent),
maintained by Electric Sheep.**

Give your agent a voice: expressive speech in 80+ languages, Fish Audio's library of community voices, your own
cloned or designed voices, and accurate speech-to-text. It works in the Hermes CLI, every messaging app Hermes
supports and the Desktop app.

```bash
hermes plugins install https://github.com/100yenadmin/fish-audio-hermes --enable
hermes fish login        # paste your key from https://fish.audio/app/api-keys
```

Hermes 0.21.5 installs the plugin's Python dependencies automatically. Newer Hermes builds ask first;
add `--yes-deps` there to answer yes. After login, your agent's voice replies, read-aloud and voice notes go
through Fish Audio.

## What you get

| | |
|---|---|
| **Expressive speech** | Fish Audio S2 models follow emotion and delivery cues such as `[excited]`, `[whispering]` and `[laughing]`. Multi-speaker dialogue and custom pronunciations are supported too. |
| **Streaming voice** | CLI voice mode and Desktop voice replies start speaking on the first sentence, in about a third of a second on our measurements. |
| **Voice library** | Search Fish Audio's community voices from chat (`/fish voices narrator`) and switch with `/fish use <id>`. |
| **Your own voices** | Clone a voice from a short sample, or describe one in words and pick from the candidates. Cloning and deleting always ask for your confirmation first. |
| **Speech-to-text** | Voice notes and Desktop dictation are transcribed by `transcribe-1-pro`. `fish_transcribe` adds speaker turns, timestamps and SRT subtitles. |
| **Account at a glance** | `/fish balance` shows your API credit and plan. If credits run out you get a clear message with a top-up link, not silence. |

## Setup

1. **Get a key** at <https://fish.audio/app/api-keys>. New accounts can start on the free `s2.1-pro-free` model.
2. **Add it** with `hermes fish login` on the machine running Hermes (it also selects Fish Audio for speech and
   transcription), or in Desktop ▸ Plugins ▸ Fish Audio, or in `hermes tools` ▸ Text-to-Speech ▸ Fish Audio.
3. **Check it** with `hermes fish doctor`. Restart a running gateway or the Desktop app to pick up a new key.

Never paste API keys into a chat. `/fish` refuses them and tells you to rotate the key.

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
| `/fish use <voice-id>` | Use a voice for this profile |
| `/fish model <id>` | Pick a model (`s2.1-pro`, `s2.1-pro-free`, `s2-pro`, `s1`, `drama-3-preview`) |
| `/fish preview <voice-id> [text]` | Hear a voice (a billed synthesis) |
| `/fish balance` | API credit and plan |
| `hermes fish login · status · doctor · use` | The same from your terminal |

**Model tools** (toolset `fish_audio`; calls are billed to your Fish Audio account)

| Tool | What it does |
|---|---|
| `fish_speak` | Expressive or multi-speaker speech; with `timestamps: true`, word timings plus SRT and WebVTT subtitles |
| `fish_voices` | Search, list and inspect voices; clone, design, save, update and delete them |
| `fish_transcribe` | Rich transcription with speakers, timestamps and SRT |

Plain read-aloud uses Hermes's own `text_to_speech` tool, with Fish Audio as the provider. Include the
`media_tag` that `fish_speak` returns in the reply to deliver its audio; the plugin also appends missing audio tags
through its output hook. Plugin skills: `fish-audio:fish-audio-setup`, `fish-audio:fish-audio-expressive-speech`
and `fish-audio:fish-audio-voice-studio`.

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
  fish-audio:
    model: transcribe-1-pro
plugins:
  entries:
    fish-audio:
      settings:
        streaming: auto        # auto | "off"
        allow_free_model: true # false = never use s2.1-pro-free
```

**Which model?** If you set `tts.fish-audio.model`, that model is used. Otherwise the plugin picks `s2.1-pro` when
your account has API credit or has ever topped up, and `s2.1-pro-free` for brand-new accounts. It tells you once
that the free model is free until 30 November 2026 and that Fish Audio may use free-tier requests to improve its
models. Set `allow_free_model: false` to always use the paid model.

**Streaming voice.** With `tts.provider: fish-audio`, the plugin registers a Fish streamer with Hermes's streaming
TTS registry through its public `register` call, and streams 24 kHz PCM from `POST /v1/tts`. Each spoken sentence
is one billed request. Set `streaming: "off"` for whole-file speech; `transport: ws` switches to Fish's live
WebSocket for diagnosis. Reviewers and packagers can turn the registration off with
`FISH_AUDIO_HERMES_NO_BRIDGE=1`. When Hermes gains its plugin PCM streaming seam, the plugin uses that instead.

Under host plugin isolation (`plugins.isolation: host`, newer Hermes builds), the `hermes fish` terminal command is
unavailable and voice replies use whole-file speech instead of streaming; `/fish`, the tools, the hooks and
both providers still work. Config changes apply to the active profile; managed installs may refuse writes.

## Privacy and security

- **No telemetry.** The plugin sends nothing to anyone except Fish Audio's API, and only for the requests you make.
  Links to Fish Audio are plain links, with no tracking parameters.
- **Keys** are read from the active profile's secret scope only. They are never logged, never put in error messages,
  and never shared across profiles.
- **Files** you ask it to upload, such as clone samples and recordings to transcribe, must be regular audio files.
  It refuses symlinks, Hermes config and secret files, and SSH or cloud credential paths.
- **Approvals:** cloning and deleting voices always go through Hermes's human-approval gate. With no human present,
  they are refused.
- Report vulnerabilities privately; see [SECURITY.md](SECURITY.md).

## Disclosure

- Every Fish Audio request is billed to your Fish Audio account (the free `s2.1-pro-free` model excepted).
- Fish Audio may use free-model requests to improve its models.
- Streaming uses a small bridge into Hermes's streaming-voice registry until Hermes ships a public plugin streaming
  API. `FISH_AUDIO_HERMES_NO_BRIDGE=1` turns it off.

## Compatibility

Hermes Agent 0.21.5 or newer, Python 3.11+, macOS and Linux. CI tests it against the latest Hermes release, Hermes
`main` and the Electric Sheep fork. Windows is untested.

## Troubleshooting

| Message | Fix |
|---|---|
| "Fish Audio isn't set up for this profile" | `hermes fish login` |
| "out of API credits" | Top up at <https://fish.audio/app/developers/billing>. Plan credits and API credits are separate. |
| "rate limited" | Fish allows 5 concurrent requests below $100 of lifetime top-ups, 15 above $100, and 50 above $1,000. |
| "voice not found" | The id is wrong or the voice is private to another account. Try `/fish voices`. |
| Anything else | Run `hermes fish doctor` and include the Fish trace id when you contact Fish Audio support. |

## Known limitations

The auto-append fallback is per session. Mid-turn context compression can drop that fallback;
the model normally includes the returned `media_tag` itself.

Core gives plugins a `.mp3` path for plain `text_to_speech` and re-encodes it to Opus for voice bubbles
([upstream #133133](https://github.com/NousResearch/hermes-agent/issues/133133)). Core's text normaliser
also alters `<|speaker:N|>` markup on that path: use `fish_speak` for multi-speaker speech
([upstream #133131](https://github.com/NousResearch/hermes-agent/issues/133131)).

## License

Apache-2.0. Maintained by [Electric Sheep](https://github.com/100yenadmin) with the Fish Audio team.
Fish Audio is a trademark of Hanabi AI Inc.
