"""Offline checks: fake credentials, mocked processes and native-audio stand-ins only."""
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest
import conftest as harness

HERE = Path(__file__).parent
NODE = os.environ.get('ACCEPTANCE_NODE') or shutil.which('node') or '/opt/homebrew/bin/node'
SECRET_NAMES = ('FISH_API_KEY', 'GLM_API_KEY', 'GH_TOKEN', 'GITHUB_TOKEN',
                'HERMES_DASHBOARD_SESSION_TOKEN', 'OTHER_API_KEY', 'OTHER_TOKEN')


def test_build_environment_drops_secrets_keeps_paths(monkeypatch):
    for name in SECRET_NAMES:
        monkeypatch.setenv(name, 'synthetic-placeholder')
    monkeypatch.setenv('PATH', '/synthetic/bin')
    monkeypatch.setenv('HOME', '/synthetic/home')
    env = harness.build_env()
    assert not set(SECRET_NAMES) & env.keys()
    assert (env['PATH'], env['HOME']) == ('/synthetic/bin', '/synthetic/home')


@pytest.mark.parametrize('reason,expected', [('WER 0.500', False), ('500 ms', False),
    ('PCM 502', False), ('HTTP 503: temporary', True), ('concurrency limit', True)])
def test_transient_status_requires_http_prefix(reason, expected):
    assert bool(harness.TRANSIENT.search(reason)) == expected


def test_all_host_build_hooks_receive_sanitized_environment(monkeypatch, tmp_path):
    monkeypatch.setattr(harness, 'CACHE', tmp_path)
    monkeypatch.setenv('FISH_API_KEY', 'synthetic-placeholder')
    monkeypatch.setenv('OTHER_TOKEN', 'synthetic-placeholder')
    repo = tmp_path / 'repos/upstream'
    repo.mkdir(parents=True)
    dest = tmp_path / 'upstream-fixture-123456789abc'
    dest.mkdir()
    (dest / 'pyproject.toml').touch()
    calls = []
    def call(args, **kw):
        calls.append((list(map(str, args)), kw))
        return '123456789abcdef'
    monkeypatch.setattr(harness, 'call', call)
    harness.build('upstream', 'fixture')
    hooks = [(a, k) for a, k in calls if a[0] != 'git']
    assert len(hooks) == 4
    for args, kw in hooks:
        assert not set(SECRET_NAMES) & kw['env'].keys()
        assert 'PATH' in kw['env'] and 'HOME' in kw['env']
    assert not any(a == ['pwd'] for a, _ in calls)


def test_build_failure_keeps_scrubbed_cause(monkeypatch, tmp_path):
    monkeypatch.setattr(harness, 'CACHE', tmp_path)
    monkeypatch.setenv('FISH_API_KEY', 'synthetic-placeholder')
    (tmp_path / 'repos/upstream').mkdir(parents=True)
    dest = tmp_path / 'upstream-fixture-123456789abc'
    dest.mkdir(); (dest / 'pyproject.toml').touch()
    def call(args, **kw):
        if args[0] == 'git': return '123456789abcdef'
        raise RuntimeError(harness.scrub('lockfile failed synthetic-placeholder'))
    monkeypatch.setattr(harness, 'call', call)
    with pytest.raises(RuntimeError, match='lockfile failed') as exc:
        harness.build('upstream', 'fixture')
    assert exc.value.__cause__ is not None
    assert 'synthetic-placeholder' not in str(exc.value)


@pytest.mark.parametrize('mode', ['term', 'kill', 'changed', 'gone', 'stubborn', 'port-closed', 'port-closed-stubborn'])
def test_gateway_shutdown_group_escalation_and_receipt(monkeypatch, tmp_path, mode):
    host = object.__new__(harness.Host)
    host.out = tmp_path; host.meta = {'gateway_url':'http://127.0.0.1:9361'}
    receipt = tmp_path / 'gateway-pid.json'
    receipt.write_text('{"pid":12345}')
    state = {'open':True, 'alive':True, 'signals':[], 'sleep':0, 'identity':0}
    identity = f'fixed start gateway-worker --run-dir {tmp_path}'
    def ps(*a, **kw):
        state['identity'] += 1
        return SimpleNamespace(stdout='changed' if mode == 'changed' and state['identity'] > 2 else identity)
    class Socket:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def connect_ex(self, address): return 0 if state['open'] else 1
    def killpg(pid, sig):
        assert pid == 12345
        if mode == 'gone' or not state['alive']: raise ProcessLookupError
        if sig == 0:
            return
        state['signals'].append(sig)
        if mode.startswith('port-closed'): state['open'] = False
        if mode == 'term' or mode in ('kill', 'port-closed') and sig == signal.SIGKILL:
            state.update(open=False, alive=False)
    monkeypatch.setattr(harness.subprocess, 'run', ps)
    monkeypatch.setattr(harness.socket, 'socket', Socket)
    monkeypatch.setattr(harness.os, 'killpg', killpg)
    monkeypatch.setattr(harness.os, 'getpgid', lambda pid: pid)
    monkeypatch.setattr(harness.time, 'sleep', lambda sec: state.update(sleep=state['sleep']+sec))
    if mode in ('changed','stubborn','port-closed-stubborn'):
        with pytest.raises(RuntimeError, match='retained'): host.down()
        assert receipt.exists()
    else:
        host.down()
        assert not receipt.exists()
    assert state['signals'] == ([] if mode == 'gone' else [signal.SIGTERM] if mode in ('term','changed') else [signal.SIGTERM, signal.SIGKILL])
    if mode not in ('term', 'gone'): assert state['sleep'] >= 9.99
    if mode in ('stubborn', 'port-closed-stubborn'): assert state['sleep'] >= 14.99


@pytest.mark.parametrize('caption_ms,expected', [(1200, False), (500, True), (1000, False), (None, False)])
def test_live_stt_caption_must_precede_eos(caption_ms, expected):
    import ast
    module = ast.parse((HERE / 'test_layer_a.py').read_text())
    helper = next(n for n in module.body if isinstance(n, ast.FunctionDef) and n.name == 'live_caption_metrics')
    namespace = {}
    exec(compile(ast.Module(body=[helper], type_ignores=[]), '<live_caption_metrics>', 'exec'), namespace)
    frames = [{'type':'final', 'transcript':'synthetic', 'received_ms':1500}]
    if caption_ms is not None:
        frames.insert(0, {'type':'partial', 'text':'synthetic', 'received_ms':caption_ms})
    metrics, passed = namespace['live_caption_metrics'](frames, 1000)
    assert passed is expected
    assert metrics == {'first_caption_ms':caption_ms, 'eos_ms':1000}


@pytest.mark.parametrize('case', ['no-key','files','missing-fish','missing-glm','keyless-fish','keyless-glm','twice-glm'])
def test_key_wrapper_only_reads_explicit_fake_files(tmp_path, case):
    # Do not inherit or inspect any real key values or machine-local key file paths.
    env = {'PATH':os.defpath, 'HOME':str(tmp_path)}
    for variable, name, key in [('FISH_ACCEPTANCE_KEY_FILE','fish','FISH_API_KEY'),
                               ('FISH_ACCEPTANCE_GLM_ENV','glm','GLM_API_KEY')]:
        file = tmp_path / name
        line = f'{key}="synthetic-{name}"\n'
        if case == f'keyless-{name}': line = ''
        if case == f'twice-{name}': line *= 2
        file.write_text(line + 'UNRELATED=ignored\n')
        if case != 'no-key': env[variable] = str(file if case != f'missing-{name}' else tmp_path / 'absent')
    command = [sys.executable, '-c', 'import os; assert os.getenv("FISH_API_KEY")=="synthetic-fish"; assert os.getenv("GLM_API_KEY")=="synthetic-glm"; assert "UNRELATED" not in os.environ']
    p = subprocess.run(['/bin/bash', str(HERE / 'with-keys.sh'), *command], env=env, capture_output=True, text=True)
    assert p.returncode == (0 if case == 'files' else 2), p.stderr
    if case.startswith('missing'): assert 'named key file missing' in p.stderr
    if case.startswith(('keyless','twice')): assert 'must set' in p.stderr and 'synthetic' not in p.stderr


def test_seed_must_have_node_modules(tmp_path):
    p = subprocess.run(['/bin/bash', str(HERE / 'desktop/build.sh'), '--host','upstream',
                        '--repo',str(tmp_path), '--ref','fixture', '--seed',str(tmp_path)],
                       env={'PATH':os.defpath, 'HOME':str(tmp_path)}, capture_output=True, text=True)
    assert p.returncode == 2 and 'seed must contain node_modules' in p.stderr
    assert not (tmp_path / '.cache').exists()


def test_desktop_offline_predicates():
    p = subprocess.run([NODE, '--test', str(HERE / 'test_desktop.mjs')], env={**harness.build_env(), 'TEST_PYTHON':sys.executable}, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stdout + p.stderr


def test_agent_tool_receipt_must_be_newer_than_chat(monkeypatch, tmp_path):
    import ast
    import re
    import sqlite3
    module = ast.parse((HERE / 'test_layer_a.py').read_text())
    function = next(n for n in module.body if isinstance(n, ast.FunctionDef) and n.name == 'test_agent')
    function.decorator_list = []
    namespace = dict(os=os, sqlite3=sqlite3, json=json, Path=Path, pytest=pytest, re=re,
                     TEXT='synthetic', CORPUS='synthetic.wav', scrub=str, evidence=lambda *a: None)
    exec(compile(ast.Module(body=[function], type_ignores=[]), '<test_agent>', 'exec'), namespace)
    dbfile = tmp_path / 'state.db'
    with sqlite3.connect(dbfile) as db:
        db.execute('CREATE TABLE messages (id INTEGER PRIMARY KEY, tool_name TEXT, content TEXT)')
        db.execute('INSERT INTO messages VALUES (1, ?, ?)', ('fish_voices', '{"success":true,"items":["synthetic"]}'))
    monkeypatch.setenv('GLM_API_KEY', 'synthetic-placeholder')
    paid = SimpleNamespace(home=tmp_path, cli=lambda *a, **kw: 'synthetic reply')
    with pytest.raises(AssertionError, match='no fish_voices tool result'):
        namespace['test_agent'](paid, 2, 'fish_voices', None)
    def fresh_chat(*a, **kw):
        with sqlite3.connect(dbfile) as db:
            db.execute('INSERT INTO messages VALUES (2, ?, ?)', ('fish_voices','{"success":true,"items":["synthetic"]}'))
        return 'synthetic reply'
    paid.cli = fresh_chat
    namespace['test_agent'](paid, 2, 'fish_voices', None)
