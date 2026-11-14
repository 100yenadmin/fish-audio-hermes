"""Shared safe copy-out for cached speech and subtitles."""
from functools import partial
import os
from pathlib import Path
import shutil

from .tool_support import require, ToolInputError

EXISTS = "Output exists; use --force to replace a file."


def _destination(path, force, *, exists_message=EXISTS):
    # A symlinked folder (macOS /tmp) is fine: each file lands by link or replace, never written through a link.
    require(not path.is_symlink(), "Output path must not be a symlink.")
    require(path.parent.is_dir(), "Output parent directory is missing.")
    require(os.access(path.parent, os.W_OK), "Output directory is not writable.")
    require(not path.exists() or force and path.is_file(), exists_message)


def _outputs(target, timestamps, *, option="--out"):
    """Every path --out may write: the audio, plus its subtitles when timestamps are on."""
    require(target.suffix.lower() not in {".srt", ".vtt"}, f"Use an audio file name for {option} (not .srt or .vtt).")
    return [target] + ([target.with_suffix(ext) for ext in (".srt", ".vtt")] if timestamps else [])


def _copy_output(result, target, force, *, exists_message=EXISTS, destination=None, place_new=None):
    destination = destination or partial(_destination, exists_message=exists_message)
    place_new = place_new or partial(_place_new, exists_message=exists_message)
    source = Path(result["file_path"])
    require(source.resolve() != target.resolve(), "Output must differ from the cached audio path.")
    pairs = [(source, target)] + [(source.with_suffix(ext), target.with_suffix(ext))
                                for ext in (".srt", ".vtt") if source.with_suffix(ext).is_file()]
    for _, dest in pairs:
        destination(dest, force)
    staged, placed = [], []  # placed: (dest, identity of the file this run put there)
    try:
        for src, dest in pairs:  # Stage every file first; a failure then removes only what this run placed.
            temp_path, identity = _new_file(dest.parent / ".fish-tmp", src)
            staged.append((temp_path, identity, dest))
        for temp_path, identity, dest in staged:
            if force:
                os.replace(temp_path, dest)
                placed.append((dest, identity))
            else:
                placed.append((dest, identity))  # recorded first: the link shares the staged file's identity
                placed[-1] = (dest, place_new(temp_path, dest, identity))
    except Exception as exc:
        if not force:
            _remove_own(placed)
        elif placed:
            raise ToolInputError("Only some outputs were replaced: " + ", ".join(str(d) for d, _ in placed)) from exc
        raise
    except BaseException:  # Ctrl-C and exits pass through unchanged after cleanup
        if not force:
            _remove_own(placed)
        raise
    finally:
        for temp_path, _, _ in staged:
            temp_path.unlink(missing_ok=True)
    result["out_path"] = str(target)


def _identity(stat):
    return stat.st_dev, stat.st_ino


def _new_file(base, src):
    """Create a fresh file next to its destination (mode 0666 minus the umask) holding src's bytes."""
    for attempt in range(100):
        path = base.with_name(f"{base.name}{attempt}")
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
        except FileExistsError:
            continue
        try:
            with os.fdopen(descriptor, "wb") as out, src.open("rb") as data:
                identity = _identity(os.fstat(out.fileno()))
                shutil.copyfileobj(data, out)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return path, identity
    raise ToolInputError("Could not create a temporary output file.")


def _remove_own(placed):
    """Delete only files that are still exactly the ones this run created (same device and inode)."""
    for dest, identity in placed:
        try:
            # POSIX has no unlink-if-inode; a file swapped in between these two calls is the accepted residue.
            if identity is not None and _identity(os.lstat(dest)) == identity:
                dest.unlink()
        except OSError:  # cleanup never hides the original error
            pass


def _place_new(temp_path, dest, identity, *, exists_message=EXISTS):
    """Put a staged file at dest without replacing a file that appeared meanwhile; return its identity."""
    try:
        os.link(temp_path, dest)  # atomic, refuses an existing name, keeps the staged file's identity
        return identity
    except FileExistsError:
        raise ToolInputError(exists_message) from None
    except OSError:  # no hard links (exFAT, some network drives): exclusive create, then copy
        pass
    try:
        _, created = _new_file_at(dest, temp_path)
    except FileExistsError:
        raise ToolInputError(exists_message) from None
    return created


def _new_file_at(path, src):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
    identity = None
    try:
        with os.fdopen(descriptor, "wb") as out, src.open("rb") as data:
            identity = _identity(os.fstat(out.fileno()))
            shutil.copyfileobj(data, out)
    except BaseException:
        _remove_own([(path, identity)])
        raise
    return path, identity


