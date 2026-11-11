---
name: fish-audio-translate-voice
description: Transcribe a clip, translate it and voice it again with one Fish voice per speaker, plus both transcripts and new subtitles
---

Use this for "translate this recording and read it back in two voices" and similar requests. The result is a
voice-over in the new language: its timing follows the translated text, not the original lips or pauses. Call it a
voice-over, never a synchronized dub. Never ask for an API key in chat (use the setup skill).

Transcription and speech are billed. When a Fish result says the operator manages this agent's account, estimates
show bytes and no price: show those, say the operator handles billing, and never say the user's account pays.

1. **Ask before transcribing.** Say transcription is billed by audio length; for a long clip, `fish_account`
   `action: "estimate"` with `audio_seconds` gives the price (free). Files over 50 MB are refused. Go on only after
   the user agrees, unless they already asked you to go ahead.
2. **Transcribe with speakers.** Call `fish_transcribe` with `file_path`, `diarize: "true"` and, when the user knows
   it, `num_speakers`. Keep `speaker_turns`: each has `speaker` (`speaker:0`, `speaker:1`, …), `text`, `start` and
   `end`. If there are no turns, or every turn has the same speaker, say speakers could not be told apart and ask
   whether to use one voice.
3. **Translate turn by turn yourself.** One translated turn per original turn, the same speaker on each, the same
   order. Keep names, numbers and product terms as the user wants; ask about ambiguous terms instead of guessing.
   Add or drop nothing. Fish cues such as `[laughing]` may carry over where the original clearly has them; at most
   one per sentence.
4. **Choose one voice per speaker.** Search with `fish_voices` `action: "search"`, `language` set to the target
   language and a short keyword `query` ("narrator", "calm") or `tags`; or use the user's saved names. Keep each
   returned voice ID exactly as given and use it, not a name, for every call. Do not clone the people in the clip
   from this recording, and do not pick a voice that imitates an identifiable real person unless the user confirms
   that person's permission; otherwise offer a generic voice. Cloning goes through the voice-studio skill and
   Hermes's approval.
5. **Build the speech text, then estimate it.** Split the turns into parts in order, each with at most four distinct
   speakers (one part when possible). In each part, number its speakers 0, 1, … by first appearance in that part, and
   put `<|speaker:N|>` before each turn. Write down the map from transcript label to part number to voice ID. A part
   with one speaker uses `voice` and no markers. Call `fish_account` `action: "estimate"` with the exact final text
   of all parts (markers and cues included) and the chosen model. Show the speakers, voices, target language, parts
   and estimate, then wait for the user's go.
6. **Voice each part.** Call `fish_speak` with the part's `text`, `speakers` as its voice IDs in its own 0, 1, …
   order (2–4 voices) or `voice` for one speaker, a multi-speaker model (`s2.1-pro`, `s2-pro` or `drama-3`; never
   `s1`), `language` set to the target language, `timestamps: true`, and `format` as the user wants (MP3 for a file
   to keep). Each part is its own audio file with subtitles starting at 0:00.
7. **Deliver.** Return the audio file(s), the subtitles when `srt_path`/`vtt_path` are present (they carry no speaker
   names), and both transcripts as text the user can save: the original with speaker labels and times, and the
   translation with the same labels. If a result has `timestamps_error` or no subtitle paths, deliver the audio and
   say subtitles were not available; ask before any billed retry. Say which voice read each speaker and that the
   result is a voice-over.

A redo of one speaker's voice re-bills that whole part, because a part is one file; ask first.
