---
name: fish-audio-setup
description: "Set up Fish Audio voices for Hermes: key, provider, voice, model"
required_environment_variables:
  - name: FISH_API_KEY
    prompt: Fish Audio API key
    help: https://fish.audio/app/api-keys
---

Set up Fish Audio for the active Hermes profile.

Operator-managed agents: when `/fish balance`, `/fish status` or a Fish result says the operator manages this
agent's voice account or setup, the operator owns the key and billing. Then only steps 3 and 4 below apply. Skip
every other step, link and troubleshooting note in this skill: never send the user to Fish's key, billing or plan
pages, and for key, credit or setup problems ask them to contact the operator of this agent.
Otherwise, follow the whole skill.

1. Create a key at https://fish.audio/app/api-keys. Never ask the user to paste it into chat.
   Use Desktop ▸ Settings ▸ Plugins ▸ Fish Audio (Capabilities ▸ Plugins on older Desktop), `hermes tools`, or `hermes fish login` on the Hermes machine.
2. `hermes fish login` selects Fish Audio for both Text-to-Speech and Speech-to-Text. It asks before
   changing existing providers; `hermes fish login --yes` permits those switches. Otherwise pick Fish Audio
   for Text-to-Speech in `hermes tools`, and for Speech-to-Text in Desktop settings or with
   `hermes config set stt.provider fish-audio` (`hermes tools` has no plugin speech-to-text rows).
   If a Hermes gateway or the desktop app is already running for this profile, restart it to pick up the new key.
3. Run `/fish voices <description>` and choose `/fish use <id>`. An existing provider is preserved.
   Browse more voices at https://fish.audio/discovery.
4. Use `/fish model s2.1-pro` for paid synthesis, or `/fish model s2.1-pro-free` for the free model.
   Managed profiles with `allow_free_model: false` substitute the paid model even for explicit free selection.
5. Check `/fish status`, `hermes fish status`, and `hermes fish doctor --no-synth`.
   A doctor synthesis check is billed; the audio is temporary and deleted afterward.

Model defaults use the active account's API wallet: paid credit, a top-up history, or free API credit selects
`s2.1-pro`. An unknown wallet also uses that model. A confirmed empty wallet may select the free model:

Using Fish Audio's free s2.1-pro-free model (free until 30 November 2026; Fish may use free-tier requests to improve its models). Top up at https://fish.audio/app/developers/billing to use s2.1-pro.

Calls are billed to the Fish Audio account behind this agent's key. App plan credits are separate from API credits.
`/fish balance` shows both; API billing is at https://fish.audio/app/developers/billing.

Troubleshooting by error kind:

- `credential`: create/rotate a key at https://fish.audio/app/api-keys and save it locally with login.
- `quota`: top up API credit; selecting an app plan does not imply API credit.
- `rate_limit`: wait and retry; concurrency tiers are 5 below $100, 15 at $100, and 50 at $1,000 in top-ups.
- `voice_not_found` / `not_found`: choose an accessible voice from the library.
- `invalid_request`: check voice/model/settings; for ASR decoding, pro accepts WebM and more formats.
- `too_large` / `unsupported_media`: reduce the input or use supported audio.
- `availability`: retry later. Include only the Fish-supplied request/trace id when reporting a problem.

Under host plugin isolation the `hermes fish` terminal command is unavailable and voice replies use whole-file speech instead of streaming; `/fish`, the tools, the hooks and both providers still work.
