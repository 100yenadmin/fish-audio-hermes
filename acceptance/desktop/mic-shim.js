// Renderer mic shim (understand-live-mode.md §6.2 step 4): every audio-only getUserMedia call gets a clone of one
// MediaStreamDestination track, silent until the harness calls __mic.say(<base64 WAV>). No device, no TCC.
// __audioLog records each scheduled AudioBufferSourceNode: start call time t, buffer duration d, scheduled start
// sched, and later its stop() call (stopAt, stopSched) and ended event (endedAt); times are performance.now() ms.
// Desktop cuts streamed speech by closing its AudioContext, which silences every source without stop() or a
// reliable ended event, so each entry names its context (ctx) and __ctxClosed maps context id → confirmed close time.
(() => {
  if (window.__mic) return
  const ctx = new AudioContext()
  const dest = ctx.createMediaStreamDestination()
  const gum = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices)
  navigator.mediaDevices.getUserMedia = async c => (c && c.audio && !c.video) ? new MediaStream([dest.stream.getAudioTracks()[0].clone()]) : gum(c)
  window.__mic = {
    async say(b64) {
      if (ctx.state !== 'running') await ctx.resume()
      const buf = await ctx.decodeAudioData(Uint8Array.from(atob(b64), x => x.charCodeAt(0)).buffer)
      const s = ctx.createBufferSource(); s.buffer = buf; s.connect(dest)
      const t0 = performance.now(); s.start()
      return new Promise(r => { s.onended = () => r({ start: t0, end: performance.now(), dur: buf.duration }) })
    }
  }
  const log = window.__audioLog = []
  const st = AudioBufferSourceNode.prototype.start
  const ctxIds = new WeakMap(), closed = window.__ctxClosed = {}, states = {}, sources = new Map()
  let nCtx = 0
  const idOf = c => {
    if (!ctxIds.has(c)) {
      const id = ++nCtx; ctxIds.set(c, id); states[id] = [{ t: performance.now(), state: c.state }]
      c.addEventListener('statechange', () => states[id].push({ t: performance.now(), state: c.state }))
    }
    return ctxIds.get(c)
  }
  window.__audioPlaying = (e, t = performance.now()) => e.playedAt != null && e.playedAt <= t && states[e.ctx]?.filter(x => x.t <= t).at(-1)?.state === 'running'
  setInterval(() => sources.forEach((node, e) => {
    if (e.playedAt == null && e.d > 0 && node.context.state === 'running' && node.context.currentTime >= e.when && e.endedAt == null) e.playedAt = performance.now()
    if (e.endedAt != null || closed[e.ctx] != null) sources.delete(e)
  }), 10)
  const cl = AudioContext.prototype.close
  AudioContext.prototype.close = function (...a) {
    const result = cl.apply(this, a)
    if (this !== ctx) { const id = idOf(this); result.then(() => { closed[id] ??= performance.now() }, () => {}) }
    return result
  }
  const at = (node, when) => { const now = performance.now(); return when > node.context.currentTime ? now + (when - node.context.currentTime) * 1000 : now }
  AudioBufferSourceNode.prototype.start = function (...a) {
    const result = st.apply(this, a)
    if (this.context !== ctx) {
      const e = { t: performance.now(), d: this.buffer?.duration, when: a[0] || 0, sched: at(this, a[0] || 0), ctx: idOf(this.context) }
      this.__acc = e; log.push(e); sources.set(e, this)
      this.addEventListener('ended', () => { e.endedAt = performance.now() }, { once: true })
    }
    return result
  }
  const sp = AudioBufferSourceNode.prototype.stop
  AudioBufferSourceNode.prototype.stop = function (...a) {
    const e = this.__acc
    if (e && e.stopAt == null) { e.stopAt = performance.now(); e.stopSched = at(this, a[0] || 0) }
    return sp.apply(this, a)
  }
  // Whole-text playback (POST /api/audio/speak) goes through an <audio>/Audio element instead.
  const media = window.__mediaLog = []
  const play = HTMLMediaElement.prototype.play
  HTMLMediaElement.prototype.play = function (...a) {
    const e = { t: performance.now(), src: String(this.currentSrc || this.src || '').slice(0, 40) }
    const update = () => { e.d = this.duration; if (e.started && (e.d > 0 || this.currentTime > 0)) e.playedAt ??= performance.now() }
    const began = () => { e.started = true; update() }
    const result = play.apply(this, a); media.push(e)
    this.addEventListener('playing', began, { once: true })
    for (const event of ['loadedmetadata', 'timeupdate', 'ended']) this.addEventListener(event, update)
    result?.then(began, () => {})
    return result
  }
})()
