// Renderer mic shim (understand-live-mode.md §6.2 step 4): every audio-only getUserMedia call gets a clone of one
// MediaStreamDestination track, silent until the harness calls __mic.say(<base64 WAV>). No device, no TCC.
// __audioLog records each scheduled AudioBufferSourceNode: start call time t, buffer duration d, scheduled start
// sched, and later its stop() call (stopAt, stopSched) and ended event (endedAt); times are performance.now() ms.
// Desktop cuts streamed speech by closing its AudioContext, which silences every source without stop() or a
// reliable ended event, so each entry names its context (ctx) and __ctxClosed maps context id → close() time.
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
  const ctxIds = new WeakMap(), closed = window.__ctxClosed = {}
  let nCtx = 0
  const idOf = c => { if (!ctxIds.has(c)) ctxIds.set(c, ++nCtx); return ctxIds.get(c) }
  const cl = AudioContext.prototype.close
  AudioContext.prototype.close = function (...a) {
    if (this !== ctx) { const id = idOf(this); if (closed[id] == null) closed[id] = performance.now() }
    return cl.apply(this, a)
  }
  const at = (node, when) => { const now = performance.now(); return when > node.context.currentTime ? now + (when - node.context.currentTime) * 1000 : now }
  AudioBufferSourceNode.prototype.start = function (...a) {
    if (this.context !== ctx) {
      const e = { t: performance.now(), d: this.buffer?.duration, sched: at(this, a[0] || 0), ctx: idOf(this.context) }
      this.__acc = e; log.push(e)
      this.addEventListener('ended', () => { e.endedAt = performance.now() }, { once: true })
    }
    return st.apply(this, a)
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
    media.push(e); this.addEventListener('ended', () => { e.end = performance.now(); e.d = this.duration }, { once: true })
    this.addEventListener('loadedmetadata', () => { e.d = this.duration }, { once: true })
    return play.apply(this, a)
  }
})()
