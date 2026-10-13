---
name: fish-audio-expressive-speech
description: Expressive Fish speech with emotion cues, multiple speakers and pronunciations
---

Use `fish_speak` when the user asks for expressive or multi-speaker speech. For plain read-aloud,
use Hermes's `text_to_speech`. Calls are billed to the Fish Audio account behind this agent's key. Include the returned
`media_tag` verbatim so the audio is delivered; the output hook can append missing audio tags.
Use one speech tool per request (`text_to_speech`, `fish_speak`, or a speech tool from Fish's MCP server), never two for the same words.

S1 is deprecated; from 2026-12-31 requests use S2.1 Pro.
For released `drama-3` (older preview id: `drama-3-preview`), put plain-language directions in square brackets
before the words they apply to, in any language. Put sounds and pauses such as `[sigh]` and `[pause]` where they happen.
Wrap only the words to change in paired tags: `<whisper>…</whisper>`, `<emphasis>`, `<soft>`, `<fast>`, `<slow>`,
`<stress>`, `<forceful>`; close each tag. Experimental language tags and `<|phoneme_start|>…<|phoneme_end|>` are
also supported. For dialogue, `speakers` sends a `reference_id` array; use `<|speaker:N|>` to select its indexed voice.
Send angle-bracket tags through `fish_speak`: Hermes's `text_to_speech` cleanup breaks closing tags such as
`</whisper>`. Square-bracket directions and sounds survive it.

S2 models use square brackets. The documented groups are:

- **Basic Emotions (24 expressions):** `[happy]`, `[sad]`, `[angry]`, `[excited]`, `[calm]`, `[nervous]`, `[confident]`, `[surprised]`, `[satisfied]`, `[delighted]`, `[scared]`, `[worried]`, `[upset]`, `[frustrated]`, `[depressed]`, `[empathetic]`, `[embarrassed]`, `[disgusted]`, `[moved]`, `[proud]`, `[relaxed]`, `[grateful]`, `[curious]`, `[sarcastic]`.

- **Advanced Emotions (25 expressions):** `[disdainful]`, `[unhappy]`, `[anxious]`, `[hysterical]`, `[indifferent]`, `[uncertain]`, `[doubtful]`, `[confused]`, `[disappointed]`, `[regretful]`, `[guilty]`, `[ashamed]`, `[jealous]`, `[envious]`, `[hopeful]`, `[optimistic]`, `[pessimistic]`, `[nostalgic]`, `[lonely]`, `[bored]`, `[contemptuous]`, `[sympathetic]`, `[compassionate]`, `[determined]`, `[resigned]`.

- **Tone Markers (6 expressions):** `[in a hurry tone]`, `[shouting]`, `[screaming]`, `[whispering]`, `[soft tone]`, `[emphasis]`.

- **Audio Effects (11 expressions):** `[laughing]`, `[chuckling]`, `[sobbing]`, `[crying loudly]`, `[sighing]`, `[groaning]`, `[panting]`, `[gasping]`, `[yawning]`, `[snoring]`, `[clear throat]`.

- **Special Effects:** `[audience laughing]`, `[background laughter]`, `[crowd laughing]`, `[break]`, `[long-break]`.

S2 also allows free-form cues such as `[very excited]`. For S1, use parentheses such as `(happy)`;
the plugin converts known bracket cues automatically. Keep emotion cues at the beginning of a sentence.
Tone and sound effects can occur anywhere; put `[emphasis]` immediately before the emphasized words.
Use **at most 3 cues per sentence**. Cues direct performance and should not be spoken as words.

For multiple speakers, pass `speakers` as 2–4 Fish voice ids and label the text with zero-based
`<|speaker:N|>` markers. Use a multi-speaker model; S1 refuses this mode. Avoid core text normalization
by calling `fish_speak` directly. `pronunciations` is a map of words to spoken forms, at most 200 entries.
`fish_speak` applies the configured synthesis knobs. Call speed wins over configured speed; call
pronunciations merge with configured inline entries (call entries win), or replace configured references.
Native `.ogg` is the default voice reply; use `format: wav` for a file. Pass `timestamps: true` for word
timings (`segments`) and subtitle files (`srt_path`, `vtt_path`).

Worked examples (replace the voice ids with voices the user selected):

1. A warm greeting:
   `fish_speak({"text":"[happy][soft tone] Welcome back. It's good to hear from you.","format":"ogg"})`.
   Return its `media_tag` alongside the reply.
2. A short exchange:
   `fish_speak({"text":"<|speaker:0|>[curious] Ready? <|speaker:1|>[confident] Let's begin.","model":"s2.1-pro","speakers":["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"]})`.
   Each marker refers to that index in the voice list.
3. A pronunciation-aware narration:
   `fish_speak({"text":"[calm] Codex is ready.","model":"s2.1-pro","pronunciations":{"Codex":"code ex"},"format":"wav"})`.
   The plugin keeps `[calm]` in bracket syntax and returns a plain audio-file media tag.
4. A Drama 3 aside:
   `fish_speak({"text":"[sound relieved] We made it. [sigh] <whisper>Keep this between us.</whisper>","model":"drama-3","format":"ogg"})`.
   This goes through `fish_speak` for the paired word tags, as explained above.
