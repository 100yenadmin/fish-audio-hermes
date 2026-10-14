# AGENTS.md

Guidance for AI coding agents, and for people, working in this repository.

## What this is

Fish Audio is a standalone Hermes Agent plugin for expressive speech, transcription, voice search, cloning and voice design. It registers speech providers, tools, chat commands and CLI diagnostics, and supplies a Hermes Desktop Voices page. Fish API requests use the active profile's account; synthesis, transcription, cloning and design can incur charges.

## Layout

- `fish_audio/`: providers, API client, commands, hooks and profile settings.
- `plugin.yaml` and `__init__.py`: plugin metadata and loader entry point.
- `tests/`: mocked unit tests and opt-in Hermes loader integration tests.
- `devtools/`: Desktop TypeScript source, tests and bundle tooling.
- `desktop/plugin.js`: committed generated Desktop bundle.
- `dashboard/`: gateway API routes and a hidden manifest; no web-dashboard UI.
- `skills/`: the three currently shipped user skills.
- `.github/workflows/`: unit, Desktop, compatibility and scheduled alarm checks.

## Setup and checks

CI is `.github/workflows/ci.yml`. Run the same suites locally.

Python unit suite (CI runs Python 3.11, 3.13 and 3.14):

```bash
python -m pip install "httpx>=0.27,<1" "websockets>=13,<16" "msgpack>=1.0,<2" "pyyaml>=6,<7" "pytest==8.4.2" "respx==0.22.0" "fastapi==0.133.1" "starlette==1.3.1"
python -m pytest -q
python scripts/parity_check.py
```

Desktop half (Node 24):

```bash
cd devtools
npm ci
npm run typecheck
npm test
npm run check:bundle   # the committed desktop bundle must match the source
```

CI also checks the plugin inside pinned Hermes builds (`hermes plugins validate`, SDK and loader checks). Leave those to CI.

## Rules

- Keep each change small and focused: one change per PR.
- Don't bump versions, create tags, or add CHANGELOG release headings. Maintainers cut releases.
- Never call the live Fish API in tests: use `respx.mock`, monkeypatched transports and synthetic audio fixtures, as in `tests/test_cli.py`.
- Do not run the billed benchmark or network drift jobs as routine local checks.
- Regenerate `desktop/plugin.js` with the build script when Desktop source changes; CI's bundle check must pass. Do not hand-edit generated files.
- Preserve per-profile secret/config scopes and Hermes approval gates. Do not expose API keys in output.
- Never commit secrets, tokens, real user data, or machine-specific paths.
- Tests use a temporary `HERMES_HOME`, never a real one.

## Filing issues

- Search first: `gh issue list -R 100yenadmin/fish-audio-hermes --state all --search "<key words>"`.
- Use the bug form headings: What happened, What you expected, Steps to reproduce, Plugin version, Hermes version, Where it happened, Operating system, Diagnostics, Relevant log lines.
- Maintainers filing from the CLI always pass labels: `--label bug,triage-pending` (or `triaged` once triaged), plus `agent-drafted` when an agent wrote it.

## Security

Report vulnerabilities privately: https://github.com/100yenadmin/fish-audio-hermes/security/advisories/new
