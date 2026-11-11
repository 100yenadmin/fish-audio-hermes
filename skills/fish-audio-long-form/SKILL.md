---
name: fish-audio-long-form
description: Turn a manuscript into chapter audio files with a cost estimate, manifest, resume and single-chapter redo
---

Use this workflow for audiobooks, podcasts or other long manuscripts with one narrator.
Fish synthesis spends API credits. Never ask for an API key in chat; use the setup skill.

Plan before spending:

1. Split the manuscript by headings or the user's chapter markers. Confirm the chapter titles,
   one voice and one model to use throughout, and the output format (MP3 unless requested otherwise).
   Pin the voice as its 32-character Fish voice ID (a saved name maps to an ID in `fish_settings`
   `get` → `names`) and use that ID, never a name, for every chapter: names can be re-pointed later.
2. Use the project folder the user names, defaulting to `fish-audiobook/<title-slug>/`
   in the working directory. Keep the manuscript and exact chapter text available for resume.
   If the folder already has `manifest.json`, follow Resume below instead of starting over.
   Before any synthesis, list every planned output name; if any already exists and is not a file of
   this manifest, ask before replacing it or use a new folder.
3. `fish_speak` has no numeric text cap in this plugin; Fish's API reference does not specify one.
   Use 5,000 characters per request as a workflow target, not a guaranteed API limit.
   Split longer chapters at paragraph or sentence boundaries into `chapter-NN-partMM` files;
   obey a lower service limit if reported. Ordinary chapters use `chapter-NN` filenames.
4. Call `fish_account` with `action: "estimate"`, `text: <total manuscript text>` and the chosen
   `model` BEFORE any synthesis. Also estimate each chapter (or part) for the manifest; these calls
   are free. Show the total estimate and chapter/part plan, then wait for the user's go.
   If the operator manages billing and no amount is returned, say so; do not invent a price.

Create `manifest.json` in the project folder with top-level `format` and `timestamps` preferences
and one entry per chapter:
`number`, `title`, `text_hash` (SHA-256 of the exact chapter text encoded as UTF-8), `file`,
`voice` (the pinned ID), `model`, `estimated_cost`, `status` (`pending`, `done`, `audio_only` or `failed`),
`alignment_file` when available, and `cache_file` (the audio-cache path it was copied from). Use project-relative filenames; pending entries can have a null file.
For a split chapter, keep a `parts` list with each part's text hash, file, `cache_file`, estimate, status and
alignment file; mark the chapter done only when every part is done. Sum part estimates for its cost.
Initialize the manifest before synthesis. Save it atomically after every chapter and every part
so an interruption loses at most the request in progress. Keep secrets out of the manifest.

Synthesize and save:

1. In chapter order, call `fish_speak` with the chapter/part `text`, the same explicit `voice`
   and `model`, `format: "mp3"` (or the chosen format) and `delivery: "file"`, so chat surfaces send
   a file rather than a voice note. Use `timestamps: true` when subtitles or alignment are wanted.
2. Immediately record the returned `file_path` as `cache_file`, any returned subtitle/alignment cache paths
   (`srt_cache`, `vtt_cache`, `alignment_cache`) and any `timestamps_error` or `alignment_error` in the chapter/part
   entry; save the manifest atomically BEFORE copying. For a redo, save these in a pending
   replacement entry without switching the old entry. With the agent's own file tools, copy the audio into
   the project folder using the planned chapter filename. Copy any returned `srt_path`, `vtt_path`
   and `alignment_path` alongside it, and set the copied alignment JSON's `audio` to the new basename.
   The alignment JSON contains every word segment even when the tool result stops at 500.
3. Record the files and mark the entry done only after the copies succeed; write the manifest.
   If timestamps were requested but `timestamps_error` is returned without subtitle/alignment paths,
   keep and copy the audio, record `audio_only` with the error, and tell the user. Do not redo it unless asked;
   another synthesis is billed again. A split chapter with such a part stays `audio_only`.
   If `alignment_error` is returned with subtitles, mark it done but keep the error in the entry and tell the user
   the full alignment file was not saved (the tool result holds at most 500 segments; the full file needs a billed redo).
   On a first synthesis, if a copy fails, mark the entry failed and copy again from `cache_file` without synthesis.
   If synthesis fails or is interrupted, record failed and explain any uncertain billing before retrying.

Resume by reading the manifest and recomputing text hashes; use its pinned voice ID, model, `format` and `timestamps`.
Finish pending copies from the recorded cache paths without synthesizing again. Skip done chapters/parts whose hash
still matches; never synthesize them again unless asked. If a recorded file is missing, copy it again
from `cache_file` if that still exists; otherwise ask before a billed replacement. Present changed, failed and
`audio_only` entries for approval; explain the timestamp error and redo `audio_only` entries only on request.
To redo one chapter, say it will be billed again and synthesize only that chapter's entry (its
parts if split). Copy the new audio and sidecars to new names with a revision suffix
(`chapter-NN-r2`, then `-r3`), leaving the old files in place. The manifest write is the switch:
only after every copy succeeds, save the manifest pointing at the new files with the new hash, cost
and status; then delete the old set. If anything fails before that write, delete only the new copies,
remove the pending replacement from the manifest, and leave the old files and entry as they were.

For multi-voice casting and timeline editing, hand off to Fish Audio's Story Studio through
Fish's own MCP server or web app. It signs in to the user's Fish account with OAuth, not this
plugin's API key, and spends the Fish app plan's credits, separate from API credits.
On Hermes 0.21.7 or newer, the README's MCP setup command is:

```sh
hermes mcp add fish-audio --url https://api.fish.audio/mcp --auth oauth --connect-timeout 180
```

Let the user choose that handoff; do not synthesize the same chapter through both tools.
