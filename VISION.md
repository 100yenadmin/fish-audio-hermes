# Vision

## Goal

The official Fish Audio plugin for Hermes Agent. Any Hermes user installs it and sets it up in minutes, and their agent
then speaks and listens with Fish Audio: speech, transcription, the voice library, voice cloning and design, and the
Fish account, wherever they use Hermes. The plugin turns installs into Fish Audio sign-ups and paid use through the
product itself (plain sign-up, key and top-up links), never through tracking.

## Users

- **Hermes users** who want their agent to talk and to understand voice notes, in any of Fish's 80+ languages.
- **Creators** who audition and design voices, voice long scripts in chapters, and make subtitles.
- **Developers and operators** who script it (`hermes fish … --json`) or run agents on an account they own
  (operator-managed keys hide billing from the people using the agent).

## Coverage on every Hermes surface

| Surface | What the plugin provides |
|---|---|
| Desktop app and `hermes serve` | Voices page (library, preview, use, favourites, clone, design, account), the Fish Audio settings page, streaming read-aloud and voice replies, composer dictation. Works for a local agent and for an agent on another machine. |
| Telegram and every other messaging app Hermes supports | Voice replies as voice notes or files, voice notes transcribed on the way in, `/fish` commands, scheduled spoken briefings. Setup links are sent in chat; a key is never asked for in chat. |
| CLI and TUI | `hermes fish` (login, status, doctor, use, scriptable JSON commands), `/fish`, streaming voice mode, live dictation. |
| API server | **Decision: no API-server-specific audio path.** The model tools run as on any surface, but Hermes's API server has no audio API (it inlines images only), so a generated audio file comes back as a path on the agent's machine. Audio delivery over the API server needs Hermes core support first; the plugin will use it when it exists rather than build its own. |

## Non-goals

- Sound effects, music, voice changer, image and video: Fish offers these only in its web app or MCP server, with no
  API to call. Story Studio is used through Fish's MCP server, not rebuilt here.
- Telemetry, UTM tags, referral codes or per-user identifiers.
- Changes to Hermes core shipped inside the plugin: generic gaps go upstream as issues or pull requests.
- Code for one particular hosted deployment: managed setups compose the plugin from outside it.

Phone agents (Fish's agent API) stay in Later, and only if Fish funds them.

## Roadmap

The pinned issue [#104](https://github.com/100yenadmin/fish-audio-hermes/issues/104) is the roadmap: what shipped, what
is next, and the Later milestone.
