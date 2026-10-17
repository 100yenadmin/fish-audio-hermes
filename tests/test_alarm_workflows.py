"""CI-only alarms: YAML admission, target resolution and soft history failures."""
import json
import os
import sys
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
    assert latest["permissions"] == {"contents": "read"}
    assert latest["jobs"]["report"]["permissions"] == {"issues": "write"}
    assert all("permissions" not in latest["jobs"][job] for job in ("resolve", "test"))
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
    assert "--workflow fish-bench.yml --status completed --limit 12" in history["run"]
    assert "--history bench-history" in next(s for s in steps if s.get("id") == "bench")["run"]
    assert next(s for s in steps if "upload-artifact@" in s.get("uses", ""))["with"]["retention-days"] == 90
    # Failed download attempts and failed listing both leave the shell step successful.
    for list_fails in (False, True):
        stub = 'gh() { if [ "$2" = list ]; then ' + ('return 1;' if list_fails else "printf '11\\n12\\n';") + ' else return 1; fi; }\n'
        result = subprocess.run(["bash", "-e", "-c", stub + history["run"]], cwd=tmp_path, capture_output=True)
        assert result.returncode == 0


def test_history_keeps_the_latest_four_runs_that_have_results(tmp_path):
    steps = workflow("fish-bench.yml")["jobs"]["bench"]["steps"]
    script = next(s for s in steps if s.get("name") == "Fetch up to four previous benchmark artifacts")["run"]
    # Runs 2 and 4 have no results artifact (skipped or errored); the step keeps going until four downloads succeed.
    stub = """gh() {
      if [ "$2" = list ]; then printf '1\\n2\\n3\\n4\\n5\\n6\\n7\\n8\\n'; return 0; fi
      echo "$3" >> tried
      case "$3" in 2|4) return 1;; esac
      echo "$3" >> got
    }
"""
    subprocess.run(["bash", "-e", "-c", stub + script], cwd=tmp_path, check=True, capture_output=True)
    assert (tmp_path / "got").read_text().split() == ["1", "3", "5", "6"]
    assert (tmp_path / "tried").read_text().split() == ["1", "2", "3", "4", "5", "6"]


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


def test_latest_reports_once_with_resolved_shas_and_unchanged_check(tmp_path):
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
    # A new run link alone is not a new report; a changed SHA is.
    env = os.environ | {"MAIN_SHA": "a" * 40, "RELEASE_SHA": "b" * 40, "RUN_URL": "https://example.test/runs/2"}
    stub = f'python() {{ "{sys.executable}" "$@"; }}\n' + """gh() {
      case "$1 $2" in
        "label create") return 0;;
        "issue list") echo 7;;
        "issue view") printf '%s' "$ISSUE_JSON" > issue.json;;
        "issue comment") echo commented >> calls;;
        "issue create") echo created >> calls;;
      esac
    }
"""
    def report_for(main):
        return f"Hermes latest compatibility failed.\n- default-branch SHA: {main}\n- latest-release SHA: {'b' * 40}\n- run: https://example.test/runs/1\n"

    def bot(main):
        return {"author": {"login": "github-actions"}, "body": report_for(main)}

    # Only the job's own reports count: a later maintainer or deleted-account comment is not the latest report.
    people = [{"author": {"login": "maintainer"}, "body": "Looking into it."}, {"author": None, "body": "+1"}]
    cases = (
        (report_for("a" * 40), [], False),
        (report_for("c" * 40), [], True),
        (report_for("c" * 40), [bot("a" * 40), *people], False),
        (report_for("a" * 40), [bot("c" * 40), *people], True),
        (report_for("a" * 40), people, False),
    )
    for i, (body, comments, commented) in enumerate(cases):
        work = tmp_path / str(i)
        work.mkdir()
        run_env = env | {"ISSUE_JSON": json.dumps({"body": body, "comments": comments})}
        subprocess.run(["bash", "-e", "-c", stub + script], cwd=work, env=run_env, check=True, capture_output=True)
        assert ((work / "calls").read_text().split() if (work / "calls").exists() else []) == (["commented"] if commented else []), i
    subprocess.run(["bash", "-n"], input=script.encode(), check=True)
