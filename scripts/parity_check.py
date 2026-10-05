#!/usr/bin/env python3
"""Offline-by-default API field coverage; never imported by the plugin."""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.request import urlopen

import yaml

ROOT = Path(__file__).resolve().parents[1]
METHODS = {"get", "post", "patch", "delete", "put", "head", "options"}
STATUSES = {"planned", "implemented", "verified"}


def load_spec(source):
    if str(source).startswith(("https://", "http://")):
        with urlopen(str(source), timeout=15) as response:
            return json.load(response)
    return json.loads(Path(source).read_text())


def deref(schema, spec):
    if "$ref" in schema:
        target = spec
        for part in schema["$ref"].removeprefix("#/").split("/"):
            target = target[part]
        return {**target, **{k: v for k, v in schema.items() if k != "$ref"}}
    return schema


def properties(schema, spec):
    schema = deref(schema, spec)
    result = {name: (value, name in schema.get("required", [])) for name, value in schema.get("properties", {}).items()}
    for union in ("allOf", "anyOf", "oneOf"):
        for branch in schema.get(union, []):
            result.update(properties(branch, spec))
    return result


def shape(schema, required, spec):
    schema = deref(schema, spec)
    types = set([schema["type"]] if isinstance(schema.get("type"), str) else schema.get("type", []))
    enums = list(schema.get("enum", [schema["const"]] if "const" in schema else []))
    for union in ("anyOf", "oneOf", "allOf"):
        for branch in schema.get(union, []):
            snapshot = shape(branch, required, spec)
            typ = snapshot["type"]
            types.update(typ if isinstance(typ, list) else [typ])
            enums += snapshot["enum"] or []
    types.discard(None)
    return {"type": next(iter(types)) if len(types) == 1 else sorted(types) or None,
            "enum": sorted(set(enums), key=str) or None, "required": bool(required)}


def fields(spec):
    result = {}
    for path, item in spec["paths"].items():
        for method, op in item.items():
            if method not in METHODS:
                continue
            for param in item.get("parameters", []) + op.get("parameters", []):
                param = deref(param, spec)
                if param["in"] in {"header", "query"}:
                    result[path, method, param["in"], param["name"]] = shape(param.get("schema", {}), param.get("required"), spec)
            body = deref(op.get("requestBody", {}), spec)
            for mime, content in body.get("content", {}).items():
                location = "msgpack" if "msgpack" in mime else "form" if "form" in mime else "json" if "json" in mime else None
                if location:
                    for name, (value, required) in properties(content.get("schema", {}), spec).items():
                        result[path, method, location, name] = shape(value, required, spec)
            for status, response in op.get("responses", {}).items():
                for content in deref(response, spec).get("content", {}).values():
                    for name, (value, required) in properties(content.get("schema", {}), spec).items():
                        key = path, method, "response", name
                        snapshot = shape(value, required, spec)
                        variants = result.get(key, {}).get("responses", {})
                        variants[status] = snapshot
                        types, enums = set(), set()
                        for variant in variants.values():
                            typ = variant["type"]
                            types.update(typ if isinstance(typ, list) else [typ])
                            enums.update(variant["enum"] or [])
                        types.discard(None)
                        result[key] = {"type": next(iter(types)) if len(types) == 1 else sorted(types) or None,
                                       "enum": sorted(enums, key=str) or None,
                                       "required": all(v["required"] for v in variants.values()), "responses": variants}
    return result


def prune(source):
    raw = Path(source).read_bytes()
    spec = json.loads(raw)
    paths = {p: op for p, op in spec["paths"].items() if not p.startswith("/v1/agent/")}
    schemas = {}
    def visit(value):
        if isinstance(value, dict):
            ref = value.get("$ref", "")
            if ref.startswith("#/components/schemas/"):
                name = ref.rsplit("/", 1)[1]
                if name not in schemas:
                    schemas[name] = spec["components"]["schemas"][name]
                    visit(schemas[name])
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(paths)
    output = {"openapi": spec["openapi"], "info": spec["info"], "paths": paths,
              "components": {"schemas": schemas, "securitySchemes": spec.get("components", {}).get("securitySchemes", {})},
              "x-source": {"url": "https://docs.fish.audio/api-reference/openapi.json",
                           "sha256": hashlib.sha256(raw).hexdigest(), "fetch_date": "2026-10-05"}}
    destination = ROOT / "parity/fish-openapi.pruned.json"
    destination.parent.mkdir(exist_ok=True)
    destination.write_text(json.dumps(output, indent=2) + "\n")
    print(f"Pruned: {len(paths)} paths, {len(schemas)} schemas")


def check(spec, mapping, *, collected=None):
    expected, seen, errors = fields(spec), set(), []
    for entry in mapping.get("entries", []):
        key = tuple(entry.get(name) for name in ("path", "method", "location", "field"))
        if key in seen:
            errors.append(f"Duplicate mapping: {key}")
        seen.add(key)
        if key not in expected:
            errors.append(f"Field not in spec: {key}")
        elif entry.get("spec") != expected[key]:
            errors.append(f"Type/enum/required drift: {key}")
        if entry.get("status") not in STATUSES:
            errors.append(f"Invalid status: {key}")
        tests = entry.get("tests", [])
        if entry.get("status") == "verified" and not tests:
            errors.append(f"Verified without tests: {key}")
        if not entry.get("surface"):
            errors.append(f"Missing surface: {key}")
        if collected is not None:
            for test in tests:
                if test not in collected:
                    errors.append(f"Unknown test id: {test}")
    errors.extend(f"Unmapped field: {key}" for key in sorted(expected.keys() - seen))
    for row in mapping.get("doc_only", []):
        if not str(row.get("url", "")).startswith("https://docs.fish.audio/"):
            errors.append("doc_only entry needs a Fish documentation URL")
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--openapi", default=str(ROOT / "parity/fish-openapi.pruned.json"))
    parser.add_argument("--mapping", type=Path, default=ROOT / "parity.yaml")
    parser.add_argument("--prune", type=Path)
    parser.add_argument("--snapshot", action="store_true")
    parser.add_argument("--no-collect", action="store_true")
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.prune:
            prune(args.prune)
            return 0
        spec = load_spec(args.openapi)
        mapping = yaml.safe_load(args.mapping.read_text())
        if args.snapshot:
            expected = fields(spec)
            for entry in mapping.get("entries", []):
                key = tuple(entry[name] for name in ("path", "method", "location", "field"))
                if key in expected:
                    entry["spec"] = expected[key]
            args.mapping.write_text(yaml.safe_dump(mapping, sort_keys=False))
        collected = None
        if not args.no_collect:
            run = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q"], cwd=ROOT,
                                 env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, capture_output=True, text=True)
            if run.returncode:
                print("Pytest collection failed", file=sys.stderr)
                return 1
            collected = set(line.strip() for line in run.stdout.splitlines() if "::" in line)
        errors = check(spec, mapping, collected=collected)
        if args.report:
            counts = Counter(e.get("status") for e in mapping.get("entries", []))
            print("Parity: " + ", ".join(f"{s}={counts[s]}" for s in ("planned", "implemented", "verified")))
            print(f"Fields={len(mapping.get('entries', []))}, doc_only={len(mapping.get('doc_only', []))}")
        for error in errors:
            print(error, file=sys.stderr)
        if not errors:
            print("Parity check passed")
        return int(bool(errors))
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as exc:
        print(f"Parity check failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
