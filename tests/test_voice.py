"""Duplex behaviour: sentence-at-a-time synthesis, and barge-in."""

from __future__ import annotations

import asyncio
import struct

import pytest

from openmirror.protocol.voice import (
    SpeechStarted,
    SynthesisCancelled,
    SynthesisStarted,
    SynthesisStopped,
    TranscriptFinal,
)
from openmirror.providers.base import StreamDone, StreamText, Transcript
from openmirror.voice.pipeline import (
    FLUSH_MIN_CHARS,
    FLUSH_QUIET_MS,
    VoiceConfig,
    VoiceSession,
    is_meaningful,
    pcm_to_wav,
    split_sentences,
)
from tests.test_vad import noise, tone


class FakeSTT:
    def __init__(self, text='delete the build directory'):
        self.text = text
        self.calls = 0
        self.seen: list[bytes] = []

    async def transcribe(self, audio, *, model, language=None):
        self.calls += 1
        self.seen.append(audio)
        return Transcript(text=self.text)


class SlowLLM:
    """Streams words with a delay, so a barge-in has something to land on."""

    def __init__(self, text: str, delay: float = 0.05):
        self.text = text
        self.delay = delay
        self.started = 0

    async def stream(self, req):
        self.started += 1
        for word in self.text.split(' '):
            await asyncio.sleep(self.delay)
            yield StreamText(text=word + ' ')
        yield StreamDone()


class FakeTTS:
    def __init__(self, delay: float = 0.02):
        self.delay = delay
        self.spoken: list[str] = []

    async def synthesize(self, text, *, model, voice, fmt='pcm16', sample_rate=16_000):
        self.spoken.append(text)
        for _ in range(3):
            await asyncio.sleep(self.delay)
            yield b'\x00\x01' * 160

    async def voices(self):
        return []


def build(llm, stt=None, tts=None):
    events: list = []
    audio: list[tuple[str, bytes]] = []

    async def emit_event(e):
        events.append(e)

    async def emit_audio(uid, chunk):
        audio.append((uid, chunk))

    session = VoiceSession(
        stt=stt or FakeSTT(),
        tts=tts or FakeTTS(),
        llm=llm,
        config=VoiceConfig(llm_model='test'),
        emit_event=emit_event,
        emit_audio=emit_audio,
    )
    return session, events, audio


# -- helpers ----------------------------------------------------------------


def test_wav_header_is_well_formed():
    pcm = b'\x00\x01' * 100
    wav = pcm_to_wav(pcm, 16_000)
    assert wav[:4] == b'RIFF' and wav[8:12] == b'WAVE'
    assert struct.unpack('<I', wav[4:8])[0] == 36 + len(pcm)
    assert struct.unpack('<I', wav[40:44])[0] == len(pcm)
    assert struct.unpack('<I', wav[24:28])[0] == 16_000


def test_sentence_splitting():
    done, rest = split_sentences('One. Two! Three? And a frag')
    assert done == ['One.', 'Two!', 'Three?']
    assert rest == 'And a frag'


def test_abbreviation_is_not_a_sentence_end():
    # 'Dr.' must not end a sentence, so the first split lands after 'it.'
    done, rest = split_sentences('See Dr. Smith about it. Then go. ')
    assert done == ['See Dr. Smith about it.', 'Then go.'], done
    assert rest.strip() == ''


def test_an_unterminated_tail_is_held_back():
    """Mid-stream, a sentence with nothing after it may not be finished yet.

    Holding it is what stops 'Delete the file.' being spoken as a complete
    thought when the next token turns out to be 'No, wait'. The caller flushes
    the remainder when the model stops.
    """
    done, rest = split_sentences('Done. Then go.')
    assert done == ['Done.']
    assert rest == 'Then go.'


def test_filler_is_not_worth_answering():
    assert is_meaningful('delete the branch')
    assert not is_meaningful('uh')
    assert not is_meaningful('  ')
    assert not is_meaningful('you')      # whisper's standard hallucination on silence


# -- the pipeline -----------------------------------------------------------


@pytest.mark.asyncio
async def test_speaks_each_sentence_as_it_completes():
    """Synthesis must not wait for the model to finish."""
    tts = FakeTTS()
    session, events, audio = build(SlowLLM('First sentence. Second sentence. Third.'), tts=tts)

    await session.say('hello')
    await asyncio.wait_for(session._reply, timeout=10)

    assert tts.spoken == ['First sentence.', 'Second sentence.', 'Third.']
    assert audio and all(uid == audio[0][0] for uid, _ in audio)
    assert any(isinstance(e, SynthesisStarted) for e in events)
    assert any(isinstance(e, SynthesisStopped) for e in events)


@pytest.mark.asyncio
async def test_audio_drives_a_full_turn():
    stt = FakeSTT('what is the time')
    session, events, audio = build(SlowLLM('It is four.', delay=0.01), stt=stt)

    # A pause to learn the room, then speech, then enough silence to close.
    await session.feed(noise(600, amplitude=0.002))
    await session.feed(tone(800, amplitude=0.3))
    await session.feed(noise(900, amplitude=0.002))

    # Transcription is no longer inside `feed` — the read loop stays deaf to
    # nothing — so finishing the audio is not finishing the turn.
    assert await session.settle()

    assert any(isinstance(e, SpeechStarted) for e in events)
    final = [e for e in events if isinstance(e, TranscriptFinal)]
    assert final and final[0].submitted and final[0].text == 'what is the time'

    assert audio


@pytest.mark.asyncio
async def test_barge_in_cancels_the_reply():
    """Talking over the assistant must stop it and stop the audio."""
    llm = SlowLLM('One. Two. Three. Four. Five. Six. Seven. Eight.', delay=0.06)
    session, events, audio = build(llm)

    await session.feed(noise(600, amplitude=0.002))   # learn the room
    await session.say('go')
    await asyncio.sleep(0.35)                          # let it get going
    assert session.is_speaking

    before = len(audio)
    await session.feed(tone(200, amplitude=0.35))      # interrupt
    await asyncio.sleep(0.3)

    cancels = [e for e in events if isinstance(e, SynthesisCancelled)]
    assert cancels and cancels[0].reason == 'barge_in'
    assert not session.is_speaking
    # Nothing further was sent for the cancelled utterance.
    assert len(audio) == before or all(uid != cancels[0].utterance_id for uid, _ in audio[before:])


@pytest.mark.asyncio
async def test_interrupted_reply_records_only_what_was_heard():
    """History must not claim it said things that were cut off."""
    llm = SlowLLM('Alpha. Beta. Gamma. Delta. Epsilon. Zeta.', delay=0.06)
    session, events, audio = build(llm)

    await session.feed(noise(600, amplitude=0.002))
    await session.say('go')
    await asyncio.sleep(0.3)
    await session.interrupt()

    assistant = [m for m in session.history if m.role == 'assistant']
    assert assistant
    text = assistant[-1].content[0].text
    assert '[interrupted]' in text
    # Whatever it claims to have said must actually have been synthesised.
    for sentence in ('Alpha.', 'Beta.', 'Gamma.', 'Delta.', 'Epsilon.', 'Zeta.'):
        if sentence in text:
            assert sentence in session.tts.spoken, f'claimed {sentence!r} without speaking it'


@pytest.mark.asyncio
async def test_mute_stops_feeding_the_detector():
    session, events, audio = build(SlowLLM('hi'))
    await session.mute(True)
    await session.feed(tone(800, amplitude=0.4))
    assert not any(isinstance(e, SpeechStarted) for e in events)


@pytest.mark.asyncio
async def test_silence_transcribed_as_filler_is_not_answered():
    llm = SlowLLM('should never run')
    session, events, audio = build(llm, stt=FakeSTT('you'))

    await session.feed(noise(600, amplitude=0.002))
    await session.feed(tone(800, amplitude=0.3))
    await session.feed(noise(900, amplitude=0.002))

    assert await session.settle()
    final = [e for e in events if isinstance(e, TranscriptFinal)]
    assert final and not final[0].submitted
    assert llm.started == 0


@pytest.mark.asyncio
async def test_a_discarded_cough_stops_the_capture():
    """A burst too short to be speech must close the capture, not orphan it.

    The detector drops an utterance under `min_speech_ms` by reporting
    nothing at all, which used to leave the session capturing for the rest of
    the call: every later frame appended to an utterance nobody would send,
    and none of them reaching the pre-roll — so the next real sentence
    arrived with its first word missing, which is the one thing the pre-roll
    buffer exists to prevent.
    """
    stt = FakeSTT('what is the time')
    session, events, _ = build(SlowLLM('It is four.', delay=0.01), stt=stt)

    await session.feed(noise(600, amplitude=0.002))
    await session.feed(tone(120, amplitude=0.4))       # a cough, under min_speech_ms
    await session.feed(noise(900, amplitude=0.002))

    assert not [e for e in events if isinstance(e, TranscriptFinal)]
    assert not session._capturing, 'still capturing after a discarded burst'
    assert not session._utterance, 'audio accumulating into an utterance nobody will send'

    # And the next real sentence still gets its pre-roll.
    await session.feed(noise(600, amplitude=0.002))
    await session.feed(tone(800, amplitude=0.3))
    await session.feed(noise(900, amplitude=0.002))

    assert await session.settle()
    final = [e for e in events if isinstance(e, TranscriptFinal)]
    assert final and final[0].submitted
    # Pre-roll plus the 800 ms burst plus the hangover, not a whole call of noise.
    assert len(stt.seen[0]) < 2 * 16_000 * 3, 'utterance carries audio from before it began'


# -- latency: the three things that made it feel broken ----------------------


@pytest.mark.asyncio
async def test_transcribing_does_not_stall_the_read_loop():
    """The compounding delay, and the one that made every turn slower than the last.

    Transcription used to be awaited from `_frame`, which the websocket read
    loop calls. For the whole of a `POST /audio/transcriptions` the server read
    no microphone frames and ran no voice-activity detection — so the silence
    you produce after your sentence was not *seen*, and the detector's 500 ms
    hangover did not start counting until the buffered frames had been drained.

    The measurement is therefore not "how long did transcription take" but
    "how much silence arrived while it was happening". It has to be most of it.
    """
    class SlowSTT(FakeSTT):
        async def transcribe(self, audio, *, model, language=None):
            self.calls += 1
            self.seen.append(audio)
            await asyncio.sleep(0.30)
            return Transcript(text=self.text)

    stt = SlowSTT('what is the time')
    session, events, _ = build(SlowLLM('It is four.', delay=0.01), stt=stt)

    await session.feed(noise(600, amplitude=0.002))
    await session.feed(tone(800, amplitude=0.3))
    await session.feed(noise(900, amplitude=0.002))
    await asyncio.sleep(0)   # let the worker task reach its first await
    assert stt.calls == 1, 'the transcription did not start'
    assert not session._transcriber.done(), 'the read loop is still inside the transcription'

    # 300 ms of silence, which is more than the hangover, arrives while the
    # transcription is in flight and must be fed to the detector immediately.
    await session.feed(noise(400, amplitude=0.002))

    assert await session.settle()
    assert stt.calls == 1
    assert any(isinstance(e, TranscriptFinal) for e in events)


@pytest.mark.asyncio
async def test_an_interruption_is_noticed_while_the_transcription_runs():
    """The same stall, seen from the side that matters: barge-in.

    Someone answering a question out loud while their own words are still being
    transcribed used to have to wait for the transcription to come back before
    the server heard them at all.
    """
    class SlowSTT(FakeSTT):
        async def transcribe(self, audio, *, model, language=None):
            self.calls += 1
            self.seen.append(audio)
            await asyncio.sleep(0.30)
            return Transcript(text=self.text)

    stt = SlowSTT('tell me a story')
    session, events, audio = build(SlowLLM('Once upon a time. There was a cat.'), stt=stt)

    await session.feed(noise(600, amplitude=0.002))
    await session.feed(tone(800, amplitude=0.3))
    await session.feed(noise(900, amplitude=0.002))
    await asyncio.sleep(0.05)

    # Talk over it, while the first utterance is still being transcribed.
    await session.feed(tone(600, amplitude=0.35))
    await asyncio.sleep(0.05)

    cancelled = [e for e in events if type(e).__name__ == 'SynthesisCancelled']
    assert cancelled or not session.is_speaking, (
        'the second utterance did not reach the detector while the first was being transcribed'
    )
    await session.settle()


@pytest.mark.asyncio
async def test_synthesis_overlaps_the_model_rather_than_queueing_behind_it():
    """The largest single thing wrong with talk mode's latency.

    `_speak` used to be awaited *inside* the loop reading the model's stream, so
    synthesis and generation were strictly serial: sentence N+1's request was
    not sent until sentence N's audio had finished arriving, and while that
    happened the model's SSE stream sat unread with the generator not being
    polled. A five-sentence reply cost five round trips instead of one.

    The measurement is overlap, not total time — a fake provider's absolute
    numbers mean nothing, but whether two things are happening at once is the
    property, and it is the thing that was missing.
    """
    state = {'synthesising': 0, 'saw_model_token_during_synthesis': False, 'tts_calls': 0}

    class MeasuringTTS:
        async def synthesize(self, text, *, model, voice, fmt='pcm16', sample_rate=16_000):
            state['tts_calls'] += 1
            state['synthesising'] += 1
            try:
                # Slow, so a serialised implementation has nothing to overlap
                # with and a pipelined one clearly does.
                for _ in range(4):
                    await asyncio.sleep(0.02)
                    yield b'\x00\x01' * 160
            finally:
                state['synthesising'] -= 1

        async def voices(self):
            return []

    class CountingLLM:
        async def stream(self, req):
            for word in 'One. Two. Three. Four. Five. Six.'.split():
                # The question is not "did the model finish first" — it is "was
                # there ever a model token produced while a synthesis was in
                # flight". Only one of those two can be true of a given design.
                if state['synthesising']:
                    state['saw_model_token_during_synthesis'] = True
                await asyncio.sleep(0.01)
                yield StreamText(text=word + ' ')
            yield StreamDone()

    session, _, _ = build(CountingLLM(), tts=MeasuringTTS())
    await session.say('go')
    assert await session.settle()

    assert state['tts_calls'] >= 3, 'the sentences were not synthesised separately'
    assert state['saw_model_token_during_synthesis'], (
        'no model token was produced while a synthesis was in flight, so every '
        'sentence waited for the one before it'
    )


@pytest.mark.asyncio
async def test_sentences_still_come_out_in_order():
    """Pipelining must not reorder what is heard.

    The queue exists to overlap two things, not to change the answer, and a
    voice assistant that says the last sentence first is worse than a slow one.
    """
    order: list[str] = []

    class SlowFirstTTS:
        async def synthesize(self, text, *, model, voice, fmt='pcm16', sample_rate=16_000):
            # The first sentence takes much longer than the rest, which is
            # where an unordered implementation would show itself.
            for _ in range(4 if text.startswith('First') else 1):
                await asyncio.sleep(0.02)
            order.append(text)
            yield b'\x00\x01' * 160

        async def voices(self):
            return []

    session, _, _ = build(SlowLLM('First one. Second one. Third one.', delay=0.005), tts=SlowFirstTTS())
    await session.say('go')
    assert await session.settle()
    assert order == ['First one.', 'Second one.', 'Third one.'], order


@pytest.mark.asyncio
async def test_an_unterminated_reply_is_spoken_before_the_turn_ends():
    """The tail used to wait for the model to finish.

    A reply written as a list, or one ending on a dash, has no sentence
    terminator at all, so it was not spoken until the stream closed — and on a
    slow model that is several seconds of the person listening to nothing. The
    quiet-flush exists so the tail is spoken while the model is still thinking.
    """
    spoken: list[str] = []
    released = asyncio.Event()

    class StalledLLM:
        """Says something with no terminator, then stops producing."""

        async def stream(self, req):
            for word in 'Here is the shortlist of things you asked for'.split():
                await asyncio.sleep(0.005)
                yield StreamText(text=word + ' ')
            # Stalls. This is the window the tail has to be spoken in.
            released.set()
            await asyncio.sleep(1.0)
            yield StreamDone()

    class RecordingTTS:
        async def synthesize(self, text, *, model, voice, fmt='pcm16', sample_rate=16_000):
            spoken.append(text)
            yield b'\x00\x01' * 160

        async def voices(self):
            return []

    session, _, _ = build(StalledLLM(), tts=RecordingTTS())
    await session.say('go')
    await released.wait()
    # Generous next to FLUSH_QUIET_MS, because a loaded machine is the normal
    # case in CI and the assertion is about ordering, not about a deadline.
    for _ in range(60):
        if spoken:
            break
        await asyncio.sleep(0.02)

    assert spoken, 'the unfinished tail was never spoken; the turn had not ended yet'
    assert not session._reply.done(), 'and it was only spoken after the model finished'
    await session.settle()


@pytest.mark.asyncio
async def test_a_short_fragment_is_not_flushed_on_its_own():
    """The other half of the quiet-flush: a couple of words is not a clause.

    Speaking "Sure" the instant it appears would be worse than the latency it
    saves, because the rest of the word-group would then be spoken as a second
    fragment.
    """
    spoken: list[str] = []
    released = asyncio.Event()

    class StalledLLM:
        async def stream(self, req):
            yield StreamText(text='Sure')
            released.set()
            # Long enough that the assertion below is nowhere near the end of
            # the turn: a fragment must survive the quiet window, not be
            # rescued by the final flush.
            await asyncio.sleep(4.0)
            yield StreamDone()

    class RecordingTTS:
        async def synthesize(self, text, *, model, voice, fmt='pcm16', sample_rate=16_000):
            spoken.append(text)
            yield b'\x00\x01' * 160

        async def voices(self):
            return []

    assert len('Sure') < FLUSH_MIN_CHARS, 'this test is only meaningful while Sure is a fragment'

    session, _, _ = build(StalledLLM(), tts=RecordingTTS())
    await session.say('go')
    await released.wait()
    await asyncio.sleep(FLUSH_QUIET_MS / 1000 * 2)

    assert spoken == [], f'a fragment was spoken on its own: {spoken}'
    await session.settle()


@pytest.mark.asyncio
async def test_cancelling_a_turn_does_not_leave_a_speech_worker_running():
    """A task left behind keeps synthesising a turn nobody is listening to."""
    session, _, audio = build(SlowLLM('One. Two. Three. Four. Five.', delay=0.05))
    await session.say('go')
    await asyncio.sleep(0.1)
    await session.interrupt()
    await asyncio.sleep(0.2)

    assert not session.is_speaking
    await session.settle(limit=2.0)
    await session.close()


def test_a_pronoun_is_not_mistaken_for_an_initial():
    """`I think. Yes.` was one utterance, so the first audio started later.

    The abbreviation rule matched any single capital letter before a stop with
    `re.I`, and the pronoun is one. A lone capital letter is now a sentence end
    unless it is visibly part of an initials run in either direction.
    """
    done, rest = split_sentences('I think. Yes, exactly.')
    assert done == ['I think.'], done
    assert rest == 'Yes, exactly.'


def test_a_run_of_initials_is_one_utterance():
    """The other direction, and the reason the scan looks more than one
    boundary ahead: 'J. R. R. Tolkien' has two false ends before the real one,
    and a splitter that only peeked one ahead said 'J. R.' out loud."""
    done, _ = split_sentences('J. R. R. Tolkien wrote it. Yes.')
    assert done == ['J. R. R. Tolkien wrote it.'], done


def test_a_quoted_letter_is_still_a_sentence_end():
    """The conservative side of the same rule: one initial, then a real end."""
    done, rest = split_sentences('He said J. Then left.')
    assert done == ['He said J.'], done
    assert rest == 'Then left.'
