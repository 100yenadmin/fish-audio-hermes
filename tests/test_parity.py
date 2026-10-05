import copy
import hashlib
from pathlib import Path

import yaml

from scripts import parity_check as parity

ROOT = Path(__file__).resolve().parents[1]


def inputs():
    return parity.load_spec(ROOT / "parity/fish-openapi.pruned.json"), yaml.safe_load((ROOT / "parity.yaml").read_text())


def test_committed_parity_map_passes():
    spec, mapping = inputs()
    assert parity.check(spec, mapping) == []
    assert len(spec["paths"]) == 9 and all(not p.startswith("/v1/agent/") for p in spec["paths"])
    if (ROOT / "specs/vendor/fish-openapi.json").exists():
        assert spec["x-source"]["sha256"] == hashlib.sha256((ROOT / "specs/vendor/fish-openapi.json").read_bytes()).hexdigest()


def test_missing_entry_is_rejected():
    spec, mapping = inputs()
    mapping["entries"].pop()
    assert any("Unmapped field" in e for e in parity.check(spec, mapping))


def test_type_enum_required_drift_is_rejected():
    spec, mapping = inputs()
    original = copy.deepcopy(mapping)
    for name, value in (("type", "impossible"), ("enum", ["impossible"]), ("required", True)):
        mapping = copy.deepcopy(original)
        mapping["entries"][0]["spec"][name] = not original["entries"][0]["spec"][name] if name == "required" else value
        assert any("drift" in e for e in parity.check(spec, mapping))


def test_verified_without_tests_is_rejected():
    spec, mapping = inputs()
    mapping["entries"][0].update(status="verified", tests=[])
    assert any("Verified without tests" in e for e in parity.check(spec, mapping))


def test_invalid_status_unknown_test_and_extra_field_are_rejected():
    spec, mapping = inputs()
    mapping["entries"][0].update(status="bad", field="invented", tests=["tests/missing.py::test_missing"])
    errors = parity.check(spec, mapping, collected=set())
    assert any("Invalid status" in e for e in errors)
    assert any("Field not in spec" in e for e in errors)
    assert any("Unknown test id" in e for e in errors)


def test_cli_snapshot_and_report(tmp_path, capsys):
    _, mapping = inputs()
    mapping["entries"][0]["spec"]["type"] = "wrong"
    target = tmp_path / "parity.yaml"
    target.write_text(yaml.safe_dump(mapping))
    assert parity.main(["--mapping", str(target), "--no-collect"]) == 1
    assert parity.main(["--mapping", str(target), "--snapshot", "--no-collect", "--report"]) == 0
    assert "Fields=316" in capsys.readouterr().out
