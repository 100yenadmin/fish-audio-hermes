"""Process-local failure receipt; never persist credentials or response bodies."""
from datetime import datetime, timezone
import threading
from .secrets import redact

_lock = threading.Lock()
_last = None


def record_failure(kind, message):
    global _last
    with _lock:
        _last = (datetime.now(timezone.utc).isoformat(), kind, redact(str(message)))


def last_failure():
    with _lock:
        return _last
