"""Bounded audio writes that leave the destination intact on failure."""
import os
import stat
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
            os.fchmod(handle.fileno(), 0o644)
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            os.unlink(temporary)
    return str(path)


class InputFileError(ValueError):
    """An unsafe, oversized or unrecognized media input (contents never echoed)."""


def _hermes_home():
    try:
        from hermes_constants import get_hermes_home
        return Path(get_hermes_home()).resolve()
    except ImportError:
        return Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))).expanduser().resolve()


def _secret_path(path):
    path = Path(str(path).casefold())
    if path.name.endswith(".pem") or path.name.startswith("id_"):
        return True
    user_home = Path.home().resolve()
    for directory in (user_home / ".ssh", user_home / ".aws", user_home / ".config" / "gcloud"):
        if any(path.is_relative_to(Path(str(d).casefold())) for d in (directory, directory.resolve())):
            return True
    try:
        from hermes_constants import get_default_hermes_root
        root = get_default_hermes_root().resolve()
    except ImportError:
        root = _hermes_home()
    for home in (_hermes_home(), root):
        home = Path(str(home).casefold())
        if path.is_relative_to(home):
            relative = path.relative_to(home)
            if (path.name in {".env", "auth.json", "config.yaml"}
                    or "secrets" in relative.parts or path.name.endswith(".key")):
                return True
    return False


def _audio_kind(header):
    # ADTS must precede MP3's broader frame-sync match.
    if header[:2] in {b"\xff\xf1", b"\xff\xf9"}:
        return "aac"
    if header.startswith(b"ID3") or len(header) >= 2 and header[0] == 0xFF and header[1] & 0xE0 == 0xE0:
        return "mp3"
    if header.startswith(b"RIFF") and header[8:12] == b"WAVE":
        return "wav"
    if header.startswith(b"OggS"):
        return "ogg"
    if header.startswith(b"\x1aE\xdf\xa3"):
        return "webm"
    if header.startswith(b"fLaC"):
        return "flac"
    if header[4:8] == b"ftyp":
        return "mp4"
    return None


def _image_kind(header):
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if header.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return "webp"
    return None


def validate_input_file(path, *, max_bytes, kinds, sniff=_audio_kind):
    label = "Image" if sniff is _image_kind else "Audio"
    try:
        path = Path(path).expanduser()
        try:
            from agent.file_safety import get_read_block_error
        except ImportError:
            pass
        else:
            if get_read_block_error(str(path.absolute())):
                raise InputFileError("Core file safety refused this credential or protected file.")
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise InputFileError(f"{label} input must be a regular file, not a symlink.")
        resolved = path.parent.resolve(strict=True) / path.name
        if resolved != Path(os.path.realpath(path)):
            raise InputFileError(f"{label} input changed while resolving its path.")
        if _secret_path(path.absolute()) or _secret_path(resolved):
            raise InputFileError(f"Secret and credential files cannot be used as {label.lower()} input.")
        if not 0 < info.st_size <= max_bytes:
            raise InputFileError(f"{label} input is empty or exceeds the size limit.")
        fd = os.open(resolved, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if (opened.st_dev, opened.st_ino, opened.st_size) != (info.st_dev, info.st_ino, info.st_size):
                raise InputFileError(f"{label} input changed while opening it.")
            data = handle.read(max_bytes + 1)
            if len(data) != opened.st_size or len(data) > max_bytes:
                raise InputFileError(f"{label} input changed or exceeds the size limit.")
            kind = sniff(data[:64])
        if kind is None or kind not in kinds:
            raise InputFileError(f"{label} input has an unsupported or mismatched media kind.")
        return resolved, kind, data
    except InputFileError:
        raise
    except (OSError, TypeError, ValueError):
        raise InputFileError(f"{label} input could not be read safely.") from None
