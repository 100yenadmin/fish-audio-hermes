"""Three explicit Fish Audio tools; ordinary read-aloud stays with Hermes TTS."""
from uuid import uuid4

from . import client, hooks, media, settings, tags
from .models import MODELS
from .secrets import fish_api_key
from .tool_support import require, voice_id, run

BILLING = " Calls are billed to the user's Fish Audio account."
SPEAK_DESCRIPTION = ("Use for expressive or multi-speaker speech with Fish Audio emotion cues like [excited] "
                     "or [whispering]. For plain read-aloud use text_to_speech." + BILLING)


def _speak(args, key, base, session, *, record_media=True):
    text = args.get("text")
    require(isinstance(text, str) and bool(text.strip()), "Nothing to say: the text is empty.")
    model, defaulted = settings.resolve_model(args.get("model"), key=key, base_url=base, prefer_call=True)
    row = next(row for row in MODELS if row["id"] == model)
    nested = settings._mapping(settings._mapping(settings._config().get("tts")).get("fish-audio"))
    voice = args.get("voice")
    if voice is not None:
        require(voice_id(voice), "Use a valid Fish Audio voice id.")
    else:
        voice = nested.get("voice") if voice_id(nested.get("voice")) else None
    if "speakers" in args:
        speakers = args["speakers"]
        require(isinstance(speakers, list) and 2 <= len(speakers) <= 4 and all(voice_id(v) for v in speakers),
                "speakers must contain 2–4 Fish Audio voice ids.")
        require(row["multi_speaker"], "Multi-speaker speech is supported by: " +
                ", ".join(r["id"] for r in MODELS if r["multi_speaker"]))
        voice = speakers
    fmt = args.get("format", "ogg")
    require(fmt in {"ogg", "mp3", "wav"}, "format must be ogg, mp3 or wav.")
    if "speed" in args:
        speed = args["speed"]
        require(settings._number(speed) and 0.5 <= speed <= 2, "speed must be between 0.5 and 2.0.")
    else:
        speed = nested.get("speed", 1.0)
        if not settings._number(speed):
            settings._warn("speed")
            speed = 1.0
        speed = max(0.5, min(2.0, speed))
    params = {"text": tags.adapt_tags(text, row["family"]), "model": model, "model_defaulted": defaulted,
              "format": "opus" if fmt == "ogg" else fmt, "prosody": {"speed": speed}}
    if voice is not None:
        params["reference_id"] = voice
    if "pronunciations" in args:
        entries = args["pronunciations"]
        require(isinstance(entries, dict) and len(entries) <= 200, "pronunciations allows at most 200 entries.")
        dictionary = [{"items": [{"key": k, "value": v} for k, v in entries.items()]}]
        require(settings._dictionary(dictionary), "Invalid pronunciation entry.")
        params["pronunciation_dictionary"] = dictionary
    path = media.audio_output_dir() / f"fish-{uuid4().hex[:12]}.{fmt}"
    require("timestamps" not in args or type(args["timestamps"]) is bool, "timestamps must be boolean.")
    events = [] if args.get("timestamps") else None
    client.tts_to_file(params, key, base, str(path), events=events)
    as_voice = fmt in {"ogg", "mp3"}
    tag = ("[[audio_as_voice]]\n" if as_voice else "") + f"MEDIA:{path}"
    if record_media:
        hooks.record(session, path, as_voice)
    result = {"file_path": str(path), "media_tag": tag, "model": model, "voice": voice, "billing": "fish-audio",
              "note": "Include media_tag verbatim in your reply so the user receives the audio."}
    if defaulted and model == "s2.1-pro-free":
        result["notice"] = settings.FREE_MODEL_NOTICE
    if events is not None:
        from .transcribe import speech_timestamps
        result.update(speech_timestamps(events, path))
    return result


def fish_speak(args, session_id="", task_id="", tool_call_id=""):
    return run(_speak, args, session_id)


def fish_voices(args, session_id="", task_id="", tool_call_id=""):
    from .voices import execute
    return run(execute, args, session_id)


def fish_transcribe(args, session_id="", task_id="", tool_call_id=""):
    from .transcribe import execute
    return run(execute, args, session_id)


def _field(typ, **constraints):
    return {"type": typ, **constraints}


S = _field("string")
B = _field("boolean")
STRINGS = _field("array", items=S)
VOICE = _field("string", pattern="^[A-Za-z0-9_-]{8,128}$")
SCHEMAS = {
    "fish_speak": {"text": S, "voice": VOICE, "model": _field("string", enum=[r["id"] for r in MODELS]),
        "format": _field("string", enum=["ogg", "mp3", "wav"], default="ogg"),
        "speakers": _field("array", items=VOICE, minItems=2, maxItems=4),
        "speed": _field("number", minimum=0.5, maximum=2), "timestamps": B,
        "pronunciations": _field("object", additionalProperties=S, maxProperties=200)},
    "fish_voices": {"action": _field("string", enum=["search", "mine", "get", "clone", "design", "save", "update", "delete"]),
        "query": S, "language": S, "tags": STRINGS, "sort": _field("string", enum=["score", "task_count", "created_at"]),
        "page": _field("integer", minimum=1), "page_size": _field("integer", minimum=1, maximum=20),
        "voice_id": VOICE, "title": S, "description": S,
        "sample_paths": _field("array", items=S, minItems=1, maxItems=20), "texts": STRINGS,
        "enhance_audio_quality": _field("boolean", default=True), "consent": B,
        "instruction": _field("string", minLength=1, maxLength=500), "reference_text": S,
        "n": _field("integer", minimum=1, maximum=4, default=2), "seed": _field("integer"),
        "speed": _field("number", exclusiveMinimum=0, maximum=3), "design_token": S},
    "fish_transcribe": {"file_path": S, "model": _field("string", enum=["transcribe-1-pro", "transcribe-1"], default="transcribe-1-pro"),
        "language": S, "timestamps": _field("boolean", default=True),
        "diarize": _field("string", enum=["auto", "true", "false"], default="auto"),
        "num_speakers": _field("integer", minimum=1), "min_speakers": _field("integer", minimum=1),
        "max_speakers": _field("integer", minimum=1), "tag_audio_events": _field("boolean", default=True),
        "srt": _field("boolean", default=False)},
}
DESCRIPTIONS = {"fish_speak": SPEAK_DESCRIPTION,
    "fish_voices": "Search and manage Fish voices, clone with speaker consent, or design and save a voice." + BILLING,
    "fish_transcribe": "Transcribe local audio with speaker turns, emotion cues, timestamps and optional SRT." + BILLING}
REQUIRED = {"fish_speak": ["text"], "fish_voices": ["action"], "fish_transcribe": ["file_path"]}


def register(ctx):
    for name in SCHEMAS:
        schema = {"name": name, "description": DESCRIPTIONS[name],
                  "parameters": {"type": "object", "properties": SCHEMAS[name], "required": REQUIRED[name]}}
        ctx.register_tool(name=name, toolset="fish_audio", schema=schema, handler=globals()[name],
                          check_fn=lambda: bool(fish_api_key()), description=DESCRIPTIONS[name], emoji="🐟")
