"""Bounded audio writes that leave the destination intact on failure."""
import os
import tempfile
from pathlib import Path

AUDIO_CAP = 64 * 1024 * 1024


def audio_output_dir():
    from hermes_constants import get_hermes_dir
    path = Path(get_hermes_dir("cache/audio", "audio_cache"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def atomic_write(path, chunks, cap=AUDIO_CAP):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
            temporary = handle.name
            total = 0
            for chunk in chunks:
                total += len(chunk)
                if total > cap:
                    raise ValueError("Fish Audio output exceeds the audio size cap.")
                handle.write(chunk)
            if not total:
                raise ValueError("Fish Audio returned empty audio.")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            os.unlink(temporary)
    return str(path)
