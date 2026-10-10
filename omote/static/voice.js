/* The voice call, as a thing two screens can both use.
 *
 * This was inside the main client until Talk mode needed the same call with a
 * completely different surface around it. Copying it would have been the
 * expensive kind of duplication: barge-in is the delicate part, and a second
 * copy is a second place for the cancel path to drift — which would present
 * as the assistant talking over you on one screen and not the other.
 *
 * So the call is here and knows nothing about the page. It reports what
 * happened through handlers; what to draw is the caller's problem.
 */

import { socket } from './dom.js';

export class Player {
  /* Schedules PCM16 into the audio graph and can drop it all instantly.
   *
   * That second part is the whole reason this is not an <audio> element:
   * barge-in has to stop what is already queued, and a media element gives
   * you no handle on individual buffers. */
  constructor(rate) {
    this.rate = rate;
    this.ctx = new (window.AudioContext || window.webkitAudioContext)();
    // Every buffer goes through one gain node rather than straight to the
    // speakers, because there was no gain stage at all — `source.connect(ctx
    // .destination)` — and so no way to turn it down from inside the page. The
    // system volume was the only control, which is why a distorted signal
    // sounded the same at every setting: clipping happens before this point,
    // and scaling down clipped audio gives quieter distortion, not cleaner.
    //
    // It is also where the peak ceiling goes. The server guards the signal, but
    // a guard on a different machine from the speakers is a guard.
    this.gain = this.ctx.createGain();
    this.gain.connect(this.ctx.destination);
    this.volume = 1;
    this.next = 0;
    this.sources = new Set();
    this.utterance = null;
    // Samples handed to the graph for the current utterance. The realtime
    // protocol needs this on a barge-in: the server believes it delivered
    // everything it sent, and has to be told where the person stopped hearing.
    this.played = 0;
    // How many buffers were dropped because they arrived after their slot. Not
    // cosmetic: it is how a page reports that it cannot keep up, and zero is
    // the only healthy answer.
    this.late = 0;
  }

  /* The context starts suspended unless it was made inside a user gesture, and
   * this one is made in a websocket message handler, which never is.
   *
   * A suspended context has a `currentTime` that does not move, so every
   * `Math.max(this.next, this.ctx.currentTime)` below resolved to the same
   * instant and the whole backlog was scheduled on top of itself. When
   * something finally resumed it — a later gesture, another tab, the platform
   * noticing — all of it played at once, which is a wall of overlapping
   * speech at full amplitude. That is the other half of "blown out", and it is
   * browser-dependent: Chrome usually resumes a document that has had a
   * gesture, Safari and several webviews do not.
   *
   * So it is resumed explicitly, and asked to keep running rather than to
   * suspend itself when the page is hidden — a backgrounded call should still
   * be heard. */
  async resume() {
    if (this.ctx.state !== 'running') {
      try { await this.ctx.resume(); } catch { /* needs a gesture; `push` will cope */ }
    }
    return this.ctx.state;
  }

  setVolume(volume) {
    const wanted = Number.isFinite(volume) ? Math.min(1, Math.max(0, volume)) : 1;
    this.volume = wanted;
    // Set it on the node as well, so a change takes effect on audio already
    // scheduled ahead of `currentTime` rather than at the next buffer.
    try { this.gain.gain.setTargetAtTime(wanted, this.ctx.currentTime, 0.01); } catch { /* closed */ }
  }

  push(utteranceId, pcm) {
    if (!pcm || !pcm.length || !this.rate) return;
    if (utteranceId !== this.utterance) {
      this.utterance = utteranceId;
      this.played = 0;
      this.late = 0;
      // A small lead so the first buffer is not scheduled in the past on a
      // loaded machine, which drops it silently.
      this.next = this.ctx.currentTime + 0.06;
    }

    const buffer = this.ctx.createBuffer(1, pcm.length, this.rate);
    const channel = buffer.getChannelData(0);
    let peak = 0;
    for (let i = 0; i < pcm.length; i++) {
      const value = pcm[i] / 32768;
      channel[i] = value;
      const magnitude = value < 0 ? -value : value;
      if (magnitude > peak) peak = magnitude;
    }

    // A last few tenths of a dB of headroom, and nothing more: this is not a
    // normaliser and must not be, because a normaliser turns one very quiet
    // sentence into a very loud one. `createBuffer` does not clip, so this is
    // about the sum of overlapping buffers rather than about any one of them.
    if (peak > 0.94) {
      const scale = 0.94 / peak;
      for (let i = 0; i < channel.length; i++) channel[i] *= scale;
    }

    const source = this.ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(this.gain);

    /* `max` used to mean "skip the gap", and it does — a buffer that arrives
     * after its slot has passed is dropped, which is right, because the audio
     * after it is already playing and inserting this one now would overlap.
     *
     * But it was also the *only* thing `next` was reset by, and it ran on
     * every chunk. So a slow websocket or a loaded machine made `next` fall
     * behind `currentTime` and stay there, and every subsequent chunk was
     * dropped: the reply lost syllables and gained clicks, which reads as
     * static. Now a buffer that is only *slightly* late is scheduled
     * immediately rather than discarded, and only one that has fallen a whole
     * buffer-length behind is counted as dropped. */
    const now = this.ctx.currentTime;
    const behind = now - this.next;
    if (behind > buffer.duration) {
      this.late += pcm.length;
      this.next = now + 0.02;
    }
    const at = Math.max(this.next, now);
    source.start(at);
    this.next = at + buffer.duration;
    this.played += pcm.length;
    this.sources.add(source);
    source.onended = () => this.sources.delete(source);
  }

  /* How much of the current utterance has actually reached the speakers,
     rather than how much was handed over. The difference is everything that
     is still scheduled ahead of `currentTime`, and it is the number the
     server needs. */
  heardMs() {
    const ahead = Math.max(0, this.next - this.ctx.currentTime);
    return Math.max(0, Math.round((this.played / this.rate - ahead) * 1000));
  }

  cancel() {
    for (const source of this.sources) {
      try { source.stop(); } catch { /* already finished */ }
    }
    this.sources.clear();
    this.next = 0;
    this.played = 0;
    this.late = 0;
    this.utterance = null;
  }

  get speaking() {
    return this.sources.size > 0;
  }

  close() {
    this.cancel();
    try { this.gain.disconnect(); } catch { /* already gone */ }
    try { this.ctx.close(); } catch { /* already closed */ }
  }
}

/* Microphone capture, shared by both kinds of call.
 *
 * Deliberately never connected to the destination: routing the microphone to
 * the speakers is how you get feedback, and it is a one-line mistake. */
export class Capture {
  constructor() {
    this.stream = null;
    this.ctx = null;
    this.node = null;
    this.muted = false;
    // Bumped by every stop() and every start(). Coming up takes two awaits —
    // the permission prompt and the worklet — and neither caller awaits
    // start(), so a call that ends in that window used to run on into a
    // torn-down Capture and fail with `this.ctx is null` reported as "the
    // microphone could not start". Worse, the stream it had already been
    // granted was never stopped: the capture light stayed on.
    this.run = 0;
  }

  async start(rate, onFrame) {
    const run = ++this.run;
    // Held locally until the graph is whole, so that a stop() arriving
    // mid-await has nothing half-built to trip over and this can clean up
    // after itself.
    let stream = null;
    let ctx = null;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      if (run !== this.run) return;

      ctx = new (window.AudioContext || window.webkitAudioContext)();
      await ctx.audioWorklet.addModule('/static/capture-worklet.js');
      if (run !== this.run) return;

      const source = ctx.createMediaStreamSource(stream);
      const node = new AudioWorkletNode(ctx, 'omote-capture', { processorOptions: { targetRate: rate } });
      node.port.onmessage = (e) => {
        if (!this.muted) onFrame(e.data.buffer);
      };
      source.connect(node);

      this.stream = stream;
      this.ctx = ctx;
      this.node = node;
      stream = ctx = null;  // handed over; stop() owns them now.
    } finally {
      // Whatever is still local belongs to a start that was stopped or threw,
      // and nothing else will ever hold it.
      if (ctx) { try { ctx.close(); } catch { /* never ran */ } }
      if (stream) for (const track of stream.getTracks()) track.stop();
    }
  }

  stop() {
    this.run++;
    if (this.node) this.node.disconnect();
    if (this.ctx) { try { this.ctx.close(); } catch { /* already closed */ } }
    if (this.stream) for (const track of this.stream.getTracks()) track.stop();
    this.node = this.ctx = this.stream = null;
  }
}

/* One duplex voice call over /ws/voice.
 *
 * `on` is a bag of handlers, all optional. Anything not supplied is simply
 * not reported — a caller that only wants to hear the audio does not have to
 * write eight empty functions. */
export class Voice {
  constructor(on = {}) {
    this.on = on;
    this.ws = null;
    this.player = null;
    this.capture = new Capture();
    this.muted = false;
    this.running = false;
  }

  fire(name, ...args) {
    const fn = this.on[name];
    if (fn) fn(...args);
  }

  async start(options = {}) {
    const url = socket('/ws/voice');
    this.ws = new WebSocket(url);
    this.ws.binaryType = 'arraybuffer';

    await new Promise((resolve, reject) => {
      this.ws.onopen = resolve;
      this.ws.onerror = () => reject(new Error('could not open the voice socket'));
    });

    this.ws.send(JSON.stringify({
      type: 'voice.start',
      format: { sample_rate: 16000, encoding: 'pcm16' },
      // Opting a voice call into acting on the machine is per call, because
      // consenting to be listened to is not the same as consenting to have
      // commands run.
      agent_session_id: options.agentSession || undefined,
      stt: options.stt || undefined,
      tts: options.tts || undefined,
      llm: options.llm || undefined,
      model: options.model || undefined,
      voice: options.voice || undefined,
    }));

    this.ws.onmessage = (msg) => this.receive(msg);
    this.ws.onclose = () => this.stop(true);
    this.running = true;
  }

  receive(msg) {
    if (msg.data instanceof ArrayBuffer) {
      // 12 ASCII bytes of utterance id, then PCM16.
      const view = new Uint8Array(msg.data);
      const id = new TextDecoder().decode(view.slice(0, 12)).trim();
      const pcm = new Int16Array(msg.data.slice(12));
      if (this.player) this.player.push(id, pcm);
      this.fire('speaking');
      return;
    }

    const ev = JSON.parse(msg.data);
    switch (ev.type) {
      case 'voice.ready':
        this.player = new Player(ev.output_format.sample_rate);
        // Resumed here and not lazily on the first buffer, because the first
        // buffer is the one that has to be scheduled at an instant the context
        // will actually reach. See `Player.resume`.
        this.player.resume();
        this.capture.start(ev.format.sample_rate, (buf) => {
          if (this.ws && this.ws.readyState === WebSocket.OPEN) this.ws.send(buf);
        }).catch((err) => this.fire('error', `the microphone could not start: ${err.message}`));
        this.fire('ready', ev);
        break;

      case 'vad.speech_started': this.fire('listening'); break;
      case 'vad.speech_stopped': this.fire('thinking'); break;
      case 'transcript.partial': this.fire('partial', ev.text); break;
      case 'transcript.final': this.fire('heard', ev.text, ev.submitted); break;
      case 'assistant.text.delta': this.fire('said', ev.text); break;
      case 'speech.started': this.fire('speaking'); break;

      case 'speech.cancelled':
        // The decisive moment. Everything already scheduled has to go, or the
        // assistant keeps talking over you for as long as the buffer lasts.
        if (this.player) this.player.cancel();
        this.fire('cancelled');
        break;

      case 'speech.stopped': this.fire('finished'); break;

      // The turn has stopped and is waiting on the person. It is spoken as
      // well — that is the point of it — but a client that reconnects
      // mid-suspension has no other way to tell waiting from idle.
      case 'voice.waiting': this.fire('waiting', ev); break;
      case 'voice.answered': this.fire('answered', ev); break;
      case 'voice.error': this.fire('error', ev.message); break;
    }
  }

  interrupt() {
    if (this.player) this.player.cancel();
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: 'voice.interrupt' }));
    }
  }

  say(text) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: 'voice.text', text }));
    }
  }

  setMuted(muted) {
    this.muted = muted;
    this.capture.muted = muted;
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: 'voice.mute', muted }));
    }
  }

  stop(remote) {
    if (!this.running) return;
    this.running = false;
    if (this.ws && this.ws.readyState === WebSocket.OPEN && !remote) {
      this.ws.send(JSON.stringify({ type: 'voice.stop' }));
      this.ws.close();
    }
    if (this.player) this.player.close();
    this.capture.stop();
    this.ws = null;
    this.player = null;
    this.fire('stopped');
  }
}
