"""Small account reads and a credential-isolated, in-process wallet cache."""
from dataclasses import dataclass
from decimal import Decimal
import hashlib
import threading
import time

from . import client
from .errors import FishAudioError, response_error


@dataclass(frozen=True)
class Wallet:
    credit: Decimal
    cumulative_top_up: Decimal
    has_free_credit: bool | None


def _get(path, key, base_url, timeout=5.0, *, strict=False):
    if not key:
        return None
    try:
        response = client._http_client().get(base_url.rstrip("/") + path,
                                             headers=client.request_headers(key), timeout=timeout)
        try:
            if response.status_code != 200:
                if strict:
                    raise response_error(response.status_code, response.headers, response.content[:65536], key=key)
                return None
            data = response.json()
            return data if isinstance(data, dict) else None
        finally:
            response.close()
    except Exception as exc:
        if strict:
            raise exc if isinstance(exc, FishAudioError) else response_error(None, key=key) from None
        return None


def get_wallet(key, base_url, *, timeout=5.0, strict=False) -> Wallet | None:
    try:
        data = _get("/wallet/self/api-credit?check_free_credit=true", key, base_url, timeout, strict=strict)
        if data is None:
            return None
        amounts = []
        for field in ("credit", "cumulative_top_up"):
            raw = data[field]
            if type(raw) not in (str, int, float):
                return None
            amount = Decimal(str(raw))
            if not amount.is_finite():
                return None
            amounts.append(amount)
        free = data.get("has_free_credit")
        if free is not None and type(free) is not bool:
            return None
        return Wallet(*amounts, free)
    except Exception as exc:
        if strict:
            raise exc if isinstance(exc, FishAudioError) else response_error(None, key=key) from None
        return None


def get_package(key, base_url, strict=False) -> dict | None:
    try:
        data = _get("/wallet/self/package", key, base_url, strict=strict)
        if data is None:
            return None
        fields = {"type", "total", "balance", "extra_balance", "finished_at", "billing_period", "subscription_status",
                  "cancel_at_period_end"}
        return {k: v for k, v in data.items() if k in fields}
    except Exception as exc:
        # A 404 means "no plan" (the OpenAPI documents no absence shape), so only real failures raise.
        if strict and getattr(exc, "status", None) != 404:
            raise
        return None


_wallet_cache = {}
_cache_lock = threading.Lock()
_clock = time.monotonic


def cached_wallet(key, base_url, *, clock=None):
    clock = _clock if clock is None else clock
    cache_key = (hashlib.sha256(key.encode()).hexdigest()[:16], base_url)
    now = clock()
    with _cache_lock:
        entry = _wallet_cache.get(cache_key)
        if entry is not None and now < entry[0]:
            return entry[1]
    wallet = get_wallet(key, base_url)
    with _cache_lock:
        _wallet_cache[cache_key] = (clock() + (300 if wallet is not None else 30), wallet)
    return wallet
