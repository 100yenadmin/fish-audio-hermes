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

## License

Apache-2.0. Fish Audio is a trademark of Hanabi AI Inc.
