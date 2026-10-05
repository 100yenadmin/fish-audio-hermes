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
- **Approvals.** Cloning and deleting voices from chat or the model tools go through Hermes's approval gate and
  follow your Hermes approval settings. By default Hermes asks you, and refuses when no one is there to answer.

## Desktop Voices page

The Desktop Voices page adds gateway routes under /api/plugins/fish-audio/, behind the Hermes dashboard's
existing authentication. On that page, previews and voice designs are billed; cloning a voice or saving a
designed one creates a voice in your Fish account; cloning and deleting are confirmed on the page (a
speaker-consent box, a typed voice name) instead of through Hermes's approval gate; and Use sets the profile's
Fish voice, and its speech provider when none is set. Preview and design audio files are deleted from the
gateway once returned; a design's audio is held in gateway memory so you can save it, until it is saved, a
later design or save finds it over an hour old, or the gateway stops; clone uploads are deleted after the
clone, or by the next upload once idle for 15 minutes. With a key set, Desktop reads your Fish wallet when an
agent is selected and about every five minutes, for the status-bar credit; favourites stay in Desktop's plugin
storage on that computer, and fetched previews stay in the window's memory until it closes.

- The routes use the same key, settings and Fish Audio requests as the tools, for the profile Desktop has
  selected; with no key they refuse without calling Fish Audio. Responses never carry the key, and a voice-design
  signature stays on the gateway behind an account-bound token that is refused once an hour old.
- **Use** writes the voice and, when unset, the speech provider into the selected profile's `config.yaml`,
  with the same writer as `/fish use`.
- Clone uploads arrive in chunks of JSON and base64, never as multipart forms. The gateway names each upload
  itself (a random id, never a client path), writes it with owner-only permissions under the profile's
  `cache/fish-audio/uploads`, refuses symlinks along that path inside the profile, caps it at 10 MB per file and 3 files, and checks that it is audio
  before sending it to Fish Audio. A clone is refused unless the consent box is ticked.
- Deleting checks that the voice belongs to your Fish account first.

## Streaming bridge

Until Hermes ships a public plugin streaming API, the plugin registers its streamer with Hermes's streaming-voice
registry through the public `tools.tts_streaming.register` call. It never rebinds Hermes module attributes. Set
`FISH_AUDIO_HERMES_NO_BRIDGE=1` to turn the bridge off. The bridge is skipped inside the plugin host process
(`plugins.isolation: host`), where voice replies use whole-file speech.
