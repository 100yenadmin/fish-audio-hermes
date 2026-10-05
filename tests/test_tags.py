import re
from pathlib import Path

import pytest

from fish_audio.tags import S1_TAGS, adapt_tags


def test_catalog_matches_all_vendored_s1_tags():
    source = (Path(__file__).resolve().parents[1] / "specs/vendor/fish-llms-full.txt")
    if not source.exists():
        pytest.skip("vendored docs are orchestrator-provided, not part of the published plugin")
    section = source.read_text().split("## S1 (legacy) syntax", 1)[1].split("## See Also", 1)[0]
    tables = section.split("<AccordionGroup>", 1)[1]
    expected = frozenset(re.findall(r"`\(([^)]+)\)`", tables))
    assert S1_TAGS == expected


@pytest.mark.parametrize("tag", sorted(S1_TAGS))
def test_fixed_tag_round_trip(tag):
    text = f"[{tag}] Hello."
    assert adapt_tags(text, "S1") == f"({tag}) Hello."
    assert adapt_tags(adapt_tags(text, "S1"), "S2") == text


def test_parentheticals_boundaries_and_case():
    text = "(Happy) Hello (see below) and (sad). (SAD) Bye!\n(soft tone) Quiet."
    assert adapt_tags(text, "S2") == "[Happy] Hello (see below) and (sad). [SAD] Bye!\n[soft tone] Quiet."


def test_free_form_removed_once_without_logging_text(caplog):
    text = "[an arbitrary cue] Hello [another\ncue]! [HAPPY] Bye."
    with caplog.at_level("DEBUG"):
        assert adapt_tags(text, "S1") == " Hello ! (HAPPY) Bye."
    assert len(caplog.records) == 1 and "arbitrary" not in caplog.text
    long = "[" + "a" * 65 + "]"
    assert adapt_tags(long, "S1") == long


@pytest.mark.parametrize("family", ["S1", "S2"])
def test_markup_and_backticks_are_opaque(family):
    text = "<|speaker:0|> `<|[happy](sad)|>` ```\n[happy] (sad)\n``` <|[happy]|>"
    assert adapt_tags(text, family) == text
    assert adapt_tags("`unclosed [happy] (sad)", family) == "`unclosed [happy] (sad)"
    assert adapt_tags("`code` (happy) mid-sentence", family) == "`code` (happy) mid-sentence"
