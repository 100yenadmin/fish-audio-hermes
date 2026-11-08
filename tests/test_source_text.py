import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
BIDI = re.compile("[؜‎‏‪-‮⁦-⁩]")


def test_desktop_sources_and_bundle_write_bidi_controls_as_escapes():
    # #69: validators flag invisible direction marks, so the source spells them as escapes. The runtime strings keep
    # them (the Desktop i18n test checks the Arabic isolates).
    files = sorted((ROOT / "devtools/src/desktop").rglob("*.ts*")) + [ROOT / "desktop/plugin.js"]
    assert len(files) > 10
    assert [str(p.relative_to(ROOT)) for p in files if BIDI.search(p.read_text(encoding="utf-8"))] == []
