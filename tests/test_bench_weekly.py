import json
import os
from pathlib import Path
import pytest
from bench import weekly as w


@pytest.mark.parametrize("gate", w.GATES)
def test_thresholds_and_rejection(gate):
    warn, fail = w.GATES[gate]
    assert w.classify(gate, warn) == "pass"
    assert w.classify(gate, warn + 1e-8) == ("fail" if warn == fail else "warn")
    assert w.classify(gate, fail) == ("pass" if warn == fail else "warn")
    assert w.classify(gate, fail + 1e-8) == "fail"
    assert w.classify(gate, status=401) == w.classify(gate, status=403) == "fail"


@pytest.mark.parametrize("status,level", [(429, "warn"), (500, "warn"), (599, "warn"), (400, "fail"), (404, "fail")])
def test_correctness(status, level):
    assert w.classify("stream", status=status) == level
    assert w.classify("live", failed=True) == "fail"


def test_caps_and_free_model():
    ledger = w.Ledger()
    ledger.tts("a" * 5000)
    asr = w.Ledger()
    asr.asr(599.1)
    for call in (lambda: ledger.tts("a"), lambda: asr.asr(.1), lambda: w.Ledger().tts("a", "unknown")):
        with pytest.raises(ValueError):
            call()
    assert (ledger.chars, asr.seconds) == (5000, 600)
    free = w.Ledger()
    free.tts("a", "s2.1-pro-free")
    assert free.usd == 0


def test_normalisation():
    assert w.normalise("en", "Ｄon’t re-check, HELLO!") == ["dont", "re", "check", "hello"]
    assert w.normalise("ja", "今日は、天気がいい。\u3000") == list("今日は天気がいい")
    assert w.normalise("zh", "你好，很高兴认识你。\n") == list("你好很高兴认识你")
    assert w.error_rate("en", "riverbank", "river bank") == (2, 1)


def test_skip_and_dry_run(tmp_path, monkeypatch):
    monkeypatch.delenv("FISH_API_KEY", raising=False)
    assert w.run(tmp_path) == 2
    assert json.loads((tmp_path / "results.json").read_text())["state"] == "skipped"
    assert w.run(tmp_path, dry_run=True) == 0
    result = json.loads((tmp_path / "results.json").read_text())
    assert {r["gate"] for r in result["gates"]} == set(w.GATES)
    assert result["tts_chars"] < 5000 and result["asr_seconds_billed"] < 600
    assert result["usd"] < result["usd_cap"] == .10
    assert result["plugin_version"] == __import__("fish_audio").PLUGIN_VERSION
    assert result["python_version"] and result["corpus_version"] == w.TEXTS["corpus_version"]
    assert set(result["models"]) == set(w.TTS_PRICES) | {"transcribe-1", "transcribe-1-pro"}
    assert not (tmp_path / "report.md").exists()


@pytest.mark.parametrize("cell", ["stream", "synth", "transcribe", "live"])
def test_rejected_key_stops_and_is_never_persisted(tmp_path, monkeypatch, cell):
    key = "sk-" + "fixture" * 7
    monkeypatch.setenv("FISH_API_KEY", key)
    calls = []
    class Rejected(w.Fake):
        pass
    def reject(self, *args, **kwargs):
        calls.append(cell)
        error = RuntimeError(key)
        error.status = 401
        raise error
    setattr(Rejected, cell, reject)
    assert w.run(tmp_path, dry_run=True, backend=Rejected) == 1
    assert calls == [cell]
    for name in ("report.md", "results.json"):
        assert key not in (tmp_path / name).read_text()
    assert "key rejected" in (tmp_path / "report.md").read_text()


def test_real_plugin_entrypoint_with_fake_http(tmp_path, monkeypatch):
    import httpx
    from fish_audio import settings, streaming
    requests = []
    def reject(request):
        requests.append(request)
        return httpx.Response(401, json={"message": "ignored response body"})
    original = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(reject), **kw))
    monkeypatch.setattr(streaming, "fish_api_key", lambda: "fixture")
    monkeypatch.setattr(settings, "_config", lambda: {})
    monkeypatch.setattr(w.shutil, "which", lambda name: "/usr/bin/" + name)
    assert w.run(tmp_path, dry_run=True, backend=w.Plugin) == 1
    assert len(requests) == 1
    result = json.loads((tmp_path / "results.json").read_text())
    assert result["tts_chars"] == 62
    assert [r["gate"] for r in result["gates"]] == ["key rejected"]  # one row per captured response


def test_harness_error_files_a_report(tmp_path):
    class Broken(w.Fake):
        def __init__(self, ledger, stack):
            raise RuntimeError("boom")
    assert w.run(tmp_path, dry_run=True, backend=Broken) == 2
    assert (tmp_path / "report.md").read_text().startswith("Fish bench: error")


def test_missing_ffmpeg_fails_before_any_paid_call(tmp_path, monkeypatch):
    monkeypatch.setattr(w.shutil, "which", lambda name: None)
    assert w.run(tmp_path, dry_run=True, backend=w.Plugin) == 2
    result = json.loads((tmp_path / "results.json").read_text())
    assert (result["state"], result["tts_chars"], result["asr_seconds_billed"]) == ("error", 0, 0)
    assert (tmp_path / "report.md").read_text().startswith("Fish bench: error")


@pytest.mark.parametrize("statuses,state", [([429], "warn"), ([503], "warn"), ([503, 200], "fail"), ([400], "fail"), ([], "fail")])
def test_transcription_failure_envelope(tmp_path, statuses, state):
    class Failing(w.Fake):
        def transcribe(self, path, seconds, reference, **kwargs):
            self.statuses.extend(statuses)  # the plugin returns an envelope after its own retries
            return {"success": False, "transcript": "", "error_kind": "unavailable"}
    assert w.run(tmp_path, dry_run=True, backend=Failing) == int(state == "fail")
    result = json.loads((tmp_path / "results.json").read_text())
    assert result["state"] == state
    worst = {}  # each gate's most severe row, so a mismatch names the gate
    for row in result["gates"]:
        if row["gate"].startswith(("asr", "roundtrip", "live")):
            worst[row["gate"]] = max(worst.get(row["gate"], "pass"), row["level"], key=("pass", "warn", "fail").index)
    assert worst == dict.fromkeys(("asr-en", "asr-ja", "asr-zh", "roundtrip-en", "roundtrip-ja", "roundtrip-zh", "roundtrip-en-t1", "live"), state)


@pytest.mark.parametrize("status,state", [(301, "fail"), (401, "fail"), (503, "warn")])
def test_every_raised_call_leaves_a_row(tmp_path, status, state):
    class Raising(w.Fake):
        def stream(self, text, voice):
            self.statuses.append(status)  # the response hook saw it before the plugin raised
            error = RuntimeError("raised")
            error.status = status
            raise error
    w.run(tmp_path, dry_run=True, backend=Raising)
    rows = [r for r in json.loads((tmp_path / "results.json").read_text())["gates"] if r["status"] == status]
    assert rows and {r["level"] for r in rows} == {state}


def result_at(path):
    return json.loads((path / "results.json").read_text())


def test_per_model_cost_and_usd_cap_are_atomic():
    ledger = w.Ledger()
    ledger.tts("é", "s2.1-pro")
    ledger.tts("é", "drama-3")
    ledger.tts("é", "s2.1-pro-free")
    ledger.asr(.1)
    assert ledger.usd == pytest.approx(4 * 15 / 1e6 + .36 / 3600)
    # Multi-byte text can cross the USD cap before the character cap.
    before = (ledger.chars, ledger.bytes, ledger.seconds, ledger.usd)
    with pytest.raises(ValueError, match="USD cap"):
        ledger.tts("😀" * 2000, "drama-3")
    assert (ledger.chars, ledger.bytes, ledger.seconds, ledger.usd) == before
    ledger.tts("a" * 4000)
    with pytest.raises(ValueError, match="USD cap"):
        ledger.asr(500)
    assert ledger.seconds == 1


@pytest.mark.parametrize("words,level", [(0, "pass"), (1, "warn"), (3, "fail")])
def test_drama_tag_discriminator(tmp_path, words, level):
    class Tagged(w.Fake):
        def transcribe(self, path, seconds, reference, **kwargs):
            result = super().transcribe(path, seconds, reference, **kwargs)
            if reference == w.TAG_TEXT:
                assert kwargs == {"language": "en"}
                result["transcript"] = "Please listen. " + "soft " * words
            return result
    assert w.run(tmp_path, dry_run=True, backend=Tagged) == int(level == "fail")
    row = next(r for r in result_at(tmp_path)["gates"] if r["gate"] == "drama-tags")
    assert (row["value"], row["level"]) == (words, level)


def test_positive_control_model_and_max_tag_count(tmp_path):
    counts = iter([0, 3])
    class Tagged(w.Fake):
        def transcribe(self, path, seconds, reference, **kwargs):
            result = super().transcribe(path, seconds, reference, **kwargs)
            if reference == w.TAG_TEXT:
                result["transcript"] = "Please listen. " + "FoRsTfUl " * next(counts)
            return result
    assert w.run(tmp_path, dry_run=True, backend=Tagged, tag_model="s2.1-pro") == 1
    assert next(r for r in result_at(tmp_path)["gates"] if r["gate"] == "drama-tags")["value"] == 3
    # The unmodified fake also discriminates the CLI positive control.
    assert w.run(tmp_path, dry_run=True, tag_model="s2.1-pro") == 1
    paid = result_at(tmp_path)["usd"]
    assert w.run(tmp_path, dry_run=True, tag_model="s2.1-pro-free") == 0
    assert paid - result_at(tmp_path)["usd"] == pytest.approx(2 * len(w.TAG_TEXT.encode()) * 15 / 1e6, abs=1e-6)


def test_new_model_calls_and_drama_median(tmp_path):
    timings, seen = iter([800, 100, 600]), []
    class Models(w.Fake):
        def stream(self, text, voice, model="s2.1-pro"):
            super().stream(text, voice, model)
            return next(timings) if model == "drama-3" else 250
        def transcribe(self, path, seconds, reference, **kwargs):
            if kwargs.get("model") == "transcribe-1":
                seen.append(path.name)
            return super().transcribe(path, seconds, reference, **kwargs)
    assert w.run(tmp_path, dry_run=True, backend=Models) == 0
    assert next(r for r in result_at(tmp_path)["gates"] if r["gate"] == "drama-stream")["value"] == 600
    assert seen == ["batch0.mp3"]


@pytest.mark.parametrize("status,level", [(400, "fail"), (429, "warn"), (503, "warn")])
def test_free_refusal_report(tmp_path, status, level):
    class Refused(w.Fake):
        def synth(self, text, voice, path, model="s2.1-pro"):
            if model == "s2.1-pro-free":
                error = RuntimeError("private upstream body must not be persisted")
                error.status = status
                raise error
            return super().synth(text, voice, path, model)
    assert w.run(tmp_path, dry_run=True, backend=Refused) == int(level == "fail")
    row = next(r for r in result_at(tmp_path)["gates"] if r["gate"] == "free")
    assert row["level"] == level
    assert "Free model s2.1-pro-free was refused" in (tmp_path / "report.md").read_text()


@pytest.mark.parametrize("factor,level", [(1.6, "warn"), (1.4, "pass")])
@pytest.mark.parametrize("gate", ["stream", "batch", "drama-stream", "live-lag", "asr-en"])
def test_relative_timing_only(tmp_path, factor, level, gate):
    history = tmp_path / "history"
    # Six earlier runs; only the latest four count. Include malformed/unreadable files.
    for i, value in enumerate([9999, 9999, 250 / factor, 250 / factor, 250 / factor, 250 / factor]):
        path = history / str(i) / "results.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"gates": [{"gate": gate, "value": value}]}))
        os.utime(path, (i + 1, i + 1))
    for i, text in enumerate(["not json", "null", '{"gates":null}', '{"gates":[{}]}']):
        path = history / f"bad{i}" / "results.json"
        path.parent.mkdir()
        path.write_text(text)
    class Timing(w.Fake):
        def synth(self, *args, **kwargs):
            path, seconds, _ = super().synth(*args, **kwargs)
            return path, seconds, .2
        def live(self, *args):
            result, _ = super().live(*args)
            return result, 250
    # batch uses an elapsed/audio ratio, unlike the millisecond timing cells.
    if gate == "batch":
        for path in history.glob("[0-5]/results.json"):
            data = json.loads(path.read_text())
            data["gates"][0]["value"] *= .2 / 250
            path.write_text(json.dumps(data))
            os.utime(path, (int(path.parent.name) + 1,) * 2)
    assert w.run(tmp_path / "out", dry_run=True, backend=Timing, history=history) == 0
    row = next(r for r in result_at(tmp_path / "out")["gates"] if r["gate"] == gate)
    assert row["level"] == ("pass" if gate == "asr-en" else level)
    if row["level"] == "warn":
        assert "1.5x the median of the last 4 runs" in (tmp_path / "out/report.md").read_text()


def test_zero_median_history_does_not_warn(tmp_path):
    # Live captions usually arrive before the first clip ends, so live-lag is often 0; a small lag is not "1.5x".
    history = tmp_path / "history"
    for i in range(4):
        path = history / str(i) / "results.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"gates": [{"gate": "live-lag", "value": 0}]}))
    class Lag(w.Fake):
        def live(self, *args):
            result, _ = super().live(*args)
            return result, 300
    assert w.run(tmp_path / "out", dry_run=True, backend=Lag, history=history) == 0
    row = next(r for r in result_at(tmp_path / "out")["gates"] if r["gate"] == "live-lag")
    assert row["level"] == "pass" and "note" not in row

def test_history_minimum_and_existing_failure(tmp_path):
    (tmp_path / "results.json").write_text(json.dumps({"gates": [{"gate": "stream", "value": 1}]}))
    out = tmp_path / "out"
    assert w.run(out, dry_run=True, history=tmp_path) == 0
    assert not (out / "report.md").exists()
    # Current output is excluded even though it lives under the history root.
    assert w.run(out, dry_run=True, history=tmp_path) == 0
    other = tmp_path / "other/results.json"
    other.parent.mkdir()
    other.write_text((tmp_path / "results.json").read_text())
    class Slow(w.Fake):
        def stream(self, *args):
            super().stream(*args)
            return 3000
    assert w.run(out, dry_run=True, backend=Slow, history=tmp_path) == 1
    assert next(r for r in result_at(out)["gates"] if r["gate"] == "stream")["level"] == "fail"


@pytest.mark.parametrize("setting", ["realtime", "compat"])
def test_paced_live_frames_and_caption_clock(monkeypatch, setting):
    from fish_audio import settings
    monkeypatch.setattr(settings, "transport_settings", lambda: {"live_transcription": setting})
    from fish_audio.stt import FishAudioTranscriptionProvider
    clock, frames, polls = [0.0], [], []
    monkeypatch.setattr(w.time, "perf_counter", lambda: clock[0])
    monkeypatch.setattr(w.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    class Audio:
        stdout = bytes(1280)  # first clip ends at 40 ms
    monkeypatch.setattr(w.subprocess, "run", lambda *a, **k: Audio())
    class Session:
        def push_audio(self, frame):
            frames.append((clock[0], frame))
        def partial_transcript(self):
            polls.append(clock[0])
            return "caption" if clock[0] >= .75 else ""
        def finalize(self):
            clock[0] += .6
            return {"success": True, "transcript": "final"}
    monkeypatch.setattr(FishAudioTranscriptionProvider, "open_stream_session", lambda _: Session())
    api = object.__new__(w.Plugin)
    api.ledger = w.Ledger()
    result, lag = api.live([Path("one"), Path("two")], "final")
    assert result["transcript"] == "final" and lag == pytest.approx(720)
    assert len(frames) == 79 and all(len(frame) == 640 for _, frame in frames)
    assert [t for t, _ in frames] == pytest.approx([.02 * i for i in range(1, 80)])
    assert polls == pytest.approx([.26, .5, .76, 1, 1.26, 1.5])

    assert api.ledger.seconds == (4 if setting == "realtime" else 2)
    assert api.ledger.models == {"transcribe-stream-1" if setting == "realtime" else "transcribe-1-pro"}

    frames.clear()
    api.ledger = w.Ledger()
    api.ledger.seconds = 600
    with pytest.raises(ValueError, match="ASR cap"):  # the cap refuses before a session opens or any audio is sent
        api.live([Path("one"), Path("two")], "final")
    assert frames == []


@pytest.mark.parametrize("arrives,lag", [(2.0, 2040), (None, None)])
def test_caption_after_the_last_frame_is_a_lag_not_a_failure(monkeypatch, arrives, lag):
    from fish_audio.stt import FishAudioTranscriptionProvider
    clock, polls = [0.0], []
    monkeypatch.setattr(w.time, "perf_counter", lambda: clock[0])
    monkeypatch.setattr(w.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    class Audio:
        stdout = bytes(1280)  # first clip ends at 40 ms; all 79 frames are pushed by 1.58 s
    monkeypatch.setattr(w.subprocess, "run", lambda *a, **k: Audio())
    class Session:
        def push_audio(self, frame):
            pass
        def partial_transcript(self):
            polls.append(clock[0])
            return "caption" if arrives is not None and clock[0] >= arrives else ""
        def finalize(self):
            return {"success": True, "transcript": "final"}
    monkeypatch.setattr(FishAudioTranscriptionProvider, "open_stream_session", lambda _: Session())
    api = object.__new__(w.Plugin)
    api.ledger = w.Ledger()
    result, measured = api.live([Path("one"), Path("two")], "final")
    assert result["transcript"] == "final"
    assert measured == (None if lag is None else pytest.approx(lag))
    # Polling after the audio stops at the fail threshold (4 s after the last frame).
    assert max(polls) <= 1.58 + 4.0 + .25


def test_missing_partial_is_a_failed_lag_gate(tmp_path):
    class Missing(w.Fake):
        def live(self, *args):
            result, _ = super().live(*args)
            return result, None
    assert w.run(tmp_path, dry_run=True, backend=Missing) == 1
    rows = {r["gate"]: r for r in result_at(tmp_path)["gates"]}
    assert rows["live"]["level"] == "pass" and rows["live-lag"]["level"] == "fail"


def test_plugin_forwards_and_charges_selected_models(monkeypatch, tmp_path):
    from fish_audio import streaming
    from fish_audio.tts import FishAudioTTSProvider
    from fish_audio.stt import FishAudioTranscriptionProvider
    api = object.__new__(w.Plugin)
    api.ledger = w.Ledger()
    seen = []
    def stream(text, **kwargs):
        seen.append(kwargs)
        api.charge()
        yield b"audio"
    def synth(_, text, path, **kwargs):
        seen.append(kwargs)
        api.charge()
        Path(path).write_bytes(b"audio")
        return path
    def transcribe(_, path, **kwargs):
        seen.append(kwargs)
        api.charge()
        return {"success": True, "transcript": "text"}
    monkeypatch.setattr(streaming, "stream_pcm", stream)
    monkeypatch.setattr(FishAudioTTSProvider, "synthesize", synth)
    monkeypatch.setattr(FishAudioTranscriptionProvider, "transcribe", transcribe)
    monkeypatch.setattr(w, "duration", lambda _: 1)
    api.stream("é", "voice", model="drama-3")
    audio = api.synth("é", "voice", tmp_path / "free.mp3", model="s2.1-pro-free")
    api.transcribe(audio[0], 1, "text", model="transcribe-1", language="en")
    assert seen == [{"voice": "voice", "model": "drama-3"},
                    {"voice": "voice", "model": "s2.1-pro-free", "format": "mp3"},
                    {"model": "transcribe-1", "language": "en"}]
    assert api.ledger.usd == pytest.approx(2 * 15 / 1e6 + .36 / 3600)


def test_free_accepts_any_nonempty_audio_and_rejects_empty(monkeypatch, tmp_path):
    from fish_audio.tts import FishAudioTTSProvider
    api = object.__new__(w.Plugin)
    api.ledger = w.Ledger()
    path = tmp_path / "free.mp3"
    monkeypatch.setattr(FishAudioTTSProvider, "synthesize", lambda *a, **k: path)
    monkeypatch.setattr(w, "duration", lambda _: pytest.fail("free canary must only check nonempty audio"))
    path.write_bytes(b"nonempty")
    assert api.synth("text", "voice", path, "s2.1-pro-free")[0] == path
    path.write_bytes(b"")
    with pytest.raises(ValueError, match="empty audio"):
        api.synth("text", "voice", path, "s2.1-pro-free")


def test_usd_cap_blocks_http_transport_including_retry(monkeypatch, tmp_path):
    import httpx
    from fish_audio import settings, tts
    requests = []
    def rate_limited(request):
        requests.append(request)
        return httpx.Response(429, json={"message": "fixture"})
    original = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(rate_limited), **kw))
    monkeypatch.setattr(tts, "fish_api_key", lambda: "fixture")
    monkeypatch.setattr(settings, "_config", lambda: {})
    monkeypatch.setattr(w.shutil, "which", lambda name: "/usr/bin/" + name)
    from fish_audio import client
    monkeypatch.setattr(client.time, "sleep", lambda _: None)
    ledger = w.Ledger()
    ledger.tts("😀" * 1500)  # $0.09, under the character cap
    with w.ExitStack() as stack:
        api = w.Plugin(ledger, stack)
        with pytest.raises(ValueError, match="USD cap"):
            api.synth("a" * 400, "voice", tmp_path / "audio.mp3", "drama-3")  # next retry refused
    assert len(requests) == 1
    assert ledger.usd == pytest.approx(.096)
    assert ledger.chars == 1900
