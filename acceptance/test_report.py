import json
from pathlib import Path
import subprocess
import sys

import pytest
from report import merge

HERE = Path(__file__).parent


@pytest.mark.parametrize('case', json.loads((HERE / 'fixtures/report-cases.json').read_text()), ids=lambda c: c['name'])
def test_gate_fixture(case, tmp_path):
    spec = [{'id': 'A-ONE', 'layer': 'a', 'hosts': ['upstream'], 'min_plugin': '1.0.0', 'required': True}]
    (tmp_path / 'catalog.json').write_text(json.dumps(spec))
    (tmp_path / 'host.json').write_text(json.dumps({'host': 'upstream', 'plugin_ref': 'fixture', 'hermes_ref': 'fixture', 'plugin_version': '1.7.5'}))
    (tmp_path / 'rows-a.json').write_text(json.dumps(case['rows']))
    (tmp_path / 'cost.json').write_text(json.dumps({'usd': case['usd']}))
    assert merge(tmp_path, ['a'], catalog=tmp_path / 'catalog.json') == case['exit']
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
    assert merge(tmp_path, ['b'], catalog=tmp_path / 'catalog.json') == 1


def _known(tmp_path, status, hermes_ref, required=True):
    (tmp_path / 'host.json').write_text(json.dumps({'host': 'upstream', 'plugin_ref': 'fixture', 'hermes_ref': hermes_ref, 'plugin_version': '1.7.5'}))
    (tmp_path / 'catalog.json').write_text(json.dumps([{'id': 'A-ONE', 'layer': 'a', 'hosts': ['upstream'], 'min_plugin': '1.0.0', 'required': required,
        'known_host_bugs': [{'host': 'upstream', 'refs': ['v0.22.2'], 'issue': 'NousResearch/hermes-agent#1'}]}]))
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


def test_advisory_row_failure_does_not_fail_the_gate(tmp_path):
    code, row = _known(tmp_path, 'fail', 'v0.22.3', required=False)
    assert (code, row['status']) == (0, 'advisory-fail')
