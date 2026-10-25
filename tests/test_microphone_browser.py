"""Exercise real Web Audio capture without recording the user's microphone."""
from tests.test_workspace_browser import workspace as workspace


def test_capture_resumes_a_suspended_context_and_delivers_pcm(workspace):
    page, _ = workspace
    page.evaluate("""async () => {
      const { Capture } = await import('/static/voice.js');
      const NativeContext = window.AudioContext;
      const generator = new NativeContext();
      const destination = generator.createMediaStreamDestination();
      const tone = generator.createOscillator();
      const level = generator.createGain(); level.gain.value = 0.1;
      tone.connect(level); level.connect(destination); tone.start();
      window.fakeTrack = destination.stream.getAudioTracks()[0];
      navigator.mediaDevices.getUserMedia = async () => destination.stream;
      window.AudioContext = class extends NativeContext {
        constructor() { super(); this.suspend(); }
      };
      window.capture = new Capture(); window.frames = []; window.captureError = null;
      const button = document.createElement('button'); button.id = 'capture-test';
      button.textContent = 'Start test capture'; document.body.appendChild(button);
      button.onclick = async () => {
        try {
          await generator.resume();
          await capture.start(16000, buffer => frames.push(Array.from(new Int16Array(buffer))));
        } catch (error) { window.captureError = String(error); }
      };
      window.generator = generator;
    }""")
    page.locator('#capture-test').click()
    try:
        page.wait_for_function('frames.length >= 3 || captureError', timeout=5000)
        result = page.evaluate("""() => ({error: captureError, state: capture.ctx?.state,
          length: frames[0]?.length, nonzero: frames.some(f => f.some(v => v !== 0))})""")
        assert result == {'error': None, 'state': 'running', 'length': 320, 'nonzero': True}
    finally:
        page.evaluate('capture.stop(); generator.close()')
    assert page.evaluate('fakeTrack.readyState') == 'ended'


def test_talk_playback_survives_microphone_activation(workspace):
    page, _ = workspace
    page.evaluate("""async () => {
      const { Voice } = await import('/static/voice.js');
      const NativeContext = window.AudioContext;
      const generator = new NativeContext();
      const destination = generator.createMediaStreamDestination();
      window.microphoneOpened = false;
      navigator.mediaDevices.getUserMedia = async () => {
        await new Promise(resolve => setTimeout(resolve, 20));
        window.microphoneOpened = true;
        return destination.stream;
      };
      window.AudioContext = class extends NativeContext {
        constructor() { super(); this.suspend(); }
        resume() {
          if (!microphoneOpened) return Promise.reject(new DOMException('Input device not ready', 'NotAllowedError'));
          return super.resume();
        }
      };
      window.voiceReady = false; window.playbackError = null;
      window.voice = new Voice({ready: () => voiceReady = true, error: message => playbackError = message});
      voice.running = true; voice.ws = {readyState: 1, send() {}, close() {}};
      const button = document.createElement('button'); button.id = 'playback-test';
      button.textContent = 'Test duplex audio'; document.body.appendChild(button);
      button.onclick = () => voice.receive({data: JSON.stringify({type: 'voice.ready',
        format: {sample_rate: 16000}, output_format: {sample_rate: 24000}})});
      window.generator = generator;
    }""")
    page.locator('#playback-test').click()
    page.wait_for_function('voiceReady || playbackError')
    try:
        page.evaluate("""() => {
          window.meter = voice.player.ctx.createAnalyser(); meter.fftSize = 256;
          voice.player.gain.connect(meter);
          const pcm = new Int16Array(24000);
          for (let i = 0; i < pcm.length; i++) pcm[i] = Math.sin(2 * Math.PI * 440 * i / 24000) * 4000;
          voice.player.push('output-test', pcm);
        }""")
        page.wait_for_function("""() => {
          const samples = new Float32Array(meter.fftSize); meter.getFloatTimeDomainData(samples);
          return samples.some(sample => Math.abs(sample) > .01);
        }""", timeout=3000)
        assert page.evaluate('voice.player.ctx.state') == 'running'
        assert page.evaluate('playbackError') is None
    finally:
        page.evaluate('voice.stop(); generator.close()')


def test_live_playback_survives_microphone_activation(workspace):
    import json
    import math
    import re
    import struct

    from playwright.sync_api import expect

    page, _ = workspace
    sockets = []

    def connect(ws):
        sockets.append(ws)
        ws.send(json.dumps({'type': 'realtime.ready', 'sample_rate': 24000,
                            'model': 'fake', 'voice': 'fake'}))

    page.route_web_socket(re.compile(r'.*/ws/realtime.*'), connect)
    page.evaluate("""() => {
      const NativeContext = window.AudioContext;
      window.generator = new NativeContext();
      const destination = generator.createMediaStreamDestination();
      window.microphoneOpened = false; window.audioGains = []; window.audioContexts = [];
      navigator.mediaDevices.getUserMedia = async () => {
        await new Promise(resolve => setTimeout(resolve, 20));
        window.microphoneOpened = true;
        return destination.stream;
      };
      window.AudioContext = class extends NativeContext {
        constructor() { super(); audioContexts.push(this); this.suspend(); }
        resume() {
          if (!microphoneOpened) return Promise.reject(new Error('Input device not ready'));
          return super.resume();
        }
        createGain() { const gain = super.createGain(); audioGains.push(gain); return gain; }
      };
      document.querySelector('#live-acts').checked = false;
    }""")
    page.locator('#live-button').click()
    try:
        expect(page.locator('#live-stop')).to_be_enabled()
        assert page.evaluate('audioContexts.length') == 1
        assert page.evaluate('audioContexts[0].state') == 'running'
        page.evaluate("""() => {
          window.meter = audioContexts[0].createAnalyser(); meter.fftSize = 256;
          audioGains[audioGains.length - 1].connect(meter);
        }""")
        sockets[0].send(struct.pack('<24000h', *[int(math.sin(2 * math.pi * 440 * i / 24000) * 4000)
                                               for i in range(24000)]))
        page.wait_for_function("""() => {
          const data = new Float32Array(meter.fftSize); meter.getFloatTimeDomainData(data);
          return data.some(sample => Math.abs(sample) > .01);
        }""", timeout=3000)
    finally:
        page.evaluate("async () => { const m = await import('/static/call.js'); await m.endCall(); generator.close(); }")
