"""Offline drift fixtures: committed spec plus synthetic skill, prose and HEAD results."""
import copy
from urllib.error import HTTPError, URLError

import pytest

from scripts import fish_drift as drift, parity_check as parity


def test_drift_ignores_raw_hash_and_detects_each_canary():
    spec = parity.load_spec(parity.ROOT / "parity/fish-openapi.pruned.json")
    fresh = copy.deepcopy(spec)
    fresh["x-source"] = {"sha256": "unrelated-agent-only-change"}
    lock = drift.canaries(b"skill", "`s1` <Update label=\"old\">")
    mapping = {"entries": [], "doc_only": [{"url": "https://docs.fish.audio/example#anchor", "status": "equivalent"}]}
    mapping["entries"] = __import__("yaml").safe_load((parity.ROOT / "parity.yaml").read_text())["entries"]
    assert drift.compare(spec, fresh, lock, b"skill", '`s1` <Update label="old">', mapping, lambda u: None) == []
    fresh["info"]["title"] += " changed"
    def failed(url):
        raise HTTPError(url, 404, "Not Found", {}, None)
    report = drift.compare(spec, fresh, lock, b"new", '`s1` `s3-pro` <Update label="new">', mapping, failed)
    for phrase in ("OpenAPI content changed", "SKILL.md sha changed", "known_model_ids", "changelog_labels_seen", "Documentation URL failed"):
        assert any(phrase in item for item in report)


@pytest.mark.parametrize("error,count", [(TimeoutError, 0), (URLError, 0), (HTTPError, 1)])
def test_url_failure_classification(monkeypatch, tmp_path, error, count):
    (tmp_path / "parity.yaml").write_text("")
    monkeypatch.setattr(parity, "ROOT", tmp_path)
    spec = {"paths": {}}
    url = "https://docs.fish.audio/example"
    mapping = {"doc_only": [{"url": url, "status": "equivalent"}]}
    def failed(url):
        raise HTTPError(url, 404, "Not Found", {}, None) if error is HTTPError else error("offline")
    inconclusive = []
    report = drift.compare(spec, spec, drift.canaries(b"", ""), b"", "", mapping, failed, inconclusive)
    assert len(report) == count and len(inconclusive) == 1 - count


@pytest.mark.parametrize("head", [False, True])
@pytest.mark.parametrize("error", [TimeoutError("offline"), URLError("offline"), HTTPError("url", 404, "Not Found", {}, None)])
def test_network_retry_budget(monkeypatch, head, error):
    attempts, delays = [], []
    def failed(request, **kwargs):
        attempts.append(request.get_method())
        raise error
    monkeypatch.setattr(drift, "urlopen", failed)
    monkeypatch.setattr(drift.time, "sleep", delays.append)
    with pytest.raises(type(error)):
        drift.request("https://docs.fish.audio/example", head=head)
    assert attempts == ["HEAD" if head else "GET"] * 3 and delays == [0.5, 1.0]


def test_inconclusive_fetch_only_reaches_summary(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(parity, "ROOT", tmp_path)
    monkeypatch.setattr(parity, "load_spec", lambda _: {})
    def failed(path):
        raise TimeoutError("offline")
    monkeypatch.setattr(drift, "fetch", failed)
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    drift.main()
    assert (tmp_path / "drift/report.md").read_text() == ""
    assert "Inconclusive" in summary.read_text() and "Inconclusive" in capsys.readouterr().out
