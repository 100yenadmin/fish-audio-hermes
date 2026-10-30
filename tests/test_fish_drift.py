"""Offline drift fixtures: committed spec plus synthetic skill, prose and HEAD results."""
import copy
import json
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


def test_drift_reports_removed_canaries():
    spec = parity.load_spec(parity.ROOT / "parity/fish-openapi.pruned.json")
    mapping = {"entries": __import__("yaml").safe_load((parity.ROOT / "parity.yaml").read_text())["entries"], "doc_only": []}
    lock = drift.canaries(b"skill", '`s1` `s2.1-pro-free` <Update label="old"> <Update label="kept">')
    report = drift.compare(spec, spec, lock, b"skill", '`s1` <Update label="kept">', mapping, lambda u: None)
    assert "Removed known_model_ids: ['s2.1-pro-free']" in report
    assert "Removed changelog_labels_seen: ['old']" in report
    assert not any(item.startswith("New ") for item in report)


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


@pytest.fixture
def extras():
    return {"compat_model_prices": {"s2.1-pro": ["0.000015", "0"], "s2.1-pro-free": ["0", "0"]},
            "doc_prices": {"s2.1-pro": r"\$15.00 / M UTF-8 bytes", "s2.1-pro-free": r"\$0.00 / M UTF-8 bytes", "transcribe-1": r"\$0.36 / audio hour"},
            "fish_agent_plugin_version": "0.1.4", "fish_mcp_config_sha256": "old-sha"}


def test_new_canary_fetch_and_price_parser(monkeypatch):
    import hashlib
    sources = {
        "https://api.fish.audio/compat/openai/v1/models": json.dumps({"data": [
            {"id": "fish-audio/s2.1-pro-free", "pricing": {"prompt": "0", "completion": "0"}},
            {"id": "fish-audio/transcribe-1", "pricing": {"prompt": "0", "completion": "0", "audio": "0.0001"}}]}).encode(),
        "https://docs.fish.audio/developer-guide/models-pricing/pricing-and-rate-limits.md": b"""### Text-to-Speech (TTS) Models
| Model Name | Price (USD) |
| - | - |
| `s2.1-pro-free` | \\$0.00 / M UTF-8 bytes |
| `s1` (deprecated) | \\$15.00 / M UTF-8 bytes |
### Automatic Speech Recognition (ASR) Models
| `transcribe-1-pro` (recommended) | \\$0.36 / audio hour |
| `transcribe-1` | \\$0.36 / audio hour |
### Voice Design
| `voice-design-1` | \\$0.01 / successful API request |
""",
        "https://raw.githubusercontent.com/fishaudio/fish-audio-plugin/main/plugins/fish-audio/.claude-plugin/plugin.json": b'{"version":"0.1.4"}',
        "https://raw.githubusercontent.com/fishaudio/fish-audio-plugin/main/plugins/fish-audio/.mcp.json": b'{"mcpServers":{"fish-audio":{"type":"http","url":"https://api.fish.audio/mcp"}}}\n',
    }
    calls = []
    def request(url, **kwargs):
        calls.append(url)
        return sources[url]
    monkeypatch.setattr(drift, "request", request)
    result = drift.extra_canaries()
    assert set(calls) == set(sources)
    assert result["compat_model_prices"] == {"s2.1-pro-free": ["0", "0"], "transcribe-1": ["0", "0", "0.0001"]}
    assert result["doc_prices"] == {"s2.1-pro-free": r"\$0.00 / M UTF-8 bytes", "s1": r"\$15.00 / M UTF-8 bytes",
                                    "transcribe-1-pro": r"\$0.36 / audio hour", "transcribe-1": r"\$0.36 / audio hour"}
    assert result["fish_agent_plugin_version"] == "0.1.4"
    assert result["fish_mcp_config_sha256"] == hashlib.sha256(sources[calls[-1]]).hexdigest()


def compare_extras(lock_extra, new_extra):
    spec = parity.load_spec(parity.ROOT / "parity/fish-openapi.pruned.json")
    mapping = {"entries": __import__("yaml").safe_load((parity.ROOT / "parity.yaml").read_text())["entries"], "doc_only": []}
    lock = drift.canaries(b"", "") | lock_extra
    return drift.compare(spec, spec, lock, b"", "", mapping, lambda _: None, extra=new_extra)


@pytest.mark.parametrize("field", ["compat_model_prices", "doc_prices"])
@pytest.mark.parametrize("change", ["price", "removed", "added", "free-price", "free-removed"])
def test_price_entry_drift_and_free_notice(extras, field, change):
    updated = copy.deepcopy(extras)
    values = updated[field]
    if change == "price":
        values["s2.1-pro"] = ["0.000016", "0"] if field == "compat_model_prices" else r"\$16.00 / M UTF-8 bytes"
    elif change == "removed":
        del values["s2.1-pro"]
    elif change == "added":
        values["new-model"] = ["0", "0"] if field == "compat_model_prices" else "$0.00"
    elif change == "free-price":
        values["s2.1-pro-free"] = ["0", "0.1"] if field == "compat_model_prices" else r"\$15.00 / M UTF-8 bytes"
    else:
        del values["s2.1-pro-free"]
    report = compare_extras(extras, updated)
    kind = "Removed" if "removed" in change else "Added" if change == "added" else "Changed"
    assert any(item.startswith(f"{kind} {field}") for item in report)
    notices = [item for item in report if item.startswith("Free model s2.1-pro-free:")]
    if change.startswith("free"):
        assert len(notices) == 1 and field in notices[0]
        assert "free period ends 30 November 2026; see the plugin's free-model notices" in notices[0]
    else:
        assert notices == []


def test_audio_only_price_change_is_drift(extras):
    before = copy.deepcopy(extras)
    before["compat_model_prices"]["transcribe-1"] = ["0", "0", "0.0001"]
    after = copy.deepcopy(before)
    after["compat_model_prices"]["transcribe-1"] = ["0", "0", "0.0002"]
    assert compare_extras(before, after) == ["Changed compat_model_prices transcribe-1: ['0', '0', '0.0001'] -> ['0', '0', '0.0002']"]


def test_plugin_manifest_and_raw_mcp_hash_drift(extras):
    updated = extras | {"fish_agent_plugin_version": "0.1.5", "fish_mcp_config_sha256": "new-sha"}
    report = compare_extras(extras, updated)
    assert report == ["Changed fish_agent_plugin_version: '0.1.4' -> '0.1.5'", "Changed fish_mcp_config_sha256: 'old-sha' -> 'new-sha'"]
    assert compare_extras(extras, extras) == []


@pytest.mark.parametrize("failed_url", ["compat", "prices", "manifest", "mcp"])
def test_new_fetch_network_failure_is_inconclusive(monkeypatch, tmp_path, capsys, extras, failed_url):
    urls = {"compat": "https://api.fish.audio/compat/openai/v1/models",
            "prices": "https://docs.fish.audio/developer-guide/models-pricing/pricing-and-rate-limits.md",
            "manifest": "https://raw.githubusercontent.com/fishaudio/fish-audio-plugin/main/plugins/fish-audio/.claude-plugin/plugin.json",
            "mcp": "https://raw.githubusercontent.com/fishaudio/fish-audio-plugin/main/plugins/fish-audio/.mcp.json"}
    monkeypatch.setattr(parity, "ROOT", tmp_path)
    monkeypatch.setattr(parity, "load_spec", lambda _: {})
    monkeypatch.setattr(parity, "prune", lambda *a, **k: {})
    (tmp_path / "parity").mkdir()
    (tmp_path / "parity/fish-upstream.lock.json").write_text(json.dumps(drift.canaries(b"", "") | extras))
    (tmp_path / "parity.yaml").write_text("{}")
    def request(url, **kwargs):
        if url == urls[failed_url]:
            raise URLError("offline")
        return {urls["compat"]: b'{"data":[]}', urls["manifest"]: b'{"version":"0.1.4"}'}.get(url, b"")
    monkeypatch.setattr(drift, "request", request)
    drift.main()
    assert (tmp_path / "drift/report.md").read_text() == ""
    assert "Inconclusive" in capsys.readouterr().out


def test_lock_contains_price_plugin_and_mcp_canaries():
    lock = json.loads((parity.ROOT / "parity/fish-upstream.lock.json").read_text())
    assert lock["compat_model_prices"]["s2.1-pro-free"] == ["0", "0"]
    assert lock["doc_prices"]["s2.1-pro-free"] == r"\$0.00 / M UTF-8 bytes"
    assert lock["fish_agent_plugin_version"]
    assert len(lock["fish_mcp_config_sha256"]) == 64


@pytest.mark.parametrize("prose,labels", [
    ('<Update label="old">', ["old"]),
    ('## outside\n# Changelog\n## new\n### sub\n# Next\n## outside', ["new"]),
    ('<Update label="old">\n# Changelog\n## new\n## old\n', ["new", "old"]),
])
def test_changelog_formats(prose, labels):
    assert drift.canaries(b"", prose)["changelog_labels_seen"] == labels
