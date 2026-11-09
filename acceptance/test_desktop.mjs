// Offline tests of the shipped shim and producer predicates; no browser, devices or host launch.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import vm from 'node:vm'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { execFileSync } from 'node:child_process'

const source = name => fs.readFileSync(new URL(`./desktop/${name}`, import.meta.url), 'utf8')
const conv = source('conv.mjs'), run = source('run.mjs')
function renderer() {
  let now = 0, tick
  class Context extends EventTarget {
    state = 'running'; currentTime = 0
    createMediaStreamDestination() { return {} }
    close() { return this.result }
    setState(state) { this.state = state; this.dispatchEvent(new Event('statechange')) }
  }
  class Source extends EventTarget {
    constructor(context, duration = 1) { super(); this.context = context; this.buffer = { duration } }
    start() { if (this.fail) throw Error('start rejected'); return 'native-start' }
    stop() {}
  }
  class Media extends EventTarget {
    duration = NaN; currentTime = 0
    play() { return this.result }
  }
  const window = {}, context = vm.createContext({ window, AudioContext:Context, AudioBufferSourceNode:Source,
    HTMLMediaElement:Media, navigator:{mediaDevices:{getUserMedia(){}}}, performance:{now:()=>now},
    setInterval: fn => { tick = fn } })
  vm.runInContext(source('mic-shim.js'), context)
  return { window, Context, Source, Media, advance: t => { now = t; tick() } }
}

test('WebAudio needs successful start, positive duration, reached schedule and running context', () => {
  const r = renderer(), c = new r.Context()
  c.setState('suspended')
  const s = new r.Source(c)
  assert.equal(s.start(2), 'native-start')
  r.advance(10); assert.equal(s.__acc.playedAt, undefined)
  c.setState('running'); r.advance(20); assert.equal(s.__acc.playedAt, undefined)
  c.currentTime = 2; r.advance(30); assert.equal(s.__acc.playedAt, 30)
  assert.equal(r.window.__audioPlaying(s.__acc, 30), true)
  r.advance(40); c.setState('suspended')
  assert.equal(r.window.__audioPlaying(s.__acc, 40), false)
  assert.equal(r.window.__audioPlaying(s.__acc, 30), true)
  const zero = new r.Source(c, 0); zero.start(); c.setState('running'); r.advance(50)
  assert.equal(zero.__acc.playedAt, undefined)
  const rejected = new r.Source(c); rejected.fail = true
  assert.throws(() => rejected.start(), /start rejected/)
  assert.equal(r.window.__audioLog.length, 2)
})

test('media rejection and pending play never count; success still needs duration or progress', async () => {
  const r = renderer(), rejected = new r.Media(), pending = new r.Media(), success = new r.Media()
  rejected.duration = 2; rejected.result = Promise.reject(Error('denied'))
  pending.duration = 2; pending.result = new Promise(() => {})
  success.result = Promise.resolve()
  assert.equal(rejected.play(), rejected.result)
  pending.play(); success.play(); await Promise.resolve(); r.advance(10)
  assert.equal(r.window.__mediaLog.filter(e => e.playedAt != null).length, 0)
  success.currentTime = .1; success.dispatchEvent(new Event('timeupdate'))
  assert.equal(r.window.__mediaLog.filter(e => e.playedAt != null).length, 1)
  const playing = new r.Media(); playing.duration = 2; playing.play()
  playing.dispatchEvent(new Event('playing'))
  assert.equal(r.window.__mediaLog.filter(e => e.playedAt != null).length, 2)
})

test('close preserves native promise; only fulfillment records completion time', async () => {
  const r = renderer(), c = new r.Context(), s = new r.Source(c)
  s.start(); r.advance(10)
  let resolve
  c.result = new Promise(r => { resolve = r })
  assert.equal(c.close(), c.result)
  r.advance(1700)
  assert.equal(r.window.__ctxClosed[s.__acc.ctx], undefined)
  resolve(); await Promise.resolve()
  assert.equal(r.window.__ctxClosed[s.__acc.ctx], 1700)
  const failed = new r.Context(), f = new r.Source(failed); f.start()
  failed.result = Promise.reject(Error('close rejected'))
  assert.equal(failed.close(), failed.result); await Promise.resolve()
  assert.equal(r.window.__ctxClosed[f.__acc.ctx], undefined)
})

test('JS build environment strips all credential names and keeps PATH/HOME', () => {
  const line = run.split('\n').find(l => l.startsWith('const buildEnv ='))
  const env = { PATH:'/fake/bin', HOME:'/fake/home', FISH_API_KEY:'fake', GLM_API_KEY:'fake',
    GH_TOKEN:'fake', GITHUB_TOKEN:'fake', HERMES_DASHBOARD_SESSION_TOKEN:'fake', OTHER_API_KEY:'fake', OTHER_TOKEN:'fake' }
  const result = vm.runInNewContext(`${line}; buildEnv()`, {process:{env}})
  assert.deepEqual(Object.keys(result).sort(), ['HOME','PATH'])
})

for (const host of ['upstream','evaos']) test(`no LLM: ${host} skips agent rows, retains dictation`, async () => {
  const start = run.indexOf('      const convWant ='), end = run.indexOf('\n    }\n    const after', start)
  const code = run.slice(start, end), rows = []
  const context = { process:{env:{}}, H:host, s:{}, host:{}, OUT:'', SHOTS:'', row:(...a)=>rows.push(a),
    want:()=>true, gw(){}, hostctl(){}, theme:'dark', shotPath(){},
    guard:async (label, fn)=>fn(), convRows:async ({want})=>{
      assert.equal(want('B-CONV-1'), false); assert.equal(want('B-EVAOS-CONV-1'), false)
      assert.equal(want('B-DICT-1'), true)
    } }
  await vm.runInNewContext(`(async()=>{${code}})()`, context)
  assert.equal(rows.length, host === 'upstream' ? 6 : 1)
  for (const [, status, reason] of rows) { assert.equal(status, 'skip'); assert.equal(reason, 'no LLM key') }
})

test('only-done signature requires actual sent done frame; silent-count intersection excludes played turns', () => {
  const lines = conv.split('\n').filter(l => l.startsWith('const onlyDone =') || l.startsWith('const brief =')).join('\n')
  const context = vm.createContext({})
  vm.runInContext(lines, context)
  const m = {speak_streams:1, spoken_text:'', speak_sent:['done'], played:false, statuses:['Speaking'], submits:1}
  context.m = m
  assert.equal(vm.runInContext('onlyDone(m)', context), true)
  assert.equal(vm.runInContext('brief(m, false).includes("no reply text")', context), false)
  assert.equal(vm.runInContext('brief(m, true).includes("no reply text")', context), true)
  m.speak_sent = []; assert.equal(vm.runInContext('onlyDone(m)', context), false)
  context.ms = [{...m, played:false}, {...m, played:true, speak_sent:['done']}]
  const predicate = conv.split('\n').find(l => l.includes('const noText = ms.filter'))
  assert.equal(vm.runInContext(`${predicate}; noText`, context), 0)
})

test('B-LIB-4 requires the Account card credit; a matching status chip cannot count', async () => {
  const start = run.indexOf("  if (want('B-LIB-4'))"), end = run.indexOf('\n}\n\n// --- main', start)
  for (const [credit, text, expected, pageText = text] of [[undefined,'NaN','fail'], [null,'0.00','fail'], ['NaN','NaN','fail'],
    ['Infinity','Infinity','fail'], ['1.2','1.2','fail'], ['1.2','1.20','pass'], ['1.2','unavailable','fail','Voices Fish $1.20']]) {
    const rows = [], c = {clickText:async()=>{}, sleep:async()=>{}, text:async()=>pageText, shot:async()=>''}
    await vm.runInNewContext(`(async()=>{${run.slice(start, end)}})()`, {
      want:()=>true, s:{page:{evaluate:async fn=>fn()}}, theme:'dark', account:{credit}, c, shotPath(){},
      document:{querySelectorAll:()=>[{textContent:'API credit', parentElement:{innerText:text}},
        {textContent:'Fish $1.20', parentElement:{innerText:pageText}}]},
      row:(id,status,reason)=>{ const r={metrics:{}}; rows.push({status,reason}); return r }
    })
    assert.equal(rows[0].status, expected)
    if (credit == null || !Number.isFinite(Number(credit))) assert.match(rows[0].reason, /missing or non-finite/)
  }
})

async function settingsRow({ host = 'upstream', theme = 'light', desktopRef = 'current', ...faults } = {}) {
  const start = run.indexOf('async function settingsPageRow('), end = run.indexOf('\nasync function paletteRow(', start)
  let value = 1, stored = 1, toast = false, reset = false
  const rows = [], shots = [], keys = [], nav = [], document = { activeElement: null }
  const element = { disabled: false, tabIndex: faults.focusable === false ? -1 : 0 }
  const output = { innerText: async () => faults.shownDefault && !keys.includes('Home') ? faults.shownDefault : `${value.toFixed(2)}×` }
  const speed = {
    waitFor: async () => { if (faults.missingControl) throw Error('no labelled Reading speed slider') },
    scrollIntoViewIfNeeded: async () => {}, inputValue: async () => String(value),
    locator: sel => { assert.equal(sel, '..'); return { locator: sel => { assert.equal(sel, 'output'); return output } } },
    focus: async () => { document.activeElement = element }, evaluate: async fn => fn(element)
  }
  const saved = { waitFor: async ({state}) => {
    if (state === 'hidden') toast = false
    else assert.equal(toast && faults.toast !== false, true, 'no fresh Reading speed saved notification')
  } }
  const page = { getByRole: (role, {name, exact}) => {
    assert.equal(exact, true)
    if (role === 'slider') { assert.equal(name, 'Reading speed'); return speed }
    assert.equal(role, 'button'); assert.equal(name, 'Reset reading speed')
    return { click: async () => { reset = true; value = 1; stored = faults.resetSpeed ?? 1; toast = true } }
  }, getByText: (text, {exact}) => { assert.equal(text, 'Reading speed saved'); assert.equal(exact, true); return saved },
  keyboard: { press: async key => {
    keys.push(key)
    if (key === 'Shift+Tab') document.activeElement = null
    else if (key === 'Tab') document.activeElement = faults.focusable === false ? null : element
    else {
      assert.equal(document.activeElement, element)
      if (key === 'Home') value = .5
      else { assert.equal(key, 'ArrowRight'); value = Math.round((value + .05) * 100) / 100 }
    }
  } } }
  const shotPath = run.split('\n').find(l => l.startsWith('const shotPath ='))
  await vm.runInNewContext(`${shotPath}\n${run.slice(start,end)}\nsettingsPageRow(s, theme)`, {
    s:{page}, theme, H:host, host:{desktop_ref:desktopRef, cache_dir:'/synthetic/cache', hermes_home:'/synthetic/home'},
    want:()=>!faults.unselected, document, path, SHOTS:'shots', buildEnv:()=>({}),
    c:{ page_escape:async()=>{}, nav:async(s, hash)=>nav.push(hash), sleep:async()=>{},
      text:async()=>faults.absentPage ? 'Settings' : 'Current voice Reading speed',
      clickText:async(s, text, {role})=>{ assert.equal(text, 'Save speed'); assert.equal(role, 'button'); stored = faults.savedSpeed ?? value; toast = true },
      shot:async(s, file)=>{ shots.push({file,value}); return file } },
    gw:async route=>{ assert.equal(route, '/api/plugins/fish-audio/settings'); return {status:200, body:{ok:true, speed:stored}} },
    execFileSync:(bin, args)=>{
      assert.equal(bin, '/synthetic/cache/.venv/bin/python'); assert.equal(args.at(-1), '/synthetic/home/config.yaml')
      assert.match(args[1], /"speed" not in/); assert.equal(reset, true)
      return JSON.stringify(!faults.retainedKey)
    }, row:(id,status,reason,extra)=>rows.push({id,status,reason,...extra})
  })
  return { rows, shots, keys, nav }
}

for (const host of ['upstream','evaos']) for (const theme of ['light','dark']) test(`B-SET-1: ${host}/${theme} keyboard save and reset`, async () => {
  const {rows, shots, keys, nav} = await settingsRow({host,theme})
  assert.equal(rows.length, 1); assert.equal(rows[0].id, 'B-SET-1'); assert.equal(rows[0].status, 'pass'); assert.equal(rows[0].theme, theme)
  assert.deepEqual({...rows[0].metrics}, {initial_speed:1, keyboard_focus:true, saved_speed:1.25, reset_speed:1, speed_key_removed:true})
  assert.deepEqual(nav, ['#/settings?tab=plugins&plugin=fish-audio'])
  assert.deepEqual(shots, [{file:`shots/B-SET-1-${theme}.png`,value:1}, {file:`shots/B-SET-1-saved-${theme}.png`,value:1.25}])
  assert.deepEqual(Array.from(rows[0].evidence), shots.map(s=>s.file))
  assert.deepEqual(keys, ['Shift+Tab','Tab','Home',...Array(15).fill('ArrowRight')])
})

test('B-SET-1 fails missing UI, keyboard, saved state, persistence and reset evidence', async () => {
  for (const [faults, reason] of [
    [{missingControl:true}, /no labelled/], [{shownDefault:'2.00×'}, /control\/default/],
    [{focusable:false}, /keyboard-focusable/], [{toast:false}, /no fresh/],
    [{savedSpeed:1}, /did not persist/], [{retainedKey:true}, /did not remove/], [{resetSpeed:1.25}, /did not remove/]
  ]) {
    const {rows} = await settingsRow(faults)
    assert.equal(rows.length, 1); assert.equal(rows[0].status, 'fail'); assert.match(rows[0].reason, reason)
  }
  const {rows} = await settingsRow({host:'evaos', absentPage:true, missingControl:true, desktopRef:'v2026.10.14-es.20'})
  assert.equal(rows[0].status, 'na'); assert.match(rows[0].reason, /no Settings ▸ Plugins page/)
  assert.equal((await settingsRow({host:'evaos', absentPage:true, missingControl:true})).rows[0].status, 'fail')
  const unselected = await settingsRow({unselected:true})
  assert.deepEqual(unselected.rows, []); assert.deepEqual(unselected.nav, [])
})

test('B-SET-1 alone enters the keyed phase and runs in both themes without library rows', async () => {
  const start = run.indexOf('async function main()'), end = run.indexOf('\nasync function guard', start)
  const themes = [], commands = [], meta = {}
  let keyed = false
  await vm.runInNewContext(`${run.slice(start,end)}\nmain()`, {
    fs:{mkdirSync(){}, existsSync:()=>false}, path, SHOTS:'shots', OUT:'synthetic', CDP:9281, VITE:5194, H:'upstream', host:{}, args:{'no-launch':true},
    THEMES:['light','dark'], process:{env:{}}, want:id=>id === 'B-SET-1', buildTree:()=>'/synthetic/tree',
    hostctl:cmd=>{ commands.push(cmd); if (cmd.endsWith('-key')) keyed = cmd === 'set-real-key' },
    waitGateway:async()=>{}, reloadRenderer:async()=>{}, setup:async()=>{},
    c:{connect:async()=>({page:{evaluate:async()=>true}}), setTheme:async()=>{}, sleep:async()=>{}},
    guard:async(label,fn)=>{ await fn(); return true }, noKeyRows:async()=>{}, pluginPageRow:async()=>{},
    paletteRow:async()=>{}, keyEntryRow:async()=>{}, convRows:async()=>{},
    settingsPageRow:async(s,theme)=>{ assert.equal(keyed, true); themes.push(theme) },
    libraryRows:()=>assert.fail('B-SET-1 does not require the Library'), shotPath(){},
    gw:async()=>({body:{credit:'1.00'}}), row:()=>assert.fail('unexpected row'),
    finish:async result=>Object.assign(meta,result)
  })
  assert.deepEqual(themes, ['light','dark']); assert.deepEqual(commands, ['clear-key','set-real-key','clear-key'])
  assert.deepEqual({...meta.wallet}, {before:'1.00',after:'1.00'})
  const spec = JSON.parse(fs.readFileSync(new URL('./rows.yaml', import.meta.url))).find(r=>r.id === 'B-SET-1')
  const version = fs.readFileSync(new URL('../plugin.yaml', import.meta.url), 'utf8').match(/^version:\s*(.+)$/m)[1]
  assert.equal(spec.min_plugin, version); assert.equal(spec.billed, 'none'); assert.equal(spec.layer, 'b')
  assert.equal(spec.required, true); assert.deepEqual(spec.hosts, ['upstream','evaos'])
})

async function forcedFailureRow(m, { requests = [], media = [], toast = [], end = () => {}, sleep = () => {} } = {}) {
  const start = conv.indexOf('    const playbackEnd =', conv.indexOf("  if (want('B-CONV-5'))"))
  const stop = conv.indexOf('\n  }', start)
  const onlyDone = conv.split('\n').find(l => l.startsWith('const onlyDone ='))
  const rows = []
  await vm.runInNewContext(`(async()=>{${onlyDone}\n${conv.slice(start,stop)}})()`, {
    m, rec:{speakRequests:requests, timeOrigin:0}, t0:0, theme:'dark', s:{page:{evaluate:async fn=>fn()}},
    window:{__toasts:toast.map(text=>({t:1,text}))}, performance:{timeOrigin:0, now:()=>1000},
    endConversation:async()=>end(), pageState:async()=>({media}),
    c:{shot:async()=>'', sleep:async()=>sleep()}, shotPath(){},
    row:(id,status,reason)=>{const r = {status,reason}; rows.push(r); return r} })
  return rows[0]
}

test('B-CONV-5 requires an injected failure and waives only zero-request host failures', async t => {
  const waiver = new RegExp(JSON.parse(fs.readFileSync(new URL('./rows.yaml', import.meta.url)))
    .find(r => r.id === 'B-CONV-5').known_host_bugs[0].match)
  for (const [frames, spoken, pcm, toast, status, waived, requests = 0, successes = 0, played = false] of [
    [['start','end'], 'reply', 3, [], 'fail', false],
    [['fallback'], 'reply', 3, [], 'pass', false],
    [['error'], 'reply', 3, [], 'pass', false],
    [[], 'reply', 0, ['Speech failed'], 'pass', false],
    [['start','end'], 'reply', 3, ['Saved'], 'fail', false],
    [['error'], '', 0, [], 'fail', true],
    [['fallback'], 'reply', 0, [], 'fail', true],
    [['start','end'], '', 0, [], 'fail', true],
    [['fallback'], 'reply', 0, [], 'fail', false, 1, 1],
    [['fallback'], 'reply', 0, ['Speech failed'], 'fail', false, 1, 1],
    [['fallback'], 'reply', 0, ['Speech failed'], 'pass', false, 1, 0],
    [['fallback'], 'reply', 0, [], 'pass', false, 1, 1, true],
    [[], '', 0, [], 'fail', false]]) await t.test(JSON.stringify({frames, spoken, pcm, toast}), async () => {
    const m = {submits:1, spoken_text:spoken, speak_frames:frames, speak_sent:['done'],
      speak_streams:1, pcm_frames:pcm, media_plays:played ? 1 : 0, audio_buffers:0, played,
      fallback_request_index:frames.includes('fallback') ? 0 : null, speaking_at:100, media_start_index:0,
      speak_requests_after_fallback:requests, speak_2xx_after_fallback:successes}
    const r = await forcedFailureRow(m, {toast, requests:Array.from({length:requests}, (_,i)=>({status:i < successes ? 200 : 500})), media:played ? [{playedAt:200}] : []})
    assert.equal(r.status, status)
    assert.equal(waiver.test(r.reason), waived, r.reason)
    assert.match(r.reason, new RegExp(`POST /api/audio/speak after fallback ${requests}$`))
    if (successes && !played) assert.match(r.reason, /whole-text 2xx without reply playback/)
    else if (status === 'fail' && !waived && spoken) assert.match(r.reason, /forced failure did not take effect/)
    if (!spoken) assert.match(r.reason, /Desktop sent no reply text to speak-stream/)
  })
})

test('B-CONV-5 recounts late requests/responses and only pre-end whole-text playback', async t => {
  const waiver = new RegExp(JSON.parse(fs.readFileSync(new URL('./rows.yaml', import.meta.url)))
    .find(r => r.id === 'B-CONV-5').known_host_bugs[0].match)
  for (const kind of ['late request', 'late response', 'late playback', 'end earcon']) await t.test(kind, async () => {
    const pending = kind === 'late response', requests = [{status:500}, ...(pending ? [{status:null}] : [])]
    const m = {submits:1, spoken_text:'reply', speak_frames:['fallback'], speak_sent:['text','done'],
      speak_streams:1, pcm_frames:0, media_plays:0, audio_buffers:0, played:false,
      fallback_request_index:1, speaking_at:100, media_start_index:1,
      speak_requests_after_fallback:pending ? 1 : 0, speak_2xx_after_fallback:0}
    const media = [{playedAt:50}, ...(kind === 'late playback' ? [{playedAt:900}] : [])]
    let polls = 0
    const r = await forcedFailureRow(m, {requests, media,
      end:()=>{if (!pending) requests.push({status:200}); if (kind === 'end earcon') media.push({playedAt:1000})},
      sleep:()=>{polls++; requests.at(-1).status = 200}})
    assert.equal(m.speak_requests_at_turn_end, pending ? 1 : 0)
    assert.equal(m.speak_2xx_at_turn_end, 0)
    assert.equal(m.speak_requests_after_fallback, 1); assert.equal(m.speak_2xx_after_fallback, 1)
    assert.equal(polls, pending ? 1 : 0)
    assert.equal(r.status, kind === 'late playback' ? 'pass' : 'fail')
    assert.equal(r.reason.includes('whole-text 2xx without reply playback'), kind !== 'late playback')
    assert.match(r.reason, /POST \/api\/audio\/speak after fallback 1$/)
    assert.equal(waiver.test(r.reason), false)
  })
})

test('CDP counts only this turn POST speak requests after fallback and records 2xx', async () => {
  const recorder = conv.slice(conv.indexOf('async function recorder'), conv.indexOf('\nconst pageState'))
  const turn = conv.slice(conv.indexOf('async function turn'), conv.indexOf('\nconst seq'))
  const handlers = new Map(), send = (event, data) => handlers.get(`Network.${event}`)(data)
  let now = 0, inject = () => {}
  const s = { cdp:{send:async()=>{}, on:(event, fn)=>handlers.set(event, fn)},
    page:{evaluate:async()=>{ inject(); return 0 }} }
  const context = vm.createContext({s, URL, Date:{now:()=>++now},
    pageState:async()=>({st:[], audio:[], media:[], users:['prompt'], replies:['reply']}),
    waitStatus:async()=>{}, utter:()=>({b64:'synthetic', speechMs:0})})
  const rec = await vm.runInContext(`${recorder}\nrecorder(s)`, context)
  const request = (id, method = 'POST', url = 'http://127.0.0.1:9361/api/audio/speak') =>
    send('requestWillBeSent', {requestId:id, request:{method,url}})
  request('old-turn')
  inject = () => {
    request('before-fallback')
    send('webSocketCreated', {requestId:'ws', url:'ws://127.0.0.1:9361/api/audio/speak-stream'})
    send('webSocketFrameReceived', {requestId:'ws', response:{opcode:1, payloadData:'{"type":"fallback"}'}})
    request('get', 'GET'); request('other', 'POST', 'http://127.0.0.1:9361/api/audio/speak-stream')
    request('success'); request('failure')
    send('responseReceived', {requestId:'success', response:{status:200}})
    send('responseReceived', {requestId:'failure', response:{status:500}})
  }
  context.rec = rec
  const metrics = await vm.runInContext(`${turn}\nturn(s, rec, '', '', {timeout:0})`, context)
  assert.equal(metrics.speak_requests_after_fallback, 2)
  assert.equal(metrics.speak_2xx_after_fallback, 1)
  assert.equal(metrics.fallback_request_index, 2)
  assert.equal(rec.speakRequests.length, 4)
})

test('clear-key restart failure aborts before gateway wait or Desktop launch', async () => {
  const start = run.indexOf('async function main()'), end = run.indexOf('\nasync function guard', start)
  let waited = false, launched = false
  const context = {fs:{mkdirSync(){}, existsSync:()=>false}, path, SHOTS:'synthetic', H:'upstream',
    host:{}, args:{}, buildTree:()=>'/synthetic/tree',
    hostctl:()=>{ throw Error('hostctl restart-gateway failed') },
    waitGateway:async()=>{ waited = true }, launch:()=>{ launched = true }}
  await assert.rejects(vm.runInNewContext(`${run.slice(start,end)}\nmain()`, context),
    /clear-key gateway restart failed; aborting before Desktop launch: hostctl restart-gateway failed/)
  assert.equal(waited, false); assert.equal(launched, false)
})

test('B-CONV-5 edits only plugin transport settings and restores exact original bytes on error', async t => {
  const py = process.env.TEST_PYTHON || 'python3'
  try { execFileSync(py, ['-c', 'import yaml'], { stdio: 'ignore' }) } catch { t.skip('needs a Python with PyYAML: set TEST_PYTHON'); return }
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'fish-config-test-')), cfg = path.join(dir,'config.yaml')
  const original = Buffer.from('# comment\r\nplugins:\r\n  enabled: [fish-audio]\r\ntts:\r\n  fish-audio:\r\n    base_url: https://unrelated.invalid\r\n')
  const start = conv.indexOf("    const cfg =", conv.indexOf("  if (want('B-CONV-5'))"))
  const end = conv.indexOf('\n    await endConversation', start)
  try {
    fs.writeFileSync(cfg, original)
    await assert.rejects(vm.runInNewContext(`(async()=>{${conv.slice(start,end)}})()`, {
      fs, path, process, host:{hermes_home:dir, cache_dir:dir}, s:{}, rec:{}, dir, PROMPTS:['','','','fake'],
      startConversation:async()=>true,
      execFileSync:(bin,args,opts)=>execFileSync(py,args,opts),
      turn:async (s,rec,dir,text,{onThinking})=>{
        onThinking()
        const check = execFileSync(py, ['-c', 'import sys,yaml; c=yaml.safe_load(open(sys.argv[1])); assert c["plugins"]["entries"]["fish-audio"]["settings"]["base_url"].endswith("/acceptance-forced-failure"); assert c["tts"]["fish-audio"]["base_url"]=="https://unrelated.invalid"', cfg])
        throw Error('synthetic turn failure')
      }
    }), /synthetic turn failure/)
    assert.deepEqual(fs.readFileSync(cfg), original)
  } finally { fs.rmSync(dir, {recursive:true, force:true}) }
})
