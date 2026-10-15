// Layer B conversation rows over the CDP mic shim (understand-live-mode.md §6.2 steps 3-7): B-CONV-1..5, B-DICT-1
// on upstream, B-EVAOS-CONV-1 on the evaOS dev build. Utterances come from macOS `say` → ffmpeg 48 kHz mono WAV.
import { execFileSync } from 'node:child_process'
import crypto from 'node:crypto'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import * as c from './cdp.mjs'

const PROMPTS = [
  'In one short sentence, what is the capital of France?',
  'In one short sentence, name a colour of the sky.',
  'In one short sentence, how many legs does a spider have?',
  'In one short sentence, what do bees make?',
  'In one short sentence, what is two plus three?',
  'In one short sentence, which planet is closest to the sun?'
]
const LONG = 'Tell me a calm story about a lighthouse keeper in at least eight sentences.'
// No leading stop word: Desktop ends the conversation when a barge clip transcribes to a stop phrase
// (lib/voice-stop-word.ts). The earlier text began with "Stop." (kept in the run evidence).
const BARGE = 'Actually, in one short sentence, what is the opposite of hot?'
const DICTATE = 'This is a dictation test about green apples.'
const STATUS = /^(Listening|Transcribing|Thinking|Speaking|Muted)$/
const norm = t => String(t || '').toLowerCase().replace(/[`*_#>~\[\]()]/g, ' ').replace(/[^a-z0-9' ]+/g, ' ').replace(/\s+/g, ' ').trim()
const overlap = (a, b) => { const A = norm(a).split(' ').filter(Boolean), B = new Set(norm(b).split(' ')); return A.length ? A.filter(w => B.has(w)).length / A.length : 0 }

function utter(dir, text) {
  fs.mkdirSync(dir, { recursive: true })
  const f = path.join(dir, crypto.createHash('sha256').update(text).digest('hex').slice(0, 12) + '.wav')
  if (!fs.existsSync(f)) {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'fa-utt-')); const aiff = path.join(tmp, 'u.aiff')
    try { execFileSync('say', ['-v', 'Samantha', '-o', aiff, text]) } catch { execFileSync('say', ['-o', aiff, text]) }
    execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-i', aiff, '-af', 'apad=pad_dur=1.5', '-ar', '48000', '-ac', '1', '-c:a', 'pcm_s16le', f])
    fs.rmSync(tmp, { recursive: true, force: true })
  }
  const buf = fs.readFileSync(f)
  return { b64: buf.toString('base64'), speechMs: Math.round(((buf.length - 44) / 96000) * 1000) - 1500, file: f }
}

// One recorder per conversation session: WS frames (CDP Network), statuses, streaming marker, toasts.
async function recorder(s) {
  const frames = [], sockets = new Map(), closed = []
  await s.cdp.send('Network.enable')
  s.cdp.on('Network.webSocketCreated', e => sockets.set(e.requestId, { url: e.url, t: Date.now() }))
  s.cdp.on('Network.webSocketClosed', e => closed.push({ id: e.requestId, url: sockets.get(e.requestId)?.url, t: Date.now() }))
  const onFrame = dir => e => {
    const url = sockets.get(e.requestId)?.url || ''
    if (!/speak-stream|transcribe-stream/.test(url)) return
    const f = { t: Date.now(), dir, id: e.requestId, kind: url.includes('speak-stream') ? 'speak' : 'transcribe', op: e.response.opcode }
    if (e.response.opcode === 1) { try { f.json = JSON.parse(e.response.payloadData) } catch { f.text = e.response.payloadData.slice(0, 200) } } else f.len = Math.round(e.response.payloadData.length * 3 / 4)
    frames.push(f)
  }
  s.cdp.on('Network.webSocketFrameSent', onFrame('sent'))
  s.cdp.on('Network.webSocketFrameReceived', onFrame('recv'))
  await s.page.evaluate(() => {
    clearInterval(window.__stIv); window.__st = []; window.__toasts = []; let last = '', lastStream = null
    window.__stIv = setInterval(() => {
      const v = [...document.querySelectorAll('.sr-only[role=status]')].map(e => e.textContent.trim()).find(x => /^(Listening|Transcribing|Thinking|Speaking|Muted)$/.test(x)) || ''
      if (v !== last) { window.__st.push({ t: Date.now(), v }); last = v }
      const st = !!document.querySelector('[data-message-streaming="true"]')
      if (st !== lastStream) { window.__st.push({ t: Date.now(), v: st ? '(reply streaming)' : '(reply done)' }); lastStream = st }
    }, 30)
    // Toasts: the app's notification region (role=region > role=alert|status); record each new text once.
    const seen = new Set()
    window.__toastIv = (clearInterval(window.__toastIv), setInterval(() => {
      document.querySelectorAll('[role=region] [role=alert], [role=region] [role=status]').forEach(e => {
        const t = (e.innerText || '').trim(); if (t && !seen.has(t)) { seen.add(t); window.__toasts.push({ t: Date.now(), text: t.slice(0, 200) }) }
      })
    }, 100))
  })
  const timeOrigin = await s.page.evaluate(() => performance.timeOrigin)
  return { frames, sockets, closed, timeOrigin }
}

const pageState = s => s.page.evaluate(() => ({
  st: window.__st.slice(), audio: (window.__audioLog || []).slice(), media: (window.__mediaLog || []).slice(),
  users: [...document.querySelectorAll('[data-role=user]')].map(e => (e.querySelector('[data-slot=aui_user-message-text]') || e).innerText.trim()),
  replies: [...document.querySelectorAll('[data-role=assistant] [data-slot=aui_assistant-message-content]')].map(e => {
    const x = e.cloneNode(true); x.querySelectorAll('[data-slot=aui_thinking-disclosure]').forEach(n => n.remove()); return (x.innerText || x.textContent).trim()
  })
}))
async function waitStatus(s, re, ms) {
  const t0 = Date.now()
  while (Date.now() - t0 < ms) { const v = (await s.page.evaluate(() => window.__st.filter(x => !x.v.startsWith('(')).at(-1)?.v)) || ''; if (re.test(v)) return v; await c.sleep(100) }
  return null
}

// Speak one utterance and wait for the turn to finish (Speaking → Listening, or a quiet return to Listening).
async function turn(s, rec, dir, text, { timeout = 90000, onSpeaking, onThinking } = {}) {
  const before = await pageState(s), f0 = rec.frames.length, sock0 = rec.sockets.size
  await waitStatus(s, /^Listening$/, 20000)
  const u = utter(dir, text)
  const tSay = Date.now()
  await s.page.evaluate(b => window.__mic.say(b), u.b64)
  const speechEnd = tSay + u.speechMs
  let st = [], spoke = false, fired = false, firedT = false
  const t0 = Date.now()
  while (Date.now() - t0 < timeout) {
    st = (await s.page.evaluate(() => window.__st)).slice(before.st.length)
    const real = st.filter(x => !x.v.startsWith('('))
    spoke = real.some(x => x.v === 'Speaking')
    if (onThinking && !firedT && real.some(x => x.v === 'Thinking')) { firedT = true; await onThinking() }
    if (spoke && onSpeaking && !fired) { fired = true; await onSpeaking() }
    if (spoke && real.at(-1)?.v === 'Listening') break
    if (!spoke && real.some(x => x.v === 'Thinking') && real.at(-1)?.v === 'Listening' && Date.now() - real.at(-1).t > 8000) break
    await c.sleep(150)
  }
  const after = await pageState(s)
  const frames = rec.frames.slice(f0)
  const speakSent = frames.filter(f => f.kind === 'speak' && f.dir === 'sent' && f.json)
  const speakRecv = frames.filter(f => f.kind === 'speak' && f.dir === 'recv')
  const at = v => st.find(x => x.v === v)?.t
  // Reply audio only, scoped to this turn: sources started (or media played) from this turn's Speaking status, which
  // follows its submit, until the Listening that ends the turn. UI earcons play outside that window.
  const tSpeak = at('Speaking') ?? Infinity
  const realEnd = st.filter(x => !x.v.startsWith('(')).at(-1)
  const tEnd = spoke && realEnd?.v === 'Listening' ? realEnd.t : Infinity
  const inTurn = a => a.t >= tSpeak - 100 && a.t < tEnd
  const audio = after.audio.slice(before.audio.length).filter(a => a.playedAt != null).map(a => ({ t: rec.timeOrigin + a.playedAt, d: a.d })).filter(inTurn)
  const media = after.media.slice(before.media.length).filter(m => m.playedAt != null).map(m => ({ t: rec.timeOrigin + m.playedAt, d: m.d })).filter(inTurn)
  const firstAudio = Math.min(...[...audio, ...media].map(a => a.t), Infinity)
  const replyDone = st.filter(x => x.v === '(reply done)' && x.t <= firstAudio).at(-1)?.t
  const startFrame = speakRecv.find(f => f.json?.type === 'start')
  const m = {
    statuses: st.filter(x => !x.v.startsWith('(')).map(x => x.v), timeline: st.map(x => `${x.t - speechEnd}ms ${x.v}`),
    submits: after.users.length - before.users.length, user_text: after.users.at(-1) || null, reply: after.replies.at(-1) || null,
    speak_streams: [...rec.sockets.values()].slice(sock0).filter(x => x.url.includes('speak-stream')).length,
    pcm_frames: speakRecv.filter(f => f.op === 2).length, speak_frames: speakRecv.filter(f => f.json).map(f => f.json.type),
    spoken_text: speakSent.map(f => f.json.text || '').join(''), speak_sent: speakSent.map(f => Object.keys(f.json).join('+')), audio_buffers: audio.length, media_plays: media.length, played: audio.length + media.length > 0,
    vad_end_to_transcribing_ms: at('Transcribing') && at('Transcribing') - speechEnd,
    vad_end_to_thinking_ms: at('Thinking') && at('Thinking') - speechEnd,
    vad_end_to_first_delta_ms: at('(reply streaming)') && at('(reply streaming)') - speechEnd,
    vad_end_to_speak_start_ms: startFrame && startFrame.t - speechEnd,
    vad_end_to_first_audio_ms: isFinite(firstAudio) ? Math.round(firstAudio - speechEnd) : null,
    reply_done_to_first_audio_ms: replyDone && isFinite(firstAudio) ? Math.round(firstAudio - replyDone) : null,
    speaking_to_first_audio_ms: isFinite(tSpeak) && isFinite(firstAudio) ? Math.round(firstAudio - tSpeak) : null,
    buffer_gaps_ms: audio.slice(1).map((a, i) => Math.round(a.t - audio[i].t - (audio[i].d || 0) * 1000)).filter(g => g > 50),
    playback_end_to_listening_ms: null
  }
  const lastA = audio.at(-1), lis = st.filter(x => x.v === 'Listening').at(-1)
  if (lastA && lis && lis.t > lastA.t) m.playback_end_to_listening_ms = Math.round(lis.t - (lastA.t + (lastA.d || 0) * 1000))
  return m
}
const seq = (statuses, want) => { let i = 0; for (const v of statuses) if (v === want[i]) i++; return i === want.length }
const onlyDone = m => m.speak_streams > 0 && !m.spoken_text && JSON.stringify(m.speak_sent) === '["done"]'
const brief = (m, valid = false) => `statuses ${m.statuses.join('→')}; submits ${m.submits}; PCM frames ${m.pcm_frames}; VAD end→first audio ${m.vad_end_to_first_audio_ms} ms${valid && onlyDone(m) ? '; Desktop sent speak-stream only ' + JSON.stringify(m.speak_sent) + ' (no reply text)' : ''}`

// Desktop speaks conversation replies only with "Read replies aloud" on (it mirrors voice.auto_tts). Turn it on
// through the composer's own voice fan, as a user would; returns what was done.
async function ensureReadAloud(s) {
  await c.page_escape(s); await c.nav(s, '#/'); await c.sleep(800)
  await s.page.locator('[aria-label="Voice dictation"]').first().hover().catch(() => {}); await c.sleep(700)
  const on = s.page.locator('[aria-label="Read replies aloud"]')
  if (await on.count()) { await on.first().click(); await c.sleep(500); return 'turned on by the harness (voice fan)' }
  return (await s.page.locator('[aria-label="Stop reading replies aloud"]').count()) ? 'already on' : 'toggle not found'
}

async function startConversation(s) {
  await c.page_escape(s)
  await c.nav(s, '#/'); await c.sleep(800)
  await s.page.locator('[aria-label="Start voice conversation"]').first().click({ timeout: 10000 })
  return waitStatus(s, /^Listening$/, 20000)
}
const endConversation = s => s.page.locator('[aria-label="End voice conversation"]').first().click({ timeout: 5000 }).catch(() => {})

export async function convRows({ s, host, OUT, row, want, theme, shotPath, gw }) {
  const dir = path.join(OUT, 'utterances')
  if (host.host === 'evaos') {
    if (!want('B-EVAOS-CONV-1')) return
    const rec = await recorder(s)
    const readAloud = await ensureReadAloud(s)
    if (!(await startConversation(s))) { row('B-EVAOS-CONV-1', 'fail', 'conversation never reached Listening', { theme, evidence: [await c.shot(s, shotPath('B-EVAOS-CONV-1', theme))] }); return }
    const m = await turn(s, rec, dir, PROMPTS[0], { onSpeaking: async () => { await c.sleep(400); await c.shot(s, shotPath('B-EVAOS-CONV-1', theme, 'speaking')) } })
    await endConversation(s)
    const spoken = m.audio_buffers > 0 || m.media_plays > 0
    const r = row('B-EVAOS-CONV-1', spoken ? 'pass' : 'fail', `${spoken ? 'reply spoken' : 'reply NOT spoken'}; speak-stream frames ${m.speak_frames.join(',') || 'none'}; dead air reply done→first audio ${m.reply_done_to_first_audio_ms} ms (Speaking→first audio ${m.speaking_to_first_audio_ms} ms); ${brief(m)} (advisory F3)`, { theme, advisory: true })
    r.metrics = { ...m, read_aloud: readAloud }; r.evidence.push(shotPath('B-EVAOS-CONV-1', theme, 'speaking'), await c.shot(s, shotPath('B-EVAOS-CONV-1', theme)))
    return
  }
  const rec = await recorder(s)
  if (['B-CONV-1', 'B-CONV-2', 'B-CONV-3', 'B-CONV-3L', 'B-CONV-4', 'B-CONV-5'].some(want)) {
  const readAloud = await ensureReadAloud(s)
  // B-CONV-1 + B-CONV-4: one turn.
  if (!(await startConversation(s))) { row('B-CONV-1', 'fail', 'conversation never reached Listening (mic gate or shim)', { theme, evidence: [await c.shot(s, shotPath('B-CONV-1', theme))] }); return }
  const m1 = await turn(s, rec, dir, PROMPTS[0], { onSpeaking: async () => { await c.sleep(300); await c.shot(s, shotPath('B-CONV-1', theme, 'speaking')) } })
  const ok1 = seq(m1.statuses, ['Listening', 'Transcribing', 'Thinking', 'Speaking', 'Listening']) || seq(m1.statuses, ['Transcribing', 'Thinking', 'Speaking', 'Listening'])
  const match1 = overlap(PROMPTS[0], m1.user_text)
  const valid1 = ok1 && match1 >= 0.8 && m1.submits === 1
  if (want('B-CONV-1')) {
    // Received PCM is not enough: the reply must have been played (sources started or media played in this turn).
    const pass = valid1 && m1.pcm_frames >= 1 && m1.played
    const r = row('B-CONV-1', pass ? 'pass' : 'fail', `${brief(m1, valid1)}; reply playback ${m1.audio_buffers} buffers/${m1.media_plays} plays; user text match ${(match1 * 100).toFixed(0)}% ("${m1.user_text}")`, { theme })
    r.metrics = { ...m1, read_aloud: readAloud }; r.evidence.push(shotPath('B-CONV-1', theme, 'speaking'), await c.shot(s, shotPath('B-CONV-1', theme)))
  }
  if (want('B-CONV-4')) {
    const a = norm(m1.spoken_text), b = norm(m1.reply)
    // Equality of the text sent to speak-stream only counts for a turn whose reply was actually played.
    const r = row('B-CONV-4', valid1 && a && a === b && m1.played && m1.pcm_frames >= 1 ? 'pass' : 'fail', a && a === b ? `spoken text == reply (${b.length} chars, normalised)${m1.played ? '' : ', but the turn played no reply audio'}` : `${valid1 && b ? 'valid turn; ' : ''}spoken ${JSON.stringify(m1.spoken_text.slice(0, 120))} vs reply ${JSON.stringify((m1.reply || '').slice(0, 120))}`, { theme })
    r.metrics = { spoken: m1.spoken_text, reply: m1.reply, played: m1.played }
  }
  // B-CONV-2: five more turns in the same conversation.
  if (want('B-CONV-2')) {
    const ms = []
    for (const p of PROMPTS.slice(1)) ms.push(await turn(s, rec, dir, p))
    const submits = ms.reduce((x, m) => x + m.submits, 0)
    const stuck = ms.filter(m => m.statuses.at(-1) !== 'Listening').length
    const texts = ms.map(m => norm(m.spoken_text)).filter(Boolean)
    const respoken = texts.length - new Set(texts).size + ms.filter(m => m.speak_streams > 1).length
    const silent = ms.filter(m => !m.played).length // no reply playback in that turn
    const pass = submits === 5 && !stuck && !respoken && !silent
    const noText = ms.filter(m => !m.played && onlyDone(m)).length
    const r = row('B-CONV-2', pass ? 'pass' : 'fail', `5 turns: submits ${submits}, stuck ${stuck}, re-spoken ${respoken}, silent ${silent} (${noText} with no reply text sent to speak-stream); first-audio ms ${ms.map(m => m.vad_end_to_first_audio_ms).join('/')}`, { theme })
    r.metrics = { turns: ms.map(m => ({ statuses: m.statuses, submits: m.submits, first_audio_ms: m.vad_end_to_first_audio_ms, user: m.user_text, pcm: m.pcm_frames, buffers: m.audio_buffers, plays: m.media_plays, sent: m.speak_sent, timeline: m.timeline })) }
    r.evidence.push(await c.shot(s, shotPath('B-CONV-2', theme)))
  }
  // B-CONV-3: barge-in 1.5 s after the first audio of a long reply.
  if (want('B-CONV-3') || want('B-CONV-3L')) {
    // Barge captures transcribe through window.hermesDesktop.api('/api/audio/transcribe') in the main process,
    // which CDP Network cannot see; wrap the bridge call to record request size and the returned transcript.
    const txWrap = await s.page.evaluate(() => {
      window.__tx = []
      if (window.__apiWrapped) return window.__apiWrapped
      try {
        const hd = window.hermesDesktop, orig = hd.api.bind(hd)
        const wrapped = async req => {
          const e = String(req?.path || '').includes('transcri') ? { t: Date.now(), path: req.path, data_url_chars: String(req.body?.data_url || '').length } : null
          if (e) window.__tx.push(e)
          try { const r = await orig(req); if (e) { e.ms = Date.now() - e.t; e.ok = r?.ok; e.provider = r?.provider; e.transcript = r?.transcript } return r }
          catch (err) { if (e) { e.ms = Date.now() - e.t; e.error = String(err?.message || err).slice(0, 300) } throw err }
        }
        hd.api = wrapped
        window.__apiWrapped = hd.api === wrapped ? 'wrapped' : 'bridge not writable'
      } catch (err) { window.__apiWrapped = 'wrap error: ' + err.message }
      return window.__apiWrapped
    })
    let barge = null, usersAtBarge = null, tBpage = null, tBwall = null, tBperf = null
    // Only this turn's playback counts: sources logged after this index belong to the long reply (or later).
    const nLong = await s.page.evaluate(() => (window.__audioLog || []).length)
    const m = await turn(s, rec, dir, LONG, {
      timeout: 120000,
      onSpeaking: async () => {
        const t0 = Date.now()
        while (Date.now() - t0 < 15000 && !(await s.page.evaluate(n => (window.__audioLog || []).length > n, nLong))) await c.sleep(50)
        await c.sleep(1500)
        const n0 = (await s.page.evaluate(() => window.__audioLog.length)), c0 = rec.closed.length
        usersAtBarge = (await pageState(s)).users
        const u = utter(dir, BARGE); const tB = Date.now(); tBwall = tB
        ;({ wall: tBpage, perf: tBperf } = await s.page.evaluate(() => ({ wall: Date.now(), perf: performance.now() })))
        s.page.evaluate(b => window.__mic.say(b), u.b64).catch(() => {})
        let cut = null, wsClosed = null
        while (Date.now() - tB < 6000 && cut == null) {
          const v = await s.page.evaluate(() => window.__st.filter(x => !x.v.startsWith('(')).at(-1))
          if (v && v.t > tB && v.v !== 'Speaking') cut = v.t - tB
          wsClosed = rec.closed.slice(c0).find(x => x.url?.includes('speak-stream'))
          await c.sleep(20)
        }
        await c.sleep(800)
        const late = (await s.page.evaluate(n => window.__audioLog.slice(n).map(a => a.t), n0)).map(t => rec.timeOrigin + t).filter(t => t > tB + (cut ?? 0) + 300)
        // Cancellation of what was playing: the long reply's sources active at onset (started, not yet ended or
        // stopped) and when each stopped or ended. Judged 2 s after onset, past the 1.5 s budget.
        const rest = 2000 - (Date.now() - tB); if (rest > 0) await c.sleep(rest)
        const src = await s.page.evaluate(({ n, tBperf, tBpage }) => {
          const off = Date.now() - performance.now()
          const think = window.__st.filter(x => x.v === 'Thinking' && x.t < tBpage).at(-1)?.t
          const tSub = think != null ? think - off : null
          // A source is silenced by its ended event, its stop() (at the stop's scheduled time) or its context's close().
          const end = e => Math.min(e.endedAt ?? Infinity, e.stopAt != null ? Math.max(e.stopAt, e.stopSched ?? e.stopAt) : Infinity, window.__ctxClosed?.[e.ctx] ?? Infinity)
          const active = window.__audioLog.slice(n).filter(e => e.t < tBperf && end(e) > tBperf)
          const playing = active.filter(e => window.__audioPlaying(e, tBperf) && (tSub == null || e.t >= tSub))
          return { submit_to_onset_ms: tSub != null ? Math.round(tBperf - tSub) : null, active_at_onset: active.length, playing_at_onset: playing.length,
            stop_ms: active.map(e => Number.isFinite(end(e)) ? Math.round(end(e) - tBperf) : null) }
        }, { n: nLong, tBperf, tBpage })
        barge = { cut_ms: cut, ws_closed_ms: wsClosed ? wsClosed.t - tB : null, buffers_after_cut: late.length, ...src }
        await c.shot(s, shotPath('B-CONV-3', theme, 'barge'))
      }
    })
    // The barge utterance is its own turn. Desktop shows Listening while the barge monitor is still capturing (the
    // clip ends on its silence window, ~4.5 s after onset), so first wait for that capture's Transcribing (15 s),
    // then for the loop to rest in Listening for 4 s (60 s). Ending earlier drops the capture (live() is false).
    let m2 = false, tTx = null
    for (const tW = Date.now(); Date.now() - tW < 15000 && tTx == null;) {
      tTx = await s.page.evaluate(t => window.__st.find(x => x.t > t && x.v === 'Transcribing')?.t ?? null, tBpage ?? 0)
      if (tTx == null) await c.sleep(100)
    }
    for (const tW = Date.now(); Date.now() - tW < 60000 && !m2;) {
      const v = await s.page.evaluate(() => window.__st.filter(x => !x.v.startsWith('(')).at(-1))
      m2 = v?.v === 'Listening' && v.t > (tTx ?? 0) && Date.now() - v.t > 4000; await c.sleep(250)
    }
    const st = await pageState(s)
    await c.shot(s, shotPath('B-CONV-3', theme, 'after'))
    // The host's own session store is the record of what was submitted: user turns stored after the barge onset.
    // Read-only; the Desktop transcript can lag it (the visible list did not show a stored barge turn).
    let stored = []
    try {
      const out = execFileSync('sqlite3', ['-readonly', '-json', `file:${path.join(host.hermes_home, 'state.db')}?mode=ro`,
        `select content, timestamp from messages where role = 'user' and timestamp >= ${(tBwall || 0) / 1000 - 1} order by timestamp`], { encoding: 'utf8', timeout: 10000 })
      stored = (out.trim() ? JSON.parse(out) : []).map(x => String(x.content).replace(/^\[Note:[^\]]*\]\s*/, '').trim())
    } catch (e) { stored = [`(session store unreadable: ${e.message.split('\n')[0]})`] }
    const tx = await s.page.evaluate(() => window.__tx || [])
    const composerText = await s.page.evaluate(() => { const e = document.querySelector('textarea, [contenteditable=true]'); return e ? (e.value ?? e.innerText).trim() : null })
    const statusesAfterBarge = st.st.filter(x => tBpage != null && x.t >= tBpage).map(x => x.v)
    // Judge only user messages that appeared after the barge onset: earlier prompts (the long one included) are
    // not echoes. A multiset difference, so a re-rendered transcript cannot reorder its way in.
    const left = [...(usersAtBarge || [])]
    const after = st.users.filter(u => { const i = left.indexOf(u); if (i >= 0) { left.splice(i, 1); return false } return true })
    const newUsers = [...after, ...stored]
    const bargeSubmitted = newUsers.some(u => overlap(BARGE, u) >= 0.6)
    const bargeShown = after.some(u => overlap(BARGE, u) >= 0.6)
    const echo = newUsers.some(u => m.reply && overlap(BARGE, u) < 0.6 && overlap(u, m.reply) >= 0.6)
    // Required: the agent stops talking within 1.5 s, no audio plays after the cut, and the interruption becomes the
    // next turn. How fast Desktop's own VAD trips (SUSTAINED_MS 300 + majority window) is host behaviour, so the
    // 600 ms latency bar is the advisory row B-CONV-3L.
    // The long reply must be audibly playing at onset, and every source active then must stop or end within 1.5 s.
    const playingAtOnset = barge && barge.playing_at_onset > 0
    const allStopped = barge && barge.stop_ms.every(x => x != null && x <= 1500)
    const pass = barge && playingAtOnset && allStopped && barge.cut_ms != null && barge.cut_ms <= 1500 && barge.buffers_after_cut === 0 && bargeSubmitted && !echo
    const fast = barge && barge.cut_ms != null && barge.cut_ms <= 600
    row('B-CONV-3L', fast ? 'pass' : 'fail', barge ? `cut ${barge.cut_ms} ms after barge onset (advisory bar ≤600 ms)` : 'never reached Speaking with audio', { theme, advisory: true })
    const r = row('B-CONV-3', pass ? 'pass' : 'fail', barge ? `cut ${barge.cut_ms} ms after barge onset (bar ≤1500 ms); reply sources playing at onset ${barge.playing_at_onset} of ${barge.active_at_onset} active, stopped/ended at ${JSON.stringify(barge.stop_ms)} ms (bar ≤1500 ms); WS closed ${barge.ws_closed_ms} ms, buffers after cut ${barge.buffers_after_cut}; barge submitted ${bargeSubmitted}; echo submit ${echo}` : 'never reached Speaking with audio', { theme })
    r.metrics = { ...barge, long_turn: { statuses: m.statuses, first_audio_ms: m.vad_end_to_first_audio_ms }, barge_text: BARGE, transcribe_wrap: txWrap, transcribe_requests: tx.map(e => ({ ...e, t: tBpage != null ? e.t - tBpage : e.t })), composer_after: composerText, statuses_after_barge: statusesAfterBarge, users_at_barge: usersAtBarge, users_after_barge: after, stored_users_after_barge: stored, barge_shown_in_transcript: bargeShown, barge_transcribing_ms: tTx != null && tBpage != null ? tTx - tBpage : null, users: st.users, reply_head: (m.reply || '').slice(0, 300), m2: !!m2 }
    r.evidence.push(shotPath('B-CONV-3', theme, 'barge'), shotPath('B-CONV-3', theme, 'after'))
    await waitStatus(s, /^Listening$/, 60000)
  }
  await endConversation(s)
  // B-CONV-5: forced Fish failure on the reply only. STT must still work, so the profile's Fish base_url is flipped to
  // a wrong path on api.fish.audio once the turn reaches Thinking (the v1.5.1 error trick), then restored.
  if (want('B-CONV-5')) {
    const cfg = path.join(host.hermes_home, 'config.yaml')
    const original = fs.readFileSync(cfg)
    // settings.py reads the transport block, not tts.fish-audio; restore even an inserted key byte-for-byte.
    const failConfig = () => execFileSync(path.join(host.cache_dir, '.venv/bin/python'), ['-c', `import pathlib,sys,yaml
p=pathlib.Path(sys.argv[1]); c=yaml.safe_load(p.read_text())
c.setdefault('plugins', {}).setdefault('entries', {}).setdefault('fish-audio', {}).setdefault('settings', {})['base_url']='https://api.fish.audio/acceptance-forced-failure'
p.write_text(yaml.safe_dump(c))`, cfg], { env: Object.fromEntries(Object.entries(process.env).filter(([k]) => !/(?:API_KEY|TOKEN|SECRET)$/.test(k))) })
    const t0 = Date.now()
    let m = null
    try {
      if (await startConversation(s)) m = await turn(s, rec, dir, PROMPTS[3], { timeout: 60000, onThinking: failConfig })
    } finally { fs.writeFileSync(cfg, original) }
    await endConversation(s)
    const toasts = (await s.page.evaluate(() => window.__toasts)).filter(x => x.t > t0).map(x => x.text.replace(/\s+/g, ' ').slice(0, 140))
    const shot = await c.shot(s, shotPath('B-CONV-5', theme))
    const fb = m && m.speak_frames.includes('fallback'), err = m && m.speak_frames.includes('error')
    const noText = m && onlyDone(m)
    // Judged as the user meets it: after the protocol fallback/error the user must hear the reply (whole-text path)
    // or see a toast. A fallback frame followed by nothing is still silent to the user (F1).
    const silent = m && !m.pcm_frames && !m.media_plays && !m.audio_buffers && !toasts.length
    const pass = !!m && m.submits === 1 && !!m.spoken_text && !silent
    const r = row('B-CONV-5', pass ? 'pass' : 'fail', !m ? 'could not start the forced-failure conversation'
      : `${m.submits === 1 ? 'submitted turn; ' : ''}${noText ? 'Desktop sent no reply text to speak-stream (only done); ' : ''}speak-stream frames ${m.speak_frames.join(',') || 'none'}, PCM ${m.pcm_frames}; whole-text plays ${m.media_plays}; toasts ${JSON.stringify(toasts.slice(0, 2))}${silent ? `; SILENT to the user (F1)${fb ? ': fallback frame sent, but no whole-text playback and no toast followed' : ''}` : ''}`, { theme, evidence: [shot] })
    r.metrics = { ...(m || {}), toasts }
  }
  }
  // B-DICT-1: composer-mic dictation over transcribe-stream.
  if (want('B-DICT-1')) {
    await c.page_escape(s); await c.nav(s, '#/'); await c.sleep(800)
    const readComposer = () => s.page.evaluate(() => { const e = document.querySelector('textarea, [contenteditable=true]'); return e ? (e.value ?? e.innerText).trim() : null })
    const composerBefore = await readComposer() // baseline: only text this capture adds is judged
    const f0 = rec.frames.length, s0 = rec.sockets.size
    await s.page.locator('[aria-label="Voice dictation"]').first().click({ timeout: 8000 })
    const u = utter(dir, DICTATE); const tS = Date.now()
    const said = s.page.evaluate(b => window.__mic.say(b), u.b64)
    // Capture the caption pill once the first partial caption arrives (or at the end of speech).
    for (let i = 0; i < 60 && !rec.frames.slice(f0).some(f => f.json?.type === 'partial'); i++) await c.sleep(100)
    await c.sleep(300); const pill = await c.shot(s, shotPath('B-DICT-1', theme, 'caption'))
    await said
    const stopped = await s.page.locator('[aria-label="Stop dictation"]').first().click({ timeout: 5000 }).then(() => true, () => false)
    let fin = null
    for (let i = 0; i < 80 && !fin; i++) { fin = rec.frames.slice(f0).find(f => f.kind === 'transcribe' && f.json?.type === 'final'); if (!fin) await c.sleep(200) }
    // Back to idle: the dictation control offers "Voice dictation" again and no "Stop dictation" remains.
    let idle = false
    for (let i = 0; i < 50 && !idle; i++) {
      idle = !(await s.page.locator('[aria-label="Stop dictation"]').count()) && (await s.page.locator('[aria-label="Voice dictation"]').count()) > 0
      if (!idle) await c.sleep(200)
    }
    await c.sleep(1200)
    const composer = await readComposer()
    const added = composer == null ? null : composerBefore && composer.startsWith(composerBefore) ? composer.slice(composerBefore.length).trim() : composer
    const ws = [...rec.sockets.values()].slice(s0).some(x => x.url.includes('transcribe-stream'))
    const partial = rec.frames.slice(f0).find(f => f.kind === 'transcribe' && f.json?.type === 'partial')
    const match = overlap(DICTATE, added)
    // The caption screenshot is evidence only; the row asserts the final frame, a clean stop and the inserted text.
    const pass = ws && !!fin && stopped && idle && match >= 0.8
    const r = row('B-DICT-1', pass ? 'pass' : 'fail', `transcribe-stream WS ${ws ? 'opened' : 'NOT opened'}; first caption ${partial ? partial.t - tS + ' ms' : 'none'}; final ${fin ? JSON.stringify(fin.json.transcript) : 'none'}; Stop dictation ${stopped ? 'clicked' : 'NOT clickable'}, control ${idle ? 'back to idle' : 'NOT back to idle'}; new composer text match ${(match * 100).toFixed(0)}%`, { theme, evidence: [pill] })
    r.metrics = { composer_before: composerBefore, composer, added, stopped, idle, first_caption_ms: partial ? partial.t - tS : null, final: fin?.json?.transcript ?? null }
    r.evidence.push(await c.shot(s, shotPath('B-DICT-1', theme)))
    await s.page.evaluate(() => { const e = document.querySelector('textarea, [contenteditable=true]'); e?.focus() })
    await s.page.keyboard.press('Meta+a'); await s.page.keyboard.press('Backspace')
  }
}
