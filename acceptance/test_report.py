import json
from pathlib import Path
import re
import subprocess
import sys

import pytest
from report import merge

HERE = Path(__file__).parent
CASES = json.loads((HERE / 'fixtures/report-cases.json').read_text())


@pytest.mark.parametrize('case', CASES['gate_cases'], ids=lambda c: c['name'])
def test_gate_fixture(case, tmp_path):
    spec = [{'id': 'A-ONE', 'layer': 'a', 'hosts': ['upstream'], 'min_plugin': '1.0.0', 'required': True}]
    if case.get('known_bug'):
        bug = {'host':'upstream', 'refs':['v0.22.2'], 'issue':'fixture#1',
               'match':case.get('match', 'field invisible')}
        if case['name'] == 'waived-missing-match':
            del bug['match']
        spec[0]['known_host_bugs'] = [bug]
    if case.get('inapplicable'):
        spec.append({'id':'B-OTHER', 'layer':'a' if case['inapplicable'] == 'host' else 'b',
                     'hosts':['evaos'] if case['inapplicable'] == 'host' else ['upstream'],
                     'min_plugin':'1.0.0', 'required':True})
    if case.get('rows_b'):
        if any(r['id'] == 'B-ONE' for r in case['rows_b']):
            spec.append({'id':'B-ONE', 'layer':'b', 'hosts':['upstream'], 'min_plugin':'1.0.0', 'required':True})
        (tmp_path / 'rows-b.json').write_text(json.dumps(case['rows_b']))
    (tmp_path / 'catalog.json').write_text(json.dumps(spec))
    (tmp_path / 'host.json').write_text(json.dumps({'host': 'upstream', 'plugin_ref': 'fixture',
        'hermes_ref': 'v0.22.3' if case['name'] == 'waived-other-build' else 'v0.22.2', 'plugin_version': '1.7.5'}))
    (tmp_path / 'rows-a.json').write_text(json.dumps(case['rows']))
    (tmp_path / 'cost.json').write_text(json.dumps({'usd': case['usd']}))
    if case.get('wallet') is not None:
        (tmp_path / 'rows-b.meta.json').write_text(json.dumps({'wallet':case['wallet']}))
    code = merge(tmp_path, case.get('layers', ['a']), catalog=tmp_path / 'catalog.json')
    report = json.loads((tmp_path / 'report.json').read_text())
    assert (code, report['gate'], [r['status'] for r in report['rows']]) == (
        case['exit'], 'fail' if case['exit'] else 'pass', case['statuses'])
    if case.get('inapplicable'):
        assert len(report['ignored']) == case['ignored']
    if case.get('unknown_reason'):
        assert report['rows'][-1]['id'] == 'B-ERROR'
        assert case['unknown_reason'] in report['rows'][-1]['reason']
    if case['name'] == 'waived-missing-match':
        assert 'catalog error' in report['rows'][0]['reason'] and 'match' in report['rows'][0]['reason']
    if 'usd_b' in case:
        assert report['cost']['usd_a'] == .10
        assert report['cost']['usd_b'] == case['usd_b']
        expected = None if case['usd_b'] is None else .10 + case['usd_b']
        assert report['cost']['usd'] == (pytest.approx(expected) if expected is not None else None)
        assert f"B {case['usd_b']}" in (tmp_path / 'REPORT.md').read_text()
    assert (tmp_path / 'REPORT.md').is_file()


def test_cli_failed_gate_exits_nonzero(tmp_path):
    (tmp_path / 'host.json').write_text(json.dumps({'host': 'upstream', 'plugin_ref': 'fixture', 'hermes_ref': 'fixture', 'plugin_version': '1.7.5'}))
    (tmp_path / 'rows-a.json').write_text('[]')
    (tmp_path / 'cost.json').write_text('{"usd":0}')
    p = subprocess.run([sys.executable, str(HERE / 'report.py'), '--out', str(tmp_path), '--layers', 'a'], capture_output=True)
    assert p.returncode == 1
    assert json.loads((tmp_path / 'report.json').read_text())['gate'] == 'fail'


def test_b_requires_both_themes(tmp_path):
    (tmp_path / 'host.json').write_text('{"host":"upstream","plugin_ref":"fixture","hermes_ref":"fixture","plugin_version":"1.7.5"}')
    (tmp_path / 'catalog.json').write_text('[{"id":"B-ONE","layer":"b","hosts":["upstream"],"min_plugin":"1.0.0","required":true}]')
    (tmp_path / 'rows-b.json').write_text('[{"id":"B-ONE","theme":"light","status":"pass"}]')
    (tmp_path / 'cost.json').write_text('{"usd":0}')
    (tmp_path / 'rows-b.meta.json').write_text('{"wallet":{"before":"1","after":"1"}}')
    assert merge(tmp_path, ['b'], catalog=tmp_path / 'catalog.json') == 1
    rows = json.loads((tmp_path / 'report.json').read_text())['rows']
    assert [(r['theme'], r['status']) for r in rows] == [('light','pass'), ('dark','fail')]


def _known(tmp_path, status, hermes_ref, required=True, intermittent=False):
    (tmp_path / 'host.json').write_text(json.dumps({'host': 'upstream', 'plugin_ref': 'fixture', 'hermes_ref': hermes_ref, 'plugin_version': '1.7.5'}))
    (tmp_path / 'catalog.json').write_text(json.dumps([{'id': 'A-ONE', 'layer': 'a', 'hosts': ['upstream'], 'min_plugin': '1.0.0', 'required': required,
        'known_host_bugs': [{'host': 'upstream', 'refs': ['v0.22.2'], 'issue': 'NousResearch/hermes-agent#1', 'intermittent': intermittent, 'match': 'field invisible'}]}]))
    (tmp_path / 'rows-a.json').write_text(json.dumps([{'id': 'A-ONE', 'status': status, 'reason': 'field invisible'}]))
    (tmp_path / 'cost.json').write_text('{"usd":0}')
    code = merge(tmp_path, ['a'], catalog=tmp_path / 'catalog.json')
    return code, json.loads((tmp_path / 'report.json').read_text())['rows'][0]


def test_known_host_bug_is_expected_failure_on_that_build_only(tmp_path):
    code, row = _known(tmp_path, 'fail', 'v0.22.2')
    assert (code, row['status']) == (0, 'xfail') and 'hermes-agent#1' in row['reason']
    code, row = _known(tmp_path, 'fail', 'v0.22.3')
    assert (code, row['status']) == (1, 'fail')


def test_known_host_bug_that_passes_asks_to_drop_the_entry(tmp_path):
    code, row = _known(tmp_path, 'pass', 'v0.22.2')
    assert code == 0 and 'drop the entry' in row['reason']


def test_intermittent_known_host_bug_that_passes_keeps_the_entry(tmp_path):
    code, row = _known(tmp_path, 'pass', 'v0.22.2', intermittent=True)
    assert code == 0 and 'did not show in this run' in row['reason'] and 'drop the entry' not in row['reason']
    code, row = _known(tmp_path, 'fail', 'v0.22.2', intermittent=True)
    assert (code, row['status']) == (0, 'xfail')


def test_advisory_row_failure_does_not_fail_the_gate(tmp_path):
    code, row = _known(tmp_path, 'fail', 'v0.22.3', required=False)
    assert (code, row['status']) == (0, 'advisory-fail')


@pytest.mark.parametrize('case', CASES['producer_reasons'],
                         ids=lambda c: f"{c['source']}-{c['id']}-{c['theme']}")
def test_real_producer_reason(case):
    spec = next(s for s in json.loads((HERE / 'rows.yaml').read_text()) if s['id'] == case['id'])
    bug = next(b for b in spec['known_host_bugs'] if b['host'] == case['host'])
    assert bool(re.search(bug['match'], case['reason'])) == case['matches']


@pytest.mark.parametrize('silent,host_race,matches', [(2,2,True), (5,5,True), (2,1,False), (0,0,False)])
def test_five_turn_waiver_requires_every_silent_turn_to_be_the_host_race(silent, host_race, matches):
    spec = next(s for s in json.loads((HERE / 'rows.yaml').read_text()) if s['id'] == 'B-CONV-2')
    reason = f'submits 5, stuck 0, re-spoken 0, silent {silent} ({host_race} with no reply text sent to speak-stream)'
    assert bool(re.search(spec['known_host_bugs'][0]['match'], reason)) == matches


def test_heavy_rows_are_never_retried():
    from conftest import retry_rows
    rows = [{'id':s['id'], 'status':'fail', 'reason':'rate limit 429'}
            for s in json.loads((HERE / 'rows.yaml').read_text()) if s['layer'] == 'a']
    selected = retry_rows(rows + [{'id':'A-ONE', 'status':'pass', 'reason':'rate limit 429'},
                                 {'id':'A-TWO', 'status':'fail', 'reason':'assertion failed'}])
    assert 'A-ROUTE-11' not in selected and 'A-ROUTE-12' not in selected
    assert 'A-CLI-3' in selected and 'A-ONE' not in selected and 'A-TWO' not in selected
