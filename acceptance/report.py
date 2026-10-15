"""Completeness and cost gate. rows.yaml is JSON-compatible YAML; stdlib only."""
import argparse
import json
from pathlib import Path

CATALOG = Path(__file__).with_name('rows.yaml')
ALLOWED_SKIP = {'billed:heavy not enabled', 'no LLM key'}


def version(value):
    import re
    return tuple(int(n) for n in re.findall(r'\d+', value)[:3])


def merge(out, layers=('a', 'b'), budget=.30, catalog=CATALOG):
    host = json.loads((out / 'host.json').read_text())
    inventory = json.loads(Path(catalog).read_text())
    results, duplicates = {}, set()
    for layer in layers:
        path = out / f'rows-{layer}.json'
        for row in json.loads(path.read_text()) if path.exists() else []:
            key = (row.get('id'), row.get('theme'))
            if key in results:
                duplicates.add(key)
            results[key] = row
    rows = []
    for spec in inventory:
        if spec['layer'] not in layers or host['host'] not in spec['hosts']:
            continue
        for theme in (['light', 'dark'] if spec['layer'] == 'b' else [None]):
            key = (spec['id'], theme)
            row = dict(results.get(key, {'id': spec['id'], 'status': 'fail',
                                        'reason': 'layer b missing' if spec['layer'] == 'b' else 'required result missing'}))
            row.setdefault('reason', '')
            row.setdefault('evidence', [])
            row.setdefault('metrics', {})
            if theme:
                row['theme'] = theme
            if row.get('flaky'):
                row['reason'] = f"{row['reason']} (retried once after a Fish outage or rate limit)"
            if version(host.get('plugin_version', '0')) < version(spec['min_plugin']):
                row.update(status='na', reason=f"requires plugin {spec['min_plugin']}")
            if key in duplicates:
                row.update(status='fail', reason='duplicate row result')
            if row['status'] not in {'pass', 'fail', 'skip', 'na'}:
                row.update(status='fail', reason='invalid status')
            if spec['required'] and row['status'] == 'skip' and row['reason'] not in ALLOWED_SKIP:
                row.update(status='fail', reason='required row skipped without allowed reason')
            if row['status'] in {'na', 'skip'} and not row['reason']:
                row.update(status='fail', reason='missing applicability reason')
            rows.append(row)
    cost_path = out / 'cost.json'
    cost = json.loads(cost_path.read_text()) if cost_path.exists() else {'usd': None}
    usd = cost.get('usd')
    import math
    within = isinstance(usd, (float, int)) and math.isfinite(usd) and 0 <= usd <= budget
    passed = within and not any(r['status'] == 'fail' for r in rows)
    report = {'gate': 'pass' if passed else 'fail', 'host': host, 'layers': list(layers),
              'cost': cost, 'budget_usd': budget, 'rows': rows,
              'proof_boundary': 'Throwaway profile and named host builds only; no signed apps, customer profiles, broker/relay or Intel proof.'}
    (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    lines = [f"# {host['host']} acceptance: {report['gate'].upper()}", '',
             f"Plugin: {host['plugin_ref']}; Hermes: {host['hermes_ref']}",
             f"Cost USD: {usd}; budget: {budget}", '', '| ID | Theme | Status | Reason |', '|---|---|---|---|']
    lines += [f"| {r['id']} | {r.get('theme', '')} | {r['status']} | {r['reason'].replace('|', '/').replace(chr(10), ' ')} |" for r in rows]
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
