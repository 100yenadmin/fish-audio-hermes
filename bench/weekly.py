"""CI regression alarm. Output is numeric metrics and fixed gate names only."""
import argparse
from contextlib import ExitStack
import json
import math
import os
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import unicodedata
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
TEXTS = json.loads((HERE / "texts.json").read_text())
TTS_PRICES = {"s2.1-pro": 15, "drama-3": 15, "s2.1-pro-free": 0}
USD_CAP = .10
TAG_TEXT = '<slow>Please</slow> <stress>listen</stress> to me. This is <emphasis>really</emphasis> <fast>quite urgent</fast> and <soft>very</soft> <forceful>important</forceful>, my <whisper>friend</whisper>.'
TAG_WORDS = re.compile(r"\b(slow|stress|emphasis|fast|soft|forceful|forstful|whisper)\b", re.I)
TIMING_GATES = ("stream", "batch", "drama-stream", "live-lag")
GATES = {"stream": (500, 800), "batch": (.35, .5), "asr-en": (.03, .05),
         "asr-ja": (.01, .03), "asr-zh": (.01, .03), "roundtrip-en": (.02, .05),
         "roundtrip-ja": (.08, .15), "roundtrip-zh": (.08, .15), "live": (.05, .05),
         "drama-stream": (1200, 2500), "drama-tags": (0, 1), "free": (0, 0),
         "roundtrip-en-t1": (.03, .06), "live-lag": (2000, 4000)}


def classify(gate, value=0, status=None, failed=False):
    if status in (401, 403):
        return "fail"
    if failed:
        return "fail"
    if status == 429 or status is not None and 500 <= status < 600:
        return "warn"
    if status is not None and 400 <= status < 500:
        return "fail"
    warn, fail = GATES[gate]
    return "fail" if value > fail else "warn" if value > warn else "pass"


class Ledger:
    def __init__(self):
        self.chars = self.bytes = self.seconds = 0
        self.model_bytes, self.models = {}, set()

    @property
    def usd(self):
        return sum(n * TTS_PRICES[m] / 1e6 for m, n in self.model_bytes.items()) + self.seconds * .36 / 3600

    def budget(self, cost):
        if self.usd + cost > USD_CAP:
            raise ValueError("USD cap refused")

    def tts(self, text, model="s2.1-pro"):
        if model not in TTS_PRICES or self.chars + len(text) > 5000:
            raise ValueError("TTS model or cap refused")
        size = len(text.encode("utf-8"))
        self.budget(size * TTS_PRICES[model] / 1e6)
        self.chars += len(text)
        self.bytes += size
        self.model_bytes[model] = self.model_bytes.get(model, 0) + size
        self.models.add(model)

    def asr(self, seconds):
        if not math.isfinite(seconds) or seconds <= 0 or self.seconds + math.ceil(seconds) > 600:
            raise ValueError("ASR cap refused")
        self.budget(math.ceil(seconds) * .36 / 3600)
        self.seconds += math.ceil(seconds)


def normalise(lang, text):
    text = unicodedata.normalize("NFKC", text).lower()
    if lang == "en":
        return re.sub(r"[^a-z0-9 ]+", " ", text.replace("’", "'").replace("'", "").replace("-", " ")).split()
    return [c for c in text if unicodedata.category(c)[0] not in "PSZC"]


def error_rate(lang, reference, hypothesis):
    ref, hyp = normalise(lang, reference), normalise(lang, hypothesis)
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i]
        for j, h in enumerate(hyp, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h)))
        prev = cur
    return prev[-1], len(ref)


def duration(path):
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                "-of", "csv=p=0", str(path)], capture_output=True, check=True).stdout)


class Plugin:
    def __init__(self, ledger, stack):
        if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
            raise RuntimeError("ffmpeg and ffprobe are required")  # fail before the first paid call
        import httpx
        from fish_audio import client
        from websockets.sync import client as ws
        self.ledger, self.statuses, self.charge = ledger, [], lambda: None
        def request(_):
            self.charge()  # includes every internal HTTP retry, before sending
        def response(reply):
            self.statuses.append(reply.status_code)
        http = stack.enter_context(httpx.Client(event_hooks={"request": [request], "response": [response]},
                                                timeout=httpx.Timeout(60, connect=10), follow_redirects=False))
        stack.enter_context(patch.object(client, "_client", http))
        connect = ws.connect
        def observed_connect(*args, **kwargs):
            try:
                return connect(*args, **kwargs)
            except Exception as exc:
                self.statuses.append(getattr(getattr(exc, "response", None), "status_code", None))
                raise
        stack.enter_context(patch.object(ws, "connect", observed_connect))

    def stream(self, text, voice, model="s2.1-pro"):
        from fish_audio.streaming import stream_pcm
        self.charge = lambda: self.ledger.tts(text, model)
        start, first, size = time.perf_counter(), None, 0
        for chunk in stream_pcm(text, voice=voice, model=model):
            if chunk and first is None:
                first = time.perf_counter() - start
            size += len(chunk)
        if not size:
            raise ValueError("empty audio")
        return first * 1000

    def synth(self, text, voice, path, model="s2.1-pro"):
        from fish_audio.tts import FishAudioTTSProvider
        self.charge = lambda: self.ledger.tts(text, model)
        start = time.perf_counter()
        final = FishAudioTTSProvider().synthesize(text, str(path), voice=voice, model=model, format="mp3")
        elapsed = time.perf_counter() - start
        if not Path(final).stat().st_size:
            raise ValueError("empty audio")
        if model == "s2.1-pro-free":
            return Path(final), 0, 0
        seconds = duration(final)
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("empty duration")
        return Path(final), seconds, elapsed / seconds

    def transcribe(self, path, seconds, reference, model="transcribe-1-pro", language=None):
        from fish_audio.stt import FishAudioTranscriptionProvider
        self.charge = lambda: self.ledger.asr(seconds)
        self.ledger.models.add(model)
        return FishAudioTranscriptionProvider().transcribe(str(path), model=model, language=language)

    def live(self, paths, reference):
        from fish_audio.stt import FishAudioTranscriptionProvider
        pause = bytes(48000)  # 1.5 s between the clips, so the first one is captioned mid-stream
        clips = [subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ar", "16000", "-ac", "1",
                                 "-f", "s16le", "pipe:1"], capture_output=True, check=True).stdout for path in paths]
        pcm = pause.join(clips)
        self.ledger.asr(math.ceil(len(pcm) / 640) * .02)
        self.ledger.models.add("transcribe-1-pro")
        session = FishAudioTranscriptionProvider().open_stream_session()
        start, poll, lag = time.perf_counter(), .25, None
        for offset in range(0, len(pcm), 640):
            time.sleep(max(0, start + (offset / 640 + 1) * .02 - time.perf_counter()))
            session.push_audio(pcm[offset:offset + 640].ljust(640, b"\0"))
            elapsed = time.perf_counter() - start
            if elapsed >= poll:
                partial = session.partial_transcript()
                if partial and lag is None:
                    lag = max(0, (time.perf_counter() - start - len(clips[0]) / 32000) * 1000)
                poll += .25
        # A caption that lands after the last frame is still a lag value, not a failure; stop at the fail threshold.
        deadline = start + len(pcm) / 32000 + GATES["live-lag"][1] / 1000
        while lag is None and time.perf_counter() < deadline:
            time.sleep(.25)
            if session.partial_transcript():
                lag = max(0, (time.perf_counter() - start - len(clips[0]) / 32000) * 1000)
        return session.finalize(), lag


class Fake(Plugin):
    def __init__(self, ledger, stack):
        self.ledger, self.statuses = ledger, []
    def stream(self, text, voice, model="s2.1-pro"):
        self.ledger.tts(text, model)
        return 250
    def synth(self, text, voice, path, model="s2.1-pro"):
        self.ledger.tts(text, model)
        if text == TAG_TEXT:
            self.tag_model = model
        return path, 15, .2
    def transcribe(self, path, seconds, reference, model="transcribe-1-pro", language=None):
        self.ledger.asr(seconds)
        self.ledger.models.add(model)
        if reference == TAG_TEXT:
            reference = "Please listen to me." + (" soft soft forceful" if self.tag_model == "s2.1-pro" else "")
        return {"success": True, "transcript": reference}
    def live(self, paths, reference):
        return self.transcribe(paths[0], 5, reference), 750


def history_values(directory, output):
    values = {g: [] for g in TIMING_GATES}
    files = []
    for path in Path(directory).rglob("results.json") if directory else []:
        try:
            files.append((path.stat().st_mtime, path))
        except OSError:
            continue
    for _, path in sorted(files):
        try:
            if path.resolve() == (output / "results.json").resolve():
                continue
            rows = {r["gate"]: r["value"] for r in json.loads(path.read_text())["gates"]}
            for gate in values:
                value = rows.get(gate)
                if type(value) in (int, float) and math.isfinite(value):
                    values[gate].append(value)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return {g: v[-4:] for g, v in values.items()}


def run(out, *, dry_run=False, backend=None, tag_model="drama-3", history=None):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    ledger, rows, rejected, harness_error = Ledger(), [], False, False
    earlier = history_values(history, out)
    def record(gate, value=0, status=None, failed=False):
        nonlocal rejected
        rejected |= status in (401, 403)
        rows.append({"gate": "key rejected" if rejected else gate, "value": value,
                     "status": status, "level": classify(gate, value, status, failed)})
        baseline = earlier.get(gate, [])
        if rows[-1]["level"] == "pass" and len(baseline) >= 2 and 0 < statistics.median(baseline) < value / 1.5:  # a zero median (live-lag) has no ratio
            rows[-1].update(level="warn", note=f"1.5x the median of the last {len(baseline)} runs")
        if gate == "free" and rows[-1]["level"] != "pass":
            rows[-1]["note"] = "Free model s2.1-pro-free was refused"
    def call(gate, fn):
        if rejected:
            return None
        api.statuses.clear()
        try:
            result = fn()
        except Exception as exc:
            status = getattr(exc, "status", None)
            if status is None or status < 400 or status not in api.statuses:  # the loop below records captured 4xx/5xx once
                record(gate, status=status, failed=not (status == 429 or status is not None and 500 <= status < 600))
            result = None
        for status in api.statuses:
            if status is not None and status >= 400:
                record(gate, status=status)
        return None if rejected else result
    skipped = not dry_run and not os.environ.get("FISH_API_KEY", "").strip()
    if not skipped:
        try:
            with ExitStack() as stack, tempfile.TemporaryDirectory() as scratch:
                api = (backend or (Fake if dry_run else Plugin))(ledger, stack)
                voice = lambda lang: TEXTS["voices"][lang]["id"]
                short, medium = (TEXTS["tts_lengths"][k] for k in ("short", "medium"))
                samples = [call("stream", lambda: api.stream(short, voice("en"))) for _ in range(5)]
                if all(v is not None for v in samples):
                    record("stream", statistics.median(samples))
                batch = [call("batch", lambda: api.synth(medium, voice("en"), Path(scratch) / f"batch{i}.mp3")) for i in range(3)]
                if all(v is not None for v in batch):
                    record("batch", statistics.median(v[2] for v in batch))
                samples = [call("drama-stream", lambda: api.stream(short, voice("en"), "drama-3")) for _ in range(3)]
                if all(v is not None for v in samples):
                    record("drama-stream", statistics.median(samples))
                if call("free", lambda: api.synth(short, voice("en"), Path(scratch) / "free.mp3", "s2.1-pro-free")):
                    record("free")
                def score(gate, lang, text, result):
                    if result is None:
                        return None
                    if not result.get("success") or not result.get("transcript"):
                        last = api.statuses[-1] if api.statuses else None  # the terminal response, not an earlier retry
                        if not (last == 429 or last and 500 <= last < 600):  # an exhausted rate limit or outage already warned
                            record(gate, status=401 if result.get("error_kind") in ("auth", "credential") else None, failed=True)
                        return None
                    return error_rate(lang, text, result["transcript"])
                counts = []
                for i in range(2):
                    audio = call("drama-tags", lambda: api.synth(TAG_TEXT, voice("en"), Path(scratch) / f"tags{i}.mp3", tag_model))
                    if audio:
                        result = call("drama-tags", lambda: api.transcribe(audio[0], audio[1], TAG_TEXT, language="en"))
                        if score("drama-tags", "en", TAG_TEXT, result) is not None:
                            counts.append(len(TAG_WORDS.findall(result["transcript"])))
                if len(counts) == 2:
                    record("drama-tags", max(counts))
                if batch[0]:
                    result = call("roundtrip-en-t1", lambda: api.transcribe(batch[0][0], batch[0][1], medium, model="transcribe-1"))
                    units = score("roundtrip-en-t1", "en", medium, result)
                    if units:
                        record("roundtrip-en-t1", units[0] / units[1])
                totals = {lang: [0, 0] for lang in ("en", "ja", "zh")}
                for item in TEXTS["asr_corpus"]:
                    path = HERE / "corpus" / (item["id"] + ".webm")
                    gate = "asr-" + item["lang"]
                    result = call(gate, lambda: api.transcribe(path, 4 if dry_run else duration(path), item["text"]))
                    units = score(gate, item["lang"], item["text"], result)
                    if units:
                        totals[item["lang"]] = [a + b for a, b in zip(totals[item["lang"]], units)]
                for lang, (edits, units) in totals.items():
                    if units:
                        record("asr-" + lang, edits / units)
                for lang in ("en", "ja", "zh"):
                    text = medium if lang == "en" else TEXTS["roundtrip_texts"][lang][0]
                    gate = "roundtrip-" + lang
                    audio = batch[0] if lang == "en" else call(gate, lambda: api.synth(text, voice(lang), Path(scratch) / (lang + ".mp3")))
                    if audio:
                        result = call(gate, lambda: api.transcribe(audio[0], audio[1], text))
                        units = score(gate, lang, text, result)
                        if units:
                            record(gate, units[0] / units[1])
                items = [next(i for i in TEXTS["asr_corpus"] if i["id"] == n) for n in ("en02", "en06")]  # no compounds
                text = " ".join(i["text"] for i in items)
                result = call("live", lambda: api.live([HERE / "corpus" / (i["id"] + ".webm") for i in items], text))
                units = score("live", "en", text, result[0] if result else None)
                if units:
                    record("live", units[0] / units[1])
                    record("live-lag", result[1] or 0, failed=result[1] is None)
        except Exception:
            harness_error = True
    state = "skipped" if skipped else "error" if harness_error else "fail" if any(r["level"] == "fail" for r in rows) else "warn" if any(r["level"] == "warn" for r in rows) else "pass"
    result = {"state": state, "dry_run": dry_run, "corpus_version": TEXTS["corpus_version"], "gates": rows,
              "tts_chars": ledger.chars, "tts_bytes": ledger.bytes, "asr_seconds_billed": ledger.seconds,
              "usd": round(ledger.usd, 6), "usd_cap": USD_CAP, "models": sorted(ledger.models),
              "plugin_version": __import__("fish_audio").PLUGIN_VERSION, "python_version": sys.version.split()[0]}
    summary = "skipped: no FISH_API_KEY secret\n" if skipped else f"Fish bench: {state}; USD {result['usd']:.6f}\n" + "".join(f"- {r['gate']}: {r['level']} ({r['value']:.6f}, status {r['status']}) {r.get('note', '')}\n" for r in rows)
    (out / "results.json").write_text(json.dumps(result, indent=2) + "\n")
    report = out / "report.md"
    if state in ("warn", "fail", "error"):  # a broken harness also needs a maintainer
        report.write_text(summary)
    elif report.exists():
        report.unlink()
    print(summary, end="")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as stream:
            stream.write(summary)
    return 2 if state in ("skipped", "error") else int(state == "fail")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--tag-model", default="drama-3", choices=TTS_PRICES)
    parser.add_argument("--history", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as scratch:
        raise SystemExit(run(args.out or scratch, dry_run=args.dry_run, tag_model=args.tag_model, history=args.history))
