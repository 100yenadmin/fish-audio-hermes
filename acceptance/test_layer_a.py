"""Real entry-point probes. Explicitly invoked by run.sh, never default collection."""
import asyncio
import base64
import copy
import json
import os
from pathlib import Path
import re
import sqlite3
import time

import pytest
import yaml
from conftest import FAKE, Host, ROOT, call, scrub, write_json

TEXT = 'Hello from the Fish Audio acceptance check.'
SENTENCES = 'The sun is shining over the garden. A little bird is singing nearby. We can hear the wind in the trees. It is a lovely morning for a walk.'
CORPUS = ROOT / 'bench/corpus/en01.webm'
REFERENCE = json.loads((ROOT / 'bench/texts.json').read_text())['asr_corpus'][0]['text']


@pytest.fixture(scope='session')
def host():
    return Host(os.environ['ACCEPTANCE_OUT'])


def evidence(request, host, value, metrics=None):
    ident = request.node.get_closest_marker('row').args[0]
    path = host.out / f'{ident}.json'
    path.write_text(scrub(json.dumps(value, indent=2)))
    request.node.user_properties.extend([('evidence', [str(path)]), ('metrics', metrics or {})])


def na(request, reason):
    request.node.user_properties.append(('status', 'na'))
    pytest.skip(reason)


def config(h):
    return yaml.safe_load((h.home / 'config.yaml').read_text()) or {}


def set_config(h, value):
    (h.home / 'config.yaml').write_text(yaml.safe_dump(value))


def rpc(h, method, params):
    from websockets.sync.client import connect
    with connect(h.meta['gateway_url'].replace('http:', 'ws:') + '/api/ws?token=' + h.token,
                 additional_headers={'Authorization': 'Bearer ' + h.token}, open_timeout=30) as ws:
        ws.send(json.dumps({'jsonrpc': '2.0', 'id': 101, 'method': method, 'params': params}))
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            for line in ws.recv(timeout=120).splitlines():
                obj = json.loads(line)
                if obj.get('id') == 101:
                    assert 'error' not in obj, scrub(obj.get('error'))
                    return obj['result']
        pytest.fail('RPC timed out')


def command(h, arg):
    result = rpc(h, 'command.dispatch', {'name': 'fish', 'arg': arg})
    text = result.get('output', '')
    assert result.get('type') == 'plugin' and text, scrub(result)
    assert not re.search(r'(?i)\b(error|failed|invalid|unavailable)\b', text), scrub(text)
    return text


@pytest.fixture(scope='session')
def real(host):
    host.key('real')
    host.restart()
    initial = host.wallet()
    if not (host.out / 'cost.json').exists():  # a retry session keeps the first session's starting balance
        write_json(host.out / 'cost.json', {'before': initial, 'after': initial, 'usd': 0.0})
    write_json(host.out / 'key-fingerprints.json', {
        name: __import__('hashlib').sha256(os.environ[name].encode()).hexdigest()[:8]
        for name in ('FISH_API_KEY', 'GLM_API_KEY') if os.environ.get(name)})
    return host


@pytest.fixture
def paid(real):
    cost = json.loads((real.out / 'cost.json').read_text())
    credit = real.wallet()
    spent = max(0, cost['before'] - credit)
    write_json(real.out / 'cost.json', {**cost, 'after': credit, 'usd': spent})
    assert spent < real.meta['budget_usd'], 'budget exhausted; further billed calls refused'
    yield real
    credit = real.wallet()
    write_json(real.out / 'cost.json', {**cost, 'after': credit, 'usd': max(0, cost['before'] - credit)})


@pytest.fixture(scope='session')
def voice(real):
    rows = real.request('/api/plugins/fish-audio/voices?q=narrator')
    return rows['items'][0]['id']


def core(h, code):
    script = ('from hermes_cli.web_server_profiles import _config_profile_scope\n'
              'from hermes_cli.plugins import discover_plugins\n'
              'with _config_profile_scope(None):\n    discover_plugins()\n    ' + code)
    output = h.cli('-c', script, python=True)
    return json.loads(output.split('ACCEPTANCE_RESULT=')[-1].splitlines()[0])


def audio(path, opus=False):
    data = json.loads(call(['ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', path]))
    assert float(data['format']['duration']) > .3, 'audio shorter than 0.3 s'
    if opus:
        assert any(s['codec_name'] == 'opus' for s in data['streams']), 'Ogg does not contain Opus'
    return {'duration': float(data['format']['duration']), 'codec': data['streams'][0]['codec_name']}


def wer(text):
    a, b = [re.findall(r'\w+', s.lower()) for s in (REFERENCE, text)]
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[-1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    rate = prev[-1] / max(1, len(a))
    assert rate <= .05, f'WER {rate} exceeds bench asr-en fail threshold 0.05'
    return rate


@pytest.mark.row('A-INST-1')
def test_validate(host, request):
    if host.meta['host'] != 'upstream':
        na(request, 'matrix O: upstream plugin validation is not the fork admission gate')
    evidence(request, host, host.cli('plugins', 'validate', str(host.home / 'plugins/fish-audio')))


@pytest.mark.row('A-INST-2')
def test_enabled(host, request):
    rows = rpc(host, 'plugins.manage', {'action': 'list'})['plugins']
    row = next(r for r in rows if r['name'] == 'fish-audio')
    evidence(request, host, row)
    assert row['status'] == 'enabled'
    assert any(s['type'] == 'secret' and s.get('env') == 'FISH_API_KEY' for s in row['settings_schema'])


@pytest.mark.row('A-INST-3')
def test_mount(host):
    assert any('Mounted plugin API routes: /api/plugins/fish-audio/' in p.read_text(errors='replace')
               for p in [host.out / 'gateway.log', *list((host.home / 'logs').glob('*.log'))])


@pytest.mark.row('A-KEY-1')
def test_no_key(host, request):
    data = host.request('/api/plugins/fish-audio/available')
    evidence(request, host, data)
    assert data['key'] is False


@pytest.mark.row('A-KEY-2')
def test_desktop_writer(host, request):
    try:
        saved = host.request('/api/env', {'key': 'FISH_API_KEY', 'value': FAKE}, 'PUT')
        available = host.request('/api/plugins/fish-audio/available')
        evidence(request, host, {'save': saved, 'available_without_restart': available,
                                'writer': 'saveAgentPluginSettings -> setEnvVar -> PUT /api/env {key,value}'})
        assert available['key'] is True, 'Desktop write did not become available without restart'
    finally:
        host.request('/api/env', {'key': 'FISH_API_KEY'}, 'DELETE')
        host.restart()
    assert host.request('/api/plugins/fish-audio/available')['key'] is False


@pytest.mark.row('A-KEY-3')
def test_key_leak(host):
    assert FAKE not in (host.home / 'config.yaml').read_text()
    assert FAKE not in json.dumps(host.request('/api/plugins/fish-audio/available'))
    assert not (host.out / 'fake-key-leak.txt').exists(), 'fake key appeared in raw gateway output'
    for path in (host.home / 'logs').glob('*.log'):
        assert FAKE not in path.read_text(errors='replace'), 'fake key appeared in host logs'


@pytest.mark.row('A-CLI-1')
def test_status(host, request):
    text = host.cli('fish', 'status')
    evidence(request, host, text)
    assert 'key' in text.lower() and any(s in text.lower() for s in ('missing', 'not set', "isn't set up"))


@pytest.mark.parametrize('synth', [pytest.param(False, marks=pytest.mark.row('A-CLI-2')),
                                        pytest.param(True, marks=pytest.mark.row('A-CLI-3'))])
def test_doctor(paid, synth, request):
    text = paid.cli('fish', 'doctor', *([] if synth else ['--no-synth']))
    evidence(request, paid, text)
    assert not re.search(r'(?im)^\s*(?:\[)?fail\b', text)
    if synth:
        assert re.search(r'(?im)^ok: TTS [1-9]\d* bytes; billed:', text)


@pytest.mark.parametrize('n', [pytest.param(i, marks=pytest.mark.row(f'A-CMD-{i}')) for i in range(1, 7)])
def test_commands(paid, voice, n, request):
    before = config(paid)
    text = command(paid, ['status', 'voices narrator', f'use {voice}', 'model s2.1-pro',
                          f'preview {voice} {TEXT}', 'balance'][n - 1])
    evidence(request, paid, text)
    if n == 3:
        expected = copy.deepcopy(before)
        expected.setdefault('tts', {}).setdefault('fish-audio', {})['voice'] = voice
        assert config(paid) == expected, '/fish use changed more than the voice'
    if n == 5:
        path = re.search(r'MEDIA:([^\s]+)', text)
        assert path and Path(path[1]).is_file(), 'preview did not return existing audio'


@pytest.mark.parametrize('n,fmt', [pytest.param(i, fmt, marks=pytest.mark.row(f'A-TTS-{i}'))
                                 for i, fmt in enumerate(('mp3', 'ogg', 'wav'), 1)])
def test_core_tts(paid, n, fmt, request):
    # Core turns every voice_compatible plugin provider's output into Ogg/Opus on every platform
    # (tools/tts_tool.py _finalize_voice_delivery; hermes-agent#133133), so .mp3/.wav may come back as .ogg.
    # The plugin's own format contract is checked at the provider level (test_provider_format).
    path = paid.out / f'core.{fmt}'
    result = core(paid, f'import json; from tools.tts_tool import text_to_speech_tool; print("ACCEPTANCE_RESULT=" + text_to_speech_tool({TEXT!r}, output_path={str(path)!r}))')
    evidence(request, paid, result)
    assert result.get('success'), scrub(result)
    got = Path(result['file_path'])
    assert got in (path, path.with_suffix('.ogg')), f'unexpected output path {got}'
    metrics = audio(got, got.suffix == '.ogg')
    metrics['core_converted_to_ogg'] = got != path
    evidence(request, paid, {'file': str(got)}, metrics)


@pytest.mark.parametrize('n,fmt,codec', [pytest.param(i, fmt, codec, marks=pytest.mark.row(f'A-TTS-{i}'))
                                         for i, fmt, codec in ((4, 'mp3', 'mp3'), (5, 'wav', 'pcm_s16le'))])
def test_provider_format(paid, n, fmt, codec, request):
    # The provider writes the format its output_path asks for, before any core conversion.
    path = paid.out / f'provider.{fmt}'
    result = core(paid, 'import json; from agent.tts_registry import get_provider; '
                        f'p = get_provider("fish-audio"); r = p.synthesize({TEXT!r}, {str(path)!r}, format={fmt!r}); '
                        'print("ACCEPTANCE_RESULT=" + json.dumps({"result": str(r)}))')
    evidence(request, paid, result)
    metrics = audio(path)
    assert metrics['codec'] == codec, f'provider wrote {metrics["codec"]} for .{fmt}'
    evidence(request, paid, {'file': str(path)}, metrics)


@pytest.mark.row('A-STT-1')
def test_core_stt(paid, request):
    result = core(paid, f'import json; from tools.transcription_tools import transcribe_audio; print("ACCEPTANCE_RESULT=" + json.dumps(transcribe_audio({str(CORPUS)!r})))')
    assert result.get('success'), scrub(result)
    evidence(request, paid, result, {'wer': wer(result['transcript'])})


@pytest.mark.parametrize('n,tool', [pytest.param(i, tool, marks=pytest.mark.row(f'A-TOOL-{i}'))
                                  for i, tool in enumerate(('fish_speak', 'fish_voices', 'fish_transcribe'), 1)])
def test_agent(paid, n, tool, request):
    if not os.environ.get('GLM_API_KEY'):
        pytest.skip('no LLM key')
    prompts = [f'Use fish_speak exactly once to say: {TEXT} Include its MEDIA tag and one short text line.',
               'Use fish_voices to search for narrator. Return at least one voice.',
               f'Use fish_transcribe on {CORPUS}. Return the transcript.']
    with sqlite3.connect(f'file:{paid.home / "state.db"}?mode=ro', uri=True) as db:
        baseline = db.execute('SELECT coalesce(max(id), 0) FROM messages').fetchone()[0]
    reply = paid.cli('chat', '-q', prompts[n - 1], '--provider', 'zai', '-m', 'glm-5.2',
                     '-t', 'fish_audio', timeout=180)
    db = sqlite3.connect(f'file:{paid.home / "state.db"}?mode=ro', uri=True)
    try:
        rows = db.execute('SELECT content FROM messages WHERE tool_name=? AND id>? ORDER BY id DESC LIMIT 1', (tool, baseline)).fetchall()
    finally:
        db.close()
    assert rows, f'state.db has no {tool} tool result'
    result = json.loads(rows[0][0])
    assert result.get('success') is True, scrub(result)
    if n == 1:
        assert 'MEDIA:' in reply and Path(result['file_path']).is_file()
        if tuple(map(int, paid.meta['plugin_version'].split('.'))) >= (1, 7, 4):
            assert re.sub(r'MEDIA:\S+|\[\[audio_as_voice\]\]', '', reply).strip()
    elif n == 2:
        assert len(result['items']) >= 1
    else:
        assert result.get('text') or result.get('transcript')
    evidence(request, paid, {'tool': tool, 'success': True, 'reply': reply})


@pytest.mark.parametrize('n', [pytest.param(i, marks=pytest.mark.row(f'A-ROUTE-{i}')) for i in range(1, 14)])
def test_routes(paid, voice, n, request):
    base = '/api/plugins/fish-audio'
    get = lambda p: paid.request(base + p)
    post = lambda p, b: paid.request(base + p, b)
    delete = lambda p: paid.request(base + p, method='DELETE')
    if n in (11, 12) and not paid.meta['billed_heavy']:
        pytest.skip('billed:heavy not enabled')
    result = None
    if n == 1:
        result = get('/available')
        assert result['key'] is True
    elif n in (2, 3):
        result = get('/voices?' + ('q=narrator' if n == 2 else 'self=true'))
        assert isinstance(result['items'], list)
    elif n == 4:
        result = get('/voices/' + voice)
        assert result['voice']['id'] == voice
    elif n == 5:
        result = post('/preview', {'voice': voice, 'text': TEXT})
        path = paid.out / 'preview.mp3'
        path.write_bytes(base64.b64decode(result.pop('audio')))
        audio(path)
    elif n == 6:
        result = post('/use', {'voice': voice})
        assert config(paid)['tts']['fish-audio']['voice'] == voice
    elif n == 7:
        result = get('/account')
        assert float(result['credit']) >= 0
    elif n in (8, 9):
        result = get('/settings') if n == 8 else post('/settings', {'model': 's2.1-pro'})
        assert isinstance(result['models'], list)
        if n == 9:
            assert config(paid)['tts']['fish-audio']['model'] == 's2.1-pro'
    elif n == 10:
        try:
            result = post('/aliases', {'name': 'acceptance', 'voice': voice})
            assert any(x['name'] == 'acceptance' for x in result['aliases'])
        finally:
            assert not any(x['name'] == 'acceptance' for x in delete('/aliases/acceptance')['aliases'])
    elif n == 11:
        created = None
        try:
            designed = post('/design', {'instruction': 'A calm clear English narrator.', 'n': 1, 'reference_text': TEXT})
            assert designed['candidates'][0]['audio']
            result = post('/design/save', {'design_token': designed['candidates'][0]['design_token'], 'title': 'Acceptance synthetic voice'})
            created = result['voice']['id']
            assert created
        finally:
            if created:
                delete('/voices/' + created)
    else:
        # Synthetic macOS say fixture (never clone a person's voice or benchmark speaker).
        path = paid.out / 'synthetic.wav'
        if n == 12:
            call(['say', '-o', paid.out / 'synthetic.aiff', TEXT + ' This is a generated test voice with no human speaker.'])
            call(['ffmpeg', '-y', '-i', paid.out / 'synthetic.aiff', '-ar', '24000', path])
        data = path.read_bytes() if n == 12 else b'fixture'
        upload = post('/clone/start', {'size': len(data)})['upload_id']
        created = None
        try:
            result = post('/clone/chunk', {'upload_id': upload, 'offset': 0, 'data': base64.b64encode(data).decode()})
            assert result['size'] == len(data)
            if n == 12:
                result = post('/clone/finish', {'files': [{'upload_id': upload, 'size': len(data)}], 'title': 'Acceptance synthetic clone', 'consent': True})
                created = result['voice']['id']
                assert created
        finally:
            post('/clone/abort', {'upload_id': upload})
            if created:
                delete('/voices/' + created)
        assert not list((paid.home / 'cache/fish-audio/uploads').glob(upload + '*'))
    evidence(request, paid, result)


async def stream(h, mode):
    import websockets
    from websockets.exceptions import ConnectionClosed
    frames, pcm, stamp = [], [], time.monotonic()
    async with websockets.connect(h.meta['gateway_url'].replace('http:', 'ws:') + '/api/audio/speak-stream?token=' + h.token,
                                  additional_headers={'Authorization': 'Bearer ' + h.token}, max_size=8 * 1024 * 1024) as ws:
        async def send():
            for i in range(0, len(SENTENCES), 30):
                await ws.send(json.dumps({'text': SENTENCES[i:i + 30]}))
                await asyncio.sleep(3 if mode == 2 and i == 0 else .1)
            await ws.send(json.dumps({'done': True}))
        task = asyncio.create_task(send())
        stopped, stop_t = False, None
        try:
            while True:
                msg = await asyncio.wait_for(ws.recv(), 45)
                if isinstance(msg, bytes):
                    pcm.append({'t': time.monotonic() - stamp, 'bytes': len(msg), 'after_stop': stopped})
                    if mode == 3 and not stopped:
                        await ws.send(json.dumps({'stop': True}))
                        stopped, stop_t = True, time.monotonic() - stamp
                else:
                    frames.append(json.loads(msg))
                    if frames[-1].get('type') in ('end', 'fallback', 'error'):
                        break
        except ConnectionClosed:
            pass
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    times = [x['t'] for x in pcm]
    return {'frames': frames, 'pcm': pcm, 'stop_t': stop_t}, {'first_pcm_ms': times[0] * 1000 if times else None,
                'inter_chunk_gaps_ms': [(b - a) * 1000 for a, b in zip(times, times[1:])], 'pcm_bytes': sum(x['bytes'] for x in pcm)}


@pytest.mark.parametrize('n', [pytest.param(i, marks=pytest.mark.row(f'A-STREAM-{i}')) for i in range(1, 5)])
def test_stream(paid, n, request):
    if paid.meta['host'] == 'evaos':
        na(request, 'matrix O: evaOS runtime lacks plugin PCM seam; use whole-text fallback')
    saved = config(paid)
    try:
        if n == 4:
            altered = copy.deepcopy(saved)
            altered.setdefault('plugins', {}).setdefault('entries', {}).setdefault('fish-audio', {}).setdefault('settings', {})['base_url'] = 'https://api.fish.audio/acceptance-invalid-path'
            set_config(paid, altered)
        data, metrics = asyncio.run(stream(paid, n))
        evidence(request, paid, data, metrics)
        kinds = [f.get('type') for f in data['frames']]
        if n == 4:
            assert 'fallback' in kinds or any(f.get('type') == 'error' and (f.get('message') or f.get('error')) for f in data['frames']), 'forced failure silently ended without cause or fallback'
        else:
            assert metrics['pcm_bytes'] > 0
            assert any(f.get('type') == 'start' and f.get('sample_rate') == 24000 for f in data['frames'])
            if n == 3:
                # Chunks already in flight when stop is sent may still land; nothing may arrive later than 300 ms.
                late = [x for x in data['pcm'] if x['after_stop'] and x['t'] - data['stop_t'] > .3]
                assert not late, f'{len(late)} PCM chunks arrived more than 300 ms after stop'
            else:
                assert 'end' in kinds
    finally:
        if n == 4:
            set_config(paid, saved)


@pytest.mark.row('A-STT-2')
def test_batch_stt(paid, request):
    result = paid.request('/api/audio/transcribe', {'data_url': 'data:audio/webm;base64,' + base64.b64encode(CORPUS.read_bytes()).decode(), 'mime_type': 'audio/webm'})
    evidence(request, paid, result, {'wer': wer(result['transcript'])})


def live_caption_metrics(frames, eos_ms):
    first = next((f['received_ms'] for f in frames if f.get('text', '').strip()), None)
    return {'first_caption_ms': first, 'eos_ms': eos_ms}, first is not None and first < eos_ms


@pytest.mark.row('A-STT-3')
def test_live_stt(paid, request):
    if paid.meta['host'] == 'evaos':
        na(request, 'matrix O: fork has no live STT seam or transcribe-stream route')
    pcm = paid.out / 'dictation.pcm'
    call(['ffmpeg', '-y', '-i', CORPUS, '-ar', '48000', '-ac', '1', '-f', 's16le', pcm])
    from websockets.sync.client import connect
    frames, started = [], time.monotonic()
    with connect(paid.meta['gateway_url'].replace('http:', 'ws:') + '/api/audio/transcribe-stream?token=' + paid.token,
                 additional_headers={'Authorization': 'Bearer ' + paid.token}) as ws:
        ws.send(json.dumps({'sample_rate': 48000}))
        data = pcm.read_bytes() + bytes(96000)
        for i in range(0, len(data), 9600):
            ws.send(data[i:i + 9600])
            time.sleep(.1)
            try:
                frame = json.loads(ws.recv(timeout=.001))
                frame['received_ms'] = (time.monotonic() - started) * 1000
                frames.append(frame)
            except TimeoutError:
                pass
        ws.send(json.dumps({'eos': True}))
        eos_ms = (time.monotonic() - started) * 1000
        while not frames or frames[-1]['type'] not in ('final', 'error'):
            frame = json.loads(ws.recv(timeout=25))
            frame['received_ms'] = (time.monotonic() - started) * 1000
            frames.append(frame)
    metrics, caption_before_eos = live_caption_metrics(frames, eos_ms)
    evidence(request, paid, frames, metrics)
    assert caption_before_eos, f'no caption before EOS: first_caption_ms={metrics["first_caption_ms"]}, eos_ms={eos_ms}'
    assert frames[-1]['type'] == 'final', scrub(frames[-1])
    wer(frames[-1]['transcript'])


@pytest.mark.row('A-EVAOS-1')
def test_whole_speech(paid, request):
    if paid.meta['host'] != 'evaos':
        na(request, 'evaOS-only fallback probe')
    data, _ = asyncio.run(stream(paid, 1))
    assert any(f.get('type') == 'fallback' for f in data['frames']), 'expected fork plugin seam fallback'
    started = time.monotonic()
    result = paid.request('/api/audio/speak', {'text': TEXT})
    elapsed = time.monotonic() - started
    path = paid.out / 'whole.mp3'
    path.write_bytes(base64.b64decode(result.pop('data_url').split(',', 1)[1]))
    metrics = audio(path)
    evidence(request, paid, result, {**metrics, 'elapsed_s': elapsed})


@pytest.mark.row('A-NEG-1')
def test_free_policy(paid, request):
    saved = config(paid)
    try:
        altered = copy.deepcopy(saved)
        altered.setdefault('plugins', {}).setdefault('entries', {}).setdefault('fish-audio', {}).setdefault('settings', {})['allow_free_model'] = False
        set_config(paid, altered)
        result = command(paid, 'model s2.1-pro-free')
        evidence(request, paid, result)
        assert 'paid s2.1-pro' in result or 'refus' in result.lower()
        assert paid.request('/api/plugins/fish-audio/settings')['model'] == 's2.1-pro'
    finally:
        set_config(paid, saved)


@pytest.mark.row('A-NEG-2')
def test_bad_key(paid, request):
    try:
        paid.key('fake')
        paid.restart()
        # In-band credential failure inspected directly, without treating it as transport success.
        import httpx
        result = httpx.get(paid.meta['gateway_url'] + '/api/plugins/fish-audio/account',
                           headers={'Authorization': 'Bearer ' + paid.token}, timeout=30).json()
        text = json.dumps(result)
        evidence(request, paid, result)
        assert result.get('ok') is False and FAKE not in text
        assert 'https://fish.audio/app/api-keys' in text, 'bad-key message does not name key page'
    finally:
        paid.key('real')
        paid.restart()


@pytest.mark.row('A-COST-1')
def test_cost(real, request):
    cost = json.loads((real.out / 'cost.json').read_text())
    after = real.wallet()
    cost.update(after=after, usd=max(0, cost['before'] - after))
    write_json(real.out / 'cost.json', cost)
    evidence(request, real, cost)
    assert cost['usd'] <= real.meta['budget_usd']
