# Security policy

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub's **Report a vulnerability** button (Security ▸ Advisories)
on this repository. Do not open a public issue. We aim to acknowledge reports within three working days.

Issues in Fish Audio's API or services belong with Fish Audio; issues in Hermes Agent itself belong with
[NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent/security).

## What the plugin does with your data

- **Where requests go.** Every request goes to Fish Audio at `api.fish.audio`, or to the `base_url` you configure;
  whatever host you configure receives your API key and the requests below. The plugin sends nothing anywhere
  else and collects no telemetry.
- **What requests carry.** Your API key; the text you ask it to speak; the audio you ask it to transcribe or clone,
  with the uploaded file names; voice titles, tags, descriptions and design instructions for voice-library actions;
  and wallet and plan reads for balance and model choice. Headers name the plugin and its version, and pass on an
  OpenTelemetry trace context when one is active.
- **Billing.** Synthesis, transcription, cloning and voice design are paid Fish Audio requests billed to your Fish
  account, except requests on the free `s2.1-pro-free` model. Fish Audio may use free-model requests to improve
  its models.
- **Keys.** Hermes stores your key in the active profile's `.env`. The plugin reads it through the profile's secret
  scope, so when one Hermes process serves several profiles each uses its own key; a single-profile install may
  also read `FISH_API_KEY` from the process environment. Keys are never logged or included in error messages, tool
  results or command output.
- **Files.** Files the model tools upload (`fish_voices` clone samples, `fish_transcribe` recordings) must be
  regular audio files. They refuse symlinks, Hermes config and secret files, and SSH or cloud credential paths.
  Audio that Hermes itself hands to the speech-to-text provider (voice notes, dictation) is read as given.
- **Approvals.** Cloning and deleting voices go through Hermes's approval gate and follow your Hermes approval
  settings. By default Hermes asks you, and refuses when no one is there to answer.

## Streaming bridge

Until Hermes ships a public plugin streaming API, the plugin registers its streamer with Hermes's streaming-voice
registry through the public `tools.tts_streaming.register` call. It never rebinds Hermes module attributes. Set
`FISH_AUDIO_HERMES_NO_BRIDGE=1` to turn the bridge off. The bridge is skipped inside the plugin host process
(`plugins.isolation: host`), where voice replies use whole-file speech.
