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

test('B-CONV-5 requires an injected failure and preserves both host-bug waiver reasons', async t => {
  const start = conv.indexOf('    const toasts =', conv.indexOf("  if (want('B-CONV-5'))"))
  const end = conv.indexOf('\n  }', start)
  const onlyDone = conv.split('\n').find(l => l.startsWith('const onlyDone ='))
  const waiver = new RegExp(JSON.parse(fs.readFileSync(new URL('./rows.yaml', import.meta.url)))
    .find(r => r.id === 'B-CONV-5').known_host_bugs[0].match)
  for (const [frames, spoken, pcm, toast, status, waived] of [
    [['start','end'], 'reply', 3, [], 'fail', false],
    [['fallback'], 'reply', 3, [], 'pass', false],
    [['error'], 'reply', 3, [], 'pass', false],
    [[], 'reply', 0, ['Speech failed'], 'pass', false],
    [['start','end'], 'reply', 3, ['Saved'], 'fail', false],
    [['error'], '', 0, [], 'fail', true],
    [['fallback'], 'reply', 0, [], 'fail', true],
    [['start','end'], '', 0, [], 'fail', true],
    [[], '', 0, [], 'fail', false]]) await t.test(JSON.stringify({frames, spoken, pcm, toast}), async () => {
    const rows = [], m = {submits:1, spoken_text:spoken, speak_frames:frames, speak_sent:['done'],
      speak_streams:1, pcm_frames:pcm, media_plays:0, audio_buffers:0}
    await vm.runInNewContext(`(async()=>{${onlyDone}\n${conv.slice(start,end)}})()`, {
      m, t0:0, theme:'dark', s:{page:{evaluate:async()=>toast.map(text=>({t:1,text}))}},
      c:{shot:async()=>''}, shotPath(){}, row:(id,status,reason)=>{rows.push({status,reason}); return {}} })
    assert.equal(rows[0].status, status)
    assert.equal(waiver.test(rows[0].reason), waived, rows[0].reason)
    if (status === 'fail' && !waived && spoken) assert.match(rows[0].reason, /forced failure did not take effect/)
    if (!spoken) assert.match(rows[0].reason, /Desktop sent no reply text to speak-stream/)
  })
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
