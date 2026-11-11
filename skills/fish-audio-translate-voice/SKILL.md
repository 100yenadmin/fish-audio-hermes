---
name: fish-audio-translate-voice
description: Transcribe a clip, translate it and voice it again with one Fish voice per speaker, plus both transcripts and new subtitles
---

Use this for "translate this recording and read it back in two voices" and similar requests. The result is a
voice-over in the new language: the timing follows the translated text, not the original lips or pauses. Call it
a voice-over, never a synchronized dub. Transcription and speech are billed to the Fish account behind this
agent's key; never ask for an API key in chat (use the setup skill).

1. **Transcribe with speakers.** Call `fish_transcribe` with the clip's `file_path`, `diarize: "true"` and, when
   the user knows it, `num_speakers`. Keep `speaker_turns`: each turn has `speaker` (`speaker:0`, `speaker:1`, …),
   `text`, `start` and `end`. If the result has no speaker turns, say the clip was heard as one speaker and ask
   whether to continue with one voice.
2. **Show the plan before more billing.** List the speakers in order of first appearance with a short sample line
   each, the target language, and the voice you propose for each speaker. Call `fish_account` with
   `action: "estimate"`, the original text and the model to show the speech cost (free). Wait for the user's go.
3. **Choose one voice per speaker.** Search the library with `fish_voices` `action: "search"`, `language` set to
   the target language and a query for the speaker's character ("calm male narrator", "bright young woman").
   Offer saved names the user already has. Do not clone the people in the clip from this recording: cloning
   needs the speaker's own consent and goes through the voice-studio skill and Hermes's approval. Pin each choice
   as its 32-character voice ID so the speakers cannot drift.
4. **Translate turn by turn yourself.** Keep one translated turn per original turn, the same speaker on each, and
   the same order. Keep names, numbers and product terms as the user wants them; ask about ambiguous terms instead
   of guessing. Do not add or drop content. Fish emotion cues such as `[laughing]` may carry over when the original
   clearly has them; at most one per sentence.
5. **Voice it.** Build one text with a `<|speaker:N|>` marker before each turn, where N is the index of that
   speaker's voice in `speakers` (`speaker:0` → index 0). Call `fish_speak` once with that `text`, `speakers` as
   the list of pinned IDs in speaker order (2–4 voices; one speaker uses `voice` instead), a multi-speaker model
   (`s2.1-pro`, `s2-pro` or `drama-3`; never `s1`), `language` set to the target language,
   `timestamps: true` for subtitles and `format` as the user wants (MP3 for a file to keep). More than four
   speakers or a very long clip: split at turn boundaries and voice the parts in order.
6. **Deliver.** Return the audio, the subtitles (`srt_path`, `vtt_path`), and both transcripts as text the user
   can save: the original with speaker labels and times, and the translation with the same labels. Say which
   voice read each speaker and that the result is a voice-over.

If one speaker's voice sounds wrong, redo only after the user agrees: it bills the whole clip again, because
the audio is one file.
