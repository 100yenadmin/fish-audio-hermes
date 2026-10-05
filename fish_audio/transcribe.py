"""Rich ASR preserves Fish speaker markers and can write local subtitles."""
import math
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


def _srt(data):
    turns = data.get("speaker_turns")
    if turns:
        cues = [{**turn, "text": f'{turn["speaker"]}: {turn["text"]}'} for turn in turns]
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
                if cues and right - cues[-1]["start"] <= 7:
                    cues[-1]["text"] += " " + text
                    cues[-1]["end"] = right
                else:
                    cues.append({"start": left, "end": right, "text": text})
    return "\n\n".join(f'{i}\n{_stamp(c["start"])} --> {_stamp(c["end"])}\n{c["text"]}'
                         for i, c in enumerate(cues, 1)) + "\n"


def execute(args, key, base, session):
    path, kind = media.validate_input_file(args.get("file_path"), max_bytes=50 * 1024 * 1024, kinds=KINDS | {"aac"})
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
    fields = {"ignore_timestamps": str(not args.get("timestamps", True)).lower()}
    if model == MODEL_PRO:
        fields.update(diarize=diarize, tag_audio_events=str(args.get("tag_audio_events", True)).lower())
        fields.update({name: str(value) for name, value in hints.items()})
    if "language" in args:
        fields["language"] = args["language"]
    data = client.transcribe_audio(path.read_bytes(), path.name, MIMES.get(kind, "audio/aac"), fields,
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
