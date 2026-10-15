// Renderer mic shim (understand-live-mode.md §6.2 step 4): every audio-only getUserMedia call gets a clone of one
// MediaStreamDestination track, silent until the harness calls __mic.say(<base64 WAV>). No device, no TCC.
// __audioLog records each scheduled AudioBufferSourceNode (time, duration) so playback is observable.
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
  AudioBufferSourceNode.prototype.start = function (...a) {
    if (this.context !== ctx) log.push({ t: performance.now(), d: this.buffer?.duration })
    return st.apply(this, a)
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
