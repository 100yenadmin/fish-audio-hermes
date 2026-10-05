from dataclasses import FrozenInstanceError
from decimal import Decimal
import hashlib

import httpx
import pytest
import respx

from fish_audio import account, client
from fish_audio.errors import FishAudioError

BASE = "https://api.fish.audio"
WALLET = BASE + "/wallet/self/api-credit?check_free_credit=true"


@pytest.fixture(autouse=True)
def transport(monkeypatch):
    account._wallet_cache.clear()
    with httpx.Client() as http:
        monkeypatch.setattr(client, "_client", http)
        yield


@pytest.mark.parametrize("credit,topup,free", [("2.630370", "10", None), (0, 0.5, True), ("0", "0", False)])
def test_wallet_decimals_and_scope(credit, topup, free):
    with respx.mock(assert_all_called=True) as mock:
        route = mock.get(WALLET).respond(json={"credit": credit, "cumulative_top_up": topup,
            "has_free_credit": free, "user_id": "synthetic-excluded"})
        wallet = account.get_wallet("test-key", BASE)
        assert wallet == account.Wallet(Decimal(str(credit)), Decimal(str(topup)), free)
        with pytest.raises(FrozenInstanceError):
            wallet.credit = Decimal(10)
        r = route.calls.last.request
        assert r.headers["authorization"] == "Bearer test-key"
        assert r.extensions["timeout"]["read"] == 5
        assert "model" not in r.headers


@pytest.mark.parametrize("data", [None, [], {}, {"credit": "oops", "cumulative_top_up": "0"},
    {"credit": "NaN", "cumulative_top_up": "0"}, {"credit": "0", "cumulative_top_up": "Infinity"},
    {"credit": True, "cumulative_top_up": "0"}, {"credit": "0", "cumulative_top_up": "0", "has_free_credit": "yes"}])
def test_wallet_parse_failures_never_raise(data):
    with respx.mock(assert_all_called=True) as mock:
        mock.get(WALLET).respond(json=data)
        assert account.get_wallet("test-key", BASE) is None


@pytest.mark.parametrize("kind", ["401", "timeout", "text"])
def test_wallet_bad_response(kind):
    with respx.mock(assert_all_called=True) as mock:
        route = mock.get(WALLET)
        if kind == "401":
            route.respond(401, text="No permission")
        elif kind == "timeout":
            route.mock(side_effect=httpx.ReadTimeout("test-key"))
        else:
            route.respond(text="not JSON")
        assert account.get_wallet("test-key", BASE) is None
        assert route.call_count == 1


def test_package_only_allowed_fields():
    expected = {"type": "plus", "total": 250000, "balance": 250000, "extra_balance": 0,
        "finished_at": "synthetic-date", "billing_period": "month", "subscription_status": "active"}
    with respx.mock(assert_all_called=True) as mock:
        mock.get(BASE + "/wallet/self/package").respond(json={**expected, "user_id": "synthetic-excluded", "_id": "synthetic-excluded"})
        assert account.get_package("test-key", BASE) == expected


@pytest.mark.parametrize("status,body", [(401, b"No permission"), (200, b"[]"), (200, b"oops")])
def test_package_failure_never_raises(status, body):
    with respx.mock(assert_all_called=True) as mock:
        mock.get(BASE + "/wallet/self/package").respond(status, content=body)
        assert account.get_package("test-key", BASE) is None


@pytest.mark.parametrize("failure", ["500", "timeout", "invalid_json"])
def test_package_strict_failure_raises_but_default_returns_none(failure):
    with respx.mock(assert_all_called=True) as mock:
        route = mock.get(BASE + "/wallet/self/package")
        if failure == "500":
            route.respond(500)
        elif failure == "timeout":
            route.mock(side_effect=httpx.ReadTimeout("synthetic failure"))
        else:
            route.respond(content=b"not JSON")
        assert account.get_package("test-key", BASE) is None
        with pytest.raises(FishAudioError):
            account.get_package("test-key", BASE, strict=True)


@pytest.mark.parametrize("status,body", [(200, b"null"), (404, b'{"status": 404, "message": "Not Found"}'),
                                         (200, b""), (200, b"  \n"), (204, b"")])
def test_package_strict_no_plan_returns_none(status, body):
    with respx.mock(assert_all_called=True) as mock:
        mock.get(BASE + "/wallet/self/package").respond(status, content=body)
        assert account.get_package("test-key", BASE, strict=True) is None


@pytest.mark.parametrize("wallet,ttl", [(account.Wallet(Decimal(0), Decimal(0), None), 300), (None, 30)])
def test_cache_ttl_and_hashed_isolation(monkeypatch, wallet, ttl):
    now, calls = [10.0], []
    def fetch(key, base):
        calls.append((key, base))
        return wallet
    monkeypatch.setattr(account, "get_wallet", fetch)
    clock = lambda: now[0]
    assert account.cached_wallet("test-key", BASE, clock=clock) == wallet
    now[0] += ttl - 0.01
    assert account.cached_wallet("test-key", BASE, clock=clock) == wallet and len(calls) == 1
    now[0] = 10 + ttl
    assert account.cached_wallet("test-key", BASE, clock=clock) == wallet and len(calls) == 2
    account.cached_wallet("other-test-key", BASE, clock=clock)
    account.cached_wallet("test-key", "https://other.example", clock=clock)
    assert len(calls) == 4
    assert (hashlib.sha256(b"test-key").hexdigest()[:16], BASE) in account._wallet_cache
    assert "test-key" not in repr(account._wallet_cache)


def test_account_no_key_no_request():
    with respx.mock(assert_all_called=True) as mock:
        assert account.get_wallet("", BASE) is None
        assert account.get_package("", BASE) is None
        assert not mock.calls


def test_wallet_strict_empty_body_still_raises():
    with respx.mock(assert_all_called=True) as mock:
        mock.get(BASE + "/wallet/self/api-credit").respond(200, content=b"")
        with pytest.raises(FishAudioError):
            account.get_wallet("test-key", BASE, strict=True)


@pytest.mark.parametrize("body", [b"[]", b"42", b'"plus"', b"true"])
def test_package_strict_non_object_raises_but_default_returns_none(body):
    with respx.mock(assert_all_called=True) as mock:
        mock.get(BASE + "/wallet/self/package").respond(200, content=body)
        assert account.get_package("test-key", BASE) is None
        with pytest.raises(FishAudioError):
            account.get_package("test-key", BASE, strict=True)
