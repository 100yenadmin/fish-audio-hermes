from pathlib import Path
import sys
from types import ModuleType

import pytest

from fish_audio.media import InputFileError, validate_input_file

MAGIC = {"mp3": b"ID3synthetic", "wav": b"RIFF\x00\x00\x00\x00WAVEsynthetic",
         "ogg": b"OggSsynthetic", "webm": b"\x1aE\xdf\xa3synthetic", "flac": b"fLaCsynthetic",
         "mp4": b"\x00\x00\x00\x18ftypisomsynthetic", "aac": b"\xff\xf1synthetic"}


@pytest.fixture(autouse=True)
def homes(monkeypatch, tmp_path):
    module = ModuleType("hermes_constants")
    module.get_hermes_home = lambda: tmp_path / "hermes"
    monkeypatch.setitem(sys.modules, "hermes_constants", module)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "user")


@pytest.mark.parametrize("kind", MAGIC)
def test_each_magic_kind(tmp_path, kind):
    path = tmp_path / "input.wav"
    path.write_bytes(MAGIC[kind])
    resolved, detected = validate_input_file(path, max_bytes=100, kinds={kind})
    assert resolved == path.resolve() and detected == kind


@pytest.mark.parametrize("header,kind", [(b"\xff\xe3synthetic", "mp3"), (b"\xff\xf9synthetic", "aac")])
def test_frame_magic_order(tmp_path, header, kind):
    path = tmp_path / "input.mp3"
    path.write_bytes(header)
    assert validate_input_file(path, max_bytes=100, kinds={kind})[1] == kind


def test_final_symlink_refused_and_parent_symlink_resolved(tmp_path):
    path = tmp_path / "voice.ogg"
    path.write_bytes(MAGIC["ogg"])
    link = tmp_path / "link.ogg"
    link.symlink_to(path)
    with pytest.raises(InputFileError, match="symlink"):
        validate_input_file(link, max_bytes=100, kinds={"ogg"})
    directory = tmp_path / "parent"
    directory.symlink_to(tmp_path, target_is_directory=True)
    assert validate_input_file(directory / "voice.ogg", max_bytes=100, kinds={"ogg"})[0] == path.resolve()


@pytest.mark.parametrize("relative", ["hermes/.env", "hermes/auth.json", "hermes/config.yaml",
    "hermes/secrets/voice.mp3", "hermes/profiles/p/.env", "hermes/voice.key",
    "user/.ssh/voice.mp3", "user/.aws/voice.mp3", "user/.config/gcloud/voice.mp3", "sample.pem", "id_voice.mp3"])
def test_secret_paths_refused_even_with_audio_magic(tmp_path, relative):
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(MAGIC["mp3"])
    with pytest.raises(InputFileError, match="credential") as exc:
        validate_input_file(path, max_bytes=100, kinds={"mp3"})
    assert "synthetic" not in str(exc.value)


def test_parent_alias_to_secrets_refused(tmp_path):
    directory = tmp_path / "hermes/secrets"
    directory.mkdir(parents=True)
    (directory / "voice.mp3").write_bytes(MAGIC["mp3"])
    alias = tmp_path / "alias"
    alias.symlink_to(directory, target_is_directory=True)
    with pytest.raises(InputFileError, match="credential"):
        validate_input_file(alias / "voice.mp3", max_bytes=100, kinds={"mp3"})


def test_secret_directory_symlink_alias_refused(tmp_path):
    target = tmp_path / "credentials"
    target.mkdir()
    (target / "voice.mp3").write_bytes(MAGIC["mp3"])
    (tmp_path / "user").mkdir()
    (tmp_path / "user/.ssh").symlink_to(target, target_is_directory=True)
    with pytest.raises(InputFileError, match="credential"):
        validate_input_file(target / "voice.mp3", max_bytes=100, kinds={"mp3"})


@pytest.mark.parametrize("contents,cap,kinds", [(b"", 100, {"mp3"}), (MAGIC["mp3"], 1, {"mp3"}),
    (MAGIC["ogg"], 100, {"mp3"}), (b"private synthetic contents", 100, {"mp3"})])
def test_empty_cap_mismatch_and_unknown(tmp_path, contents, cap, kinds):
    path = tmp_path / "input.wav"
    path.write_bytes(contents)
    with pytest.raises(InputFileError) as exc:
        validate_input_file(path, max_bytes=cap, kinds=kinds)
    assert "private" not in str(exc.value) and "synthetic" not in str(exc.value)


def test_missing_and_directory_fail_safely(tmp_path):
    for path in (tmp_path / "missing.wav", tmp_path):
        with pytest.raises(InputFileError):
            validate_input_file(path, max_bytes=100, kinds={"wav"})
