---
name: fish-audio-voice-studio
description: Find, clone, design and manage a Fish Audio voice for this agent
---

Use `fish_voices` for the voice library and voice creation. Calls are billed to the user's Fish Audio account.
Never ask for an API key in chat; use the setup skill or `hermes fish login` on the Hermes machine.

Search public voices with `action: search`, a query/language/tags, and at most 20 results per page.
Use `action: mine` for voices in the user's Fish account. Search windows end at 1,000 results;
"1000+" is a lower bound. Use `action: get` with a voice id to inspect text-only sample descriptions.

For cloning, ask the user to confirm the speaker's permission. Set `consent: true` only after that confirmation.
Use 10–30 seconds of clean audio, one speaker, without music or background noise. Supply 1–20 local sample
paths and optional matching transcripts. Hermes asks for human approval before cloning. The clone is private
and uses fast training. Publish through Fish's web flow when the user wants a public voice.

For a new voice: call `design` with a short instruction, optional reference text/language, and `n: 2` or `n: 3`.
Include each returned WAV `media_tag` so the user can listen. Ask which candidate they prefer, then call
`save` with that candidate's opaque `design_token` and a title. Tokens expire after one hour and the process
holds at most 64 candidates. If the token expired or Hermes restarted, design again. The plugin keeps the
signature privately; the model only needs the token.

Persona-voice recipe:

1. Read this agent's SOUL.md or persona when the user asks for a voice that fits the agent.
2. Write a 1–2 sentence voice instruction covering gender/presentation, age, accent, tone and pacing.
   Do not send the whole persona or unrelated private context to Fish.
3. `design` with `n: 3` and a short representative reference text.
4. Play all candidates by including their `media_tag` values. Let the user choose.
5. Save the chosen `design_token` with a descriptive title, then `/fish use <saved id>`.

For metadata changes, ask the user to approve the update, then call `update` with the id and changed
`title`, `description`, or `tags`. For permanent deletion, confirm the intended voice and call `delete`;
Hermes's human approval gate must approve it. Update uses conversational user approval; the plugin's
hard approval gate covers clone and delete. Avoid switching away from another Fish provider when selecting a voice.
