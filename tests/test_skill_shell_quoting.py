"""Model-written gh arguments never sit inside double quotes, where the shell runs `...` and $(...).

The title comes from a file via "$(cat <file>)": the shell does not re-expand substitution output."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOUBLE_QUOTED_ARG = re.compile(r"--(title|search|body) \"(?!\$\(cat <)")


def test_shipped_instructions_single_quote_model_written_gh_args():
    files = sorted(ROOT.glob("skills/*/SKILL.md")) + [ROOT / "AGENTS.md"]
    offenders = [f"{p.relative_to(ROOT)}:{n}" for p in files
                 for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if DOUBLE_QUOTED_ARG.search(line)]
    assert not offenders, offenders
    report = (ROOT / "skills/fish-audio-report-problem/SKILL.md").read_text(encoding="utf-8")
    assert '--title "$(cat <title file>)"' in report and "Never type the title itself into the command." in report
    assert "letters and digits only" in report
