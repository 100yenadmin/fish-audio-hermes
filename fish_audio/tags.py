"""Adapt the vendored S1 fixed cues without rewriting prose or code."""
import logging
import re

logger = logging.getLogger(__name__)
S1_TAGS = frozenset({
    "happy", "sad", "angry", "excited", "calm", "nervous", "confident", "surprised",
    "satisfied", "delighted", "scared", "worried", "upset", "frustrated", "depressed",
    "empathetic", "embarrassed", "disgusted", "moved", "proud", "relaxed", "grateful",
    "curious", "sarcastic", "disdainful", "unhappy", "anxious", "hysterical", "indifferent",
    "uncertain", "doubtful", "confused", "disappointed", "regretful", "guilty", "ashamed",
    "jealous", "envious", "hopeful", "optimistic", "pessimistic", "nostalgic", "lonely",
    "bored", "contemptuous", "sympathetic", "compassionate", "determined", "resigned",
    "in a hurry tone", "shouting", "screaming", "whispering", "soft tone", "laughing",
    "chuckling", "sobbing", "crying loudly", "sighing", "groaning", "panting", "gasping",
    "yawning", "snoring", "audience laughing", "background laughter", "crowd laughing",
    "break", "long-break",
})
# Backtick-delimited regions and Fish markup are opaque. The final alternative
# also protects an unclosed backtick span through the end of the text.
_PROTECTED = re.compile(r"(`+)[\s\S]*?\1|<\|[\s\S]*?\|>|`+[\s\S]*$")


def adapt_tags(text, family):
    if family not in {"S1", "S2"}:
        return text
    removed = False
    if family == "S1":
        pattern = re.compile(r"\[([^\[\]]{1,64})\]")
        def replace(match):
            nonlocal removed
            cue = match.group(1)
            if cue.lower() in S1_TAGS:
                return f"({cue})"
            removed = True
            return ""
    else:
        pattern = re.compile(r"\(([^()\n]{1,64})\)")
        def replace(match):
            cue = match.group(1)
            # Look at original text, so splitting around markup/code never creates
            # a false sentence boundary.
            before = text[:offset + match.start()].rstrip(" \t\r")
            if cue.lower() in S1_TAGS and (not before or before[-1] in ".!?。！？\n"):
                return f"[{cue}]"
            return match.group(0)
    result = []
    offset = 0
    for protected in _PROTECTED.finditer(text):
        result.append(pattern.sub(replace, text[offset:protected.start()]))
        result.append(protected.group(0))
        offset = protected.end()
    result.append(pattern.sub(replace, text[offset:]))
    if removed:
        logger.debug("Removed unsupported S2 cues for the S1 model.")
    return "".join(result)
