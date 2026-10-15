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
