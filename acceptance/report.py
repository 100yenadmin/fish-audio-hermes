"""Completeness and cost gate. rows.yaml is JSON-compatible YAML; stdlib only."""
import argparse
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re

CATALOG = Path(__file__).with_name('rows.yaml')
ALLOWED_SKIP = {'billed:heavy not enabled', 'no LLM key'}


def version(value):
    return tuple(int(n) for n in re.findall(r'\d+', value)[:3])


def known_bug(spec, host, reason=None):
    """A failure tied to a filed host bug, pinned to the exact host builds it was seen on. Any other build re-arms
    the row, so a host release is always re-checked."""
    for bug in spec.get('known_host_bugs', []):
        if (bug['host'] == host['host'] and host.get(bug.get('field', 'hermes_ref')) in bug['refs']
                and (reason is None or re.search(bug['match'], reason))):
            return bug
    return None


def merge(out, layers=('a', 'b'), budget=.30, catalog=CATALOG):
    host = json.loads((out / 'host.json').read_text())
    inventory = json.loads(Path(catalog).read_text())
    catalog_errors = {}
    for spec in inventory:
        for bug in spec.get('known_host_bugs', []):
            try:
                if not isinstance(bug.get('match'), str) or not bug['match']:
                    raise ValueError('missing match regex')
                re.compile(bug['match'])
            except (ValueError, re.error) as exc:
                catalog_errors[spec['id']] = f"catalog error: {spec['id']} {bug['issue']}: {exc}"
    results, duplicates = {}, set()
    for layer in dict.fromkeys([*layers, 'a', 'b']):
        path = out / f'rows-{layer}.json'
        for row in json.loads(path.read_text()) if path.exists() else []:
            key = (row.get('id'), row.get('theme'))
            if key in results:
                duplicates.add(key)
            results[key] = row
    rows, ignored, consumed = [], [], set()
    for spec in inventory:
        if spec['layer'] not in layers or host['host'] not in spec['hosts']:
            continue
        for theme in (['light', 'dark'] if spec['layer'] == 'b' else [None]):
            key = (spec['id'], theme)
            consumed.add(key)
            row = dict(results.get(key, {'id': spec['id'], 'status': 'fail',
                                        'reason': 'layer b missing' if spec['layer'] == 'b' else 'required result missing'}))
            row.setdefault('reason', '')
            row.setdefault('evidence', [])
            row.setdefault('metrics', {})
            producer_reason = row['reason']
            producer_failed = key in results and row.get('status') == 'fail'
            invalid = key not in results
            if theme:
                row['theme'] = theme
            if row.get('flaky'):
                row['reason'] = f"{row['reason']} (retried once after a Fish outage or rate limit)"
            if key in duplicates:
                row.update(status='fail', reason='duplicate row result')
                invalid = True
            if row.get('status') not in {'pass', 'fail', 'skip', 'na'}:
                row.update(status='fail', reason='invalid status')
                invalid = True
            if spec['required'] and row['status'] == 'skip' and producer_reason not in ALLOWED_SKIP:
                row.update(status='fail', reason='required row skipped without allowed reason')
                invalid = True
            if row['status'] in {'na', 'skip'} and not producer_reason:
                row.update(status='fail', reason='missing applicability reason')
                invalid = True
            if spec['id'] in catalog_errors:
                row.update(status='fail', reason=catalog_errors[spec['id']])
                invalid = True
            if not invalid and version(host.get('plugin_version', '0')) < version(spec['min_plugin']):
                row.update(status='na', reason=f"requires plugin {spec['min_plugin']}")
            bug = None if invalid else known_bug(spec, host, producer_reason if producer_failed else None)
            if bug and producer_failed and row['status'] == 'fail':
                row.update(status='xfail', reason=f"known host bug {bug['issue']}: {row['reason']}")
            elif bug and row['status'] == 'pass' and bug.get('intermittent'):
                row['reason'] += f" (intermittent known host bug {bug['issue']} did not show in this run)"
            elif bug and row['status'] == 'pass':
                row['reason'] += f" (known host bug {bug['issue']} no longer reproduces on this version: drop the entry)"
            if row['status'] == 'fail' and not spec['required'] and not invalid:
                row.update(status='advisory-fail')
            rows.append(row)
    by_id = {s['id']: s for s in inventory}
    for key, result in results.items():
        if key in consumed:
            continue
        row = dict(result)
        spec = by_id.get(row.get('id'))
        inapplicable = spec and (spec['layer'] not in layers or host['host'] not in spec['hosts'])
        if inapplicable and row.get('status') in {'na', 'skip'}:
            ignored.append(row)
            continue
        problem = 'inapplicable row' if inapplicable else 'unexpected row theme' if spec else 'unknown row id'
        row.update(status='fail', reason=f"{problem}: {row.get('reason', '')}")
        rows.append(row)
    cost_path = out / 'cost.json'
    cost = json.loads(cost_path.read_text()) if cost_path.exists() else {'usd': None}
    usd_a, usd_b, usd = cost.get('usd') if 'a' in layers else 0, None if 'b' in layers else 0, None
    try:
        b = Decimal(0)
        if 'b' in layers:
            wallet = json.loads((out / 'rows-b.meta.json').read_text())['wallet']
            before, after = (Decimal(wallet[k]) for k in ('before', 'after'))
            if not before.is_finite() or not after.is_finite() or before < 0 or after < 0:
                raise ValueError('negative or non-finite wallet')
            b = before - after
            usd_b = float(b)
        a = Decimal(str(usd_a))
        if a.is_finite() and b.is_finite() and a >= 0 and b >= 0:
            usd = float(a + b)
        within = usd is not None and a + b <= Decimal(str(budget))
    except (OSError, ValueError, TypeError, KeyError, InvalidOperation):
        within = False
    cost.update(usd_a=usd_a, usd_b=usd_b, usd=usd)
    passed = within and not catalog_errors and not any(r['status'] == 'fail' for r in rows)
    report = {'gate': 'pass' if passed else 'fail', 'host': host, 'layers': list(layers),
              'cost': cost, 'budget_usd': budget, 'rows': rows, 'ignored': ignored,
              'catalog_errors': list(catalog_errors.values()),
              'proof_boundary': 'Throwaway profile and named host builds only; no signed apps, customer profiles, broker/relay or Intel proof.'}
    (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    lines = [f"# {host['host']} acceptance: {report['gate'].upper()}", '',
             f"Plugin: {host['plugin_ref']}; Hermes: {host['hermes_ref']}",
             f"Cost USD: A {usd_a}; B {usd_b}; total {usd}; budget: {budget}", '', '| ID | Theme | Status | Reason |', '|---|---|---|---|']
    lines += [f"| {r['id']} | {r.get('theme', '')} | {r['status']} | {r['reason'].replace('|', '/').replace(chr(10), ' ')} |" for r in rows]
    known = sorted({r['reason'].split(':', 1)[0].removeprefix('known host bug ') for r in rows if r['status'] == 'xfail'})
    if known:
        lines += ['', 'Known host bugs (expected failures on these exact host builds): ' + ', '.join(known)]
    lines += ['', *catalog_errors.values()]
    lines += ['', report['proof_boundary'], '']
    (out / 'REPORT.md').write_text('\n'.join(lines))
    return 0 if passed else 1


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--layers', default='a,b')
    p.add_argument('--budget-usd', type=float, default=.30)
    args = p.parse_args()
    raise SystemExit(merge(args.out, args.layers.split(','), args.budget_usd))
