"""Rich ASR preserves Fish speaker markers and can write local subtitles."""
from bisect import bisect_left
import math
import re
import unicodedata
from pathlib import Path
from uuid import uuid4

from . import client, media
from .stt import MODEL_PRO, MODEL_T1
from .tool_support import require, integer
from .voices import MIMES, KINDS


def _stamp(seconds):
    ms = max(0, round(float(seconds) * 1000))
    hours, ms = divmod(ms, 3600000)
    minutes, ms = divmod(ms, 60000)
    seconds, ms = divmod(ms, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{ms:03}"


SENTENCE_END = tuple(".!?。！？…")
_CLOSERS = "\"'”’)]」』"
_TRAILING = ".,!?;:。！？，、；：…" + _CLOSERS


def _sentence_end(text):
    return text.rstrip(_CLOSERS).endswith(SENTENCE_END)


def _srt(data, *, sentences=False):
    """SRT cues of at most 7 s; with ``sentences``, a cue also closes at sentence punctuation."""
    turns = data.get("speaker_turns")
    if turns:
        cues = []
        for turn in turns:
            speaker = str(turn["speaker"])
            match = re.fullmatch(r"speaker:(\d+)", speaker)
            label = f"Speaker {int(match[1]) + 1}" if match else speaker
            cues.append({**turn, "text": f'{label}: {turn["text"]}'})
    else:
        cues = []
        for segment in data.get("segments", []):
            start, end = float(segment["start"]), float(segment["end"])
            # Word-level segments normally fit; split unusually long segments too.
            count = max(1, math.ceil((end - start) / 7))
            words = segment["text"].split()
            for i in range(count):
                left, right = start + (end - start) * i / count, start + (end - start) * (i + 1) / count
                text = " ".join(words[len(words) * i // count:len(words) * (i + 1) // count])
                if not text:
                    continue
                if cues and right - cues[-1]["start"] <= 7 and not (sentences and _sentence_end(cues[-1]["text"])):
                    cues[-1]["text"] += " " + text
                    cues[-1]["end"] = right
                else:
                    cues.append({"start": left, "end": right, "text": text})
    timed = []
    for i, cue in enumerate(cues):
        start, end = float(cue["start"]), float(cue["end"])
        if end <= start or _stamp(end) == _stamp(start):
            end = min(start + 0.5, float(cues[i + 1]["start"])) if i + 1 < len(cues) else start + 0.5
        # A coincident/backwards next start leaves no positive interval within the bound.
        if end <= start or _stamp(end) == _stamp(start):
            continue
        timed.append({**cue, "start": start, "end": end})
    return "\n\n".join(f'{i}\n{_stamp(c["start"])} --> {_stamp(c["end"])}\n{c["text"]}'
                         for i, c in enumerate(timed, 1)) + "\n"


def _vtt(srt):
    return "WEBVTT\n\n" + re.sub(r"^\S+ --> \S+$", lambda m: m[0].replace(",", "."), srt, flags=re.M)


def _folded(text):
    """Lower-cased letters, digits and spaces of ``text``, with each kept character's index in ``text``."""
    chars, index = [], []
    for i, ch in enumerate(text):
        if ch.isalnum() or ch.isspace():
            for folded in ch.lower():
                chars.append(folded)
                index.append(i)
    return "".join(chars), index


def speech_timestamps(events, audio_path):
    """fish_speak timestamps: keep the last alignment per chunk_seq, shifted onto the audio timeline."""
    latest = {}
    for event in events:
        if isinstance(event.get("alignment"), dict):
            latest[event.get("chunk_seq", 0)] = event
    segments, captions = [], []
    for seq in sorted(latest):
        event = latest[seq]
        offset = float(event.get("chunk_audio_offset_sec") or 0)
        content = event.get("content") if isinstance(event.get("content"), str) else ""
        # Fish's tokens drop punctuation and apostrophes ("I've" -> "Ive"): match folded text.
        haystack, index = _folded(content)
        cursor = 0
        for segment in event["alignment"].get("segments") or []:
            text = str(segment["text"])
            start, end = float(segment["start"]) + offset, float(segment["end"]) + offset
            segments.append({"text": text, "start": round(start, 3), "end": round(end, 3)})
            # Recover the original spelling and punctuation from the content for captions.
            needle = _folded(text)[0]
            found = haystack.find(needle, bisect_left(index, cursor)) if needle else -1
            caption = text
            if found >= 0:
                found, stop = index[found], index[found + len(needle) - 1] + 1
                # Folding drops combining marks; keep those that finish the last letter (é, Indic vowel signs).
                while stop < len(content) and unicodedata.category(content[stop]).startswith("M"):
                    stop += 1
                while stop < len(content) and content[stop] in _TRAILING:
                    stop += 1
                caption, cursor = content[found:stop], stop
            captions.append({"text": caption, "start": start, "end": end})
    result = {"segments": segments[:500]}
    if len(segments) > 500:
        result.update(segments_truncated=True, segments_total=len(segments))
    if captions:
        srt = _srt({"segments": captions}, sentences=True)
        base = Path(audio_path)
        result["srt_path"] = media.atomic_write(base.with_suffix(".srt"), [srt.encode("utf-8")])
        result["vtt_path"] = media.atomic_write(base.with_suffix(".vtt"), [_vtt(srt).encode("utf-8")])
    return result


def execute(args, key, base, session):
    path, kind, audio = media.validate_input_file(args.get("file_path"), max_bytes=50 * 1024 * 1024, kinds=KINDS | {"aac"})
    model = args.get("model", MODEL_PRO)
    require(model in {MODEL_PRO, MODEL_T1}, "Use transcribe-1-pro or transcribe-1 (exact lowercase).")
    if model == MODEL_T1 and kind == "webm":
        model = MODEL_PRO
    diarize = args.get("diarize", "auto")
    require(diarize in {"auto", "true", "false"}, "diarize must be auto, true or false.")
    for flag in ("timestamps", "tag_audio_events", "srt"):
        require(flag not in args or type(args[flag]) is bool, f"{flag} must be boolean.")
    hints = {name: args[name] for name in ("num_speakers", "min_speakers", "max_speakers") if name in args}
    require(all(integer(v, 1) for v in hints.values()), "Speaker counts must be positive integers.")
    require(not ("num_speakers" in hints and len(hints) > 1), "num_speakers cannot be combined with min/max_speakers.")
    require(hints.get("min_speakers", 1) <= hints.get("max_speakers", float("inf")), "min_speakers cannot exceed max_speakers.")
    require(not hints or model == MODEL_PRO and diarize != "false", "Speaker hints require transcribe-1-pro and diarize != false.")
    fields = {"ignore_timestamps": str(not (args.get("srt", False) or args.get("timestamps", True))).lower()}
    if model == MODEL_PRO:
        fields.update(diarize=diarize, tag_audio_events=str(args.get("tag_audio_events", True)).lower())
        fields.update({name: str(value) for name, value in hints.items()})
    if "language" in args:
        fields["language"] = args["language"]
    data = client.transcribe_audio(audio, path.name, MIMES.get(kind, "audio/aac"), fields,
                                  key=key, base_url=base, model=model, read_timeout=600)
    segments = data.get("segments", [])
    result = {"text": data["text"], "language_code": data.get("language_code"), "duration": data.get("duration"),
              "segments": segments[:500], "speaker_turns": data.get("speaker_turns", [])}
    if len(segments) > 500:
        result.update(segments_truncated=True, segments_total=len(segments))
    if data.get("request_id"):
        result["request_id"] = data["request_id"]
    if args.get("srt"):
        output = media.audio_output_dir() / f"fish-transcribe-{uuid4().hex[:12]}.srt"
        result["srt_path"] = media.atomic_write(output, [_srt(data).encode("utf-8")])
    return result
