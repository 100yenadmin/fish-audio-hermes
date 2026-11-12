---
name: fish-audio-voice-studio
description: Find, clone, design and manage a Fish Audio voice for this agent
---

Use `fish_voices` for the voice library and voice creation. Calls are billed to the Fish Audio account behind this agent's key.
Never ask for an API key in chat; use the setup skill or `hermes fish login` on the Hermes machine.

When a Fish result or `/fish` reply says the operator manages this agent's voice account, `update` and `delete`
are refused: do not offer them, and skip the metadata and deletion steps at the end of this skill. There, `mine`
lists only the voices assigned to this agent through its saved names, and a voice you clone or save gets a saved
name automatically (or a note saying why not). Search, get, audition, clone, design and save still work.

Search public voices with `action: search`, a query/language/tags, and at most 20 results per page.
Search also accepts `author_id`, `title_language` (one code or 1–10 codes) and boolean `licensed`;
these filters apply only to search. Results include `has_more` when Fish returns a boolean.
Use `action: mine` for your own voices (on an operator-managed agent, the voices assigned to it). Search windows end at 1,000 results;
"1000+" is a lower bound. Use `action: get` with a voice id to inspect text-only sample descriptions.

## Choosing a voice

For a request like "deep British narrator", search with query words such as `British narrator`, `language: "en"`
and relevant `tags` such as `deep`; use only query, language and tags for these traits, not invented accent/gender filters.
Pick 2–4 distinct candidates. Ask for the user's own short passage if they gave none, keeping it at most 200
characters to keep cost low. Call `fish_voices` once with `action: "audition"`, those `voices` and that `text`;
each clip is a billed synthesis. Present every returned `media_tag` verbatim on its own line, labelled with its
voice title, plus a short text line. Say plainly when the match is uncertain: titles are community-written and
do not guarantee accent or gender. Then offer `/fish use <id>` or a saved name via `/fish alias add <name> <id>`.

For cloning, ask the user to confirm the speaker's permission. Set `consent: true` only after that confirmation.
Use 10–30 seconds of clean audio, one speaker, without music or background noise. Supply 1–20 local sample
paths and optional matching transcripts. Hermes asks for human approval before cloning. The clone is private
and uses fast training; `visibility: unlist` is also available. Optional `generate_sample` is boolean,
and `cover_image_path` accepts a safe PNG, JPEG or WebP file of at most 5 MiB. Publish through Fish's web flow when the user wants a public voice.

For a new voice: call `design` with a short instruction, optional reference text/language, and `n: 2` or `n: 3`.
Optional `num_step` is an integer from 1–128; `guidance_scale` and `instruct_guidance_scale` are finite
numbers at least zero. Include each returned WAV `media_tag` so the user can listen. Ask which candidate they prefer, then call
`save` with that candidate's opaque `design_token` and a title. Tokens expire after one hour and the process
holds at most 64 candidates. If the token expired or Hermes restarted, design again. The plugin keeps the
signature privately; the model only needs the token.

Persona-voice recipe:

1. Read this agent's SOUL.md or persona when the user asks for a voice that fits the agent.
2. Write a 1–2 sentence voice instruction covering gender/presentation, age, accent, tone and pacing.
   Do not send the whole persona or unrelated private context to Fish.
3. `design` with `n: 3` and a short representative reference text.
4. Play all candidates by including their `media_tag` values. Let the user choose.
5. Save the chosen `design_token` with a descriptive title, then call `fish_settings` with `action: "use"` and `voice: "<saved id>"` to make it this agent's voice. A saved name or unique title of your own voice also works. A person can use `/fish use <saved id>` or `/fish use Eva`.

Before a long or Drama 3 speech job, call `fish_account` with `action: "estimate"`, the text and the intended model.

Skip the next two steps when the operator manages the account. For metadata changes, ask the user to approve
the update, then call `update` with the id and changed
`title`, `description`, `tags`, `visibility` (private or unlist), or `cover_image_path`. For permanent deletion, confirm the intended voice and call `delete`;
Hermes's human approval gate must approve it. Update uses conversational user approval; the plugin's
hard approval gate covers clone and delete. Avoid switching away from another Fish provider when selecting a voice.
