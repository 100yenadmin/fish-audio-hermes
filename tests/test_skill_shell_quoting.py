"""Model-written gh arguments never sit inside double quotes, where the shell runs `...` and $(...).

Title and search words come from a file via "$(cat '<file>')": the shell does not re-expand substitution output."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOUBLE_QUOTED_ARG = re.compile(r"--(title|search|body) \"(?!\$\(cat '<)")


def test_shipped_instructions_single_quote_model_written_gh_args():
    files = sorted(ROOT.glob("skills/*/SKILL.md")) + [ROOT / "AGENTS.md"]
    offenders = [f"{p.relative_to(ROOT)}:{n}" for p in files
                 for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if DOUBLE_QUOTED_ARG.search(line)]
    assert not offenders, offenders
    report = (ROOT / "skills/fish-audio-report-problem/SKILL.md").read_text(encoding="utf-8")
    assert '--title "$(cat \'<title file>\')"' in report and "Never type the title itself into the command." in report
    assert '--search "$(cat \'<search file>\')"' in report and "Never type the search words themselves into the command." in report


def test_report_needs_an_explicit_yes_to_publishing():
    # A yes to "does this hold any secrets?" must not double as approval to publish.
    report = (ROOT / "skills/fish-audio-report-problem/SKILL.md").read_text(encoding="utf-8")
    assert "a yes to the secrets question alone" in report
    assert "Only an explicit yes to publishing, given after the user has seen this text, does." in report
