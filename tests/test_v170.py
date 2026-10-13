"""Free-period wording across UTC boundaries; every Fish/account call is stubbed."""
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from fish_audio import account, cli, client, commands, errors, media, models, settings, tools, tts
from test_cli import config  # noqa: F401 — isolated, in-memory CLI config
from test_dashboard_api import env  # noqa: F401 — real REST route, stubbed profiles

DATES = [("2026-10-31", "active"), ("2026-11-01", "ending"),
         ("2026-11-30", "ending"), ("2026-12-01", "ended")]
NOTICES = {
    "active": "Using Fish Audio's free s2.1-pro-free model (free until 30 November 2026; Fish may use free-tier requests to improve its models). Top up at https://fish.audio/app/developers/billing to use s2.1-pro.",
    "ending": "Using Fish Audio's free s2.1-pro-free model, free until 30 November 2026 (Fish may use free-tier requests to improve its models). To keep speaking after that, add API credit at https://fish.audio/app/developers/billing; with credit the plugin uses s2.1-pro.",
    "ended": "This Fish account has no API credit, so the plugin is using s2.1-pro-free, whose free period Fish announced would end on 30 November 2026. If speech fails, add API credit at https://fish.audio/app/developers/billing; with credit the plugin uses s2.1-pro.",
}
WARNING = ("s2.1-pro-free: Fish announced its free period would end on 30 November 2026, so Fish may refuse these requests or "
           "serve and bill them as S2.1 Pro. Choose s2.1-pro.")
PREFIX = "Fish announced the end of the free s2.1-pro-free period on 30 November 2026. "
EMPTY = account.Wallet(Decimal(0), Decimal(0), False)
BASE = "https://api.fish.audio"


@pytest.fixture
def clock(monkeypatch):
    def set_today(day, module=models):
        today = date.fromisoformat(day)
        def now(tz):
            assert tz is timezone.utc
            return datetime.combine(today, time(), tz)
        monkeypatch.setattr(module, "datetime", SimpleNamespace(now=now))
    return set_today


@pytest.fixture
def empty_account(config, monkeypatch):
    cfg = config[0]
    monkeypatch.setattr(settings, "cached_wallet", lambda *a: EMPTY)
    monkeypatch.setattr(account, "cached_wallet", lambda *a: EMPTY)
    monkeypatch.setattr(account, "get_wallet", lambda *a, **kw: EMPTY)
    monkeypatch.setattr(account, "get_package", lambda *a: None)
    monkeypatch.setattr(commands, "require_unmanaged", lambda *a: None)
    monkeypatch.setattr(client, "tts_to_file", lambda *a, **kw: None)
    return cfg


def test_phase_boundaries_and_row_dates(clock, monkeypatch):
    row = next(row for row in models.MODELS if row["id"] == "s2.1-pro-free")
    assert row["ends"] == "2026-11-30"
    for day, phase in DATES:
        clock(day)
        assert models.free_model_phase() == models.free_model_phase(date.fromisoformat(day)) == phase
        assert models.free_model_warning(date.fromisoformat(day)) == (WARNING if phase == "ended" else None)
    monkeypatch.setitem(row, "ends", "2026-12-31")
    for day, phase in [("2026-11-30", "active"), ("2026-12-01", "ending"),
                       ("2026-12-31", "ending"), ("2027-01-01", "ended")]:
        assert models.free_model_phase(date.fromisoformat(day)) == phase


def test_notice_text_per_phase(clock):
    for day, phase in DATES:
        clock(day)
        assert settings.free_model_notice(date.fromisoformat(day)) == NOTICES[phase]
        assert settings.free_model_notice() == NOTICES[phase]


def test_resolution_does_not_change_across_phases(empty_account, monkeypatch, clock):
    cfg = empty_account
    for day, phase in DATES:
        clock(day)
        assert models.free_model_phase() == phase
        cfg["tts"]["fish-audio"] = {}
        cfg["plugins"] = {}
        monkeypatch.setattr(settings, "cached_wallet", lambda *a: EMPTY)
        assert settings.resolve_model(None, key="synthetic", base_url=BASE) == ("s2.1-pro-free", True)
        cfg["tts"]["fish-audio"]["model"] = "s2.1-pro-free"
        assert settings.resolve_model(None, key="synthetic", base_url=BASE) == ("s2.1-pro-free", False)
        assert settings.resolve_model("s2.1-pro-free", key="synthetic", base_url=BASE, prefer_call=True) == ("s2.1-pro-free", False)
        assert models.effective_model("s2.1-pro-free") == "s2.1-pro-free"
        cfg["plugins"] = {"entries": {"fish-audio": {"settings": {"allow_free_model": False}}}}
        assert settings.resolve_model(None, key="synthetic", base_url=BASE) == ("s2.1-pro", False)
        cfg["tts"]["fish-audio"] = {}
        assert settings.resolve_model(None, key="synthetic", base_url=BASE) == ("s2.1-pro", True)
        cfg["plugins"] = {}
        monkeypatch.setattr(settings, "cached_wallet", lambda *a: account.Wallet(Decimal(1), Decimal(0), False))
        assert settings.resolve_model(None, key="synthetic", base_url=BASE) == ("s2.1-pro", True)


def test_notice_log_once_per_process(empty_account, monkeypatch, clock, caplog):
    for day, phase in DATES:
        clock(day)
        caplog.clear()
        monkeypatch.setattr(settings._notice_logger, "_fish_notice_logged", False, raising=False)
        for _ in range(2):
            settings.resolve_model(None, key="synthetic", base_url=BASE)
        assert [r.message for r in caplog.records] == [NOTICES[phase]]


def test_tool_notice(empty_account, monkeypatch, clock, tmp_path):
    monkeypatch.setattr(media, "audio_output_dir", lambda: tmp_path)
    for day, phase in DATES:
        clock(day)
        result = tools._speak({"text": "Hello"}, "synthetic", BASE, "", record_media=False)
        assert result["notice"] == NOTICES[phase]
        assert result["model"] == "s2.1-pro-free"
        explicit = tools._speak({"text": "Hello", "model": "s2.1-pro-free"}, "synthetic", BASE, "", record_media=False)
        assert "notice" not in explicit


def test_tts_metadata_notice(empty_account, monkeypatch, clock, tmp_path):
    monkeypatch.setattr(tts, "fish_api_key", lambda: "synthetic")
    for day, phase in DATES:
        clock(day)
        metadata = {}
        tts.FishAudioTTSProvider().synthesize("Hello", str(tmp_path / "out.mp3"), result_metadata=metadata)
        assert metadata["fish_audio_notice"] == NOTICES[phase]


def test_model_reply_notice(empty_account, clock):
    for day, phase in DATES:
        clock(day)
        reply = commands.handle("model s2.1-pro-free")
        # After the end date an explicit choice gets the warning instead of the empty-wallet notice.
        assert reply.splitlines()[-1] == (WARNING if phase == "ended" else NOTICES[phase])
        assert (NOTICES["ended"] in reply) is False
        assert empty_account["tts"]["fish-audio"]["model"] == "s2.1-pro-free"


def test_cli_login_notice(empty_account, clock, capsys):
    for day, phase in DATES:
        clock(day)
        assert cli._login(SimpleNamespace(key_stdin=False, yes=True)) == 0
        assert NOTICES[phase] in capsys.readouterr().out.splitlines()


def test_explicit_status_warning(empty_account, clock):
    cfg = empty_account
    cfg["tts"]["fish-audio"] = {"model": "s2.1-pro-free"}
    for day, phase in DATES:
        clock(day)
        for operator in (False, True):
            for allowed in (True, False):
                cfg["plugins"] = {"entries": {"fish-audio": {"settings": {"allow_free_model": allowed, "operator_account": operator}}}}
                lines = commands.status("synthetic").splitlines()
                assert ("warn: " + WARNING in lines) == (phase == "ended" and allowed)
        cfg["plugins"] = {}
        cfg["tts"]["fish-audio"] = {}
        assert WARNING not in commands.status("synthetic")  # wallet default is not an explicit choice
        cfg["tts"]["fish-audio"] = {"model": "s2.1-pro-free"}


def test_explicit_model_reply_warning(empty_account, clock):
    for day, phase in DATES:
        clock(day)
        for operator in (False, True):
            for allowed in (True, False):
                empty_account["plugins"] = {"entries": {"fish-audio": {"settings": {"allow_free_model": allowed, "operator_account": operator}}}}
                reply = commands.handle("model s2.1-pro-free")
                assert (WARNING in reply.splitlines()) == (phase == "ended" and allowed)
                if operator or not allowed:
                    assert NOTICES[phase] not in reply


def test_explicit_cli_warning(empty_account, clock, capsys):
    cfg = empty_account
    cfg["tts"]["fish-audio"] = {"model": "s2.1-pro-free"}
    for day, phase in DATES:
        clock(day)
        for operator in (False, True):
            for allowed in (True, False):
                cfg["plugins"] = {"entries": {"fish-audio": {"settings": {"allow_free_model": allowed, "operator_account": operator}}}}
                cli._doctor(SimpleNamespace(no_synth=True))
                assert ("warn: " + WARNING in capsys.readouterr().out.splitlines()) == (phase == "ended" and allowed)
                cli._handle(SimpleNamespace(fish_command="status"))
                assert ("warn: " + WARNING in capsys.readouterr().out.splitlines()) == (phase == "ended" and allowed)
        cfg["plugins"] = {}
        cfg["tts"]["fish-audio"] = {}
        cli._doctor(SimpleNamespace(no_synth=True))
        assert WARNING not in capsys.readouterr().out
        cfg["tts"]["fish-audio"] = {"model": "s2.1-pro-free"}


def test_setup_badge(clock):
    for day, phase in DATES:
        clock(day)
        assert tts.FishAudioTTSProvider().get_setup_schema()["badge"] == ("" if phase == "ended" else "free model")


def test_error_hint_prefix(empty_account, clock):
    for day, phase in DATES:
        clock(day)
        for status in (400, 402, 403, 429):
            for model in ("s2.1-pro-free", "s2.1-pro", "s2-pro"):
                for defaulted in (False, True):
                    message = str(errors.response_error(status, model=model, defaulted=defaulted))
                    has_prefix = phase == "ended" and model == "s2.1-pro-free" and status in (402, 403, 429)
                    assert (PREFIX in message) == has_prefix
                    if has_prefix:
                        assert message.endswith(PREFIX + "Switch to s2.1-pro and top up: " + errors.BILLING_URL)
        empty_account["plugins"] = {"entries": {"fish-audio": {"settings": {"operator_account": True}}}}
        assert PREFIX not in str(errors.response_error(402, model="s2.1-pro-free"))
        empty_account["plugins"] = {}


def test_available_phase(env, clock, monkeypatch):
    for day, phase in DATES:
        clock(day, env.api._fa("models"))
        for key in ("", "synthetic"):
            monkeypatch.setattr(env.api._fa("secrets"), "fish_api_key", lambda: key)
            payload = env.get("/available")
            assert payload["free_model"] == phase
            assert payload["key"] is bool(key)


@pytest.mark.parametrize("path", ["README.md", "skills/fish-audio-setup/SKILL.md"])
def test_notice_docs_cover_each_phase(path):
    text = (Path(__file__).resolve().parents[1] / path).read_text()
    assert "Before 1 November 2026" in text
    assert "during November" in text
    assert "After 30 November" in text
    assert "model sent" in text
