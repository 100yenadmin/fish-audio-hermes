"""Drift canaries: no credentials; keyless public documents and endpoints only."""
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import yaml
try:
    from . import parity_check as parity
except ImportError:
    import parity_check as parity

MODEL_IDS = r"`((?:s\d[\w.\-]*|drama-[\w.\-]+|transcribe-[\w.\-]+|voice-design-\d))`"
COMPAT_URL = "https://api.fish.audio/compat/openai/v1/models"
PRICE_PATH = "developer-guide/models-pricing/pricing-and-rate-limits.md"
PLUGIN_URL = "https://raw.githubusercontent.com/fishaudio/fish-audio-plugin/main/plugins/fish-audio/"


def extra_canaries():
    models = json.loads(request(COMPAT_URL))["data"]
    tables = fetch(PRICE_PATH).decode().split("### Voice Design")[0]
    prices = dict(re.findall(r"^\| `([^`]+)`[^|]*\|\s*([^|]+?)\s*\|", tables, re.M))
    return {"compat_model_prices": {m["id"].removeprefix("fish-audio/"): [m["pricing"][k] for k in ("prompt", "completion", "audio") if k in m["pricing"]] for m in models},
            "doc_prices": prices,
            "fish_agent_plugin_version": json.loads(request(PLUGIN_URL + ".claude-plugin/plugin.json"))["version"],
            "fish_mcp_config_sha256": hashlib.sha256(request(PLUGIN_URL + ".mcp.json")).hexdigest()}


def canaries(skill, prose):
    return {"fish_audio_api_skill_sha256": hashlib.sha256(skill).hexdigest(),
            "known_model_ids": sorted(set(re.findall(MODEL_IDS, prose))),
            "changelog_labels_seen": sorted(set(re.findall(r'<Update label="([^"]+)"', prose)))}


def compare(pinned, fresh, lock, skill, prose, mapping, head, inconclusive=None, extra=None):
    report = []
    if {k: v for k, v in pinned.items() if k != "x-source"} != {k: v for k, v in fresh.items() if k != "x-source"}:
        report.append("In-scope OpenAPI content changed (pruned paths/schemas differ).")
    report.extend(parity.check(fresh, mapping))
    current = canaries(skill, prose)
    if current["fish_audio_api_skill_sha256"] != lock["fish_audio_api_skill_sha256"]:
        report.append("fish-audio-api SKILL.md sha changed.")
    for field in ("known_model_ids", "changelog_labels_seen"):
        added = sorted(set(current[field]) - set(lock[field]))
        removed = sorted(set(lock[field]) - set(current[field]))
        if added:
            report.append(f"New {field}: {added}")
        if removed:
            report.append(f"Removed {field}: {removed}")
    for field, value in (extra or {}).items():
        before = lock.get(field)
        if isinstance(value, dict):
            before = before or {}
            for model in sorted(before.keys() | value.keys()):
                if before.get(model) != value.get(model):
                    kind = "Added" if model not in before else "Removed" if model not in value else "Changed"
                    report.append(f"{kind} {field} {model}: {before.get(model)!r} -> {value.get(model)!r}")
            free = value.get("s2.1-pro-free")
            amounts = free if isinstance(free, list) else re.findall(r"\$([\d.]+)", free or "")
            if free is None or not amounts or any(float(n) != 0 for n in amounts):
                report.append(f"Free model s2.1-pro-free: {field} {'missing' if free is None else 'price is not zero: ' + repr(free)} (the free period ends 30 November 2026; see the plugin's free-model notices).")
        elif before != value:
            report.append(f"Changed {field}: {before!r} -> {value!r}")
    urls = {row["url"].split("#")[0] for row in mapping.get("doc_only", [])}
    urls.update(re.findall(r'https://docs\.fish\.audio/[^\s]+', (parity.ROOT / "parity.yaml").read_text()))
    for url in sorted({url.split("#")[0] for url in urls}):
        try:
            head(url)
        except HTTPError as exc:
            report.append(f"Documentation URL failed: {url} ({exc})")
        except (TimeoutError, URLError) as exc:
            message = f"Inconclusive: {url} ({exc})"
            print(message)
            if inconclusive is not None:
                inconclusive.append(message)
    return report


def request(url, *, head=False):
    for attempt in range(3):
        try:
            with urlopen(Request(url, method="HEAD" if head else "GET"), timeout=15 if head else 30) as response:
                return None if head else response.read()
        except (HTTPError, URLError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(0.5 * (attempt + 1))


def fetch(path):
    return request("https://docs.fish.audio/" + path)


def main():
    root = parity.ROOT
    output = root / "drift/report.md"
    output.parent.mkdir(exist_ok=True)
    inconclusive = []
    try:
        pinned = parity.load_spec(root / "parity/fish-openapi.pruned.json")
        fetch(".well-known/skills/index.json")
        skill, prose = fetch(".well-known/skills/fish-audio-api/SKILL.md"), fetch("llms-full.txt").decode()
        with tempfile.TemporaryDirectory() as directory:
            raw = Path(directory) / "openapi.json"
            raw.write_bytes(fetch("api-reference/openapi.json"))
            fresh = parity.prune(raw, destination=Path(directory) / "pruned.json")
        def head(url):
            request(url, head=True)
        report = compare(pinned, fresh, json.loads((root / "parity/fish-upstream.lock.json").read_text()),
                         skill, prose, yaml.safe_load((root / "parity.yaml").read_text()), head, inconclusive, extra_canaries())
    except (TimeoutError, URLError) as exc:
        if isinstance(exc, HTTPError):
            report = [f"Upstream document failed: {exc}"]
        else:
            report = []
            inconclusive.append(f"Inconclusive: drift check could not complete ({exc})")
            print(inconclusive[-1])
    except Exception as exc:
        report = [f"Drift check could not complete: {exc}"]
    output.write_text("\n".join("- " + item for item in report) + ("\n" if report else ""))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
            summary.write(output.read_text() + "\n".join(inconclusive) + "\n")
    print("Drift detected" if report else "Inconclusive" if inconclusive else "No drift")


if __name__ == "__main__":
    main()
