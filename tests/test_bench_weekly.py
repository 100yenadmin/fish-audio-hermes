import json
import pytest
from bench import weekly as w


@pytest.mark.parametrize("gate", w.GATES)
def test_thresholds_and_rejection(gate):
    warn, fail = w.GATES[gate]
    assert w.classify(gate, warn) == "pass"
    assert w.classify(gate, warn + 1e-8) == ("fail" if gate == "live" else "warn")
    assert w.classify(gate, fail) == ("pass" if gate == "live" else "warn")
    assert w.classify(gate, fail + 1e-8) == "fail"
    assert w.classify(gate, status=401) == w.classify(gate, status=403) == "fail"


@pytest.mark.parametrize("status,level", [(429, "warn"), (500, "warn"), (599, "warn"), (400, "fail"), (404, "fail")])
def test_correctness(status, level):
    assert w.classify("stream", status=status) == level
    assert w.classify("live", failed=True) == "fail"


def test_caps_and_free_model():
    ledger = w.Ledger()
    ledger.tts("a" * 5000)
    ledger.asr(599.1)
    for call in (lambda: ledger.tts("a"), lambda: ledger.asr(.1), lambda: w.Ledger().tts("a", "s2.1-pro-free")):
        with pytest.raises(ValueError):
            call()
    assert (ledger.chars, ledger.seconds) == (5000, 600)


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
    assert (result["tts_chars"], result["asr_seconds_billed"]) == (1271, 130)
    assert not (tmp_path / "report.md").exists()


@pytest.mark.parametrize("cell", ["stream", "synth", "transcribe", "live"])
def test_rejected_key_stops_and_is_never_persisted(tmp_path, monkeypatch, cell):
    key = "sk-" + "fixture" * 7
    monkeypatch.setenv("FISH_API_KEY", key)
    calls = []
    class Rejected(w.Fake):
        pass
    def reject(self, *args):
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


@pytest.mark.parametrize("statuses,state", [([429], "warn"), ([503], "warn"), ([400], "fail"), ([], "fail")])
def test_transcription_failure_envelope(tmp_path, statuses, state):
    class Failing(w.Fake):
        def transcribe(self, path, seconds, reference):
            self.statuses.extend(statuses)  # the plugin returns an envelope after its own retries
            return {"success": False, "transcript": "", "error_kind": "unavailable"}
    assert w.run(tmp_path, dry_run=True, backend=Failing) == int(state == "fail")
    result = json.loads((tmp_path / "results.json").read_text())
    levels = {r["level"] for r in result["gates"] if r["gate"].startswith(("asr", "roundtrip", "live"))}
    assert (result["state"], levels) == (state, {state})
