from datetime import datetime, timezone
import json

import pytest
import respx

from fish_audio import account, account_tool, models, settings, tool_support, tools
from test_commands import BASE, config, wallet_routes


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(tool_support, "fish_api_key", lambda: "test-key")
    with respx.mock:
        yield


def call(action, **kwargs):
    return json.loads(tools.fish_account({"action": action, **kwargs}))


def test_balance_shared_read_once(config):
    with respx.mock(assert_all_called=True) as mock:
        wallet_routes(mock)
        result = call("balance")
        assert result["success"] and result["credit"] == "2.5" and result["cumulative_top_up"] == "10"
        assert result["plan"]["type"] == "plus" and len(mock.calls) == 2
        assert [r.request.url.path for r in mock.calls] == ["/wallet/self/api-credit", "/wallet/self/package"]


@pytest.mark.parametrize("action", ["balance", "models", "estimate"])
def test_operator_hides_billing(config, monkeypatch, action):
    monkeypatch.setattr(settings, "operator_account", lambda: True)
    with respx.mock as mock:
        result = call(action, text="日本語")
        assert result["success"] and not mock.calls
    if action == "balance":
        assert result == {"operator_managed": True, "message": "Voice billing for this agent is handled by its operator.", "success": True}
    elif action == "models":
        for row in result["models"]:
            assert not {"price", "usd", "source", "price_as_of"} & row.keys()
            assert "$" not in json.dumps(row)
        assert next(r for r in result["models"] if r["id"] == "s1")["retires"] == "2026-12-31"
    else:
        assert result["billable_bytes"] == 9 and not {"usd_estimate", "unit_price", "price_as_of", "source"} & result.keys()


@pytest.mark.parametrize("when,usd", [("2026-12-01T07:59:59.999999+00:00", 10),
                                       ("2026-12-01T08:00:00+00:00", 50),
                                       ("2026-12-01T17:00:00+09:00", 50)])
def test_drama_price_cutoff(config, when, usd):
    now = datetime.fromisoformat(when)
    row = next(r for r in models.pricing(now) if r["id"] == "drama-3")
    assert row["price"] == {"usd": usd, "unit": "1,000,000 UTF-8 bytes"}
    assert account_tool.estimate({"bytes": 1_000_000, "model": "drama-3"}, now=now)["usd_estimate"] == usd


def test_catalog_is_single_source(config):
    result = call("models")
    rows = {r["id"]: r for r in result["models"]}
    assert set(rows) == models.MODEL_IDS | {"transcribe-1-pro", "transcribe-1", "transcribe-stream-1", "voice-design-1"}
    assert all(any(row is item for item in models.MODEL_CATALOG) for row in models.MODELS)
    assert all(r["price_as_of"] == "2026-11-09" and r["source"] == models.PRICE_SOURCE for r in rows.values())
    assert rows["transcribe-1-pro"]["price"]["usd"] == .36
    assert rows["transcribe-stream-1"]["kind"] == "realtime_stt" and rows["voice-design-1"]["price"]["usd"] == .01
    assert rows["s2.1-pro-free"]["ends"] == "2026-11-30"


def test_utf8_and_normalized_estimate(config):
    result = call("estimate", text="日本語", model="s2.1-pro")
    assert result["success"] and result["billable_bytes"] == 9
    assert result["usd_estimate"] == pytest.approx(9 * 15 / 1_000_000)
    assert result["unit_price"]["usd"] == 15 and account_tool.NOTE in result["note"]
    assert call("estimate", text="[excited] 日本語", model="s1")["billable_bytes"] == len("(excited) 日本語".encode())
    assert call("estimate", text="[unsupported] 日本語", model="s1")["billable_bytes"] == len(" 日本語".encode())


def test_offline_default_uses_shared_resolver(config, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("estimate must not read a wallet")
    monkeypatch.setattr(settings, "cached_wallet", forbidden)
    result = call("estimate", bytes=1_000_000)
    assert result["model"] == "s2.1-pro" and "Wallet default was not read" in result["note"]
    config[0]["tts"]["fish-audio"] = {"model": "drama-3"}
    assert call("estimate", bytes=1)["model"] == "drama-3"
    assert call("estimate", bytes=1, model="s2-pro")["model"] == "s2-pro"
    config[0]["plugins"] = {"entries": {"fish-audio": {"settings": {"allow_free_model": False}}}}
    assert call("estimate", bytes=1, model="s2.1-pro-free")["model"] == "s2.1-pro"


def test_units_and_asr_estimates(config):
    assert call("estimate", characters=1000)["billable_bytes"] == 1000
    assert "one UTF-8 byte per character" in call("estimate", characters=1)["note"]
    result = call("estimate", audio_seconds=1800, asr_model="transcribe-1")
    assert result["audio_seconds"] == 1800 and result["usd_estimate"] == pytest.approx(.18)
    assert result["unit_price"] == {"usd": .36, "unit": "audio hour"}
    assert "billable_bytes" not in result
    result = account_tool.estimate({"audio_seconds": 1, "asr_model": "transcribe-1"}, now=datetime(2027, 1, 1, tzinfo=timezone.utc))
    assert result["model"] == "transcribe-1-pro"


@pytest.mark.parametrize("args", [{}, {"text": "hi", "bytes": 2}, {"text": 3}, {"characters": -1},
    {"bytes": True}, {"audio_seconds": -1}, {"audio_seconds": float("nan")},
    {"audio_seconds": 2, "asr_model": "bad"}, {"bytes": 3, "model": "bad"}])
def test_invalid_estimates(config, args):
    assert call("estimate", **args)["success"] is False


@pytest.mark.parametrize("when, usd", [(datetime(2026, 11, 30, 23, 59, tzinfo=timezone.utc), 0),
                                       (datetime(2026, 12, 1, 0, 0, tzinfo=timezone.utc), 15)])
def test_free_model_price_after_announced_end(config, when, usd):
    row = next(r for r in models.pricing(when) if r["id"] == "s2.1-pro-free")
    assert row["price"]["usd"] == usd
    assert ("has passed" in row["notes"]) == (usd == 15)


def test_provider_model_list_stays_price_free(config):
    from fish_audio.tts import FishAudioTTSProvider
    rows = FishAudioTTSProvider().list_models()
    assert rows and all(not ({"usd", "unit", "kind", "use"} & set(r)) for r in rows)
