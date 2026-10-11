"""CI regression alarm. Output is numeric metrics and fixed gate names only."""
import argparse
from contextlib import ExitStack
import json
import math
import os
from pathlib import Path
import re
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
GATES = {"stream": (500, 800), "batch": (.35, .5), "asr-en": (.03, .05),
         "asr-ja": (.01, .03), "asr-zh": (.01, .03), "roundtrip-en": (.02, .05),
         "roundtrip-ja": (.08, .15), "roundtrip-zh": (.08, .15), "live": (.05, .05)}


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

    def tts(self, text, model="s2.1-pro"):
        if model != "s2.1-pro" or self.chars + len(text) > 5000:
            raise ValueError("TTS model or cap refused")
        self.chars += len(text)
        self.bytes += len(text.encode("utf-8"))

    def asr(self, seconds):
        if not math.isfinite(seconds) or seconds <= 0 or self.seconds + math.ceil(seconds) > 600:
            raise ValueError("ASR cap refused")
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

    def stream(self, text, voice):
        from fish_audio.streaming import stream_pcm
        self.charge = lambda: self.ledger.tts(text)
        start, first, size = time.perf_counter(), None, 0
        for chunk in stream_pcm(text, voice=voice, model="s2.1-pro"):
            if chunk and first is None:
                first = time.perf_counter() - start
            size += len(chunk)
        if not size:
            raise ValueError("empty audio")
        return first * 1000

    def synth(self, text, voice, path):
        from fish_audio.tts import FishAudioTTSProvider
        self.charge = lambda: self.ledger.tts(text)
        start = time.perf_counter()
        final = FishAudioTTSProvider().synthesize(text, str(path), voice=voice, model="s2.1-pro", format="mp3")
        elapsed = time.perf_counter() - start
        if not Path(final).stat().st_size:
            raise ValueError("empty audio")
        seconds = duration(final)
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("empty duration")
        return Path(final), seconds, elapsed / seconds

    def transcribe(self, path, seconds, reference):
        from fish_audio.stt import FishAudioTranscriptionProvider
        self.charge = lambda: self.ledger.asr(seconds)
        return FishAudioTranscriptionProvider().transcribe(str(path), model="transcribe-1-pro")

    def live(self, paths, reference):
        from fish_audio.stt import FishAudioTranscriptionProvider
        pause = bytes(48000)  # 1.5 s between the clips, so the first one is captioned mid-stream
        pcm = pause.join(subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ar", "16000", "-ac", "1",
                                         "-f", "s16le", "pipe:1"], capture_output=True, check=True).stdout for path in paths)
        self.ledger.asr(math.ceil(len(pcm) / 640) * .02)
        session = FishAudioTranscriptionProvider().open_stream_session()
        for offset in range(0, len(pcm), 640):
            session.push_audio(pcm[offset:offset + 640].ljust(640, b"\0"))
        return session.finalize()


class Fake(Plugin):
    def __init__(self, ledger, stack):
        self.ledger, self.statuses = ledger, []
    def stream(self, text, voice):
        self.ledger.tts(text)
        return 250
    def synth(self, text, voice, path):
        self.ledger.tts(text)
        return path, 15, .2
    def transcribe(self, path, seconds, reference):
        self.ledger.asr(seconds)
        return {"success": True, "transcript": reference}
    def live(self, paths, reference):
        return self.transcribe(paths[0], 5, reference)


def run(out, *, dry_run=False, backend=None):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    ledger, rows, rejected, harness_error = Ledger(), [], False, False
    def record(gate, value=0, status=None, failed=False):
        nonlocal rejected
        rejected |= status in (401, 403)
        rows.append({"gate": "key rejected" if rejected else gate, "value": value,
                     "status": status, "level": classify(gate, value, status, failed)})
    def call(gate, fn):
        if rejected:
            return None
        api.statuses.clear()
        try:
            result = fn()
        except Exception as exc:
            status = getattr(exc, "status", None)
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
                def score(gate, lang, text, result):
                    if result is None:
                        return None
                    if not result.get("success") or not result.get("transcript"):
                        record(gate, status=401 if result.get("error_kind") in ("auth", "credential") else None, failed=True)
                        return None
                    return error_rate(lang, text, result["transcript"])
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
                units = score("live", "en", text, result)
                if units:
                    record("live", units[0] / units[1])
        except Exception:
            harness_error = True
    state = "skipped" if skipped else "error" if harness_error else "fail" if any(r["level"] == "fail" for r in rows) else "warn" if any(r["level"] == "warn" for r in rows) else "pass"
    result = {"state": state, "dry_run": dry_run, "corpus_version": TEXTS["corpus_version"], "gates": rows,
              "tts_chars": ledger.chars, "tts_bytes": ledger.bytes, "asr_seconds_billed": ledger.seconds,
              "usd": round(ledger.bytes * 15 / 1e6 + ledger.seconds * .36 / 3600, 6)}
    summary = "skipped: no FISH_API_KEY secret\n" if skipped else f"Fish bench: {state}; USD {result['usd']:.6f}\n" + "".join(f"- {r['gate']}: {r['level']} ({r['value']:.6f}, status {r['status']})\n" for r in rows)
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
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as scratch:
        raise SystemExit(run(args.out or scratch, dry_run=args.dry_run))
