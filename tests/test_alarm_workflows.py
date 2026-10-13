"""CI-only alarms: YAML admission, target resolution and soft history failures."""
import os
from pathlib import Path
import subprocess

import yaml

ROOT = Path(__file__).resolve().parents[1]


def workflow(name):
    return yaml.safe_load((ROOT / ".github/workflows" / name).read_text())


def test_alarm_yaml_permissions_schedules_and_install_parity():
    bench, latest, ci = (workflow(n) for n in ("fish-bench.yml", "hermes-latest.yml", "ci.yml"))
    assert bench["permissions"]["issues"] == "write"
    assert bench["permissions"]["actions"] == "read"
    assert latest["permissions"] == {"contents": "read", "issues": "write"}
    triggers = latest[True]  # YAML 1.1 interprets the Actions 'on' key as True.
    assert set(triggers) == {"schedule", "workflow_dispatch"}
    assert triggers["schedule"][0]["cron"] not in ("41 6 * * 3", "17 6 * * 1")
    assert latest["jobs"]["test"]["strategy"]["matrix"]["target"] == ["main", "release"]
    steps = latest["jobs"]["test"]["steps"]
    pinned_steps = {s.get("name"): s for s in ci["jobs"]["hermes"]["steps"] if "name" in s}
    for step in steps:
        if step.get("name") in pinned_steps:
            assert step["run"] == pinned_steps[step["name"]]["run"]
            assert step.get("env") == pinned_steps[step["name"]].get("env")
    assert steps[1]["with"]["ref"] == "${{ needs.resolve.outputs[matrix.target] }}"


def test_history_artifacts_are_bounded_optional_and_retained(tmp_path):
    steps = workflow("fish-bench.yml")["jobs"]["bench"]["steps"]
    history = next(s for s in steps if s.get("name") == "Fetch up to four previous benchmark artifacts")
    assert history["continue-on-error"] is True
    assert "--workflow fish-bench.yml --status completed --limit 4" in history["run"]
    assert "--history bench-history" in next(s for s in steps if s.get("id") == "bench")["run"]
    assert next(s for s in steps if "upload-artifact@" in s.get("uses", ""))["with"]["retention-days"] == 90
    # Failed download attempts and failed listing both leave the shell step successful.
    for list_fails in (False, True):
        stub = 'gh() { if [ "$2" = list ]; then ' + ('return 1;' if list_fails else "printf '11\\n12\\n';") + ' else return 1; fi; }\n'
        result = subprocess.run(["bash", "-e", "-c", stub + history["run"]], cwd=tmp_path, capture_output=True)
        assert result.returncode == 0


def test_latest_resolves_default_branch_and_release_to_shas(tmp_path):
    step = workflow("hermes-latest.yml")["jobs"]["resolve"]["steps"][0]
    script = step["run"]
    env = os.environ | {"HERMES_UPSTREAM_REPO": "fixture/hermes", "GITHUB_OUTPUT": str(tmp_path / "output")}
    stub = """gh() {
      case "$2" in
        repos/fixture/hermes) echo trunk;;
        repos/fixture/hermes/releases/latest) echo vfixture;;
        repos/fixture/hermes/commits/trunk) echo aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa;;
        repos/fixture/hermes/commits/vfixture) echo bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb;;
        *) return 1;;
      esac
    }
"""
    result = subprocess.run(["bash", "-e", "-c", stub + script], cwd=tmp_path, env=env, capture_output=True)
    assert result.returncode == 0
    assert (tmp_path / "output").read_text() == "main=" + "a" * 40 + "\nrelease=" + "b" * 40 + "\n"
    # Resolution errors must not select the checkout action's default branch accidentally.
    result = subprocess.run(["bash", "-e", "-c", "gh() { return 1; }\n" + script], cwd=tmp_path, env=env, capture_output=True)
    assert result.returncode != 0


def test_latest_reports_once_with_resolved_shas_and_unchanged_check():
    jobs = workflow("hermes-latest.yml")["jobs"]
    report = jobs["report"]
    assert report["needs"] == ["resolve", "test"] and "always()" in report["if"]
    assert "needs.resolve.result == 'failure'" in report["if"] and "needs.test.result == 'failure'" in report["if"]
    script = report["steps"][0]["run"]
    assert "--label hermes-latest" in script and script.count("gh issue create") == 1
    for word in ("$MAIN_SHA", "$RELEASE_SHA"):
        assert word[1:] in script
    assert "$RUN_URL" in script and "text == latest" in script
    assert "gh issue comment" in script
    subprocess.run(["bash", "-n"], input=script.encode(), check=True)
