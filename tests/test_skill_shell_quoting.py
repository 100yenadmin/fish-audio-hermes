"""Model-written gh arguments must be single-quoted: inside double quotes the shell runs `...` and $(...)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOUBLE_QUOTED_ARG = re.compile(r"--(title|search|body) \"")


def test_shipped_instructions_single_quote_model_written_gh_args():
    files = sorted(ROOT.glob("skills/*/SKILL.md")) + [ROOT / "AGENTS.md"]
    offenders = [f"{p.relative_to(ROOT)}:{n}" for p in files
                 for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if DOUBLE_QUOTED_ARG.search(line)]
    assert not offenders, offenders
    report = (ROOT / "skills/fish-audio-report-problem/SKILL.md").read_text(encoding="utf-8")
    assert "--title '<short title>'" in report and "no quotes, backticks or `$`" in report
