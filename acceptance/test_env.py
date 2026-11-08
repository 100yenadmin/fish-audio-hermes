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


def test_saved_output_hides_the_session_token(tmp_path):
    token = 'synthetic-session-token-0123456789'
    path = tmp_path / 'pytest.txt'
    harness.save_output(path, f'FAILED test_ws - InvalidURI ws://127.0.0.1:9361/api/ws?token={token}\n', token)
    assert token not in path.read_text() and '?token=[redacted]' in path.read_text()


def test_node_bin_prefers_acceptance_node_bin_on_macos(monkeypatch):
    monkeypatch.setattr(harness.sys, 'platform', 'darwin')
    monkeypatch.setenv('ACCEPTANCE_NODE_BIN', '/synthetic/node26/bin')
    assert harness.node_bin() == Path('/synthetic/node26/bin')
    monkeypatch.delenv('ACCEPTANCE_NODE_BIN')
    assert harness.node_bin() == Path('/opt/homebrew/bin')


def test_node_bin_uses_the_path_node_elsewhere(monkeypatch):
    monkeypatch.setattr(harness.sys, 'platform', 'linux')
    monkeypatch.setattr(harness.shutil, 'which', lambda name: '/synthetic/bin/node' if name == 'node' else None)
    assert harness.node_bin() == Path('/synthetic/bin')
    monkeypatch.setattr(harness.shutil, 'which', lambda name: None)
    with pytest.raises(RuntimeError, match='node not found on PATH'):
        harness.node_bin()


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
    monkeypatch.setattr(harness, 'node_bin', lambda: Path('/synthetic/node26/bin'))
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
    web = [(a, k) for a, k in hooks if 'scripts/build/web.mjs' in a]
    assert len(web) == 1 and web[0][0][0] == '/synthetic/node26/bin/node'
    for _, kw in hooks[-2:]:
        assert kw['env']['PATH'].split(os.pathsep)[0] == '/synthetic/node26/bin'


@pytest.mark.parametrize('port', [None, '5196'])
def test_run_keeps_renderer_port_out_of_layer_a_and_uses_selected_node(monkeypatch, tmp_path, port):
    import report
    # No real home, builds, subprocesses, keys or network: run() only sees these fixtures.
    monkeypatch.setattr(harness.os, 'environ', {'PATH': '/synthetic/bin', **({'ACCEPTANCE_VITE_PORT': port} if port else {})})
    monkeypatch.setattr(harness, 'CACHE', tmp_path / 'cache')
    monkeypatch.setattr(harness, 'node_bin', lambda: Path('/synthetic/node26/bin'))
    src = tmp_path / 'src'
    (src / 'hermes_cli').mkdir(parents=True)
    (src / 'hermes_cli/__init__.py').write_text('__version__ = "0.23.0"')
    monkeypatch.setattr(harness, 'call', lambda *a, **kw: 'synthetic-sha')
    monkeypatch.setattr(harness, 'build', lambda *a: (src, src, 'synthetic-sha'))
    def archive(repo, sha, dest):
        dest.mkdir(parents=True)
        (dest / 'plugin.yaml').write_text('version: 1.9.1')
    monkeypatch.setattr(harness, 'archive', archive)
    events = []
    class Host:
        token = 'synthetic-session-token'
        def __init__(self, out): pass
        def cli(self, *a): return 'Hermes 0.23.0'
        def up(self):
            assert 'ACCEPTANCE_VITE_PORT' not in harness.os.environ
            events.append('up')
        def down(self): events.append('down')
    monkeypatch.setattr(harness, 'Host', Host)
    calls = []
    def launch(args, **kw):
        assert 'ACCEPTANCE_VITE_PORT' not in kw['env']
        calls.append(args)
        return SimpleNamespace(stdout='', stderr='', returncode=0)
    monkeypatch.setattr(harness.subprocess, 'run', launch)
    monkeypatch.setattr(report, 'merge', lambda *a: 0)
    out = tmp_path / 'out'
    args = SimpleNamespace(out=out, host='upstream', hermes_ref='v0.23.0', plugin='fixture',
                           port=None, budget_usd=.3, billed_heavy=False, evaos_overlay=None, layers='a,b')
    assert harness.run(args) == 0
    assert events == ['up', 'down'] and not (out / 'setup-error.txt').exists()
    assert len(calls) == 2 and 'pytest' in calls[0]
    assert calls[1][0] == '/synthetic/node26/bin/node'
    assert calls[1][1] == str(harness.ROOT / 'acceptance/desktop/run.mjs')
    assert (calls[1][-2:] == ['--vite-port', port]) if port else '--vite-port' not in calls[1]


@pytest.mark.parametrize('down_fails', [False, True])
def test_run_leaves_no_fish_key_in_the_run_home(monkeypatch, tmp_path, down_fails):
    import report
    monkeypatch.setattr(harness.os, 'environ', {'PATH': '/synthetic/bin', 'FISH_API_KEY': 'synthetic-real-key'})
    monkeypatch.setattr(harness, 'CACHE', tmp_path / 'cache')
    monkeypatch.setattr(harness, 'node_bin', lambda: Path('/synthetic/node26/bin'))
    src = tmp_path / 'src'
    (src / 'hermes_cli').mkdir(parents=True)
    (src / 'hermes_cli/__init__.py').write_text('__version__ = "0.23.0"')
    monkeypatch.setattr(harness, 'call', lambda *a, **kw: 'synthetic-sha')
    monkeypatch.setattr(harness, 'build', lambda *a: (src, src, 'synthetic-sha'))
    def archive(repo, sha, dest):
        dest.mkdir(parents=True)
        (dest / 'plugin.yaml').write_text('version: 1.10.0')
    monkeypatch.setattr(harness, 'archive', archive)
    out = tmp_path / 'out'
    class Host:
        token = 'synthetic-session-token'
        def __init__(self, run_dir): pass
        def cli(self, *a): return 'Hermes 0.23.0'
        def up(self): pass
        def down(self):
            if down_fails:
                raise RuntimeError('gateway process group still alive; retained gateway-pid.json')
    monkeypatch.setattr(harness, 'Host', Host)
    def layer_a(args, **kw):
        # A Layer A row places the real key in the run home, as Host.key('real') does.
        (out / 'home/.env').write_text('GLM_API_KEY=synthetic-glm\n')
        harness.write_key(out / 'home', harness.os.environ['FISH_API_KEY'])
        return SimpleNamespace(stdout='', stderr='', returncode=0)
    monkeypatch.setattr(harness.subprocess, 'run', layer_a)
    monkeypatch.setattr(report, 'merge', lambda *a: 0)
    args = SimpleNamespace(out=out, host='upstream', hermes_ref='v0.23.0', plugin='fixture',
                           port=None, budget_usd=.3, billed_heavy=False, evaos_overlay=None, layers='a')
    if down_fails:
        with pytest.raises(RuntimeError, match='still alive'):
            harness.run(args)
    else:
        assert harness.run(args) == 0
    env = out / 'home/.env'
    assert env.read_text().splitlines() == ['GLM_API_KEY=synthetic-glm']
    assert env.stat().st_mode & 0o777 == 0o600


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


def test_gateway_trusts_the_desktop_renderer_only_when_layer_b_names_its_port(monkeypatch, tmp_path):
    # A trusted dev-renderer origin can read the session token over CORS, so Layer A's gateway trusts no other port,
    # even one inherited from the operator's shell; desktop/run.mjs exports the Vite port it uses before its restart.
    monkeypatch.setattr(harness, 'CACHE', tmp_path / 'cache')
    host = object.__new__(harness.Host)
    host.out, host.home, host.src, host.token = tmp_path / 'out', tmp_path / 'home', tmp_path / 'src', 'token'
    host.meta = {'host': 'upstream'}
    monkeypatch.delenv('ACCEPTANCE_VITE_PORT', raising=False)
    monkeypatch.setenv('HERMES_DESKTOP_DEV_SERVER', 'http://127.0.0.1:5194')
    assert 'HERMES_DESKTOP_DEV_SERVER' not in host.env()
    monkeypatch.setenv('ACCEPTANCE_VITE_PORT', '5196')
    assert host.env()['HERMES_DESKTOP_DEV_SERVER'] == 'http://127.0.0.1:5196'


def test_gateway_control_socket_fits_a_private_runtime_dir(monkeypatch, tmp_path):
    socket_leaf = '/hermes-gw-0123456789abcdef/control.sock'  # Hermes: hermes-gw-<16 hex>/control.sock
    assert len((str(harness.CACHE / 'run') + socket_leaf).encode()) <= 100  # Hermes's _MAX_UNIX_PATH
    monkeypatch.setattr(harness, 'CACHE', tmp_path / 'cache')
    host = object.__new__(harness.Host)
    host.out, host.home, host.src, host.token = tmp_path / 'out', tmp_path / 'home', tmp_path / 'src', 'token'
    host.meta = {'host': 'evaos'}
    monkeypatch.setattr(harness.sys, 'platform', 'linux')
    assert 'XDG_RUNTIME_DIR' not in host.env() or host.env()['XDG_RUNTIME_DIR'] == os.environ.get('XDG_RUNTIME_DIR')
    monkeypatch.setattr(harness.sys, 'platform', 'darwin')
    runtime = Path(host.env()['XDG_RUNTIME_DIR'])
    assert runtime == tmp_path / 'cache' / 'run' and runtime.stat().st_mode & 0o777 == 0o700

