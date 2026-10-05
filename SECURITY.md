# Security policy

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub's **Report a vulnerability** button (Security ▸ Advisories)
on this repository. Do not open a public issue. We aim to acknowledge reports within three working days.

Issues in Fish Audio's API or services belong with Fish Audio; issues in Hermes Agent itself belong with
[NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent/security).

## What the plugin does with your data

- It sends only the text and audio you ask it to process, and your API key, to Fish Audio (`api.fish.audio`, or
  the self-hosted `base_url` you configure). It sends nothing anywhere else and collects no telemetry.
- Every Fish Audio request is billed to your Fish Audio account, except requests on the free `s2.1-pro-free`
  model. Fish Audio may use free-model requests to improve its models.
- Your key is stored by Hermes in the active profile's `.env` and is read from that profile's secret scope only.
  It is never logged or included in error messages, tool results or command output.
- Files the model asks to upload (clone samples, recordings to transcribe) must be regular audio files. The plugin
  refuses symlinks, Hermes config and secret files, and SSH or cloud credential paths.
- Cloning and deleting voices go through Hermes's human-approval gate and are refused when no human is present.

## Streaming bridge

Until Hermes ships a public plugin streaming API, the plugin registers its streamer with Hermes's streaming-voice
registry through the public `tools.tts_streaming.register` call. It never rebinds Hermes module attributes. Set
`FISH_AUDIO_HERMES_NO_BRIDGE=1` to turn the bridge off. The bridge is skipped inside the plugin host process
(`plugins.isolation: host`), where voice replies use whole-file speech.
