"""Host lifecycle and pytest row receipts; never imports the candidate plugin."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import time

ROOT = Path(__file__).resolve().parents[1]
CACHE = Path.home() / '.cache/fish-audio-acceptance'
FAKE = 'sk-fish-acceptance-fake-000000'
PIN = 'evaos-runtime-es.12-v0.21.5-r34.13'
REPOS = {'upstream': 'NousResearch/hermes-agent', 'evaos': 'electricsheephq/evaOS-hermes-desktop-app-adapter'}


TRANSIENT = re.compile(r'(?i)Fish Audio is unavailable|try again later|rate.?limit|\b(429|500|502|503|504)\b')


def scrub(value):
    text = str(value)
    for name in ('FISH_API_KEY', 'GLM_API_KEY', 'HERMES_DASHBOARD_SESSION_TOKEN'):
        key = os.environ.get(name)
        if key:
            text = text.replace(key, '[redacted]')
    return re.sub(r'(?i)Bearer\s+\S+|sk-[A-Za-z0-9_-]{16,}', '[redacted]', text)


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


def call(args, cwd=None, env=None, timeout=300):
    p = subprocess.run([str(a) for a in args], cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    if p.returncode:
        raise RuntimeError(scrub(p.stderr[-3000:] or p.stdout[-3000:] or f'exit {p.returncode}'))
    return p.stdout.strip()


def archive(repo, ref, dest):
    dest.mkdir(parents=True, exist_ok=True)
    # git archive is the only operation on the existing evaOS/plugin checkouts.
    with subprocess.Popen(['git', '-C', str(repo), 'archive', ref], stdout=subprocess.PIPE) as p:
        with tarfile.open(fileobj=p.stdout, mode='r|') as tf:
            tf.extractall(dest, filter='data')
        if p.wait():
            raise RuntimeError('git archive failed')


def build(host, ref):
    repo = CACHE / 'repos' / host
    local = Path.home() / 'repos/evaOS-hermes-desktop-app-adapter'
    if host == 'evaos' and local.is_dir():
        repo = local
    elif not repo.exists():
        call(['git', 'clone', '--filter=blob:none', 'https://github.com/' + REPOS[host], repo], timeout=600)
    try:
        sha = call(['git', '-C', repo, 'rev-parse', ref + '^{commit}'])
    except RuntimeError:
        if repo == local:
            raise
        call(['git', '-C', repo, 'fetch', 'origin', ref])
        sha = call(['git', '-C', repo, 'rev-parse', 'FETCH_HEAD^{commit}'])
    dest = CACHE / f'{host}-{ref.replace("/", "_")}-{sha[:12]}'
    if not (dest / 'pyproject.toml').exists():
        archive(repo, sha, dest)
    env = {**os.environ, 'UV_CACHE_DIR': str(CACHE / 'uv'), 'UV_CONCURRENT_BUILDS': '1',
           'UV_CONCURRENT_INSTALLS': '2', 'UV_CONCURRENT_DOWNLOADS': '4', 'CMAKE_BUILD_PARALLEL_LEVEL': '4'}
    marker = dest / '.acceptance-built'
    if not marker.exists():
        for attempt in range(2):
            try:
                call(['pwd'], cwd=dest)
                call(['uv', 'sync', '--frozen', '--no-dev'], cwd=dest, env=env, timeout=600)
                call(['uv', 'pip', 'install', '--python', dest / '.venv/bin/python',
                      'msgpack>=1.0,<2', 'pytest==8.4.2', 'respx==0.22.0', 'pyyaml>=6,<7'], cwd=dest, env=env, timeout=300)
                marker.write_text(sha)
                break
            except Exception:
                if attempt == 1:
                    raise RuntimeError(f'{host} venv build failed twice') from None
    if host == 'upstream' and not (dest / 'hermes_cli/web_dist/index.html').exists():
        node_dir = '/opt/homebrew/bin' if sys.platform == 'darwin' else str(Path(shutil.which('node')).parent)
        jsenv = {**env, 'PATH': node_dir + os.pathsep + env['PATH'], 'npm_config_userconfig': os.devnull,
                 'npm_config_cache': str(CACHE / 'npm'), 'RAYON_NUM_THREADS': '4', 'CI': '1'}
        call(['npm', 'ci', '--workspace', 'web', '--include-workspace-root', '--ignore-scripts', '--no-audit', '--no-fund'], cwd=dest, env=jsenv, timeout=600)
        call([str(Path(node_dir) / 'node'), 'scripts/build/web.mjs', '--source', dest, '--icons', dest,
              '--out', dest / 'hermes_cli/web_dist'], cwd=dest, env=jsenv, timeout=600)
    return repo, dest, sha


class Host:
    def __init__(self, out):
        self.out = Path(out).resolve()
        self.meta = json.loads((self.out / 'host.json').read_text())
        self.home = Path(self.meta['hermes_home'])
        self.src = Path(self.meta['cache_dir'])
        self.token = Path(self.meta['session_token_file']).read_text().strip()

    def env(self):
        env = {k: v for k, v in os.environ.items() if not k.endswith(('API_KEY', 'TOKEN', 'SECRET'))}
        env.update(HERMES_HOME=str(self.home), HERMES_DISABLE_LAZY_INSTALLS='1',
                   HERMES_DASHBOARD_SESSION_TOKEN=self.token, PYTHONDONTWRITEBYTECODE='1',
                   XDG_CACHE_HOME=str(CACHE / 'xdg'), UV_CACHE_DIR=str(CACHE / 'uv'),
                   XDG_STATE_HOME=str(self.out / 'state'),
                   GLM_BASE_URL='https://api.z.ai/api/coding/paas/v4',
                   TMPDIR=str(Path(os.environ.get('TMPDIR', self.out / 'tmp')).resolve()))
        if os.environ.get('GLM_API_KEY'):
            env['GLM_API_KEY'] = os.environ['GLM_API_KEY']
        if self.meta['host'] == 'upstream':
            env['HERMES_WEB_DIST'] = str(self.src / 'hermes_cli/web_dist')
        return env

    def sandbox(self, args):
        if sys.platform != 'darwin':
            return list(map(str, args))
        temp = Path(self.env()['TMPDIR']).resolve()
        return ['sandbox-exec', '-D', f'RUN_DIR={self.out}', '-D', f'CACHE_DIR={CACHE.resolve()}',
                '-D', f'TEMP_DIR={temp}', '-f', str(ROOT / 'acceptance/macos.sb'), *map(str, args)]

    def cli(self, *args, python=False, timeout=120):
        binary = self.src / ('.venv/bin/python' if python else '.venv/bin/hermes')
        cmd = self.sandbox([binary, *args])
        p = subprocess.Popen(cmd, cwd=self.src, env=self.env(), stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, start_new_session=True)
        with (self.out / 'pids.txt').open('a') as f:
            f.write(f'{p.pid} command\n')
        try:
            output, _ = p.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGTERM)
            try:
                output, _ = p.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGKILL)
                output, _ = p.communicate()
            raise RuntimeError('host command timed out; owned process group stopped') from None
        safe = scrub(output).replace(self.token, '[redacted]')
        if p.returncode:
            raise RuntimeError(safe[-2500:] or f'host exit {p.returncode}')
        return safe

    def request(self, path, body=None, method=None):
        import urllib.request
        import urllib.error
        req = urllib.request.Request(self.meta['gateway_url'] + path,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     method=method or ('POST' if body is not None else 'GET'),
                                     headers={'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                data = json.load(r)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f'HTTP {exc.code}: ' + scrub(exc.read().decode()[:600])) from None
        if data.get('ok') is False:
            raise RuntimeError(scrub(data.get('message', data)))
        return data

    def key(self, kind):
        value = {'real': os.environ.get('FISH_API_KEY', ''), 'fake': FAKE, 'clear': ''}[kind]
        if kind == 'real' and not value:
            raise RuntimeError('FISH_API_KEY missing')
        path = self.home / '.env'
        lines = [s for s in path.read_text().splitlines() if not s.startswith('FISH_API_KEY=')] if path.exists() else []
        if value:
            lines.append('FISH_API_KEY=' + value)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write('\n'.join(lines) + '\n')
        path.chmod(0o600)

    def up(self):
        port = int(self.meta['gateway_url'].rsplit(':', 1)[1])
        with socket.socket() as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(('127.0.0.1', port))
        p = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), 'gateway-worker',
                              '--run-dir', str(self.out)], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        write_json(self.out / 'gateway-pid.json', {'pid': p.pid, 'started': time.time()})
        with (self.out / 'pids.txt').open('a') as f:
            f.write(f'{p.pid} gateway-group\n')
        for _ in range(120):
            try:
                self.request('/api/plugins/fish-audio/available')
                return
            except Exception:
                if p.poll() is not None:
                    raise RuntimeError('gateway exited; see sanitized gateway.log') from None
                time.sleep(.5)
        self.down()
        raise RuntimeError('gateway startup timed out; see sanitized gateway.log')

    def down(self):
        path = self.out / 'gateway-pid.json'
        if not path.exists():
            return
        pid = json.loads(path.read_text())['pid']
        # Verify custody before signaling a potentially recycled PID.
        p = subprocess.run(['ps', '-p', str(pid), '-o', 'command='], capture_output=True, text=True)
        if 'gateway-worker' in p.stdout and str(self.out) in p.stdout:
            tree = [pid]
            listing = call(['ps', '-axo', 'pid=,ppid='])
            pairs = [tuple(map(int, line.split())) for line in listing.splitlines()]
            for parent in tree:
                tree.extend(child for child, ppid in pairs if ppid == parent and child not in tree)
            with (self.out / 'pids.txt').open('a') as f:
                for child in tree:
                    f.write(f'{child} shutdown-tree\n')
            for child in reversed(tree):
                try:
                    os.kill(child, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            # Wait for the listener to disappear before a restart (TIME_WAIT is harmless).
            port = int(self.meta['gateway_url'].rsplit(':', 1)[1])
            for _ in range(50):
                with socket.socket() as s:
                    if s.connect_ex(('127.0.0.1', port)):
                        break
                time.sleep(.1)
        path.unlink()

    def restart(self):
        self.down()
        self.up()

    def wallet(self):
        return float(self.request('/api/plugins/fish-audio/account')['credit'])


def worker(out):
    h = Host(out)
    port = h.meta['gateway_url'].rsplit(':', 1)[1]
    args = ['dashboard', '--port', port, '--no-open'] if h.meta['host'] == 'upstream' else ['serve', '--isolated', '--port', port]
    p = subprocess.Popen(h.sandbox([h.src / '.venv/bin/hermes', *args]), cwd=h.src,
                         env=h.env(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    with (h.out / 'pids.txt').open('a') as f:
        f.write(f'{p.pid} gateway\n')
    with (h.out / 'gateway.log').open('a') as f:
        for line in p.stdout:
            if FAKE in line:
                (h.out / 'fake-key-leak.txt').write_text('fake key appeared in gateway output\n')
            f.write(scrub(line).replace(h.token, '[redacted]'))
            f.flush()
    return p.wait()


def run(args):
    from report import merge
    out = args.out.resolve()
    if out.exists() and any(out.iterdir()):
        raise RuntimeError('output directory must be empty; preserve previous evidence')
    out.mkdir(parents=True, mode=0o700, exist_ok=True)
    out.chmod(0o700)
    CACHE.mkdir(parents=True, exist_ok=True)
    ref = args.hermes_ref or (PIN if args.host == 'evaos' else call(['gh', 'api', 'repos/NousResearch/hermes-agent/releases/latest', '--jq', '.tag_name']))
    plugin_sha = call(['git', '-C', ROOT, 'rev-parse', args.plugin + '^{commit}'])
    meta = {'host': args.host, 'hermes_ref': ref, 'hermes_version': 'unbuilt', 'gateway_url': f'http://127.0.0.1:{args.port or (9361 if args.host == "upstream" else 9362)}',
            'session_token_file': str(out / 'session-token'), 'hermes_home': str(out / 'home'),
            'plugin_ref': plugin_sha, 'plugin_version': '0', 'desktop_src_repo': 'https://github.com/' + REPOS[args.host],
            'desktop_ref': ref, 'cache_dir': '', 'budget_usd': args.budget_usd, 'billed_heavy': args.billed_heavy}
    write_json(out / 'host.json', meta)
    h = None
    try:
        repo, src, sha = build(args.host, ref)
        meta.update(cache_dir=str(src), hermes_sha=sha)
        if args.host == 'evaos':
            # Resolve live tags without mutating the operator's checkout.
            tags = call(['git', 'ls-remote', '--tags', meta['desktop_src_repo'], 'v*-es.*'])
            names = {line.split('refs/tags/')[-1] for line in tags.splitlines() if not line.endswith('^{}')}
            meta['desktop_ref'] = max(names, key=lambda x: tuple(map(int, re.findall(r'\d+', x))))
        home = out / 'home'
        plugin = home / 'plugins/fish-audio'
        archive(ROOT, plugin_sha, plugin)
        meta['plugin_version'] = re.search(r'^version:\s*[\"\']?([\d.]+)', (plugin / 'plugin.yaml').read_text(), re.M)[1]
        config = {'plugins': {'enabled': ['fish-audio']}, 'tts': {'provider': 'fish-audio'},
                  'stt': {'provider': 'fish-audio', 'streaming': True},
                  'model': {'provider': 'zai', 'default': 'glm-5.2', 'base_url': 'https://api.z.ai/api/coding/paas/v4'},
                  'voice': {'voice_chat_mode': 'chained', 'client_direct': False}}
        if args.evaos_overlay:
            raise RuntimeError('PCS overlay loading is outside this core implementation; no private files read')
        (home / 'config.yaml').write_text(json.dumps(config))
        (out / 'session-token').write_text(secrets.token_urlsafe(32))
        (out / 'session-token').chmod(0o600)
        (out / 'tmp').mkdir()
        write_json(out / 'host.json', meta)
        h = Host(out)
        meta['hermes_version_cli'] = h.cli('--version').splitlines()[0]
        declared = re.search(r'__version__\s*=\s*[\"\']([\d.]+)', (src / 'hermes_cli/__init__.py').read_text())
        tagged = re.fullmatch(r'v(\d+\.\d+\.\d+)', ref)
        meta['hermes_version'] = tagged[1] if tagged else declared[1] if declared else meta['hermes_version_cli']
        write_json(out / 'host.json', meta)
        h.up()
        env = {**os.environ, 'ACCEPTANCE_OUT': str(out), 'PYTHONDONTWRITEBYTECODE': '1'}
        if 'a' in args.layers.split(','):
            # Explicit collection only; root testpaths stays tests/.
            p = subprocess.run([str(src / '.venv/bin/python'), '-m', 'pytest', '-q',
                                str(ROOT / 'acceptance/test_layer_a.py'), '-p', 'no:cacheprovider',
                                '--tb=no'], cwd=ROOT, env=env, capture_output=True, text=True)
            (out / 'pytest.txt').write_text(scrub(p.stdout + p.stderr))
            # One retry for rows that failed on a Fish outage or rate limit; the report marks them.
            rows = json.loads((out / 'rows-a.json').read_text()) if (out / 'rows-a.json').exists() else []
            transient = [r['id'] for r in rows if r['status'] == 'fail' and TRANSIENT.search(r['reason'])]
            if transient:
                p = subprocess.run([str(src / '.venv/bin/python'), '-m', 'pytest', '-q',
                                    str(ROOT / 'acceptance/test_layer_a.py'), '-p', 'no:cacheprovider', '--tb=no'],
                                   cwd=ROOT, env={**env, 'ACCEPTANCE_RERUN': ','.join(transient)},
                                   capture_output=True, text=True)
                (out / 'pytest-retry.txt').write_text(scrub(p.stdout + p.stderr))
        if 'b' in args.layers.split(',') and (ROOT / 'acceptance/desktop/run.mjs').exists():
            p = subprocess.run(['/opt/homebrew/bin/node' if sys.platform == 'darwin' else 'node',
                                str(ROOT / 'acceptance/desktop/run.mjs'), '--host-json', str(out / 'host.json'),
                                '--out', str(out)], cwd=ROOT, env=env, capture_output=True, text=True)
            (out / 'desktop.txt').write_text(scrub(p.stdout + p.stderr))
    except Exception as exc:
        (out / 'setup-error.txt').write_text(scrub(exc) + '\n')
    finally:
        if h:
            h.down()
    return merge(out, args.layers.split(','), args.budget_usd)


def pytest_configure(config):
    config.addinivalue_line('markers', 'row(id): acceptance row identifier')
    config._acceptance_rows = []
    out = os.environ.get('ACCEPTANCE_OUT')
    if os.environ.get('ACCEPTANCE_RERUN') and out and (Path(out) / 'rows-a.json').exists():
        config._acceptance_rows = json.loads((Path(out) / 'rows-a.json').read_text())


def pytest_collection_modifyitems(config, items):
    wanted = set(filter(None, os.environ.get('ACCEPTANCE_RERUN', '').split(',')))
    if not wanted:
        return
    keep = [i for i in items if i.get_closest_marker('row') and i.get_closest_marker('row').args[0] in wanted]
    config.hook.pytest_deselected(items=[i for i in items if i not in keep])
    items[:] = keep


def pytest_runtest_makereport(item, call):
    marker = item.get_closest_marker('row')
    if marker is None or call.when == 'teardown' and call.excinfo is None:
        return
    if call.when == 'setup' and call.excinfo is None:
        return
    status = 'pass' if call.excinfo is None else 'fail'
    reason = item.user_properties and dict(item.user_properties).get('reason', '') or ''
    if call.excinfo:
        import pytest
        status = 'skip' if call.excinfo.errisinstance(pytest.skip.Exception) else 'fail'
        reason = scrub(str(call.excinfo.value))[-1500:]
    props = dict(item.user_properties)
    if status == 'skip' and props.get('status') == 'na':
        status = 'na'
    row = {'id': marker.args[0], 'status': status, 'reason': reason or 'assertions passed',
           'evidence': props.get('evidence', []), 'metrics': props.get('metrics', {})}
    if os.environ.get('ACCEPTANCE_RERUN'):
        row['flaky'] = True  # failed once on a Fish outage or rate limit; this is the retry's result
    item.config._acceptance_rows = [r for r in item.config._acceptance_rows if r['id'] != row['id']] + [row]
    # Flush each row so interruptions cannot turn missing coverage green.
    out = os.environ.get('ACCEPTANCE_OUT')
    if out:
        write_json(Path(out) / 'rows-a.json', item.config._acceptance_rows)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest='mode', required=True)
    r = sub.add_parser('run')
    r.add_argument('--host', choices=REPOS, required=True)
    r.add_argument('--plugin', required=True)
    r.add_argument('--out', type=Path, default=ROOT / 'acceptance-out')
    r.add_argument('--layers', default='a,b')
    r.add_argument('--budget-usd', type=float, default=.30)
    r.add_argument('--hermes-ref')
    r.add_argument('--port', type=int)
    r.add_argument('--billed-heavy', action='store_true')
    r.add_argument('--evaos-overlay', type=Path)
    c = sub.add_parser('control')
    c.add_argument('--run-dir', type=Path, required=True)
    c.add_argument('action', choices=['up', 'down', 'restart-gateway', 'set-real-key', 'set-fake-key', 'clear-key', 'wallet'])
    w = sub.add_parser('gateway-worker')
    w.add_argument('--run-dir', type=Path, required=True)
    a = p.parse_args()
    if a.mode == 'run':
        if not set(a.layers.split(',')) <= {'a', 'b'} or a.budget_usd <= 0:
            p.error('layers must be a,b and budget must be positive')
        raise SystemExit(run(a))
    if a.mode == 'gateway-worker':
        raise SystemExit(worker(a.run_dir))
    h = Host(a.run_dir)
    if a.action.endswith('-key'):
        h.key({'set-real-key': 'real', 'set-fake-key': 'fake', 'clear-key': 'clear'}[a.action])
    elif a.action == 'wallet':
        print(h.wallet())
    else:
        {'up': h.up, 'down': h.down, 'restart-gateway': h.restart}[a.action]()
