"""Offline checks: fake credentials, mocked processes and native-audio stand-ins only."""
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import time
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


@pytest.mark.parametrize('mode', ['term', 'kill', 'changed', 'gone', 'stubborn', 'port-closed', 'port-closed-stubborn', 'leader-exits',
                                  'zombie-elsewhere', 'members-unknown'])
def test_gateway_shutdown_group_escalation_and_receipt(monkeypatch, tmp_path, mode):
    host = object.__new__(harness.Host)
    host.out = tmp_path; host.meta = {'gateway_url':'http://127.0.0.1:9361'}
    receipt = tmp_path / 'gateway-pid.json'
    receipt.write_text('{"pid":12345}')
    state = {'open':True, 'alive':True, 'signals':[], 'sleep':0, 'identity':0}
    identity = f'fixed start gateway-worker --run-dir {tmp_path}'
    def ps(args, **kw):
        if '-A' in args:  # group membership
            if mode == 'members-unknown':
                return SimpleNamespace(stdout='', returncode=1)
            # Linux lists a dead worker its parent has not reaped (the outer runner, while pytest restarts) as a zombie.
            zombie = mode == 'zombie-elsewhere' and signal.SIGTERM in state['signals']
            return SimpleNamespace(stdout=f'    1 Ss\n12345 {"Z" if zombie else "S"}\n', returncode=0)
        assert '-ww' in args  # GNU ps would truncate the command line to the terminal width
        state['identity'] += 1
        if mode == 'leader-exits' and signal.SIGTERM in state['signals']:
            return SimpleNamespace(stdout='')  # the worker exited on TERM; a descendant still holds the group
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
        if mode == 'term' or mode in ('kill', 'port-closed', 'leader-exits') and sig == signal.SIGKILL:
            state.update(open=False, alive=False)
    monkeypatch.setattr(harness.subprocess, 'run', ps)
    monkeypatch.setattr(harness.socket, 'socket', Socket)
    monkeypatch.setattr(harness.os, 'killpg', killpg)
    monkeypatch.setattr(harness.os, 'getpgid', lambda pid: pid)
    monkeypatch.setattr(harness.time, 'sleep', lambda sec: state.update(sleep=state['sleep']+sec))
    if mode in ('changed','stubborn','port-closed-stubborn','members-unknown'):
        with pytest.raises(RuntimeError, match='retained'): host.down()
        assert receipt.exists()
    else:
        host.down()
        assert not receipt.exists()
    assert state['signals'] == ([] if mode == 'gone' else [signal.SIGTERM] if mode in ('term','changed','zombie-elsewhere')
                                else [signal.SIGTERM, signal.SIGKILL])
    if mode not in ('term', 'gone', 'zombie-elsewhere'): assert state['sleep'] >= 9.99
    if mode in ('stubborn', 'port-closed-stubborn', 'members-unknown'): assert state['sleep'] >= 14.99


def test_gateway_shutdown_reaps_a_worker_this_process_started(tmp_path):
    # up() keeps no Popen, so after TERM the worker is a zombie of this process until reaped; macOS answers
    # killpg(pid, 0) for a group of zombies with EPERM. A real child, not a fake.
    host = object.__new__(harness.Host)
    host.out = tmp_path; host.meta = {'gateway_url': 'http://127.0.0.1:9'}
    pid = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)', 'gateway-worker', '--run-dir', str(tmp_path)],
                           start_new_session=True).pid
    (tmp_path / 'gateway-pid.json').write_text(json.dumps({'pid': pid}))
    try:
        time.sleep(.5)  # until ps shows the worker's own command line
        started = time.monotonic()
        host.down()
        assert not (tmp_path / 'gateway-pid.json').exists()
        assert time.monotonic() - started < 5
        with pytest.raises((ProcessLookupError, PermissionError)):  # EPERM: only a zombie, if it exited after the last reap
            os.killpg(pid, 0)
    finally:
        try:
            os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def test_gateway_shutdown_kills_a_descendant_that_outlives_the_worker(monkeypatch, tmp_path):
    # The worker exits on TERM while a descendant in its process group ignores TERM: down() must SIGKILL the group.
    host = object.__new__(harness.Host)
    host.out = tmp_path; host.meta = {'gateway_url': 'http://127.0.0.1:9'}
    child = 'import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)'
    code = f'import subprocess, sys, time; subprocess.Popen([sys.executable, "-c", {child!r}]); time.sleep(60)'
    pid = subprocess.Popen([sys.executable, '-c', code, 'gateway-worker', '--run-dir', str(tmp_path)], start_new_session=True).pid
    (tmp_path / 'gateway-pid.json').write_text(json.dumps({'pid': pid}))
    real_sleep = time.sleep
    try:
        real_sleep(1)  # until the descendant has started and ps shows the worker's command line
        monkeypatch.setattr(harness.time, 'sleep', lambda sec: real_sleep(sec / 10))
        host.down()
        assert not (tmp_path / 'gateway-pid.json').exists()
        with pytest.raises((ProcessLookupError, PermissionError)):  # EPERM: only a zombie awaiting its new parent's reap
            os.killpg(pid, 0)
    finally:
        monkeypatch.undo()
        try:
            os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


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


def test_fault_proxy_retries_and_streams_only_metadata():
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import httpx
    from test_layer_a import fault_proxy
    seen, release, finished = [], threading.Event(), threading.Event()
    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            seen.append((self.command, self.path, self.headers.get('Authorization'),
                         self.rfile.read(int(self.headers.get('Content-Length', 0)))))
            self.send_response(200)
            self.send_header('Content-Type', 'audio/pcm')
            self.send_header('x-request-id', 'synthetic-request')
            self.end_headers()
            self.wfile.write(b'first'); self.wfile.flush()
            release.wait(3)
            for chunk in (b'second', b'third'):
                time.sleep(.02)
                self.wfile.write(chunk); self.wfile.flush()
            finished.set()
        do_GET = do_POST
    upstream = ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    proxy = fault_proxy(f'http://127.0.0.1:{upstream.server_port}')
    try:
        with httpx.Client(base_url=proxy.base_url, trust_env=False) as http:
            refused = http.post('/v1/tts', content=b'synthetic')
            assert refused.status_code == 503 and refused.json() == {'message': 'acceptance injected 503'}
            assert refused.headers['Content-Type'] == 'application/json' and seen == []
            with http.stream('POST', '/v1/tts', content=b'synthetic', headers={'Authorization': 'Bearer synthetic'}) as response:
                chunks = response.iter_bytes()
                first = next(chunks)
                assert first == b'first' and not finished.is_set()  # relay before upstream finishes
                release.set()
                assert first + b''.join(chunks) == b'firstsecondthird' and response.status_code == 200
                assert response.headers['x-request-id'] == 'synthetic-request' and response.headers['Content-Type'] == 'audio/pcm'
            assert http.get('/wallet?test=1').content == b'firstsecondthird'
        assert seen == [('POST', '/v1/tts', 'Bearer synthetic', b'synthetic'), ('GET', '/wallet?test=1', None, b'')]
        assert proxy.requests == [('POST', '/v1/tts', 503), ('POST', '/v1/tts', 200), ('GET', '/wallet?test=1', 200)]
    finally:
        release.set()
        proxy.shutdown(); proxy.server_close()
        upstream.shutdown(); upstream.server_close()


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
