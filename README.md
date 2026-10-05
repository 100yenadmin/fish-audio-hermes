# Fish Audio for Hermes

Expressive [Fish Audio](https://fish.audio) voices for [Hermes Agent](https://github.com/NousResearch/hermes-agent):
text-to-speech, speech-to-text, a searchable voice library, voice cloning and voice design — in the CLI, every
messaging gateway and the Desktop app.

> **Status:** pre-release, under active development. Not yet in the Hermes plugin catalog.

## Install (preview)

```bash
hermes plugins install https://github.com/100yenadmin/fish-audio-hermes --enable --yes-deps
```

You need a Fish Audio API key — create one free at <https://fish.audio/app/api-keys>.

## Tools (preview)

`fish_speak` makes expressive or multi-speaker audio. Use Hermes's `text_to_speech` for plain
read-aloud. Include the returned `media_tag` verbatim in the reply to deliver the audio;
the plugin also appends missing audio tags through its output hook. Speech timestamps are coming in v0.2.

`fish_voices` searches the library, manages your voices, clones with the speaker's permission,
and designs voices. Clone and delete request human approval. Clones and saved designs are private;
publish through Fish's web flow. Design previews return opaque tokens that expire after one hour.

`fish_transcribe` preserves speaker markers and emotion cues, with optional timestamps and SRT subtitles.
All these calls are billed to your Fish Audio account. Setting `allow_free_model: false` in
`plugins.entries.fish-audio.settings` forces paid `s2.1-pro` whenever the free model is selected.

## Commands (preview)

Use `/fish help` in chat for `status`, `voices`, `use`, `model`, `preview`, and `balance`.
`/fish preview <voice_id> [text]` returns a voice reply through Hermes's media delivery.
Never paste a key into chat; enter it locally with `hermes fish login` or Desktop ▸ Plugins ▸ Fish Audio.

`hermes fish` prints help. It supports `login [--key-stdin] [--yes]`, `status`,
`doctor [--no-synth]`, and `use <voice_id>`. Login asks before changing existing providers.
Doctor's synthesis check is billed and deletes its temporary audio; use `--no-synth` to skip it.
Config changes apply to the active profile; managed installs may refuse writes.
Host plugin isolation does not expose `hermes fish` or these hooks.

Plugin skills: `fish-audio:fish-audio-setup`, `fish-audio:fish-audio-expressive-speech`,
and `fish-audio:fish-audio-voice-studio`. The parity map tracks implemented, tested and deferred API fields.

## Known limitations

The auto-append fallback is per session. Mid-turn context compression can drop that fallback;
the model normally includes the returned `media_tag` itself.

Core gives plugins a `.mp3` path for plain `text_to_speech` and re-encodes it to Opus for voice bubbles
([upstream #133133](https://github.com/NousResearch/hermes-agent/issues/133133)). Core's text normaliser
also alters `<|speaker:N|>` markup on that path: use `fish_speak` for multi-speaker speech
([upstream #133131](https://github.com/NousResearch/hermes-agent/issues/133131)).

## License

Apache-2.0. Fish Audio is a trademark of Hanabi AI Inc.
